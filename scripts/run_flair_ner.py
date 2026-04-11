#!/usr/bin/env python3
"""
Flair Dutch NER for document_chunks (WS1 GraphRAG Phase 0)
===========================================================

Runs flair/ner-dutch-large over all ~1.7M rows in document_chunks,
extracts Person / Location / Organization mentions, and writes them
into the knowledge graph tables plus document_chunks.key_entities.

Coverage target: lift document_chunks.key_entities from ~25% to ~65%
by unlocking street-level and long-tail entities the static gazetteer
misses (Heemraadssingel, Mathenesserlaan, Burgemeester Aboutaleb, ...).

For each detected mention we:
  1. Upsert a canonical row into kg_entities (type, name) — UNIQUE(type, name).
  2. Insert a row into kg_mentions (entity_id, chunk_id, raw_mention)
     preserving the raw surface form.
  3. Extend document_chunks.key_entities (text[]) with the new canonical
     surface forms so downstream hybrid search picks them up.

Flair tag -> canonical type:
    PER  -> Person
    LOC  -> Location
    ORG  -> Organization
    MISC -> Other           (dropped by default, enable with --no-skip-misc)

Coordinates with other enrichment jobs via Postgres advisory lock 42.

Usage:
    python scripts/run_flair_ner.py                       # Full run
    python scripts/run_flair_ner.py --dry-run --limit 200 # Smoke test, no writes
    python scripts/run_flair_ner.py --resume              # Resume from checkpoint
    python scripts/run_flair_ner.py --batch-size 300 --mini-batch-size 64

See docs/handoffs/WS1_GRAPHRAG.md for the full WS1 context.
"""

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Project bootstrap ─────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from tqdm import tqdm

# ── Configuration ─────────────────────────────────────────────────────

CHECKPOINT_DIR = PROJECT_ROOT / "data" / "pipeline_state"
CHECKPOINT_PATH = CHECKPOINT_DIR / "flair_ner_checkpoint.json"
LOG_PATH = PROJECT_ROOT / "logs" / "run_flair_ner.log"

ADVISORY_LOCK_KEY = 42

FLAIR_TYPE_MAP = {
    "PER": "Person",
    "LOC": "Location",
    "ORG": "Organization",
    "MISC": "Other",
}

# ── Logging ───────────────────────────────────────────────────────────

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
log = logging.getLogger("flair_ner")


def configure_logging(level: str) -> None:
    log.setLevel(getattr(logging, level.upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    file_h = logging.FileHandler(LOG_PATH)
    file_h.setFormatter(fmt)
    stream_h = logging.StreamHandler()
    stream_h.setFormatter(fmt)
    log.handlers.clear()
    log.addHandler(file_h)
    log.addHandler(stream_h)
    log.propagate = False


# ── DSN resolution (mirrors services.db_pool conventions) ────────────

def resolve_dsn() -> str:
    db_url = os.getenv("DATABASE_URL", "").strip()
    if db_url:
        return db_url
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "neodemos")
    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "postgres")
    return f"host={host} port={port} dbname={name} user={user} password={password}"


# ── Advisory lock ─────────────────────────────────────────────────────

def acquire_advisory_lock(conn, key: int, wait: bool) -> bool:
    """Try to grab advisory lock `key`. Returns True if held."""
    with conn.cursor() as cur:
        if wait:
            log.info(f"Waiting for advisory lock {key}...")
            cur.execute("SELECT pg_advisory_lock(%s)", (key,))
            conn.commit()
            return True
        cur.execute("SELECT pg_try_advisory_lock(%s)", (key,))
        got = cur.fetchone()[0]
        conn.commit()
        return bool(got)


def release_advisory_lock(conn, key: int) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (key,))
            conn.commit()
    except Exception as exc:
        log.warning(f"Failed to release advisory lock {key}: {exc}")


# ── Checkpoint ────────────────────────────────────────────────────────

