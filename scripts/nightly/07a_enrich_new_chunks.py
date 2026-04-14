#!/usr/bin/env python3
"""
WS7 nightly step 07a — re-enrich chunks that have no kg_mentions.

After the OCR recovery pipeline re-chunks garbled moties, the new chunks
have no kg_mentions entries yet.  This script finds those chunks and
delegates to run_flair_ner.py (via subprocess) to fill the gap.

Why subprocess instead of a direct import?
  run_flair_ner.py manages its own advisory lock 42 lifecycle and its own
  DB connections.  Importing and calling run() directly would require us
  to coordinate lock ownership across connections.  The subprocess approach
  is simpler: we check lock 42 is free, count unenriched chunks, release our
  probe connection, then hand off to Flair which re-acquires the lock cleanly.

Lock protocol
  1. Probe: pg_try_advisory_lock(42) on a short-lived connection.
     • If already held → another enrichment job is running; exit 0 (skip).
     • If acquired → note the count of unenriched chunks, then release
       (pg_advisory_unlock) before launching the subprocess.
  2. Flair subprocess acquires lock 42 independently via its own --resume run.

Flair will naturally process the new chunks because:
  • New chunks from re-chunking always receive IDs higher than the checkpoint
    watermark stored in data/pipeline_state/flair_ner_checkpoint.json.
  • --resume makes Flair continue from that watermark, catching all new IDs.

Usage:
    python scripts/nightly/07a_enrich_new_chunks.py
    python scripts/nightly/07a_enrich_new_chunks.py --max-chunks 5000
    python scripts/nightly/07a_enrich_new_chunks.py --dry-run
    python scripts/nightly/07a_enrich_new_chunks.py --max-chunks 500 --log-level DEBUG

Handoff: docs/handoffs/done/WS7_OCR_RECOVERY.md
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

# ── Project bootstrap ──────────────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv
load_dotenv(_REPO_ROOT / ".env")

FLAIR_SCRIPT = _REPO_ROOT / "scripts" / "run_flair_ner.py"
LOG_PATH = _REPO_ROOT / "logs" / "enrich_new_chunks.log"

ADVISORY_LOCK_KEY = 42

# ── Logging ────────────────────────────────────────────────────────────

log = logging.getLogger("ws7.07a_enrich_new_chunks")


def _setup_logging(level: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    file_h = logging.FileHandler(LOG_PATH)
    file_h.setFormatter(fmt)

    stream_h = logging.StreamHandler()
    stream_h.setFormatter(fmt)

    logging.basicConfig(level=getattr(logging, level), handlers=[file_h, stream_h])
    log.setLevel(getattr(logging, level))
    log.propagate = False


# ── CLI ────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument(
        "--max-chunks", type=int, default=5000,
        help="Maximum chunks to enrich in one run (default: 5000).",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Report how many chunks need enrichment; do not run Flair.",
    )
    p.add_argument(
        "--log-level", default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    return p.parse_args()


# ── Advisory lock helpers ──────────────────────────────────────────────

def _probe_and_release_lock() -> bool:
    """
    Try to acquire advisory lock 42 on a short-lived connection.

    Returns True if the lock was free (and is now released so the Flair
    subprocess can take it).  Returns False if another job holds it.

    We acquire + release rather than just inspect pg_locks so that the
    semantics are identical to what Flair itself does: if Flair is running,
    pg_try_advisory_lock returns false; if it isn't, we get the lock,
    confirm the coast is clear, and hand off.
    """
    import psycopg2

    dsn = _resolve_dsn()
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
            got: bool = bool(cur.fetchone()[0])
            conn.commit()

        if got:
            # Release immediately so the Flair subprocess can acquire it.
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
                conn.commit()

        return got
    finally:
        conn.close()


def _resolve_dsn() -> str:
    db_url = os.getenv("DATABASE_URL", "").strip()
    if db_url:
        return db_url
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "neodemos")
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "postgres")
    return f"host={host} port={port} dbname={name} user={user} password={password}"


# ── Unenriched chunk discovery ─────────────────────────────────────────

def _count_unenriched_chunks(limit: int) -> int:
    """
    Return the number of chunks that have no kg_mentions, up to `limit`.

    We cap the count at `limit` so we can log a meaningful "N chunks need
    enrichment" message without a full-table scan on large backlogs.
    """
    import psycopg2

    dsn = _resolve_dsn()
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT COUNT(*) FROM (
                    SELECT dc.id
                    FROM document_chunks dc
                    LEFT JOIN kg_mentions km ON km.chunk_id = dc.id
                    WHERE km.chunk_id IS NULL
                      AND dc.content IS NOT NULL
                      AND LENGTH(dc.content) > 50
                    ORDER BY dc.id
                    LIMIT %s
                ) AS capped
                """,
                (limit,),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0
    finally:
        conn.close()


