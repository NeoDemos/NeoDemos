#!/usr/bin/env python3
"""
Chunk-Level Gazetteer Enrichment for document_chunks.key_entities
==================================================================

Quick-win extension to the Tier 2 enrichment pass
(scripts/enrich_and_extract.py). That pass only matched the PARENT
DOCUMENT TITLE against the domain gazetteer, which leaves chunks that
mention Rotterdam locations, projects, programmes, or organisations
only in their body text with empty key_entities -- causing location-
query recall failures in the retrieval layer.

This script closes that gap by scanning every chunk's content against
data/knowledge_graph/domain_gazetteer.json using pre-compiled word-
boundary regexes and UNIONing matches into the existing text[]
key_entities column. Existing entries from the doc-title pass are
preserved; duplicates are removed.

See docs/handoffs/WS1_GRAPHRAG.md (line 45) for the quick-win context.

Usage:
    python scripts/enrich_chunks_gazetteer.py --dry-run --limit 1000
    python scripts/enrich_chunks_gazetteer.py --only-empty
    python scripts/enrich_chunks_gazetteer.py --resume

Flags:
    --dry-run           Scan and report only; no DB writes.
    --limit N           Process only the first N chunks.
    --resume            Resume from data/pipeline_state/gazetteer_chunk_checkpoint.json.
    --batch-size N      UPDATE batch size (default 500).
    --only-empty        Skip chunks whose key_entities is already populated.
    --wait-for-lock     Block waiting on advisory lock 42 (default).
    --no-wait-for-lock  Fail fast if advisory lock 42 is held.
    --log-level         INFO (default) or DEBUG.

Coordinates with other enrichment jobs via pg_advisory_lock(42).
"""

import os
import sys
import json
import re
import argparse
import logging
import time
from pathlib import Path
from datetime import datetime

# -- Project bootstrap ------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import psycopg2
from psycopg2.extras import RealDictCursor, execute_batch
from tqdm import tqdm

# -- Configuration ----------------------------------------------------

def _resolve_dsn() -> str:
    """
    Resolve the DB DSN: prefer DATABASE_URL, else assemble from
    DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD. Mirrors the
    resolution in services/db_pool.py.
    """
    db_url = os.getenv("DATABASE_URL", "")
    if db_url:
        return db_url
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "neodemos")
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "postgres")
    return f"host={host} port={port} dbname={name} user={user} password={password}"


DB_URL = _resolve_dsn()
GAZETTEER_PATH = PROJECT_ROOT / "data" / "knowledge_graph" / "domain_gazetteer.json"
CHECKPOINT_DIR = PROJECT_ROOT / "data" / "pipeline_state"
CHECKPOINT_PATH = CHECKPOINT_DIR / "gazetteer_chunk_checkpoint.json"
LOG_PATH = PROJECT_ROOT / "logs" / "enrich_chunks_gazetteer.log"

# Advisory lock key shared across enrichment jobs.
ADVISORY_LOCK_KEY = 42

# Gazetteer list keys that feed key_entities. Parties are excluded on
# purpose -- they are handled elsewhere and their short aliases cause
# false positives.
GAZETTEER_LISTS = [
    "organisations",
    "projects",
    "programmes",
    "locations",
    "committees",
    "rotterdam_places",
]

# Minimum term length for a gazetteer entry to be compiled. Shorter
# entries (e.g. "de", "HR") produce too many false positives even with
# word boundaries.
MIN_TERM_LEN = 3

# -- Logging ----------------------------------------------------------

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# -- Gazetteer loading ------------------------------------------------