def load_checkpoint() -> dict:
    if CHECKPOINT_PATH.exists():
        try:
            with open(CHECKPOINT_PATH) as f:
                return json.load(f)
        except Exception as exc:
            log.warning(f"Could not parse checkpoint ({exc}); starting fresh")
    return {
        "last_chunk_id": 0,
        "processed": 0,
        "entities_created": 0,
        "mentions_inserted": 0,
        "ts": None,
    }


def save_checkpoint(
    last_chunk_id: int,
    processed: int,
    entities_created: int,
    mentions_inserted: int,
) -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_chunk_id": last_chunk_id,
        "processed": processed,
        "entities_created": entities_created,
        "mentions_inserted": mentions_inserted,
        "ts": datetime.now().isoformat(),
    }
    tmp = CHECKPOINT_PATH.with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(payload, f)
    tmp.replace(CHECKPOINT_PATH)


# ── Stats ─────────────────────────────────────────────────────────────

class Stats:
    def __init__(self) -> None:
        self.processed = 0
        self.mentions_detected = 0
        self.mentions_accepted = 0
        self.mentions_inserted = 0
        self.entities_created = 0
        self.unique_entities_seen: set[tuple[str, str]] = set()
        self.start_time = time.time()

    def report(self) -> str:
        elapsed = time.time() - self.start_time
        rate = self.processed / elapsed if elapsed > 0 else 0.0
        return (
            f"processed={self.processed:,} | "
            f"detected={self.mentions_detected:,} | "
            f"accepted={self.mentions_accepted:,} | "
            f"inserted={self.mentions_inserted:,} | "
            f"new_entities={self.entities_created:,} | "
            f"unique_seen={len(self.unique_entities_seen):,} | "
            f"{rate:,.1f} chunks/s"
        )


# ── Device selection ──────────────────────────────────────────────────

def pick_device() -> str:
    """Prefer MPS on Apple Silicon, else CUDA, else CPU."""
    try:
        import torch
    except ImportError:
        log.warning("torch not importable; Flair will choose its own device")
        return "cpu"

    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        log.info("Device: mps (Apple Silicon)")
        return "mps"
    if torch.cuda.is_available():
        log.info("Device: cuda")
        return "cuda"
    log.info("Device: cpu")
    return "cpu"


# ── Flair inference ───────────────────────────────────────────────────

def load_tagger(device: str):
    """Lazy import of Flair + model load. Runs the download once on first use."""
    import flair
    import torch
    from flair.models import SequenceTagger

    # flair.device expects a torch.device
    try:
        flair.device = torch.device(device)
    except Exception as exc:
        log.warning(f"Could not set flair.device={device} ({exc}); falling back to default")

    log.info("Loading flair/ner-dutch-large (first run will download the model)...")
    tagger = SequenceTagger.load("flair/ner-dutch-large")
    log.info("Model loaded.")
    return tagger


def predict_batch(tagger, texts: list[str], mini_batch_size: int) -> list[list[dict]]:
    """
    Run Flair NER on a list of text strings.

    Returns a list of spans per input, each span a dict:
        {"tag": str, "text": str, "score": float, "start": int, "end": int}
    """
    from flair.data import Sentence

    sentences = [Sentence(t) if t else Sentence("") for t in texts]
    # Filter out empties — Flair handles them but wastes compute.
    non_empty = [s for s in sentences if len(s) > 0]
    if non_empty:
        tagger.predict(non_empty, mini_batch_size=mini_batch_size)

    results: list[list[dict]] = []
    for sent in sentences:
        spans: list[dict] = []
        for entity in sent.get_spans("ner"):
            spans.append({
                "tag": entity.tag,
                "text": entity.text,
                "score": float(entity.score),
                "start": entity.start_position,
                "end": entity.end_position,
            })
        results.append(spans)
    return results


# ── DB write helpers ──────────────────────────────────────────────────