# ── Flair subprocess ───────────────────────────────────────────────────

def _run_flair_subprocess(max_chunks: int, log_level: str) -> int:
    """
    Launch run_flair_ner.py as a subprocess.

    Flags:
      --resume     : continue from the checkpoint watermark (catches new
                     high-ID chunks without re-scanning the whole table)
      --limit N    : stop after N chunks so nightly runs stay bounded
      --log-level  : propagate our log level

    Returns the subprocess exit code.
    """
    cmd = [
        sys.executable,
        str(FLAIR_SCRIPT),
        "--resume",
        "--limit", str(max_chunks),
        "--log-level", log_level,
    ]
    log.info("Launching Flair NER subprocess: %s", " ".join(cmd))

    result = subprocess.run(cmd, check=False)

    if result.returncode == 0:
        log.info("Flair NER subprocess completed successfully.")
    elif result.returncode == 2:
        log.warning(
            "Flair NER subprocess exited with code 2 "
            "(advisory lock 42 was taken — another enrichment run started concurrently)."
        )
    else:
        log.error(
            "Flair NER subprocess exited with code %d.", result.returncode
        )
    return result.returncode


# ── Main ───────────────────────────────────────────────────────────────

def main() -> int:
    args = _parse_args()
    _setup_logging(args.log_level)

    log.info("=" * 64)
    log.info("  07a_enrich_new_chunks — WS7 nightly re-enrichment")
    log.info("  max_chunks=%d dry_run=%s", args.max_chunks, args.dry_run)
    log.info("=" * 64)

    # 1. Probe advisory lock 42.
    try:
        lock_free = _probe_and_release_lock()
    except Exception:
        log.exception("Failed to probe advisory lock %d — aborting.", ADVISORY_LOCK_KEY)
        return 1

    if not lock_free:
        log.info(
            "enrichment already running (advisory lock %d is held); skipping.",
            ADVISORY_LOCK_KEY,
        )
        return 0

    # 2. Count unenriched chunks.
    try:
        unenriched = _count_unenriched_chunks(limit=args.max_chunks)
    except Exception:
        log.exception("Failed to query unenriched chunks — aborting.")
        return 1

    if unenriched == 0:
        log.info("no new chunks to enrich, exiting.")
        return 0

    log.info(
        "Found %d chunk(s) without kg_mentions (cap=%d).",
        unenriched, args.max_chunks,
    )

    # 3. Dry-run exits here.
    if args.dry_run:
        log.info("[DRY-RUN] Would run Flair NER on up to %d chunks. Exiting.", args.max_chunks)
        return 0

    # 4. Delegate to Flair NER.
    rc = _run_flair_subprocess(max_chunks=args.max_chunks, log_level=args.log_level)

    log.info("=" * 64)
    log.info("  07a_enrich_new_chunks done (exit_code=%d)", rc)
    log.info("=" * 64)

    # Exit 0 even if Flair exited with code 2 (lock race — transient, harmless).
    # Exit 1 for genuine Flair failures so cron can alert.
    if rc in (0, 2):
        return 0
    return rc


if __name__ == "__main__":
    sys.exit(main())
