#!/usr/bin/env python3
"""
WS6 nightly step 06b — compute cached per-document summaries (batch API).

Tiered summarization with Gemini Batch API (50% cost discount):

  skip     : docs < 500 chars        → ignored
  excerpt  : docs 500–3,000 chars     → first 2-3 sentences, no LLM call
  direct   : docs 3,000–30K chars     → single Gemini call, all chunks fit
  extract  : docs > 30K chars         → reranker selects 25 diverse chunks,
                                        then single Gemini call

Excerpt results are written immediately. Direct + extract prompts are
collected and submitted as a Gemini batch. After the batch completes,
raw results are checkpointed to a local JSONL file, then verified via the
source-span verifier and cached in Postgres.

Performance:
  • Bulk chunk fetching: one DB query per PREFETCH_BATCH docs (default 200)
    instead of one query per document.
  • Phase 1 parallel: ThreadPoolExecutor (--workers, default 8) for Jina
    reranker calls and excerpt writes.
  • Phase 3 parallel: same pool for post-batch verification + DB writes.

Resilience:
  • After Phase 2 (Gemini batch), all raw results are written to
    logs/ws6_results_YYYYMMDD-HHMM.jsonl BEFORE any DB writes.
  • If Phase 3 fails (e.g. DB outage), re-run with:
      --replay-from logs/ws6_results_YYYYMMDD-HHMM.jsonl
    to skip Phase 1+2 and replay only Phase 3 from the saved file,
    re-fetching chunks from DB per doc.

Safety:
  • Postgres advisory lock (WS6_SUMMARIES_LOCK_KEY) — only one instance.
  • Aborts if conflicting pipeline detected (ps check).
  • --max-docs cap for budget control.
  • --dry-run: lists candidates and tier distribution, writes nothing.

Usage:
    python scripts/nightly/06b_compute_summaries.py \\
        --max-docs 86100 [--dry-run] [--workers 8] [--log-level DEBUG]

    # Replay failed Phase 3 from checkpoint:
    python scripts/nightly/06b_compute_summaries.py \\
        --replay-from logs/ws6_results_20260413-1931.jsonl [--workers 8]

Handoff: docs/handoffs/WS6_SUMMARIZATION.md
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Dict, List, Optional, Tuple

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_REPO_ROOT, ".env"))

from services.db_pool import get_connection  # noqa: E402
from services.summarizer import Summarizer  # noqa: E402
from services.storage_ws6 import (  # noqa: E402
    get_chunks_bulk,
    get_all_chunks_for_document,
    list_documents_needing_summary,
    update_document_summary_columns,
)

log = logging.getLogger("ws6.06b_compute_summaries")

WS6_SUMMARIES_LOCK_KEY: int = 7_640_601
PREFETCH_BATCH: int = 200  # docs per bulk chunk-fetch query
RESULTS_DIR: str = "logs"  # where JSONL checkpoints are written

CONFLICTING_PROCESSES = (
    "committee_notulen_pipeline",
    "run_flair_ner.py",
    "enrich_chunks_gazetteer.py",
    "promote_financial_docs.py",
    "migrate_embeddings.py",
)

# Thread-local Summarizer so each worker has its own lazy-init state.
_tl = threading.local()


def _get_summarizer() -> Summarizer:
    if not hasattr(_tl, "summarizer"):
        _tl.summarizer = Summarizer()
    return _tl.summarizer


# ── CLI + logging ───────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--max-docs", type=int, default=500)
    p.add_argument("--min-content-chars", type=int, default=500)
    p.add_argument("--workers", type=int, default=8,
                   help="ThreadPoolExecutor workers for Phase 1 + Phase 3 (default: 8).")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="Skip conflicting-pipeline check.")
    p.add_argument("--replay-from", metavar="JSONL_FILE",
                   help="Skip Phase 1+2. Replay Phase 3 from a saved results JSONL checkpoint.")
    p.add_argument("--log-level", default="INFO",
                   choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return p.parse_args()


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


# ── Safety checks ───────────────────────────────────────────────────────

def _is_conflicting_process_running() -> Optional[str]:
    try:
        out = subprocess.run(
            ["ps", "auxww"], capture_output=True, text=True, check=True, timeout=10,
        ).stdout
    except Exception:
        return None
    for proc in CONFLICTING_PROCESSES:
        if proc in out:
            return proc
    return None


def _try_advisory_lock() -> bool:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (WS6_SUMMARIES_LOCK_KEY,))
            row = cur.fetchone()
            return bool(row and row[0])


def _release_advisory_lock() -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (WS6_SUMMARIES_LOCK_KEY,))


# ── Stats tracking ──────────────────────────────────────────────────────

@dataclass
class RunStats:
    considered: int = 0
    tier_counts: dict = field(default_factory=lambda: Counter())
    excerpts_written: int = 0
    batch_submitted: int = 0
    batch_ok: int = 0
    batch_verified: int = 0
    batch_partial: int = 0
    errors: int = 0
    skipped_no_chunks: int = 0
    started_at: float = field(default_factory=time.monotonic)

    def summary(self) -> dict:
        return {
            "considered": self.considered,
            "tiers": dict(self.tier_counts),
            "excerpts_written": self.excerpts_written,
            "batch_submitted": self.batch_submitted,
            "batch_ok": self.batch_ok,
            "batch_verified": self.batch_verified,
            "batch_partial": self.batch_partial,
            "errors": self.errors,
            "skipped_no_chunks": self.skipped_no_chunks,
            "elapsed_s": round(time.monotonic() - self.started_at, 1),
        }


# ── Helpers ─────────────────────────────────────────────────────────────

def _wrap_chunks(rows: List[dict]) -> List[SimpleNamespace]:
    """Wrap storage dicts as attribute-access objects for the Summarizer."""
    return [
        SimpleNamespace(
            chunk_id=r["chunk_id"],
            document_id=r["document_id"],
            title=r.get("title") or "",
            content=r.get("content") or "",
        )
        for r in rows
    ]


def _checkpoint_path() -> str:
    """Return a timestamped path for the results JSONL checkpoint."""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    return os.path.join(RESULTS_DIR, f"ws6_results_{time.strftime('%Y%m%d-%H%M')}.jsonl")


def _write_checkpoint(results: Dict[str, str], path: str) -> None:
    """Write raw Gemini results to a JSONL file before any DB writes."""
    with open(path, "w", encoding="utf-8") as f:
        for doc_id, raw_text in results.items():
            f.write(json.dumps({"doc_id": doc_id, "raw_text": raw_text}, ensure_ascii=False) + "\n")
    log.info("Checkpoint written: %s (%d results)", path, len(results))


def _read_checkpoint(path: str) -> Dict[str, str]:
    """Read a JSONL checkpoint back into {doc_id: raw_text}."""
    results: Dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            results[obj["doc_id"]] = obj["raw_text"]
    log.info("Loaded checkpoint: %s (%d results)", path, len(results))
    return results


# ── Per-document worker (Phase 1) ────────────────────────────────────────

def _process_doc(
    doc_id: str,
    doc_name: str,
    chunk_rows: List[dict],
    dry_run: bool,
    stats: RunStats,
    stats_lock: threading.Lock,
) -> Optional[Tuple[str, str, List]]:
    """Process one document.

    Returns (doc_id, prompt, verify_chunks) for LLM-tier docs, else None.
    Side-effects: writes excerpt results to DB; updates stats under stats_lock.
    """
    if not chunk_rows:
        with stats_lock:
            stats.skipped_no_chunks += 1
        return None

    summarizer = _get_summarizer()
    chunks = _wrap_chunks(chunk_rows)
    tier = summarizer.classify_tier(chunks)

    with stats_lock:
        stats.tier_counts[tier] += 1

    if tier == "skip":
        return None

    if tier == "excerpt":
        if dry_run:
            log.info(f"[DRY-RUN] {doc_id} tier=excerpt '{doc_name[:50]}'")
            return None
        result = summarizer.build_excerpt(chunks)
        ok = update_document_summary_columns(
            doc_id,
            summary_short=result.text,
            summary_verified=result.verified,
        )
        with stats_lock:
            if ok:
                stats.excerpts_written += 1
            else:
                stats.errors += 1
        return None

    # direct or extract — need LLM call
    try:
        prompt, verify_chunks = summarizer.build_prompt(chunks)
    except ValueError as e:
        log.warning(f"{doc_id}: build_prompt failed: {e}")
        with stats_lock:
            stats.errors += 1
        return None

    if dry_run:
        log.info(
            f"[DRY-RUN] {doc_id} tier={tier} chunks={len(chunks)} "
            f"verify_chunks={len(verify_chunks)} '{doc_name[:50]}'"
        )
        return None

    return (doc_id, prompt, verify_chunks)


# ── Per-result worker (Phase 3) ──────────────────────────────────────────

def _verify_and_write(
    doc_id: str,
    raw_text: str,
    verify_chunks: List,
    stats: RunStats,
    stats_lock: threading.Lock,
) -> None:
    """Verify a batch result and write to DB. Runs in a worker thread.

    If verify_chunks is empty (replay mode), chunks are fetched from DB.
    """
    # Replay mode: re-fetch chunks from DB
    if not verify_chunks:
        chunk_rows = get_all_chunks_for_document(doc_id)
        verify_chunks = _wrap_chunks(chunk_rows)

    if not verify_chunks:
        log.warning(f"{doc_id}: no chunks found for verification, skipping")
        with stats_lock:
            stats.errors += 1
        return

    summarizer = _get_summarizer()
    try:
        result = summarizer.verify_and_build_result(
            raw_text,
            verify_chunks,
            mode="short",
            tier=summarizer.classify_tier(verify_chunks),
        )
    except Exception as e:
        log.exception(f"{doc_id}: verification failed: {e}")
        with stats_lock:
            stats.errors += 1
        return

    if not result.text:
        with stats_lock:
            stats.errors += 1
        return

    ok = update_document_summary_columns(
        doc_id,
        summary_short=result.text,
        summary_verified=result.verified,
    )
    with stats_lock:
        if ok:
            stats.batch_ok += 1
            if result.verified:
                stats.batch_verified += 1
            else:
                stats.batch_partial += 1
        else:
            stats.errors += 1

    log.info(
        "%s: tier=%s verified=%s stripped=%d/%d",
        doc_id, result.tier, result.verified,
        result.stripped_count, result.total_sentences,
    )


def _run_phase3(
    results: Dict[str, str],
    verify_map: Dict[str, List],
    stats: RunStats,
    stats_lock: threading.Lock,
    workers: int,
) -> None:
    """Parallel Phase 3: verify + write all batch results."""
    log.info(f"Phase3: verifying and writing {len(results)} results...")
    phase3_start = time.monotonic()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _verify_and_write,
                doc_id,
                raw_text,
                verify_map.get(doc_id, []),
                stats,
                stats_lock,
            ): doc_id
            for doc_id, raw_text in results.items()
        }
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                log.exception(f"{futures[future]}: phase3 worker raised: {e}")
                with stats_lock:
                    stats.errors += 1

    phase3_elapsed = round(time.monotonic() - phase3_start, 1)
    log.info(f"Phase3 done in {phase3_elapsed}s.")


# ── Main ────────────────────────────────────────────────────────────────

def main() -> int:
    args = _parse_args()
    _setup_logging(args.log_level)

    # ── Replay mode: skip Phase 1+2, run Phase 3 from JSONL checkpoint ──
    if args.replay_from:
        log.info(f"REPLAY MODE: loading results from {args.replay_from}")
        if not os.path.exists(args.replay_from):
            log.error(f"Checkpoint file not found: {args.replay_from}")
            return 1

        # Advisory lock still required to prevent concurrent replays
        try:
            got = _try_advisory_lock()
        except Exception as e:
            log.exception(f"Advisory-lock acquisition failed: {e}")
            return 1
        if not got:
            log.error(f"Another 06b run holds advisory lock {WS6_SUMMARIES_LOCK_KEY}.")
            return 1

        stats = RunStats()
        stats_lock = threading.Lock()
        try:
            results = _read_checkpoint(args.replay_from)
            stats.batch_submitted = len(results)
            # verify_map is empty — _verify_and_write will re-fetch chunks from DB
            _run_phase3(results, {}, stats, stats_lock, args.workers)
        finally:
            try:
                _release_advisory_lock()
            except Exception as e:
                log.warning(f"Failed to release advisory lock: {e}")

        log.info(f"Replay summary: {stats.summary()}")
        return 0

    # ── Normal mode ─────────────────────────────────────────────────────

    # 1. Conflict check
    if not args.force:
        conflict = _is_conflicting_process_running()
        if conflict:
            log.error(f"Refusing to run: conflicting process '{conflict}' is active.")
            return 2

    # 2. Advisory lock
    try:
        got = _try_advisory_lock()
    except Exception as e:
        log.exception(f"Advisory-lock acquisition failed: {e}")
        return 1
    if not got:
        log.error(f"Another 06b run holds advisory lock {WS6_SUMMARIES_LOCK_KEY}.")
        return 1

    stats = RunStats()
    stats_lock = threading.Lock()

    try:
        # 3. List candidates
        candidates = list_documents_needing_summary(
            limit=args.max_docs,
            min_content_chars=args.min_content_chars,
        )
        stats.considered = len(candidates)
        log.info(
            f"Found {stats.considered} documents needing summaries. "
            f"workers={args.workers} prefetch_batch={PREFETCH_BATCH}"
        )

        if not candidates:
            log.info("Nothing to do.")
            return 0

        # 4. Phase 1 — classify, excerpt, collect batch prompts
        batch_items: List[Tuple[str, str, List]] = []  # (doc_id, prompt, verify_chunks)

        phase1_start = time.monotonic()
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for prefetch_start in range(0, len(candidates), PREFETCH_BATCH):
                prefetch_slice = candidates[prefetch_start : prefetch_start + PREFETCH_BATCH]
                doc_ids = [r["id"] for r in prefetch_slice]
                doc_names = {r["id"]: (r.get("name") or "") for r in prefetch_slice}

                chunks_map = get_chunks_bulk(doc_ids)

                futures = {
                    pool.submit(
                        _process_doc,
                        doc_id,
                        doc_names[doc_id],
                        chunks_map.get(doc_id, []),
                        args.dry_run,
                        stats,
                        stats_lock,
                    ): doc_id
                    for doc_id in doc_ids
                }

                for future in as_completed(futures):
                    try:
                        result = future.result()
                    except Exception as e:
                        log.exception(f"{futures[future]}: worker raised: {e}")
                        with stats_lock:
                            stats.errors += 1
                        continue
                    if result is not None:
                        batch_items.append(result)

                done_so_far = prefetch_start + len(prefetch_slice)
                log.info(
                    f"Phase1 progress: {done_so_far}/{stats.considered} scanned | "
                    f"excerpts={stats.excerpts_written} batch_q={len(batch_items)} "
                    f"errors={stats.errors}"
                )

        phase1_elapsed = round(time.monotonic() - phase1_start, 1)
        stats.batch_submitted = len(batch_items)
        log.info(
            f"Phase1 done in {phase1_elapsed}s. "
            f"Tiers: {dict(stats.tier_counts)}. "
            f"Excerpts: {stats.excerpts_written}. Batch queue: {stats.batch_submitted}."
        )

        if args.dry_run or not batch_items:
            log.info(f"Summary: {stats.summary()}")
            return 0

        # 5. Phase 2 — submit to Gemini Batch API
        log.info(f"Submitting {len(batch_items)} prompts to Gemini Batch API...")
        from services.gemini_batch import run_batch

        prompts_map: Dict[str, str] = {item[0]: item[1] for item in batch_items}
        verify_map: Dict[str, List] = {item[0]: item[2] for item in batch_items}

        try:
            results = run_batch(
                prompts=prompts_map,
                display_name=f"ws6-nightly-{time.strftime('%Y%m%d-%H%M')}",
            )
        except RuntimeError as e:
            log.error(f"Batch API failed: {e}")
            stats.errors += len(batch_items)
            log.info(f"Summary: {stats.summary()}")
            return 1

        # 5b. Checkpoint raw results to JSONL before any DB writes
        checkpoint_path = _checkpoint_path()
        try:
            _write_checkpoint(results, checkpoint_path)
        except Exception as e:
            log.error(f"Failed to write checkpoint: {e}. Continuing without checkpoint.")

        # 6. Phase 3 — parallel verify + write
        _run_phase3(results, verify_map, stats, stats_lock, args.workers)

        # 7. Report missing results
        missing = set(prompts_map.keys()) - set(results.keys())
        if missing:
            log.warning(
                f"{len(missing)} docs submitted but got no response: {list(missing)[:5]}..."
            )
            stats.errors += len(missing)

    finally:
        try:
            _release_advisory_lock()
        except Exception as e:
            log.warning(f"Failed to release advisory lock: {e}")

    log.info(f"Summary: {stats.summary()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
