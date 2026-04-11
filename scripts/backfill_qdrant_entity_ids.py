#!/usr/bin/env python3
"""
Backfill Qdrant point payloads with entity_ids — WS1 GraphRAG Phase 0
======================================================================

Scrolls every point in the Qdrant `notulen_chunks` collection and attaches
an `entity_ids: int[]` payload field pulled from PostgreSQL `kg_mentions`.
Enables fast entity-pre-filtered dense search: a graph walk resolves a set
of entity ids, then the retriever prunes the dense ANN search to only those
chunks that mention at least one of those entities.

Purpose
-------
Qdrant points currently carry:
    ['child_id', 'chunk_type', 'content', 'doc_type', 'document_id',
     'meeting_id', 'start_date', 'title']
No `entity_ids`. This script is the one-shot additive backfill that closes
that gap. It NEVER touches vectors — only `set_payload` operations.

Join model
----------
- Qdrant point id  == document_chunks.id  (same int).
  Confirmed against `scripts/sync_enrichment_to_qdrant.py`, which writes
  payloads with `points=[chunk_id]` where `chunk_id = row["id"]` from
  `document_chunks`.
- `kg_mentions (id, entity_id, chunk_id, raw_mention, created_at)` is the
  sole join table. There is NO `chunk_entities` table.

Safety — READ THIS BEFORE RUNNING
---------------------------------
This script WRITES to Qdrant. Concurrent writes from a background embedding
or migration job can corrupt Qdrant segment files. Before starting:

    1. Confirm no `compute_embeddings.py`, `sync_enrichment_to_qdrant.py`,
       `enrich_qdrant_metadata.py`, or WS5a nightly pipeline is running.
    2. The script acquires `pg_advisory_lock(42)` for the duration of the
       run. Other writer jobs in the repo honor the same lock and will
       block (or bail with --no-wait-for-lock). Reads are never blocked.
    3. A prominent warning is printed at startup; the operator must
       acknowledge no background embedding process is alive.

See `memory/project_embedding_process.md` and `docs/handoffs/README.md`
rule #1 for the full rationale.

Usage
-----
    # Smoke test against the first 1000 points, no writes:
    python scripts/backfill_qdrant_entity_ids.py --dry-run --limit 1000

    # Full run, blocking on the advisory lock:
    python scripts/backfill_qdrant_entity_ids.py

    # Resume after an interrupt (scroll offset is persisted to a
    # checkpoint; pass the last known chunk id to override):
    python scripts/backfill_qdrant_entity_ids.py --resume-after-chunk-id 1234567

    # Write empty arrays for chunks with zero mentions (default: skip):
    python scripts/backfill_qdrant_entity_ids.py --no-skip-empty

    # Fail fast if advisory lock 42 is held by another writer:
    python scripts/backfill_qdrant_entity_ids.py --no-wait-for-lock

Flags
-----
    --dry-run                 Scroll + resolve entity_ids, but no Qdrant writes.
    --limit N                 Process at most N points.
    --resume-after-chunk-id N Override scroll offset with an explicit chunk id.
    --batch-size N            Points per scroll/write batch (default 500).
    --wait-for-lock           Block on pg_advisory_lock(42) (default).
    --no-wait-for-lock        Fail fast if advisory lock 42 is held.
    --skip-empty              Skip points with zero mentions (default).
    --no-skip-empty           Write entity_ids=[] for consistency.
    --log-level               INFO (default) or DEBUG.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# -- Project bootstrap ------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from services.db_pool import get_connection

# -- Configuration ----------------------------------------------------

COLLECTION_NAME = "notulen_chunks"
ADVISORY_LOCK_KEY = 42

CHECKPOINT_DIR = PROJECT_ROOT / "data" / "pipeline_state"
CHECKPOINT_PATH = CHECKPOINT_DIR / "qdrant_entity_backfill_checkpoint.json"
LOG_PATH = PROJECT_ROOT / "logs" / "backfill_qdrant_entity_ids.log"

# Save checkpoint every N processed points (separate from --batch-size
# which controls scroll/write granularity).
CHECKPOINT_EVERY = 10_000


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


# -- Checkpoint -------------------------------------------------------

def load_checkpoint() -> dict[str, Any]:
    if CHECKPOINT_PATH.exists():
        try:
            with open(CHECKPOINT_PATH) as f:
                return json.load(f)
        except Exception:
            log.warning("Checkpoint file unreadable, starting fresh", exc_info=True)
    return {
        "last_offset": None,
        "processed": 0,
        "with_entities": 0,
        "total_entity_ids": 0,
        "ts": None,
    }


def save_checkpoint(
    last_offset: Any,
    processed: int,
    with_entities: int,
    total_entity_ids: int,
) -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    with open(CHECKPOINT_PATH, "w") as f:
        json.dump(
            {
                "last_offset": last_offset,
                "processed": processed,
                "with_entities": with_entities,
                "total_entity_ids": total_entity_ids,
                "ts": datetime.now().isoformat(),
            },
            f,
        )


# -- Advisory lock ----------------------------------------------------

def acquire_advisory_lock(conn, wait: bool) -> None:
    """
    Acquire cross-job advisory lock 42. Blocks in wait mode, otherwise
    tries pg_try_advisory_lock and exits on contention.
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
    conn.commit()
    cur.close()


