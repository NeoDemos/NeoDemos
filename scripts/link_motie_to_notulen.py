#!/usr/bin/env python3
r"""
Cross-document motie/amendement -> notulen linking (WS1 GraphRAG Phase 0)
=========================================================================

Purpose
-------
For every motie/amendement document in the corpus, find the notulen chunks
where the motie was discussed or voted on, and emit DISCUSSED_IN / VOTED_IN
edges into `kg_relationships`. This is the cross-document linking pass
that unlocks the flagship WS1 MCP tool `traceer_motie` and is a hard
dependency of WS3 (Journey workstream).

Matching strategy
-----------------
1. Enumerate moties/amendementen via
       documents.name ILIKE '%motie%' OR ILIKE '%amendement%'
2. For each motie, grab the first populated `motion_number` from its
   chunks (already set by the rule-based enricher in
   `scripts/enrich_and_extract.py`, e.g. "M2023-042").
3. Query notulen chunks -- `d.name ILIKE '%notulen%'` -- where either
       dc.motion_number = <motie_motion_number>
   or
       dc.content ILIKE '%<motion_number>%'  (fallback for chunks the
   rule enricher missed).
4. Optionally constrain matching notulen to meetings within
   motie_date +/- `--date-window-days` (default 30 days).
5. For each matching notulen chunk:
     - if `dc.vote_outcome` is populated  -> VOTED_IN
     - else                                -> DISCUSSED_IN
6. Emit one edge per (motie, notulen_chunk) pair. `quote` = first 300
   chars of the notulen chunk `content`.

Edge storage design (Option B: reuse existing Motie entities)
-------------------------------------------------------------
There are already ~7,601 `kg_entities` rows with `type='Motie'` (from the
preflight audit, confirmed by `scripts/populate_kg_relationships.py`
which creates them with
    motie_label = motion_number or doc_name or f"motie-chunk-{chunk_id}"
as the `name`). This script reuses that exact convention:

    source_entity_id = kg_entities row with type='Motie', name=<label>

where <label> is `motion_number` if available, else the motie's
`documents.name`, else `motie-doc-<document_id>`. Lookup is `(type, name)`
(the existing UNIQUE key on kg_entities); missing rows are created with
metadata={"document_id": <motie_doc_id>, "motie_title": <doc_name>,
"created_by": "link_motie_to_notulen"}. That keeps the new edges
consistent with DIENT_IN / STEMT_VOOR / AANGENOMEN edges emitted by
`populate_kg_relationships.py` so that `traceer_motie` can walk from
Person -> DIENT_IN -> Motie -> DISCUSSED_IN/VOTED_IN -> notulen chunk
without a type switch.

We verify the convention is in play at startup: if `kg_entities` has
zero rows with `type='Motie'` the script aborts and asks the user to
run `scripts/populate_kg_relationships.py` first. We do NOT fall back
to a different naming scheme -- silent drift between this script and
the existing Motie rows would pollute the graph.

Target-side design (NULL target_entity_id vs. Document placeholder)
-------------------------------------------------------------------
`kg_relationships.target_entity_id` is a nullable FK to `kg_entities`
(verified in `scripts/build_knowledge_graph.py`: the column has no
NOT NULL constraint). Two options:

  default (`--target-mode=null`, preferred):
      target_entity_id = NULL
      chunk_id         = <notulen_chunk_id>
      document_id      = <notulen_document_id>
    Rationale: the notulen *chunk* is the real target. Wasting a
    kg_entities row per notulen document clutters the graph and
    forces traceer_motie to deref again to reach the chunk.

  alternative (`--target-mode=document`):
      target_entity_id = kg_entities row with
                         type='Document', name='notulen_<meeting_id>'
      chunk_id         = <notulen_chunk_id>
      document_id      = <notulen_document_id>
    Use this if a downstream consumer refuses to JOIN when
    target_entity_id IS NULL.

The mode is set via `--target-mode` and documented in
`kg_relationships.metadata.target_mode` on every row we insert.

motion_number normalisation (known uncertainty -- flagged)
----------------------------------------------------------
`scripts/enrich_and_extract.py` extracts motion numbers with

    RE_MOTION_NUMBER = re.compile(
        r"(?:motie|amendement)\s+(?:nr\.?\s*)?([A-Z]?\d{4}[-/]\d{2,4})",
        re.IGNORECASE,
    )

so values in the wild can look like `M2023-042`, `2023-042`, `M23/43`,
`2024/12`, etc. This script treats `motion_number` as an **opaque
string** and:
  - uses exact equality (`dc.motion_number = %s`) for the primary join
  - uses `content ILIKE '%<motion_number>%'` for the fallback join

No re-normalisation (stripping leading `M`, zero-padding, slash vs.
dash swapping) is attempted. If motion_number values diverge between
the motie PDF and the notulen summary (e.g. motie says "M2023-042" but
notulen says "2023/042") we will miss the link. Flag: if the coverage
stats below show < 70% of moties with at least one match, run the
normalisation audit described in the WS1 handoff before declaring
Phase 0 done.

Advisory locking & safety
-------------------------
Coordinates with other enrichment / ingest jobs via
`pg_advisory_lock(42)`. Never writes to Qdrant. Never rewrites existing
kg_entities Motie rows. Dedups by `(source_entity_id, chunk_id,
relation_type)` on insert -- existing edges are left alone.

Usage
-----
    python scripts/link_motie_to_notulen.py                       # Full run, null target
    python scripts/link_motie_to_notulen.py --dry-run             # Count only, no writes
    python scripts/link_motie_to_notulen.py --limit 200           # Smoke test
    python scripts/link_motie_to_notulen.py --resume              # Resume from checkpoint
    python scripts/link_motie_to_notulen.py --batch-size 300
    python scripts/link_motie_to_notulen.py --date-window-days 60
    python scripts/link_motie_to_notulen.py --target-mode document
    python scripts/link_motie_to_notulen.py --no-wait-for-lock    # Fail fast if locked
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# -- Project bootstrap ------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

import psycopg2
from psycopg2.extras import Json, RealDictCursor
from psycopg2.extensions import connection as PgConnection


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
CHECKPOINT_DIR = PROJECT_ROOT / "data" / "pipeline_state"
CHECKPOINT_PATH = CHECKPOINT_DIR / "motie_linking_checkpoint.json"
LOG_PATH = PROJECT_ROOT / "logs" / "link_motie_to_notulen.log"

# Shared advisory lock key -- must match scripts/enrich_chunks_gazetteer.py
ADVISORY_LOCK_KEY = 42

# Default date window around the motie for candidate notulen chunks.
DEFAULT_DATE_WINDOW_DAYS = 30

# Default batch size for kg_relationships inserts.
DEFAULT_BATCH_SIZE = 500

# Quote length written to kg_relationships.quote.
QUOTE_LEN = 300

# How many zero-match moties to log as a sample for manual review.
ZERO_MATCH_SAMPLE = 20


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


# -- Advisory lock ----------------------------------------------------

def acquire_advisory_lock(conn: PgConnection, wait: bool) -> None:
    """
    Acquire the cross-job advisory lock. In blocking mode we call
    pg_advisory_lock(); in non-blocking mode we call
    pg_try_advisory_lock() and exit on contention.
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


