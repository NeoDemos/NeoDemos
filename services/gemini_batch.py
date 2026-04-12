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

POLL_INITIAL_INTERVAL: int = 15
POLL_MAX_INTERVAL: int = 120
POLL_BACKOFF_FACTOR: float = 1.5


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

    # ── Phase 1: Submit ALL sub-batches upfront ────────────────────────
    jobs: List[Tuple[int, object]] = []  # (batch_idx, job)
    for batch_idx, batch_requests in enumerate(sub_batches):
        batch_label = f"{display_name}-{batch_idx + 1}of{len(sub_batches)}"
        logger.info(
            "gemini_batch: submitting sub-batch %d/%d (%d requests)",
            batch_idx + 1, len(sub_batches), len(batch_requests),
        )
        try:
            job = client.batches.create(
                model=model,
                src=batch_requests,
                config={"display_name": batch_label},
            )
            jobs.append((batch_idx, job))
            logger.info("gemini_batch: job %s created (state=%s)", job.name, job.state)
        except Exception as e:
            logger.error("gemini_batch: failed to submit sub-batch %d: %s", batch_idx + 1, e)
            # Continue submitting remaining batches — don't abort the whole run.

    if not jobs:
        raise RuntimeError("All batch submissions failed.")

    logger.info("gemini_batch: %d/%d jobs submitted. Polling...", len(jobs), len(sub_batches))

    # ── Phase 2: Poll all jobs concurrently ────────────────────────────
    pending = {job.name: (batch_idx, job) for batch_idx, job in jobs}
    completed: List[Tuple[int, object]] = []  # (batch_idx, completed_job)
    interval = POLL_INITIAL_INTERVAL

    while pending:
        time.sleep(interval)
        still_pending = {}
        for job_name, (batch_idx, _) in pending.items():
            try:
                refreshed = client.batches.get(name=job_name)
            except Exception as e:
                logger.warning("gemini_batch: poll failed for %s: %s", job_name, e)
                still_pending[job_name] = (batch_idx, _)
                continue

            if refreshed.state in terminal_states:
                completed.append((batch_idx, refreshed))
                logger.info(
                    "gemini_batch: job %s finished (state=%s) [%d/%d done]",
                    job_name, refreshed.state, len(completed), len(jobs),
                )
            else:
                still_pending[job_name] = (batch_idx, refreshed)

        pending = still_pending
        if pending:
            logger.info(
                "gemini_batch: %d/%d jobs still pending, next poll in %ds",
                len(pending), len(jobs), min(int(interval * POLL_BACKOFF_FACTOR), POLL_MAX_INTERVAL),
            )
            interval = min(interval * POLL_BACKOFF_FACTOR, POLL_MAX_INTERVAL)

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