def release_advisory_lock(conn) -> None:
    try:
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
        conn.commit()
        cur.close()
        log.info(f"Advisory lock {ADVISORY_LOCK_KEY} released")
    except Exception:
        log.warning(
            f"Could not release advisory lock {ADVISORY_LOCK_KEY}",
            exc_info=True,
        )


# -- Mentions lookup --------------------------------------------------

MENTIONS_SQL = """
    SELECT chunk_id, array_agg(DISTINCT entity_id ORDER BY entity_id) AS entity_ids
    FROM kg_mentions
    WHERE chunk_id = ANY(%s)
    GROUP BY chunk_id
"""


def fetch_entity_ids_for_chunks(chunk_ids: list[int]) -> dict[int, list[int]]:
    """
    Batch-fetch entity_ids for a list of chunk ids. Returns a dict keyed
    by chunk_id; chunks with zero mentions are absent from the dict.
    Uses a pooled short-lived connection — NOT the same connection that
    holds the advisory lock (advisory locks are session-scoped, so the
    reader must be a different session).
    """
    if not chunk_ids:
        return {}
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(MENTIONS_SQL, (chunk_ids,))
            return {row[0]: list(row[1]) for row in cur.fetchall()}


# -- Stats ------------------------------------------------------------

class Stats:
    def __init__(self, start_processed: int = 0, start_with_entities: int = 0,
                 start_total_entity_ids: int = 0) -> None:
        self.processed = start_processed
        self.with_entities = start_with_entities
        self.total_entity_ids = start_total_entity_ids
        self.batches = 0
        self.batch_latency_sum = 0.0
        self.start_time = time.time()

    def note_batch(self, latency_s: float) -> None:
        self.batches += 1
        self.batch_latency_sum += latency_s

    @property
    def avg_batch_latency_ms(self) -> float:
        if self.batches == 0:
            return 0.0
        return (self.batch_latency_sum / self.batches) * 1000.0

    @property
    def avg_entities_per_chunk(self) -> float:
        if self.with_entities == 0:
            return 0.0
        return self.total_entity_ids / self.with_entities

    def report(self) -> str:
        elapsed = time.time() - self.start_time
        rate = self.processed / elapsed if elapsed > 0 else 0.0
        return (
            f"processed={self.processed:,} | "
            f"with_entities={self.with_entities:,} "
            f"({(self.with_entities / self.processed * 100 if self.processed else 0):.1f}%) | "
            f"total_entity_ids={self.total_entity_ids:,} | "
            f"avg_per_chunk={self.avg_entities_per_chunk:.2f} | "
            f"avg_batch={self.avg_batch_latency_ms:.0f}ms | "
            f"{rate:,.0f} pts/s"
        )


# -- Main loop --------------------------------------------------------