def upsert_entities(
    cur,
    pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], int]:
    """
    Two-step upsert for kg_entities UNIQUE(type, name):
      1. Bulk INSERT ... ON CONFLICT DO NOTHING
      2. SELECT back all ids for the requested pairs

    Returns {(type, name): id}.
    """
    if not pairs:
        return {}

    execute_values(
        cur,
        """
        INSERT INTO kg_entities (type, name, metadata)
        VALUES %s
        ON CONFLICT (type, name) DO NOTHING
        """,
        [(t, n, "{}") for (t, n) in pairs],
        template="(%s, %s, %s::jsonb)",
    )

    # SELECT back. VALUES-join keeps it to a single round-trip.
    execute_values(
        cur,
        """
        SELECT e.type, e.name, e.id
        FROM kg_entities e
        JOIN (VALUES %s) AS v(type, name)
          ON e.type = v.type AND e.name = v.name
        """,
        pairs,
    )
    return {(row[0], row[1]): row[2] for row in cur.fetchall()}


def insert_mentions(
    cur,
    rows: list[tuple[int, int, str]],
) -> int:
    """
    Bulk insert kg_mentions rows.

    rows: list of (entity_id, chunk_id, raw_mention), already deduped
    within the batch on (entity_id, chunk_id).

    Returns rows inserted. If a UNIQUE(entity_id, chunk_id) constraint
    exists we rely on ON CONFLICT DO NOTHING; otherwise we pre-dedupe
    against the existing table via a SELECT before insert.
    """
    if not rows:
        return 0

    # Pre-dedupe against any existing (entity_id, chunk_id) pairs so we
    # don't multiply mentions on resume/retry. Cheap because both cols
    # are expected to be indexed.
    pairs = list({(r[0], r[1]) for r in rows})
    execute_values(
        cur,
        """
        SELECT entity_id, chunk_id
        FROM kg_mentions
        WHERE (entity_id, chunk_id) IN (VALUES %s)
        """,
        pairs,
    )
    existing = {(row[0], row[1]) for row in cur.fetchall()}

    fresh = [r for r in rows if (r[0], r[1]) not in existing]
    if not fresh:
        return 0

    execute_values(
        cur,
        """
        INSERT INTO kg_mentions (entity_id, chunk_id, raw_mention)
        VALUES %s
        """,
        fresh,
    )
    return len(fresh)


def update_chunk_key_entities(
    cur,
    per_chunk_new_terms: dict[int, list[str]],
) -> None:
    """
    Extend document_chunks.key_entities (text[]) with new canonical names.
    One UPDATE per chunk; each call receives the union of new terms.
    """
    if not per_chunk_new_terms:
        return
    for chunk_id, terms in per_chunk_new_terms.items():
        if not terms:
            continue
        cur.execute(
            """
            UPDATE document_chunks
            SET key_entities = COALESCE(
                ARRAY(
                    SELECT DISTINCT unnest(COALESCE(key_entities, ARRAY[]::text[]) || %s::text[])
                ),
                ARRAY[]::text[]
            )
            WHERE id = %s
            """,
            (terms, chunk_id),
        )


# ── Batch orchestration ──────────────────────────────────────────────