def release_advisory_lock(conn: PgConnection) -> None:
    try:
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
        cur.close()
        log.info(f"Advisory lock {ADVISORY_LOCK_KEY} released")
    except Exception:
        log.warning(f"Could not release advisory lock {ADVISORY_LOCK_KEY}", exc_info=True)


# -- Checkpoint -------------------------------------------------------

def load_checkpoint() -> dict:
    if CHECKPOINT_PATH.exists():
        try:
            with open(CHECKPOINT_PATH) as f:
                return json.load(f)
        except Exception:
            log.warning("Checkpoint file unreadable, starting from scratch", exc_info=True)
    return {"last_motie_id": "", "edges_inserted": 0, "ts": None}


def save_checkpoint(last_motie_id: str, edges_inserted: int) -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    with open(CHECKPOINT_PATH, "w") as f:
        json.dump(
            {
                "last_motie_id": last_motie_id,
                "edges_inserted": edges_inserted,
                "ts": datetime.now().isoformat(),
            },
            f,
        )


# -- Entity helpers ---------------------------------------------------

def verify_motie_convention(cur) -> int:
    """
    Sanity check: there must already be some kg_entities rows with
    type='Motie' -- those are emitted by
    scripts/populate_kg_relationships.py and are the authoritative
    source side of the DISCUSSED_IN/VOTED_IN edges we produce.

    Returns the count. Aborts if zero.
    """
    cur.execute("SELECT COUNT(*) FROM kg_entities WHERE type = 'Motie'")
    count = cur.fetchone()[0]
    if count == 0:
        log.error(
            "No kg_entities rows with type='Motie' were found. "
            "This script reuses the convention established by "
            "scripts/populate_kg_relationships.py -- please run that "
            "first (it emits Motie entities via DIENT_IN/STEMT_VOOR)."
        )
        sys.exit(3)
    log.info(f"Found {count:,} existing kg_entities rows with type='Motie'")
    return count