def run(
    *,
    dry_run: bool,
    limit: int | None,
    resume_after_chunk_id: int | None,
    batch_size: int,
    wait_for_lock: bool,
    skip_empty: bool,
) -> None:
    log.info("=" * 64)
    log.info("  QDRANT ENTITY_IDS BACKFILL — WS1 GraphRAG Phase 0")
    log.info(f"  Collection:          {COLLECTION_NAME}")
    log.info(f"  Dry run:             {dry_run}")
    log.info(f"  Limit:               {limit or 'unlimited'}")
    log.info(f"  Resume after chunk:  {resume_after_chunk_id or 'checkpoint'}")
    log.info(f"  Batch size:          {batch_size}")
    log.info(f"  Wait for lock:       {wait_for_lock}")
    log.info(f"  Skip empty:          {skip_empty}")
    log.info("=" * 64)

    # -- CRITICAL SAFETY WARNING -------------------------------------
    log.warning("")
    log.warning("!" * 64)
    log.warning("!  THIS SCRIPT WRITES TO QDRANT.")
    log.warning("!  It MUST NOT run concurrently with:")
    log.warning("!    - scripts/compute_embeddings.py")
    log.warning("!    - scripts/sync_enrichment_to_qdrant.py")
    log.warning("!    - scripts/enrich_qdrant_metadata.py")
    log.warning("!    - any WS5a nightly pipeline writer")
    log.warning("!    - any other background Qdrant migration/backfill")
    log.warning("!  Concurrent writes can corrupt Qdrant segment files.")
    log.warning("!  Advisory lock 42 coordinates with other repo writers,")
    log.warning("!  but non-repo processes (ad-hoc notebooks, stale jobs)")
    log.warning("!  WILL NOT be blocked. VERIFY before proceeding.")
    log.warning("!" * 64)
    log.warning("")

    # Lazy imports — keeps --help fast and avoids importing the Qdrant
    # client when we only want to print the banner.
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qmodels

    # -- Qdrant client -------------------------------------------------
    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    qdrant_api_key = os.getenv("QDRANT_API_KEY", None)
    log.info(f"Initializing QdrantClient ({qdrant_url})...")
    qdrant = QdrantClient(url=qdrant_url, api_key=qdrant_api_key, timeout=60)

    # Verify collection exists and dim matches the canonical embedder.
    # Copied from services/rag_service.py init sanity-check.
    from services.embedding import EMBEDDING_DIM, QDRANT_COLLECTION
    if QDRANT_COLLECTION != COLLECTION_NAME:
        raise RuntimeError(
            f"Collection name mismatch: embedding module says "
            f"'{QDRANT_COLLECTION}' but this script targets '{COLLECTION_NAME}'"
        )
    info = qdrant.get_collection(COLLECTION_NAME)
    actual_dim = info.config.params.vectors.size
    if actual_dim != EMBEDDING_DIM:
        raise RuntimeError(
            f"[embedding] Dimension mismatch: collection '{COLLECTION_NAME}' "
            f"has {actual_dim}D vectors but current model produces {EMBEDDING_DIM}D. "
            f"Aborting — re-embedding is required before backfilling payloads."
        )
    log.info(
        f"Qdrant collection '{COLLECTION_NAME}': "
        f"{info.points_count:,} points, dim={actual_dim}"
    )

    # -- Checkpoint ----------------------------------------------------
    checkpoint = load_checkpoint()
    if resume_after_chunk_id is not None:
        # Explicit override: treat the chunk_id as the scroll offset.
        # Qdrant's default scroll order is by point id, so feeding the
        # last-processed chunk id as offset yields "strictly after" it
        # on the next batch (Qdrant's offset semantics are "start here").
        offset: Any = resume_after_chunk_id
        processed = 0
        with_entities = 0
        total_entity_ids = 0
        log.info(f"Overriding checkpoint: starting scroll offset = {offset}")
    else:
        offset = checkpoint["last_offset"]
        processed = checkpoint["processed"]
        with_entities = checkpoint["with_entities"]
        total_entity_ids = checkpoint.get("total_entity_ids", 0)
        if offset is not None:
            log.info(
                f"Resuming from checkpoint: offset={offset}, "
                f"processed={processed:,}, with_entities={with_entities:,}"
            )

    stats = Stats(
        start_processed=processed,
        start_with_entities=with_entities,
        start_total_entity_ids=total_entity_ids,
    )

    # -- Dedicated session holding the advisory lock -------------------
    # Advisory locks are session-scoped, so we hold a dedicated psycopg2
    # connection for the lock. Batched kg_mentions reads use the pool.
    import psycopg2
    db_url = os.getenv("DATABASE_URL", "")
    if not db_url:
        host = os.getenv("DB_HOST", "localhost")
        port = os.getenv("DB_PORT", "5432")
        name = os.getenv("DB_NAME", "neodemos")
        user = os.getenv("DB_USER", "postgres")
        password = os.getenv("DB_PASSWORD", "postgres")
        db_url = f"host={host} port={port} dbname={name} user={user} password={password}"
    lock_conn = psycopg2.connect(db_url)
    lock_conn.autocommit = False

    try:
        acquire_advisory_lock(lock_conn, wait=wait_for_lock)

        # -- Scroll loop -----------------------------------------------
        while True:
            if limit is not None and stats.processed - checkpoint["processed"] >= limit:
                log.info(f"Limit {limit:,} reached. Stopping.")
                break

            scroll_limit = batch_size
            if limit is not None:
                remaining = limit - (stats.processed - checkpoint["processed"])
                scroll_limit = min(batch_size, remaining)

            t0 = time.time()
            points, next_offset = qdrant.scroll(
                collection_name=COLLECTION_NAME,
                limit=scroll_limit,
                with_payload=["document_id", "title"],
                with_vectors=False,
                offset=offset,
            )
            if not points:
                log.info("Scroll returned no more points — backfill complete.")
                break

            chunk_ids = [int(p.id) for p in points]
            mentions = fetch_entity_ids_for_chunks(chunk_ids)

            # Build the per-point SetPayloadOperation list.
            ops: list[Any] = []
            batch_with_entities = 0
            batch_total_entity_ids = 0
            for cid in chunk_ids:
                ent_ids = mentions.get(cid, [])
                if not ent_ids and skip_empty:
                    continue
                if ent_ids:
                    batch_with_entities += 1
                    batch_total_entity_ids += len(ent_ids)
                ops.append(
                    qmodels.SetPayloadOperation(
                        set_payload=qmodels.SetPayload(
                            payload={"entity_ids": ent_ids},
                            points=[cid],
                        )
                    )
                )

            if dry_run:
                log.debug(
                    f"[dry-run] would update {len(ops)} points "
                    f"(with_entities={batch_with_entities})"
                )
            elif ops:
                # True batching: one HTTP round-trip for N heterogeneous
                # per-point set_payload operations.
                for attempt in range(5):
                    try:
                        qdrant.batch_update_points(
                            collection_name=COLLECTION_NAME,
                            update_operations=ops,
                            wait=False,
                        )
                        break
                    except Exception as e:
                        if attempt == 4:
                            log.error(
                                f"batch_update_points failed after 5 attempts "
                                f"({len(ops)} ops): {e}"
                            )
                            raise
                        wait_s = 2 ** (attempt + 1)
                        log.warning(
                            f"batch_update_points retry {attempt + 1} "
                            f"(wait {wait_s}s): {e}"
                        )
                        time.sleep(wait_s)
                        if "Connection" in str(e):
                            try:
                                qdrant = QdrantClient(
                                    url=qdrant_url,
                                    api_key=qdrant_api_key,
                                    timeout=60,
                                )
                                log.info("Reconnected to Qdrant after batch failure")
                            except Exception:
                                pass

            batch_latency = time.time() - t0
            stats.note_batch(batch_latency)
            stats.processed += len(points)
            stats.with_entities += batch_with_entities
            stats.total_entity_ids += batch_total_entity_ids

            offset = next_offset

            # Progress logging every batch_size points (== every scroll).
            log.info(stats.report())

            # Periodic checkpoint (independent of batch size).
            if stats.processed % CHECKPOINT_EVERY < batch_size:
                save_checkpoint(
                    offset,
                    stats.processed,
                    stats.with_entities,
                    stats.total_entity_ids,
                )
                log.info(f"Checkpoint saved at processed={stats.processed:,}")

            if next_offset is None:
                log.info("Qdrant scroll exhausted (next_offset=None). Done.")
                break

        # Final checkpoint.
        save_checkpoint(
            offset,
            stats.processed,
            stats.with_entities,
            stats.total_entity_ids,
        )

        log.info("=" * 64)
        log.info("  BACKFILL COMPLETE")
        log.info(f"  {stats.report()}")
        log.info(f"  Last offset: {offset}")
        log.info("=" * 64)

    finally:
        release_advisory_lock(lock_conn)
        lock_conn.close()