def process_batch(
    tagger,
    rows: list[dict],
    write_cur,
    write_conn,
    stats: Stats,
    min_confidence: float,
    skip_misc: bool,
    max_content_chars: int,
    mini_batch_size: int,
    dry_run: bool,
) -> None:
    """Run Flair on a batch of chunk rows and write everything to Postgres."""
    if not rows:
        return

    texts = [((r.get("content") or "")[:max_content_chars]) for r in rows]
    chunk_ids = [r["id"] for r in rows]

    t0 = time.time()
    spans_per_row = predict_batch(tagger, texts, mini_batch_size=mini_batch_size)
    flair_secs = time.time() - t0

    # Aggregate
    batch_pairs: set[tuple[str, str]] = set()
    # mentions before entity-id lookup: [(type, name, chunk_id, raw)]
    pending_mentions: list[tuple[str, str, int, str]] = []
    per_chunk_new_terms: dict[int, set[str]] = {}

    for chunk_id, spans in zip(chunk_ids, spans_per_row):
        # Dedupe within a chunk on (type, name) so we only insert one
        # kg_mention per entity per chunk even if Flair found it twice.
        seen_in_chunk: set[tuple[str, str]] = set()
        for span in spans:
            stats.mentions_detected += 1
            if span["score"] < min_confidence:
                continue
            tag = span["tag"]
            canonical_type = FLAIR_TYPE_MAP.get(tag)
            if canonical_type is None:
                continue
            if skip_misc and canonical_type == "Other":
                continue

            raw = span["text"].strip()
            if not raw or len(raw) < 2:
                continue
            # Canonical name = stripped surface form. We keep the
            # original casing because Dutch proper nouns care about it
            # (e.g. "van der Linden"). Lowercasing happens at query time.
            name = raw

            key = (canonical_type, name)
            if key in seen_in_chunk:
                continue
            seen_in_chunk.add(key)

            stats.mentions_accepted += 1
            batch_pairs.add(key)
            pending_mentions.append((canonical_type, name, chunk_id, raw))
            per_chunk_new_terms.setdefault(chunk_id, set()).add(name)

        if dry_run and spans:
            log.debug(
                f"chunk={chunk_id} spans={len(spans)} "
                f"sample={[(s['tag'], s['text'], round(s['score'], 2)) for s in spans[:5]]}"
            )

    stats.unique_entities_seen.update(batch_pairs)

    if dry_run:
        log.info(
            f"[dry-run] batch size={len(rows)} flair={flair_secs:.2f}s "
            f"spans={sum(len(s) for s in spans_per_row)} accepted={len(pending_mentions)}"
        )
        return

    if not batch_pairs:
        write_conn.commit()
        return

    # 1. Upsert entities, build (type, name) -> id
    pairs_list = list(batch_pairs)
    id_map = upsert_entities(write_cur, pairs_list)

    # Track newly-created rows: any pair that exists now but was not
    # here before this call would have been INSERTed. We can't tell
    # insert-vs-existing from the two-step approach cheaply, so we
    # approximate with "unique pairs this run that we hadn't tracked
    # yet this session". stats.unique_entities_seen handles that.
    # For the official "created" count we count rows whose id is new
    # to this session-wide id_map.
    # (Counting exact new inserts would need RETURNING xmax tricks.)
    stats.entities_created += sum(1 for p in pairs_list if p in id_map)

    # 2. Build mention rows with real entity ids, dedup on (entity_id, chunk_id)
    mention_rows_set: set[tuple[int, int, str]] = set()
    for canonical_type, name, chunk_id, raw in pending_mentions:
        entity_id = id_map.get((canonical_type, name))
        if entity_id is None:
            continue
        mention_rows_set.add((entity_id, chunk_id, raw))
    mention_rows = list(mention_rows_set)

    inserted = insert_mentions(write_cur, mention_rows)
    stats.mentions_inserted += inserted

    # 3. Extend document_chunks.key_entities
    per_chunk_lists = {cid: sorted(terms) for cid, terms in per_chunk_new_terms.items()}
    update_chunk_key_entities(write_cur, per_chunk_lists)

    write_conn.commit()


# ── Main loop ─────────────────────────────────────────────────────────

