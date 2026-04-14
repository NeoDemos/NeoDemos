"""
repair_scheme_b_points.py — Phase 3: Remove Scheme B Qdrant points for mis-keyed chunks

For each chunk where embedded_at IS NOT NULL (written by the old document_processor using
Scheme B), this script:
  1. Computes the Scheme B point ID: md5(f"{document_id}_{child_id}_{chunk_index}")[:15]
  2. Deletes that point from Qdrant (skips if already absent — idempotent)
  3. Resets embedded_at = NULL so migrate_embeddings.py picks it up under Scheme A

Run AFTER deploying DOCUMENT_PROCESSOR_PHASE2_ENABLED=false to Hetzner (so no new
Scheme B writes happen during the repair window).

Usage:
    python scripts/repair_scheme_b_points.py [--dry-run]
"""

import argparse
import hashlib
import logging
import os
import sys

import psycopg2
from qdrant_client import QdrantClient
from qdrant_client.http.models import PointIdsList

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

COLLECTION = "notulen_chunks"


def scheme_b_point_id(document_id: str, child_id, chunk_index: int) -> int:
    """Legacy Scheme B hash (as used by old document_processor.py)."""
    h = hashlib.md5(f"{document_id}_{child_id}_{chunk_index}".encode()).hexdigest()
    return int(h[:15], 16)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would happen but make no changes")
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
    qdrant = QdrantClient(url=qdrant_url, api_key=qdrant_api_key, timeout=30)

    # Fetch all mis-keyed chunks
    cur = conn.cursor()
    cur.execute("""
        SELECT id, document_id, child_id, chunk_index
        FROM document_chunks
        WHERE embedded_at IS NOT NULL
        ORDER BY id
    """)
    rows = cur.fetchall()
    cur.close()

    logger.info("Found %d chunks with embedded_at IS NOT NULL (Scheme B writes)", len(rows))
    if not rows:
        logger.info("Nothing to repair.")
        conn.close()
        return

    deleted = 0
    missing = 0
    errors = 0
    db_ids_to_reset = []

    for db_id, document_id, child_id, chunk_index in rows:
        b_id = scheme_b_point_id(document_id, child_id, chunk_index)
        try:
            # Check existence before delete
            result = qdrant.retrieve(
                collection_name=COLLECTION,
                ids=[b_id],
                with_vectors=False,
            )
            if result:
                if not args.dry_run:
                    qdrant.delete(
                        collection_name=COLLECTION,
                        points_selector=PointIdsList(points=[b_id]),
                    )
                deleted += 1
                if deleted <= 5 or deleted % 100 == 0:
                    logger.info("  Deleted Scheme B point %d (db_id=%d, doc=%s)",
                                b_id, db_id, document_id[:40])
            else:
                missing += 1

            db_ids_to_reset.append(db_id)

        except Exception as e:
            logger.error("  Error processing db_id=%d: %s", db_id, e)
            errors += 1

    logger.info("Qdrant: deleted=%d, already-absent=%d, errors=%d", deleted, missing, errors)

    # Reset embedded_at = NULL in batches of 1000
    if db_ids_to_reset and not args.dry_run:
        reset_cur = conn.cursor()
        batch_size = 1000
        total_reset = 0
        for i in range(0, len(db_ids_to_reset), batch_size):
            batch = db_ids_to_reset[i:i + batch_size]
            reset_cur.execute(
                "UPDATE document_chunks SET embedded_at = NULL WHERE id = ANY(%s)",
                (batch,)
            )
            conn.commit()
            total_reset += len(batch)
        reset_cur.close()
        logger.info("Reset embedded_at = NULL for %d chunks → migrate_embeddings will re-embed under Scheme A", total_reset)
    elif args.dry_run:
        logger.info("[DRY RUN] Would reset embedded_at for %d chunks", len(db_ids_to_reset))

    conn.close()
    logger.info("Done.")


if __name__ == "__main__":
    main()