# -- CLI --------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill Qdrant notulen_chunks payloads with entity_ids from "
            "PostgreSQL kg_mentions. WS1 GraphRAG Phase 0."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scroll + resolve entity_ids but do not write to Qdrant.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N points (smoke testing).",
    )
    parser.add_argument(
        "--resume-after-chunk-id",
        type=int,
        default=None,
        help="Override scroll offset with an explicit chunk id.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Points per scroll/write batch (default 500).",
    )

    lock_group = parser.add_mutually_exclusive_group()
    lock_group.add_argument(
        "--wait-for-lock",
        dest="wait_for_lock",
        action="store_true",
        help="Block on pg_advisory_lock(42) (default).",
    )
    lock_group.add_argument(
        "--no-wait-for-lock",
        dest="wait_for_lock",
        action="store_false",
        help="Fail fast if advisory lock 42 is held.",
    )
    parser.set_defaults(wait_for_lock=True)

    skip_group = parser.add_mutually_exclusive_group()
    skip_group.add_argument(
        "--skip-empty",
        dest="skip_empty",
        action="store_true",
        help="Skip points with zero mentions (default).",
    )
    skip_group.add_argument(
        "--no-skip-empty",
        dest="skip_empty",
        action="store_false",
        help="Write entity_ids=[] for chunks with no mentions.",
    )
    parser.set_defaults(skip_empty=True)

    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default INFO).",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    logging.getLogger().setLevel(getattr(logging, args.log_level))
    run(
        dry_run=args.dry_run,
        limit=args.limit,
        resume_after_chunk_id=args.resume_after_chunk_id,
        batch_size=args.batch_size,
        wait_for_lock=args.wait_for_lock,
        skip_empty=args.skip_empty,
    )


if __name__ == "__main__":
    main()
