import json
import psycopg2
from collections import Counter
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("transcript_purge")

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"

def find_contaminated_meetings():
    mapping = json.load(open('ibabs_uuid_mapping.json'))
    
    # Counter for UUIDs
    counts = Counter(mapping.values())
    
    # UUIDs that appear more than once are contaminated
    contaminated_uuids = {uuid for uuid, count in counts.items() if count > 1}
    
    # Numeric IDs affected
    affected_ids = [nid for nid, uuid in mapping.items() if uuid in contaminated_uuids]
    
    return affected_ids, contaminated_uuids

from qdrant_client import QdrantClient
from qdrant_client.http import models

QDRANT_PATH = "./data/qdrant_storage"
COLLECTION_NAME = "notulen_chunks_local"

def run_purge(dry_run=True):
    affected_ids, contaminated_uuids = find_contaminated_meetings()
    
    logger.info(f"Found {len(contaminated_uuids)} shared UUIDs affecting {len(affected_ids)} meeting IDs.")
    
    if not affected_ids:
        logger.info("No contaminated meetings found.")
        return

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    
    # Get document IDs for these meetings
    cur.execute("SELECT id FROM documents WHERE meeting_id IN %s", (tuple(affected_ids),))
    doc_ids = [str(r[0]) for r in cur.fetchall()]
    
    if not doc_ids:
        logger.info("No documents found for affected IDs.")
        return

    logger.info(f"Purging {len(doc_ids)} documents...")

    if dry_run:
        logger.info(f"[DRY RUN] Would delete documents and chunks for: {doc_ids[:5]}...")
    else:
        # Delete from Qdrant first (safest order)
        try:
            qdrant = QdrantClient(path=QDRANT_PATH)
            logger.info(f"Deleting points from Qdrant [{COLLECTION_NAME}]...")
            for did in doc_ids:
                qdrant.delete(
                    collection_name=COLLECTION_NAME,
                    points_selector=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="document_id",
                                match=models.MatchValue(value=did),
                            )
                        ]
                    ),
                )
            logger.info("Qdrant points deleted.")
        except Exception as e:
            logger.error(f"Qdrant purge failed: {e}")

        # Delete from Postgres
        cur.execute("DELETE FROM documents WHERE id IN %s", (tuple(doc_ids),))
        conn.commit()
        logger.info(f"Postgres documents and chunks deleted successfully.")

    conn.close()

if __name__ == "__main__":
    import sys
    dry = "--apply" not in sys.argv
    run_purge(dry_run=dry)