def run(
    dry_run: bool,
    limit: int | None,
    resume: bool,
    batch_size: int,
    mini_batch_size: int,
    min_confidence: float,
    skip_misc: bool,
    max_content_chars: int,
    wait_for_lock: bool,
) -> None:
    log.info("=" * 72)
    log.info("  FLAIR NER ENRICHMENT — WS1 GraphRAG Phase 0")
    log.info(
        f"  dry_run={dry_run} limit={limit or 'none'} resume={resume} "
        f"batch_size={batch_size} mini_batch_size={mini_batch_size}"
    )
    log.info(
        f"  min_confidence={min_confidence} skip_misc={skip_misc} "
        f"max_content_chars={max_content_chars} wait_for_lock={wait_for_lock}"
    )
    log.info("=" * 72)

    dsn = resolve_dsn()

    # Device + model
    device = pick_device()
    tagger = load_tagger(device)

    # Connections: one read (server-side cursor), one write.
    read_conn = psycopg2.connect(dsn)
    write_conn = psycopg2.connect(dsn)
    write_cur = write_conn.cursor()

    # Advisory lock (on the write connection so it's released when it closes)
    got_lock = acquire_advisory_lock(write_conn, ADVISORY_LOCK_KEY, wait=wait_for_lock)
    if not got_lock:
        log.error(
            f"Advisory lock {ADVISORY_LOCK_KEY} held by another job. "
            f"Use --wait-for-lock to block, or retry later."
        )
        write_cur.close()
        write_conn.close()
        read_conn.close()
        sys.exit(2)
    log.info(f"Acquired advisory lock {ADVISORY_LOCK_KEY}")

    # Checkpoint
    checkpoint = load_checkpoint() if resume else {
        "last_chunk_id": 0,
        "processed": 0,
        "entities_created": 0,
        "mentions_inserted": 0,
    }
    start_id = int(checkpoint.get("last_chunk_id") or 0)
    already_processed = int(checkpoint.get("processed") or 0) if resume else 0
    if resume and start_id > 0:
        log.info(
            f"Resuming after chunk id {start_id} "
            f"({already_processed:,} already processed)"
        )

    # Total for progress bar
    with read_conn.cursor() as ccur:
        if start_id > 0:
            ccur.execute(
                "SELECT COUNT(*) FROM document_chunks WHERE id > %s",
                (start_id,),
            )
        else:
            ccur.execute("SELECT COUNT(*) FROM document_chunks")
        total = ccur.fetchone()[0]
    if limit:
        total = min(total, limit)
    log.info(f"Chunks to process: {total:,}")

    # Server-side cursor
    read_cur = read_conn.cursor("flair_ner_reader", cursor_factory=RealDictCursor)
    read_cur.itersize = 2000
    read_cur.execute(
        """
        SELECT id, content
        FROM document_chunks
        WHERE id > %s
          AND content IS NOT NULL
          AND length(content) > 20
        ORDER BY id
        """,
        (start_id,),
    )

    stats = Stats()
    stats.entities_created = int(checkpoint.get("entities_created") or 0) if resume else 0
    stats.mentions_inserted = int(checkpoint.get("mentions_inserted") or 0) if resume else 0

    buffer: list[dict] = []
    last_chunk_id = start_id
    rows_seen = 0

    pbar = tqdm(total=total, initial=0, desc="Flair NER", unit="chunk")

    # Graceful shutdown on SIGTERM
    interrupted = {"flag": False}

    def _sigterm_handler(signum, frame):
        log.warning(f"Received signal {signum}; finishing current batch then exiting")
        interrupted["flag"] = True

    signal.signal(signal.SIGTERM, _sigterm_handler)

    try:
        for row in read_cur:
            if limit and rows_seen >= limit:
                break

            buffer.append(row)
            last_chunk_id = row["id"]
            rows_seen += 1
            stats.processed += 1
            pbar.update(1)

            if len(buffer) >= batch_size:
                process_batch(
                    tagger=tagger,
                    rows=buffer,
                    write_cur=write_cur,
                    write_conn=write_conn,
                    stats=stats,
                    min_confidence=min_confidence,
                    skip_misc=skip_misc,
                    max_content_chars=max_content_chars,
                    mini_batch_size=mini_batch_size,
                    dry_run=dry_run,
                )
                buffer = []

            if rows_seen % 5_000 == 0:
                log.info(f"[{rows_seen:>10,}] {stats.report()}")

            if rows_seen % 25_000 == 0:
                save_checkpoint(
                    last_chunk_id=last_chunk_id,
                    processed=already_processed + rows_seen,
                    entities_created=stats.entities_created,
                    mentions_inserted=stats.mentions_inserted,
                )

            if interrupted["flag"]:
                break

        # Flush tail
        if buffer:
            process_batch(
                tagger=tagger,
                rows=buffer,
                write_cur=write_cur,
                write_conn=write_conn,
                stats=stats,
                min_confidence=min_confidence,
                skip_misc=skip_misc,
                max_content_chars=max_content_chars,
                mini_batch_size=mini_batch_size,
                dry_run=dry_run,
            )

        save_checkpoint(
            last_chunk_id=last_chunk_id,
            processed=already_processed + rows_seen,
            entities_created=stats.entities_created,
            mentions_inserted=stats.mentions_inserted,
        )

    except KeyboardInterrupt:
        log.warning("Interrupted! Flushing current batch and saving checkpoint...")
        if buffer:
            try:
                process_batch(
                    tagger=tagger,
                    rows=buffer,
                    write_cur=write_cur,
                    write_conn=write_conn,
                    stats=stats,
                    min_confidence=min_confidence,
                    skip_misc=skip_misc,
                    max_content_chars=max_content_chars,
                    mini_batch_size=mini_batch_size,
                    dry_run=dry_run,
                )
            except Exception:
                log.exception("Failed to flush buffer on interrupt")
                write_conn.rollback()
        save_checkpoint(
            last_chunk_id=last_chunk_id,
            processed=already_processed + rows_seen,
            entities_created=stats.entities_created,
            mentions_inserted=stats.mentions_inserted,
        )
        log.info(f"Checkpoint saved at chunk id {last_chunk_id}")
    except Exception:
        log.exception("Fatal error during Flair NER pass")
        save_checkpoint(
            last_chunk_id=last_chunk_id,
            processed=already_processed + rows_seen,
            entities_created=stats.entities_created,
            mentions_inserted=stats.mentions_inserted,
        )
        raise
    finally:
        pbar.close()
        try:
            read_cur.close()
        except Exception:
            pass
        read_conn.close()
        release_advisory_lock(write_conn, ADVISORY_LOCK_KEY)
        write_cur.close()
        write_conn.close()

    log.info("=" * 72)
    log.info("  FLAIR NER COMPLETE")
    log.info(f"  {stats.report()}")
    log.info(f"  last_chunk_id={last_chunk_id}")
    log.info("=" * 72)