class GazetteerMatcher:
    """
    Pre-compiles a word-boundary regex for every gazetteer term in the
    six allowed lists and exposes a single `match(text)` call that
    returns the set of matched canonical forms plus a per-list tally.

    One compiled pattern per term keeps matching trivially parallel-
    friendly and makes it cheap to track which list each hit came from.
    """

    def __init__(self, raw: dict):
        self.patterns: list[tuple[re.Pattern, str, str]] = []  # (regex, canonical, list_key)
        self.canonical_to_list: dict[str, str] = {}
        self.per_list_counts: dict[str, int] = {k: 0 for k in GAZETTEER_LISTS}
        self._build(raw)

    def _build(self, raw: dict) -> None:
        seen_lower: set[str] = set()
        for list_key in GAZETTEER_LISTS:
            entries = raw.get(list_key, [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, str):
                    continue
                canonical = entry.strip()
                if not canonical:
                    continue
                lower = canonical.lower()
                if len(lower) < MIN_TERM_LEN:
                    continue
                # De-dup across lists: first list wins for attribution.
                if lower in seen_lower:
                    continue
                seen_lower.add(lower)
                pattern = re.compile(
                  rf"\b{re.escape(canonical)}\b",
                  re.IGNORECASE,
                )
                self.patterns.append((pattern, canonical, list_key))
                self.canonical_to_list[canonical] = list_key

        log.info(
            f"Gazetteer compiled: {len(self.patterns)} unique terms across "
            f"{len(GAZETTEER_LISTS)} lists"
        )

    def match(self, text: str) -> set[str]:
        """Return the set of canonical terms that appear in `text`."""
        if not text:
            return set()
        found: set[str] = set()
        for pattern, canonical, list_key in self.patterns:
            if pattern.search(text):
                if canonical not in found:
                    found.add(canonical)
                    self.per_list_counts[list_key] += 1
        return found


def load_gazetteer() -> GazetteerMatcher:
    with open(GAZETTEER_PATH) as f:
        raw = json.load(f)
    return GazetteerMatcher(raw)


# -- Merge helper -----------------------------------------------------

def merge_entities(existing: list[str] | None, additions: set[str]) -> list[str]:
    """
    Union existing key_entities with gazetteer additions. Case-
    insensitive dedup keeps whichever canonical form was seen first
    (existing entries win, preserving the doc-title pass output).
    """
    merged: list[str] = []
    seen: set[str] = set()

    if existing:
        for term in existing:
            if not term:
                continue
            key = term.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(term)

    for term in additions:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        merged.append(term)

    return merged


# -- Checkpoint -------------------------------------------------------

def load_checkpoint() -> dict:
    if CHECKPOINT_PATH.exists():
        with open(CHECKPOINT_PATH) as f:
            return json.load(f)
    return {"last_chunk_id": 0, "processed": 0}


def save_checkpoint(last_chunk_id: int, processed: int) -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    with open(CHECKPOINT_PATH, "w") as f:
        json.dump(
            {
                "last_chunk_id": last_chunk_id,
                "processed": processed,
                "ts": datetime.now().isoformat(),
            },
            f,
        )


# -- Advisory lock ----------------------------------------------------

def acquire_advisory_lock(conn, wait: bool) -> None:
    """
    Acquire cross-job advisory lock ADVISORY_LOCK_KEY. In blocking mode
    uses pg_advisory_lock; otherwise pg_try_advisory_lock and exits on
    contention.
    """
    cur = conn.cursor()
    if wait:
        log.info(f"Waiting for advisory lock {ADVISORY_LOCK_KEY} (blocking)...")
        cur.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        log.info(f"Advisory lock {ADVISORY_LOCK_KEY} acquired")
    else:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
        acquired = cur.fetchone()[0]
        if not acquired:
            log.error(
                f"Advisory lock {ADVISORY_LOCK_KEY} is held by another job "
                f"and --no-wait-for-lock was set. Exiting."
            )
            cur.close()
            sys.exit(2)
        log.info(f"Advisory lock {ADVISORY_LOCK_KEY} acquired (non-blocking)")
    cur.close()


def release_advisory_lock(conn) -> None:
    try:
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
        cur.close()
        log.info(f"Advisory lock {ADVISORY_LOCK_KEY} released")
    except Exception:
        # Connection may already be closed; surface but do not mask
        # whatever brought us to the finally.
        log.warning(f"Could not release advisory lock {ADVISORY_LOCK_KEY}", exc_info=True)


# -- Batch write ------------------------------------------------------

UPDATE_SQL = "UPDATE document_chunks SET key_entities = %s WHERE id = %s"


def write_batch(cur, batch: list[tuple[list[str], int]]) -> None:
    execute_batch(cur, UPDATE_SQL, batch, page_size=len(batch))


# -- Stats ------------------------------------------------------------

class Stats:
    def __init__(self):
        self.processed = 0
        self.chunks_updated = 0
        self.new_terms_added = 0
        self.start_time = time.time()

    def report(self, matcher: GazetteerMatcher) -> str:
        elapsed = time.time() - self.start_time
        rate = self.processed / elapsed if elapsed > 0 else 0
        per_list = " ".join(f"{k}={v:,}" for k, v in matcher.per_list_counts.items())
        return (
            f"processed={self.processed:,} | "
            f"updated={self.chunks_updated:,} | "
            f"new_terms={self.new_terms_added:,} | "
            f"{rate:,.0f} rows/s | "
            f"[{per_list}]"
        )


# -- Main enrichment loop ---------------------------------------------

def run(
    dry_run: bool,
    limit: int | None,
    resume: bool,
    batch_size: int,
    only_empty: bool,
    wait_for_lock: bool,
) -> None:
    log.info("=" * 64)
    log.info("  CHUNK GAZETTEER ENRICHMENT")
    log.info(f"  Dry run:     {dry_run}")
    log.info(f"  Limit:       {limit or 'unlimited'}")
    log.info(f"  Resume:      {resume}")
    log.info(f"  Batch size:  {batch_size}")
    log.info(f"  Only-empty:  {only_empty}")
    log.info(f"  Wait lock:   {wait_for_lock}")
    log.info("=" * 64)

    matcher = load_gazetteer()

    # Separate read/write connections so the server-side cursor is not
    # disrupted by commits on the write side.
    read_conn = psycopg2.connect(DB_URL)
    write_conn = psycopg2.connect(DB_URL)
    write_cur = write_conn.cursor()

    # Advisory lock goes on the write connection so it is held for the
    # duration of any UPDATEs.
    acquire_advisory_lock(write_conn, wait=wait_for_lock)

    checkpoint = load_checkpoint() if resume else {"last_chunk_id": 0, "processed": 0}
    start_id = checkpoint["last_chunk_id"]
    already_processed = checkpoint["processed"] if resume else 0

    if resume and start_id > 0:
        log.info(f"Resuming from chunk id > {start_id} ({already_processed:,} already done)")

    # -- Count remaining rows for progress bar -----------------------
    count_cur = read_conn.cursor()
    if only_empty:
        count_cur.execute(
            """
            SELECT COUNT(*) FROM document_chunks
            WHERE id > %s
              AND content IS NOT NULL
              AND (cardinality(key_entities) = 0 OR key_entities IS NULL)
            """,
            (start_id,),
        )
    else:
        count_cur.execute(
            """
            SELECT COUNT(*) FROM document_chunks
            WHERE id > %s AND content IS NOT NULL
            """,
            (start_id,),
        )
    total = count_cur.fetchone()[0]
    count_cur.close()

    if limit:
        total = min(total, limit)

    log.info(f"Total chunks to process: {total:,}")

    # -- Server-side cursor for memory-efficient read ---------------
    read_cur = read_conn.cursor("gazetteer_reader", cursor_factory=RealDictCursor)
    read_cur.itersize = 2000

    if only_empty:
        query = """
            SELECT id, content, key_entities
            FROM document_chunks
            WHERE id > %s
              AND content IS NOT NULL
              AND (cardinality(key_entities) = 0 OR key_entities IS NULL)
            ORDER BY id
        """
    else:
        query = """
            SELECT id, content, key_entities
            FROM document_chunks
            WHERE id > %s AND content IS NOT NULL
            ORDER BY id
        """
    read_cur.execute(query, (start_id,))

    stats = Stats()
    batch: list[tuple[list[str], int]] = []
    last_chunk_id = start_id
    rows_seen = 0

    pbar = tqdm(total=total, initial=0, desc="Gazetteer enrich", unit="chunk")

    try:
        for row in read_cur:
            if limit and rows_seen >= limit:
                break

            chunk_id = row["id"]
            content = row["content"] or ""
            existing = row["key_entities"] or []

            matches = matcher.match(content)
            stats.processed += 1
            rows_seen += 1
            last_chunk_id = chunk_id
            pbar.update(1)

            if not matches:
                continue

            merged = merge_entities(existing, matches)
            # Only write when the merge actually changes anything.
            if len(merged) == len(existing):
                continue

            added = len(merged) - len(existing)
            stats.chunks_updated += 1
            stats.new_terms_added += added

            if not dry_run:
                batch.append((merged, chunk_id))
                if len(batch) >= batch_size:
                    write_batch(write_cur, batch)
                    write_conn.commit()
                    batch = []

            if rows_seen % 10_000 == 0:
                log.info(f"[{rows_seen:>10,}] {stats.report(matcher)}")

            if rows_seen % 50_000 == 0:
                save_checkpoint(last_chunk_id, already_processed + rows_seen)

        if batch and not dry_run:
            write_batch(write_cur, batch)
            write_conn.commit()

        save_checkpoint(last_chunk_id, already_processed + rows_seen)

    except KeyboardInterrupt:
        log.warning("Interrupted! Flushing current batch and saving checkpoint...")
        if batch and not dry_run:
            write_batch(write_cur, batch)
            write_conn.commit()
        save_checkpoint(last_chunk_id, already_processed + rows_seen)
        log.info(f"Checkpoint saved at chunk id {last_chunk_id}")
    except Exception:
        log.exception("Fatal error during enrichment")
        save_checkpoint(last_chunk_id, already_processed + rows_seen)
        raise
    finally:
        pbar.close()
        read_cur.close()
        read_conn.close()
        release_advisory_lock(write_conn)
        write_cur.close()
        write_conn.close()

    log.info("=" * 64)
    log.info("  CHUNK GAZETTEER ENRICHMENT COMPLETE")
    log.info(f"  {stats.report(matcher)}")
    log.info(f"  Last chunk id: {last_chunk_id}")
    if dry_run:
        log.info("  DRY RUN -- no rows were written.")
    log.info("=" * 64)


# -- CLI --------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Chunk-level domain gazetteer enrichment for document_chunks.key_entities"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Match and report only; do not UPDATE the database.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process only the first N chunks (for smoke tests).",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from data/pipeline_state/gazetteer_chunk_checkpoint.json.",
    )
    parser.add_argument(
        "--batch-size", type=int, default=500,
        help="UPDATE batch size (default 500).",
    )
    parser.add_argument(
        "--only-empty", action="store_true",
        help="Skip chunks whose key_entities is already populated.",
    )
    parser.add_argument(
        "--wait-for-lock", dest="wait_for_lock", action="store_true",
        help="Block waiting on advisory lock 42 (default).",
    )
    parser.add_argument(
        "--no-wait-for-lock", dest="wait_for_lock", action="store_false",
        help="Fail fast if advisory lock 42 is already held.",
    )
    parser.set_defaults(wait_for_lock=True)
    parser.add_argument(
        "--log-level", default="INFO", choices=["INFO", "DEBUG"],
        help="Log level (default INFO).",
    )
    args = parser.parse_args()

    logging.getLogger().setLevel(getattr(logging, args.log_level))

    run(
        dry_run=args.dry_run,
        limit=args.limit,
        resume=args.resume,
        batch_size=args.batch_size,
        only_empty=args.only_empty,
        wait_for_lock=args.wait_for_lock,
    )


if __name__ == "__main__":
    main()
