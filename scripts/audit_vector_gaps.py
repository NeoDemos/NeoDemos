import os
import sys
import json
import hashlib
import psycopg2
from tqdm import tqdm
from qdrant_client import QdrantClient

# Add project root to sys.path
sys.path.insert(0, os.getcwd())

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
COLLECTION_NAME = "notulen_chunks"
OUTPUT_FILE = "data/pipeline_state/missing_ids_gap_audit.json"

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
            limit=10000,
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

    # 3. Cross-reference using the same hash as migrate_embeddings.py
    missing_ids = []
    for db_id, doc_id in tqdm(stream_cur, desc="Auditing gaps", unit="chunk", total=total_pg):
        hash_str = hashlib.md5(f"{doc_id}_{db_id}".encode()).hexdigest()
        point_id = int(hash_str[:15], 16)
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