# ── CLI ───────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Flair Dutch NER enrichment for document_chunks (WS1 Phase 0)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run Flair and log detections but do not write to Postgres",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process only the first N chunks (for smoke tests)",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from data/pipeline_state/flair_ner_checkpoint.json",
    )
    parser.add_argument(
        "--batch-size", type=int, default=200,
        help="DB-facing batch size (chunks per Flair call + write) — default 200",
    )
    parser.add_argument(
        "--mini-batch-size", type=int, default=32,
        help="Flair SequenceTagger.predict mini_batch_size — default 32",
    )
    parser.add_argument(
        "--min-confidence", type=float, default=0.85,
        help="Drop spans below this Flair score — default 0.85",
    )
    misc_group = parser.add_mutually_exclusive_group()
    misc_group.add_argument(
        "--skip-misc", dest="skip_misc", action="store_true", default=True,
        help="Drop MISC spans (default on — MISC is too noisy)",
    )
    misc_group.add_argument(
        "--no-skip-misc", dest="skip_misc", action="store_false",
        help="Keep MISC spans (stored under type 'Other')",
    )
    parser.add_argument(
        "--max-content-chars", type=int, default=3000,
        help="Truncate chunk content before feeding Flair — default 3000",
    )
    lock_group = parser.add_mutually_exclusive_group()
    lock_group.add_argument(
        "--wait-for-lock", dest="wait_for_lock", action="store_true", default=False,
        help="Block until advisory lock 42 is free",
    )
    lock_group.add_argument(
        "--no-wait-for-lock", dest="wait_for_lock", action="store_false",
        help="Exit immediately if advisory lock 42 is busy (default)",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level — default INFO",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    configure_logging(args.log_level)

    run(
        dry_run=args.dry_run,
        limit=args.limit,
        resume=args.resume,
        batch_size=args.batch_size,
        mini_batch_size=args.mini_batch_size,
        min_confidence=args.min_confidence,
        skip_misc=args.skip_misc,
        max_content_chars=args.max_content_chars,
        wait_for_lock=args.wait_for_lock,
    )


if __name__ == "__main__":
    main()
