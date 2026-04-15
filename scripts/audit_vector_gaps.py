import os
import sys
import json
import psycopg2
from tqdm import tqdm
from qdrant_client import QdrantClient

# Add project root to sys.path
sys.path.insert(0, os.getcwd())

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
COLLECTION_NAME = "notulen_chunks"
OUTPUT_FILE = "data/pipeline_state/missing_ids_gap_audit.json"


def run_audit(limit: int | None = None, db_conn=None) -> dict:
    """
    Callable API for the QA digest.

    WS5a Phase A addition — returns a structured dict without touching disk
    and without running the full 1.7M-row cross-reference when a ``limit`` is
    supplied. Read-only; safe to call while WS6 Phase 3 / WS11 Phase 6 are in
    flight because every SELECT is short (planner stats or indexed LIMIT).

    Returns::

        {
            "missing_count": int,         # chunks in PG without a Qdrant point
            "sample_missing_ids": [int],  # up to 20 IDs for operator triage
            "pg_chunks": int,             # total chunks in Postgres
            "qdrant_points": int,         # total points in Qdrant (estimated)
            "sampled": bool,              # True when limit was applied
        }
    """
    from services.db_pool import get_connection
    from services.embedding import compute_point_id

    qdrant_client = QdrantClient(
        url=os.getenv("QDRANT_URL", "http://localhost:6333"),
        api_key=os.getenv("QDRANT_API_KEY"),
        timeout=30,
    )

    # 1. Qdrant point count (cheap — /collections/<name> returns it from metadata)
    try:
        info = qdrant_client.get_collection(COLLECTION_NAME)
        qdrant_points = int(info.points_count or 0)
    except Exception:
        qdrant_points = -1

    # 2. Postgres chunk count (planner estimate — the precise COUNT(*) can trip
    # the 60s statement_timeout on the 1.7M-row table). Also pull a bounded
    # sample to cross-check.
    ctx = get_connection() if db_conn is None else _NoCloseConn(db_conn)
    with ctx as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT reltuples::bigint
            FROM pg_class
            WHERE relname = 'document_chunks'
              AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
            """
        )
        row = cur.fetchone()
        pg_chunks = int(row[0]) if row else -1
        cur.close()

        # 3. Gap detection — either full cross-reference or a sampled probe.
        sample_ids: list[int] = []
        missing_count = 0
        sampled = limit is not None

        # Pull Qdrant point IDs once — for a sampled run we still need the full
        # set to accurately answer "is this chunk's point present?".
        qdrant_ids: set[int] = set()
        offset = None
        while True:
            res, next_offset = qdrant_client.scroll(
                collection_name=COLLECTION_NAME,
                limit=50000,
                with_payload=False,
                with_vectors=False,
                offset=offset,
            )
            for p in res:
                qdrant_ids.add(p.id)
            offset = next_offset
            if not offset:
                break

        stream_cur = conn.cursor(name="qa_gap_stream")
        stream_cur.itersize = 10000
        if limit is not None:
            stream_cur.execute(
                "SELECT id, document_id FROM document_chunks ORDER BY id LIMIT %s",
                (limit,),
            )
        else:
            stream_cur.execute(
                "SELECT id, document_id FROM document_chunks ORDER BY id"
            )

        for db_id, doc_id in stream_cur:
            point_id = compute_point_id(str(doc_id), db_id)
            if point_id not in qdrant_ids:
                missing_count += 1
                if len(sample_ids) < 20:
                    sample_ids.append(db_id)
        stream_cur.close()

    return {
        "missing_count": missing_count,
        "sample_missing_ids": sample_ids,
        "pg_chunks": pg_chunks,
        "qdrant_points": qdrant_points,
        "sampled": sampled,
    }


class _NoCloseConn:
    """Adapt a borrowed psycopg2 connection into a context manager that does
    not close it on exit (the caller owns the connection lifetime)."""

    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            try:
                self._conn.rollback()
            except Exception:
                pass
        else:
            try:
                self._conn.commit()
            except Exception:
                pass
        return False

def compute_missing_ids(qdrant_client=None, pg_conn=None) -> list:
    """
    Cross-references Qdrant and Postgres to find chunk IDs not yet embedded.
    Returns a sorted list of Postgres chunk IDs that are missing from Qdrant.
    Callers can pass existing clients to avoid re-connecting.
    """
    own_qdrant = qdrant_client is None
    own_conn = pg_conn is None

    if own_qdrant:
        qdrant_client = QdrantClient(url="http://localhost:6333")
    if own_conn:
        pg_conn = psycopg2.connect(DB_URL)

    # 1. Fetch all Qdrant point IDs (no payload/vectors — fast)
    print("Auditing: fetching existing IDs from Qdrant...")
    qdrant_ids = set()
    offset = None
    while True:
        res, next_offset = qdrant_client.scroll(
            collection_name=COLLECTION_NAME,
            limit=50000,
            with_payload=False,
            with_vectors=False,
            offset=offset
        )
        for p in res:
            qdrant_ids.add(p.id)
        offset = next_offset
        if not offset:
            break
    print(f"Auditing: {len(qdrant_ids)} points in Qdrant.")

    # 2. Stream all Postgres chunk IDs via server-side cursor (avoids loading 1.3M rows into RAM)
    print("Auditing: fetching all chunk IDs from Postgres...")
    stream_cur = pg_conn.cursor(name='audit_stream')
    stream_cur.itersize = 50000
    stream_cur.execute("SELECT id, document_id FROM document_chunks ORDER BY id ASC")

    # Count separately for the progress bar (fast index scan)
    count_cur = pg_conn.cursor()
    count_cur.execute("SELECT COUNT(*) FROM document_chunks")
    total_pg = count_cur.fetchone()[0]
    count_cur.close()
    print(f"Auditing: {total_pg} chunks in Postgres.")

    # 3. Cross-reference using the canonical hash (services.embedding.compute_point_id)
    from services.embedding import compute_point_id
    missing_ids = []
    for db_id, doc_id in tqdm(stream_cur, desc="Auditing gaps", unit="chunk", total=total_pg):
        point_id = compute_point_id(str(doc_id), db_id)
        if point_id not in qdrant_ids:
            missing_ids.append(db_id)

    stream_cur.close()
    if own_conn:
        pg_conn.cursor().close()

    print(f"Audit complete: {len(missing_ids)} missing chunks.")
    return missing_ids


def audit():
    """Standalone audit: compute missing IDs and save to disk."""
    missing_ids = compute_missing_ids()

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        json.dump(missing_ids, f)
    print(f"Gap list saved to {OUTPUT_FILE}")


if __name__ == "__main__":
    audit()