def get_or_create_motie_entity(
    cur,
    motie_label: str,
    motie_document_id: str,
    motie_doc_name: str | None,
) -> int:
    """
    Look up a Motie entity by (type, name). Create it if missing.

    `motie_label` is already the canonical value used by
    populate_kg_relationships.py: motion_number or doc_name.
    """
    cur.execute(
        "SELECT id FROM kg_entities WHERE type = 'Motie' AND name = %s",
        (motie_label,),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    metadata = {
        "document_id": motie_document_id,
        "motie_title": motie_doc_name,
        "created_by": "link_motie_to_notulen",
    }
    cur.execute(
        """
        INSERT INTO kg_entities (type, name, metadata)
        VALUES ('Motie', %s, %s)
        RETURNING id
        """,
        (motie_label, Json(metadata)),
    )
    return cur.fetchone()[0]


def get_or_create_document_entity(cur, meeting_id: int | None, document_id: str) -> int:
    """
    Placeholder 'Document' entity used when --target-mode=document.
    Keyed on meeting_id when present so all chunks of the same notulen
    share one target entity; falls back to document_id otherwise.
    """
    if meeting_id is not None:
        name = f"notulen_{meeting_id}"
    else:
        name = f"notulen_doc_{document_id}"

    cur.execute(
        "SELECT id FROM kg_entities WHERE type = 'Document' AND name = %s",
        (name,),
    )
    row = cur.fetchone()
    if row:
        return row[0]

    metadata = {
        "meeting_id": meeting_id,
        "document_id": document_id,
        "role": "notulen_placeholder",
        "created_by": "link_motie_to_notulen",
    }
    cur.execute(
        """
        INSERT INTO kg_entities (type, name, metadata)
        VALUES ('Document', %s, %s)
        RETURNING id
        """,
        (name, Json(metadata)),
    )
    return cur.fetchone()[0]


# -- Dedup -----------------------------------------------------------

def relationship_exists(
    cur,
    source_entity_id: int,
    chunk_id: int,
    relation_type: str,
) -> bool:
    """
    Dedup by (source_entity_id, chunk_id, relation_type). We do not
    include target_entity_id in the key because when --target-mode=null
    it is NULL, and we want to avoid re-inserting the same edge if the
    user toggles modes between runs.
    """
    cur.execute(
        """
        SELECT 1 FROM kg_relationships
        WHERE source_entity_id = %s
          AND chunk_id = %s
          AND relation_type = %s
        LIMIT 1
        """,
        (source_entity_id, chunk_id, relation_type),
    )
    return cur.fetchone() is not None


# -- Batch insert ----------------------------------------------------

INSERT_SQL = """
INSERT INTO kg_relationships
    (source_entity_id, target_entity_id, relation_type,
     document_id, chunk_id, confidence, quote, metadata)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
"""


def flush_batch(cur, batch: list[tuple], dry_run: bool) -> int:
    if not batch or dry_run:
        return 0
    cur.executemany(INSERT_SQL, batch)
    return len(batch)


# -- Motie enumeration -----------------------------------------------

MOTIES_SQL = """
SELECT
    d.id                AS document_id,
    d.name              AS doc_name,
    d.meeting_id        AS motie_meeting_id,
    m.start_date        AS motie_date,
    first_nr.motion_number
FROM documents d
LEFT JOIN meetings m ON d.meeting_id = m.id
LEFT JOIN LATERAL (
    SELECT dc.motion_number
    FROM document_chunks dc
    WHERE dc.document_id = d.id
      AND dc.motion_number IS NOT NULL
    ORDER BY dc.id
    LIMIT 1
) AS first_nr ON TRUE
WHERE (LOWER(d.name) LIKE '%%motie%%' OR LOWER(d.name) LIKE '%%amendement%%')
  AND LOWER(d.name) NOT LIKE '%%notulen%%'
  AND d.id > %s
ORDER BY d.id
"""


# -- Notulen candidate lookup ----------------------------------------
#
# Two separate queries keep the planner happy: exact motion_number
# lookups are index-assisted, while the ILIKE fallback is a full scan
# over a filtered subset and can be expensive -- we only run it when
# the exact lookup returns nothing, so the cost is proportional to the
# miss rate, not the motie count.

NOTULEN_EXACT_SQL = """
SELECT
    dc.id           AS chunk_id,
    dc.document_id  AS notulen_document_id,
    dc.content,
    dc.vote_outcome,
    d.meeting_id    AS notulen_meeting_id,
    m.start_date    AS notulen_date
FROM document_chunks dc
JOIN documents d ON dc.document_id = d.id
LEFT JOIN meetings m ON d.meeting_id = m.id
WHERE dc.motion_number = %s
  AND LOWER(d.name) LIKE '%%notulen%%'
  AND (%s::date IS NULL OR m.start_date IS NULL
       OR m.start_date BETWEEN (%s::date - (%s || ' days')::interval)
                           AND (%s::date + (%s || ' days')::interval))
"""

NOTULEN_FALLBACK_SQL = """
SELECT
    dc.id           AS chunk_id,
    dc.document_id  AS notulen_document_id,
    dc.content,
    dc.vote_outcome,
    d.meeting_id    AS notulen_meeting_id,
    m.start_date    AS notulen_date
FROM document_chunks dc
JOIN documents d ON dc.document_id = d.id
LEFT JOIN meetings m ON d.meeting_id = m.id
WHERE dc.motion_number IS DISTINCT FROM %s
  AND dc.content ILIKE %s
  AND LOWER(d.name) LIKE '%%notulen%%'
  AND (%s::date IS NULL OR m.start_date IS NULL
       OR m.start_date BETWEEN (%s::date - (%s || ' days')::interval)
                           AND (%s::date + (%s || ' days')::interval))
LIMIT 200
"""


# -- Stats -----------------------------------------------------------

class Stats:
    def __init__(self):
        self.moties_processed: int = 0
        self.moties_with_match: int = 0
        self.moties_zero_match: int = 0
        self.zero_match_sample: list[str] = []
        self.discussed_edges: int = 0
        self.voted_edges: int = 0
        self.skipped_dupe_edges: int = 0
        self.total_matches: int = 0
        self.start_time: float = time.time()

    def record_zero(self, document_id: str) -> None:
        self.moties_zero_match += 1
        if len(self.zero_match_sample) < ZERO_MATCH_SAMPLE:
            self.zero_match_sample.append(document_id)

    def report(self) -> str:
        elapsed = time.time() - self.start_time
        rate = self.moties_processed / elapsed if elapsed > 0 else 0.0
        avg = (
            self.total_matches / self.moties_with_match
            if self.moties_with_match > 0
            else 0.0
        )
        return (
            f"moties={self.moties_processed:,} | "
            f"matched={self.moties_with_match:,} | "
            f"zero={self.moties_zero_match:,} | "
            f"DISCUSSED_IN={self.discussed_edges:,} | "
            f"VOTED_IN={self.voted_edges:,} | "
            f"skipped_dupes={self.skipped_dupe_edges:,} | "
            f"avg_matches/motie={avg:.2f} | "
            f"{rate:.1f} moties/s"
        )


# -- Core linker -----------------------------------------------------

def link_one_motie(
    read_cur,
    write_cur,
    motie_row: dict,
    target_mode: str,
    date_window_days: int,
    stats: Stats,
    dry_run: bool,
) -> list[tuple]:
    """
    Process a single motie row and return the batch of edge-insert
    tuples (not yet flushed). The caller handles batching/commits so
    that one slow motie does not force a per-row round-trip.
    """
    document_id: str = motie_row["document_id"]
    doc_name: str | None = motie_row["doc_name"]
    motion_number: str | None = motie_row["motion_number"]
    motie_date = motie_row["motie_date"]

    motie_label = motion_number or doc_name or f"motie-doc-{document_id}"

    # Motie with no motion_number and no name cannot be matched at all.
    if not motion_number and not doc_name:
        stats.record_zero(document_id)
        return []

    source_entity_id = get_or_create_motie_entity(
        write_cur, motie_label, document_id, doc_name
    )

    # --- Exact motion_number match (primary path) --------------------
    candidates: dict[int, dict] = {}

    if motion_number:
        read_cur.execute(
            NOTULEN_EXACT_SQL,
            (motion_number, motie_date, motie_date, date_window_days,
             motie_date, date_window_days),
        )
        for row in read_cur.fetchall():
            candidates[row["chunk_id"]] = row

        # Fallback: content ILIKE on notulen chunks whose motion_number
        # is not the exact value (or NULL). This catches chunks the
        # rule enricher missed.
        like_pattern = f"%{motion_number}%"
        read_cur.execute(
            NOTULEN_FALLBACK_SQL,
            (motion_number, like_pattern, motie_date, motie_date,
             date_window_days, motie_date, date_window_days),
        )
        for row in read_cur.fetchall():
            candidates.setdefault(row["chunk_id"], row)

    if not candidates:
        stats.record_zero(document_id)
        return []

    stats.moties_with_match += 1
    stats.total_matches += len(candidates)

    # --- Build insert tuples ----------------------------------------
    batch: list[tuple] = []
    for chunk_id, notulen in candidates.items():
        relation_type = "VOTED_IN" if notulen["vote_outcome"] else "DISCUSSED_IN"

        # Dedup against existing edges from a prior run.
        if relationship_exists(write_cur, source_entity_id, chunk_id, relation_type):
            stats.skipped_dupe_edges += 1
            continue

        if target_mode == "document":
            target_entity_id: int | None = get_or_create_document_entity(
                write_cur,
                notulen["notulen_meeting_id"],
                notulen["notulen_document_id"],
            )
        else:
            target_entity_id = None

        content: str | None = notulen["content"]
        quote = content[:QUOTE_LEN] if content else None

        metadata = {
            "motion_number": motion_number,
            "motie_document_id": document_id,
            "motie_doc_name": doc_name,
            "motie_date": str(motie_date) if motie_date else None,
            "notulen_date": (
                str(notulen["notulen_date"]) if notulen["notulen_date"] else None
            ),
            "notulen_meeting_id": notulen["notulen_meeting_id"],
            "vote_outcome": notulen["vote_outcome"],
            "match_type": (
                "motion_number_exact"
                if motion_number and notulen.get("content")
                and motion_number in (notulen["content"] or "")
                else "motion_number_exact"
            ),
            "target_mode": target_mode,
            "date_window_days": date_window_days,
        }

        confidence = 0.95 if relation_type == "VOTED_IN" else 0.85

        batch.append(
            (
                source_entity_id,
                target_entity_id,
                relation_type,
                notulen["notulen_document_id"],
                chunk_id,
                confidence,
                quote,
                Json(metadata),
            )
        )

        if relation_type == "VOTED_IN":
            stats.voted_edges += 1
        else:
            stats.discussed_edges += 1

    if dry_run:
        # In dry-run mode we built the tuples only for stats; caller
        # will not flush them.
        return []

    return batch


# -- Main loop -------------------------------------------------------

def run(
    dry_run: bool,
    limit: int | None,
    resume: bool,
    batch_size: int,
    date_window_days: int,
    target_mode: str,
    wait_for_lock: bool,
) -> None:

    log.info("=" * 72)
    log.info("  LINK MOTIE -> NOTULEN (WS1 Phase 0)")
    log.info(f"  dry_run={dry_run}  limit={limit}  resume={resume}")
    log.info(f"  batch_size={batch_size}  date_window_days={date_window_days}")
    log.info(f"  target_mode={target_mode}  wait_for_lock={wait_for_lock}")
    log.info("=" * 72)

    # Two connections: one server-side cursor for reading moties, one
    # for per-motie lookups + writes. The write connection holds the
    # advisory lock.
    read_conn = psycopg2.connect(DB_URL)
    work_conn = psycopg2.connect(DB_URL)
    work_cur = work_conn.cursor(cursor_factory=RealDictCursor)

    acquire_advisory_lock(work_conn, wait=wait_for_lock)

    # Preflight: convention check.
    verify_motie_convention(work_cur)

    checkpoint = load_checkpoint() if resume else {
        "last_motie_id": "",
        "edges_inserted": 0,
        "ts": None,
    }
    start_motie_id: str = checkpoint["last_motie_id"] or ""
    already_inserted: int = checkpoint["edges_inserted"] if resume else 0

    if resume and start_motie_id:
        log.info(
            f"Resuming from document_id > '{start_motie_id}' "
            f"({already_inserted:,} edges already inserted)"
        )

    # Total motie count for the progress log.
    count_cur = work_conn.cursor()
    count_cur.execute(
        """
        SELECT COUNT(*) FROM documents
        WHERE (LOWER(name) LIKE '%%motie%%' OR LOWER(name) LIKE '%%amendement%%')
          AND LOWER(name) NOT LIKE '%%notulen%%'
          AND id > %s
        """,
        (start_motie_id,),
    )
    total_moties = count_cur.fetchone()[0]
    count_cur.close()
    if limit is not None:
        total_moties = min(total_moties, limit)
    log.info(f"Total moties/amendementen to process: {total_moties:,}")

    # Server-side cursor for moties.
    read_cur = read_conn.cursor(
        name="link_motie_reader", cursor_factory=RealDictCursor
    )
    read_cur.itersize = 500
    read_cur.execute(MOTIES_SQL, (start_motie_id,))

    stats = Stats()
    batch: list[tuple] = []
    last_motie_id: str = start_motie_id
    edges_inserted_session: int = 0

    try:
        for motie_row in read_cur:
            if limit is not None and stats.moties_processed >= limit:
                break

            per_motie = link_one_motie(
                read_cur=work_cur,
                write_cur=work_cur,
                motie_row=motie_row,
                target_mode=target_mode,
                date_window_days=date_window_days,
                stats=stats,
                dry_run=dry_run,
            )
            batch.extend(per_motie)
            stats.moties_processed += 1
            last_motie_id = motie_row["document_id"]

            if len(batch) >= batch_size:
                inserted = flush_batch(work_cur, batch, dry_run)
                if not dry_run:
                    work_conn.commit()
                edges_inserted_session += inserted
                batch = []

            if stats.moties_processed % 500 == 0:
                log.info(f"[{stats.moties_processed:>8,}] {stats.report()}")

            if stats.moties_processed % 2_000 == 0 and not dry_run:
                save_checkpoint(
                    last_motie_id, already_inserted + edges_inserted_session
                )

        # Flush tail
        if batch:
            inserted = flush_batch(work_cur, batch, dry_run)
            if not dry_run:
                work_conn.commit()
            edges_inserted_session += inserted
            batch = []

        if not dry_run:
            save_checkpoint(
                last_motie_id, already_inserted + edges_inserted_session
            )

    except KeyboardInterrupt:
        log.warning("Interrupted! Flushing current batch and saving checkpoint...")
        if batch and not dry_run:
            edges_inserted_session += flush_batch(work_cur, batch, dry_run)
            work_conn.commit()
        if not dry_run:
            save_checkpoint(
                last_motie_id, already_inserted + edges_inserted_session
            )
    except Exception:
        log.exception("Fatal error during linking")
        if not dry_run:
            save_checkpoint(
                last_motie_id, already_inserted + edges_inserted_session
            )
        raise
    finally:
        read_cur.close()
        read_conn.close()
        release_advisory_lock(work_conn)
        work_cur.close()
        work_conn.close()

    # -- Final report ------------------------------------------------
    log.info("=" * 72)
    log.info("  MOTIE -> NOTULEN LINKING COMPLETE")
    log.info(f"  {stats.report()}")
    log.info(f"  Edges inserted this session: {edges_inserted_session:,}")
    log.info(f"  Last motie document_id: {last_motie_id}")
    if stats.zero_match_sample:
        log.info(
            f"  Zero-match sample (up to {ZERO_MATCH_SAMPLE}): "
            f"{', '.join(stats.zero_match_sample)}"
        )
    if stats.moties_processed > 0:
        coverage = 100.0 * stats.moties_with_match / stats.moties_processed
        log.info(f"  Coverage: {coverage:.1f}% of moties linked to at least one notulen chunk")
        if coverage < 70.0:
            log.warning(
                "Coverage < 70%. Before declaring Phase 0 done, audit "
                "motion_number normalisation (see header docstring)."
            )
    if dry_run:
        log.info("  DRY RUN -- nothing was written.")
    log.info("=" * 72)


# -- CLI -------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Link moties/amendementen to the notulen chunks that "
                    "discussed or voted on them (DISCUSSED_IN / VOTED_IN)."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Compute stats without writing any kg_relationships rows.",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Process only the first N moties (for smoke tests).",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="Resume from data/pipeline_state/motie_linking_checkpoint.json.",
    )
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
        help=f"kg_relationships insert batch size (default {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--date-window-days", type=int, default=DEFAULT_DATE_WINDOW_DAYS,
        help=(
            "Candidate notulen meetings must fall within motie_date "
            f"+/- this many days (default {DEFAULT_DATE_WINDOW_DAYS}). "
            "Set to a very large value to disable."
        ),
    )
    parser.add_argument(
        "--target-mode", choices=["null", "document"], default="null",
        help=(
            "How to fill kg_relationships.target_entity_id. "
            "'null' (default): leave NULL and rely on chunk_id. "
            "'document': create/reuse a type='Document' placeholder "
            "per notulen meeting."
        ),
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
        date_window_days=args.date_window_days,
        target_mode=args.target_mode,
        wait_for_lock=args.wait_for_lock,
    )


if __name__ == "__main__":
    main()
