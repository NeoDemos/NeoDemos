"""
Backfill embedded_at on existing document_chunks that are already in Qdrant.

Runs in small batches (no single large transaction) so it's safe over SSH tunnel.
Two modes:
  --fast   Mark ALL existing chunks as embedded (assumes corpus is in Qdrant).
           Safe after a full migrate_embeddings.py run. ~5min for 1.74M rows.
  --exact  Cross-reference Qdrant to only mark chunks that are actually there.
           Slower (~20min) but correct if Qdrant has gaps.

Usage:
    python scripts/backfill_embedded_at.py --fast
    python scripts/backfill_embedded_at.py --exact
"""

import os, sys, hashlib, argparse
sys.path.insert(0, os.getcwd())

from dotenv import load_dotenv
load_dotenv()

import psycopg2
from tqdm import tqdm

DB_URL = os.environ["DATABASE_URL"]
BATCH_SIZE = 10_000


def fast_backfill():
    """Batch-update ALL chunks with embedded_at IS NULL in BATCH_SIZE chunks."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM document_chunks WHERE embedded_at IS NULL")
    total = cur.fetchone()[0]
    print(f"Chunks needing backfill: {total:,}")

    cur.execute("SELECT MIN(id), MAX(id) FROM document_chunks")
    min_id, max_id = cur.fetchone()
    conn.close()

    done = 0
    lo = min_id
    with tqdm(total=total, desc="Backfilling embedded_at", unit="chunk") as pbar:
        while lo <= max_id:
            hi = lo + BATCH_SIZE - 1
            conn2 = psycopg2.connect(DB_URL)
            c2 = conn2.cursor()
            c2.execute(
                "UPDATE document_chunks SET embedded_at = NOW() "
                "WHERE id BETWEEN %s AND %s AND embedded_at IS NULL",
                (lo, hi)
            )
            updated = c2.rowcount
            conn2.commit()
            conn2.close()
            done += updated
            pbar.update(updated)
            lo = hi + 1

    print(f"Done. {done:,} rows backfilled.")


def exact_backfill():
    """Cross-reference Qdrant, only mark chunks confirmed present there."""
    from qdrant_client import QdrantClient
    from audit_vector_gaps import compute_missing_ids

    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    qdrant_key = os.getenv("QDRANT_API_KEY", "")
    qdrant = QdrantClient(url=qdrant_url,
                          api_key=qdrant_key if qdrant_key else None,
                          timeout=120)

    conn = psycopg2.connect(DB_URL)

    print("Computing missing IDs (cross-referencing Qdrant ↔ Postgres)...")
    missing_ids = set(compute_missing_ids(qdrant_client=qdrant, pg_conn=conn))
    print(f"  {len(missing_ids):,} chunks NOT in Qdrant — will skip those")

    # Fetch all chunk IDs where embedded_at IS NULL
    cur = conn.cursor()
    cur.execute("SELECT id FROM document_chunks WHERE embedded_at IS NULL ORDER BY id")
    all_null_ids = [r[0] for r in cur.fetchall()]
    cur.close()
    print(f"  {len(all_null_ids):,} chunks with embedded_at IS NULL")

    to_mark = [i for i in all_null_ids if i not in missing_ids]
    print(f"  {len(to_mark):,} chunks confirmed in Qdrant → marking embedded_at")

    # Batch update
    for start in tqdm(range(0, len(to_mark), BATCH_SIZE), desc="Updating", unit="batch"):
        batch = to_mark[start:start + BATCH_SIZE]
        c2 = conn.cursor()
        c2.execute(
            "UPDATE document_chunks SET embedded_at = NOW() WHERE id = ANY(%s)",
            (batch,)
        )
        conn.commit()
        c2.close()

    conn.close()
    print(f"Done. {len(to_mark):,} rows backfilled.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--fast", action="store_true",
                       help="Mark all existing chunks as embedded (assumes Qdrant is complete)")
    group.add_argument("--exact", action="store_true",
                       help="Cross-reference Qdrant first, only mark confirmed chunks")
    args = parser.parse_args()

    if args.fast:
        fast_backfill()
    else:
        exact_backfill()
