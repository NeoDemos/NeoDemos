"""
Gemini Batch API wrapper — WS6.

Submits all sub-batches upfront (parallel), then polls all job IDs
concurrently. This avoids the 18-hour sequential bottleneck for large
backfills (86K docs → ~36 sub-batches).

For small batches (≤ MAX_BATCH_SIZE), it's a single submit + poll.

Public surface:
    results = run_batch(prompts={"doc-1": "prompt...", ...})
    # results: {"doc-1": "Summary text...", ...}

Requires: google-genai >= 1.x, GEMINI_API_KEY from .env.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# 1500 requests × ~12K chars ≈ 18MB — safely under the 20MB inline limit.
MAX_BATCH_SIZE: int = 1500

# Google Gemini Batch API has a per-project concurrent-jobs quota (~17 active).
# We cap below that and interleave submit + poll so we can process >17 sub-batches
# in a single run. If quota tightens, this still makes progress — just slower.
MAX_CONCURRENT_JOBS: int = int(os.getenv("GEMINI_MAX_CONCURRENT_BATCHES", "15"))

POLL_INITIAL_INTERVAL: int = 15
POLL_MAX_INTERVAL: int = 120
POLL_BACKOFF_FACTOR: float = 1.5

# Recovery: every submitted job ID is appended here so a crashed run can be
# resumed via scripts/ws6_save_completed_jobs.py (reads these IDs and polls).
JOB_ID_LOG: str = os.path.join("logs", "ws6_gemini_job_ids.log")


def run_batch(
    prompts: Dict[str, str],
    model: str = "gemini-2.5-flash-lite",
    system_instruction: Optional[str] = None,
    temperature: float = 0.2,
    display_name: str = "ws6-summary-batch",
    api_key: Optional[str] = None,
) -> Dict[str, str]:
    """
    Submit prompts to the Gemini Batch API and wait for results.

    All sub-batches are submitted upfront, then polled concurrently.
    """
    if not prompts:
        return {}

    key = api_key or os.getenv("GEMINI_API_KEY")
    if not key:
        raise ValueError("GEMINI_API_KEY required for batch submission.")

    try:
        import google.genai as genai
        from google.genai.types import (
            GenerateContentConfig,
            InlinedRequest,
            JobState,
        )
    except ImportError as e:
        raise RuntimeError(f"google-genai not installed: {e}")

    client = genai.Client(api_key=key)

    config = GenerateContentConfig(temperature=temperature)
    if system_instruction:
        config.system_instruction = system_instruction

    all_requests = [
        InlinedRequest(
            contents=prompt_text,
            metadata={"key": req_id},
            config=config,
        )
        for req_id, prompt_text in prompts.items()
    ]

    sub_batches = [
        all_requests[i : i + MAX_BATCH_SIZE]
        for i in range(0, len(all_requests), MAX_BATCH_SIZE)
    ]

    logger.info(
        "gemini_batch: %d prompts → %d sub-batch(es) of max %d",
        len(prompts), len(sub_batches), MAX_BATCH_SIZE,
    )

    terminal_states = {
        JobState.JOB_STATE_SUCCEEDED,
        JobState.JOB_STATE_FAILED,
        JobState.JOB_STATE_CANCELLED,
        JobState.JOB_STATE_EXPIRED,
    }

    # ── Submit + poll interleaved with concurrency cap ─────────────────
    # Google Gemini Batch has a per-project concurrent-jobs quota (~17). We
    # keep at most MAX_CONCURRENT_JOBS in flight. When quota is hit, we poll
    # for completions before submitting more. This lets us cover >17 batches
    # in a single run instead of silently dropping the overflow.
    os.makedirs(os.path.dirname(JOB_ID_LOG), exist_ok=True)
    pending_to_submit: List[Tuple[int, list]] = list(enumerate(sub_batches))
    active: Dict[str, Tuple[int, object]] = {}  # {job_name: (batch_idx, job)}
    completed: List[Tuple[int, object]] = []
    total_subs = len(sub_batches)
    interval = POLL_INITIAL_INTERVAL

    while pending_to_submit or active:
        # Submit as many as the concurrency cap allows
        submitted_this_pass = 0
        while pending_to_submit and len(active) < MAX_CONCURRENT_JOBS:
            batch_idx, batch_requests = pending_to_submit[0]
            batch_label = f"{display_name}-{batch_idx + 1}of{total_subs}"
            try:
                job = client.batches.create(
                    model=model,
                    src=batch_requests,
                    config={"display_name": batch_label},
                )
                active[job.name] = (batch_idx, job)
                pending_to_submit.pop(0)
                submitted_this_pass += 1
                with open(JOB_ID_LOG, "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {job.name} batch={batch_idx + 1}of{total_subs}\n")
                logger.info(
                    "gemini_batch: submitted sub-batch %d/%d (%s) [active=%d, pending_submit=%d]",
                    batch_idx + 1, total_subs, job.name, len(active), len(pending_to_submit),
                )
            except Exception as e:
                # Quota full or other transient — stop submitting, wait for slots via polling
                logger.warning(
                    "gemini_batch: submit paused after %d new jobs (active=%d, %d still pending_submit). Reason: %s",
                    submitted_this_pass, len(active), len(pending_to_submit), e,
                )
                break

        if not active:
            # Nothing submitted and nothing active → permanent failure
            raise RuntimeError(
                f"Cannot submit any batches (quota or API error). {len(pending_to_submit)} sub-batches pending."
            )

        # Poll active jobs for completions
        time.sleep(interval)
        for job_name in list(active.keys()):
            batch_idx, _ = active[job_name]
            try:
                refreshed = client.batches.get(name=job_name)
            except Exception as e:
                logger.warning("gemini_batch: poll failed for %s: %s", job_name, e)
                continue
            if refreshed.state in terminal_states:
                completed.append((batch_idx, refreshed))
                del active[job_name]
                logger.info(
                    "gemini_batch: job %s finished (state=%s) [%d/%d done, %d active, %d pending_submit]",
                    job_name, refreshed.state, len(completed), total_subs, len(active), len(pending_to_submit),
                )

        # Only back off polling if nothing changed and nothing to submit
        if active and submitted_this_pass == 0:
            interval = min(interval * POLL_BACKOFF_FACTOR, POLL_MAX_INTERVAL)
        else:
            interval = POLL_INITIAL_INTERVAL  # reset so new submissions get prompt polling

    # ── Phase 3: Collect results from all completed jobs ───────────────
    all_results: Dict[str, str] = {}
    total_ok = 0
    total_err = 0

    for batch_idx, job in sorted(completed, key=lambda x: x[0]):
        if job.state != JobState.JOB_STATE_SUCCEEDED:
            error_msg = getattr(job, "error", "unknown")
            logger.error(
                "gemini_batch: sub-batch %d failed (state=%s): %s",
                batch_idx + 1, job.state, error_msg,
            )
            continue

        for resp in job.dest.inlined_responses:
            req_key = resp.metadata.get("key", "?") if resp.metadata else "?"
            if resp.response and resp.response.text:
                all_results[req_key] = resp.response.text
                total_ok += 1
            else:
                error_detail = getattr(resp, "error", "no response text")
                logger.warning("gemini_batch: request %s failed: %s", req_key, error_detail)
                total_err += 1

    logger.info(
        "gemini_batch: complete. %d ok, %d failed, %d total submitted",
        total_ok, total_err, len(prompts),
    )
    return all_results
