"""
rekey_vn_points.py — Phase 5: Re-key existing Virtual Notulen Qdrant points Scheme B → Scheme A

Existing VN points in notulen_chunks were written by the old promote_committee_notulen.py
using Scheme B: md5(f"{document_id}_{staging_child_id}_{chunk_index}")[:15]

This script:
  1. Scrolls notulen_chunks filtering is_virtual_notulen=True
  2. For each point, looks up the production document_chunks.id (db_id)
     using (document_id, chunk_index) — same chunk_index is preserved during promotion
  3. Computes the canonical Scheme A point ID: compute_point_id(document_id, db_id)
  4. If Scheme A ID differs from the current ID: upserts under Scheme A, deletes old ID
  5. Sets embedded_at = NOW() in document_chunks (the old promote script wrote it to staging)

Safe to re-run (idempotent):
  - If Scheme A ID already exists, upsert is a no-op
  - If old Scheme B ID is already gone, delete is a no-op

Usage:
    python scripts/rekey_vn_points.py [--dry-run] [--batch-size N]
"""

import argparse
import logging
import os
import sys

import psycopg2
from psycopg2.extras import execute_values
from qdrant_client import QdrantClient
from qdrant_client.http.models import Filter, FieldCondition, MatchValue, PointIdsList, PointStruct

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.embedding import compute_point_id

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

COLLECTION = "notulen_chunks"
SCROLL_BATCH = 200


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--batch-size", type=int, default=50,
                        help="DB lookup batch size (default: 50)")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        user = os.getenv("DB_USER", "postgres")
        pw = os.getenv("DB_PASSWORD", "postgres")
        host = os.getenv("DB_HOST", "localhost")
        port = os.getenv("DB_PORT", "5432")
        name = os.getenv("DB_NAME", "neodemos")
        db_url = f"postgresql://{user}:{pw}@{host}:{port}/{name}"

    qdrant_url = os.environ.get("QDRANT_URL", "http://localhost:6333")
    qdrant_api_key = os.environ.get("QDRANT_API_KEY")

    conn = psycopg2.connect(db_url)
    qdrant = QdrantClient(url=qdrant_url, api_key=qdrant_api_key, timeout=60)

    vn_filter = Filter(must=[
        FieldCondition(key="is_virtual_notulen", match=MatchValue(value=True))
    ])

    total_scrolled = 0
    rekeyed = 0
    already_ok = 0
    no_db_id = 0
    errors = 0
    db_ids_to_mark = []  # production db_ids where embedded_at should be set

    offset = None
    logger.info("Scanning notulen_chunks for is_virtual_notulen=True points...")

    while True:
        results, next_offset = qdrant.scroll(
            collection_name=COLLECTION,
            scroll_filter=vn_filter,
            limit=SCROLL_BATCH,
            offset=offset,
            with_vectors=True,
            with_payload=True,
        )

        if not results:
            break

        total_scrolled += len(results)

        # Batch-lookup production db_ids
        lookup_keys = []
        for pt in results:
            doc_id = pt.payload.get("document_id")
            chunk_index = pt.payload.get("chunk_index")
            if doc_id is not None and chunk_index is not None:
                lookup_keys.append((doc_id, chunk_index))

        # Fetch production db_ids from postgres using row-value IN comparison
        db_id_map = {}
        if lookup_keys:
            placeholders = ",".join(["(%s,%s)"] * len(lookup_keys))
            flat = [x for pair in lookup_keys for x in pair]
            cur = conn.cursor()
            cur.execute(
                f"SELECT document_id, chunk_index, id FROM document_chunks "
                f"WHERE (document_id, chunk_index::integer) IN ({placeholders})",
                flat
            )
            for doc_id, cidx, db_id in cur.fetchall():
                db_id_map[(doc_id, cidx)] = db_id
            cur.close()

        # Process each point
        to_upsert = []
        to_delete = []

        for pt in results:
            doc_id = pt.payload.get("document_id")
            chunk_index = pt.payload.get("chunk_index")
            old_id = pt.id

            if doc_id is None or chunk_index is None:
                logger.warning("  Point %d missing document_id/chunk_index — skipping", old_id)
                no_db_id += 1
                continue

            db_id = db_id_map.get((doc_id, chunk_index))
            if db_id is None:
                # Try string key (psycopg2 might return different types)
                db_id = db_id_map.get((str(doc_id), chunk_index))
            if db_id is None:
                logger.warning("  No production db_id for doc=%s idx=%s (pt=%d) — skipping",
                               doc_id, chunk_index, old_id)
                no_db_id += 1
                continue

            new_id = compute_point_id(str(doc_id), db_id)

            if new_id == old_id:
                already_ok += 1
                db_ids_to_mark.append(db_id)
                continue

            # Need re-key: upsert under new ID, delete old
            to_upsert.append(PointStruct(
                id=new_id,
                vector=pt.vector,
                payload=pt.payload,
            ))
            to_delete.append(old_id)
            db_ids_to_mark.append(db_id)
            rekeyed += 1

        if to_upsert and not args.dry_run:
            qdrant.upsert(collection_name=COLLECTION, points=to_upsert)
        if to_delete and not args.dry_run:
            qdrant.delete(
                collection_name=COLLECTION,
                points_selector=PointIdsList(points=to_delete),
            )

        if to_upsert:
            logger.info("  Batch: rekeyed %d VN points (upserted→deleted)", len(to_upsert))

        if next_offset is None:
            break
        offset = next_offset

    logger.info("Scroll complete: total=%d, rekeyed=%d, already_scheme_a=%d, no_db_id=%d, errors=%d",
                total_scrolled, rekeyed, already_ok, no_db_id, errors)

    # Mark embedded_at in production for all VN chunks we confirmed
    if db_ids_to_mark and not args.dry_run:
        cur = conn.cursor()
        batch_size = 1000
        total_marked = 0
        for i in range(0, len(db_ids_to_mark), batch_size):
            batch = db_ids_to_mark[i:i + batch_size]
            cur.execute(
                "UPDATE document_chunks SET embedded_at = NOW() WHERE id = ANY(%s) AND embedded_at IS NULL",
                (batch,)
            )
            total_marked += cur.rowcount
            conn.commit()
        cur.close()
        logger.info("Set embedded_at = NOW() for %d VN chunks in production document_chunks", total_marked)
    elif args.dry_run:
        logger.info("[DRY RUN] Would mark embedded_at for %d VN chunks", len(db_ids_to_mark))

    conn.close()
    logger.info("Done.")


if __name__ == "__main__":
    main()
