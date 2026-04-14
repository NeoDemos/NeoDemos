"""
cleanup_safe_orphans.py — Phase 9: delete safe orphan Qdrant points

Reads data/pipeline_state/orphan_snapshot/orphan_full_audit.jsonl.gz and deletes
all orphans EXCEPT those in category NO_OVERLAP (preserved for investigation).

Safe categories deleted:
  EXACT_DUP, SUBSTRING_OF_CURRENT, CONTAINS_CURRENT, PARTIAL_OVERLAP,
  GARBLED, DOC_DELETED, NO_DOC_ID, EMPTY
"""
import argparse, gzip, json, os, time, logging
from pathlib import Path
from qdrant_client import QdrantClient
from qdrant_client.http.models import PointIdsList

try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError: pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

AUDIT_FILE = Path("data/pipeline_state/orphan_snapshot/orphan_full_audit.jsonl.gz")
COLLECTION = "notulen_chunks"
PRESERVE = {"NO_OVERLAP"}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--batch-size", type=int, default=1000)
    args = ap.parse_args()

    q = QdrantClient(url=os.environ.get("QDRANT_URL"),
                     api_key=os.environ.get("QDRANT_API_KEY"), timeout=120)

    logger.info("Reading audit file %s", AUDIT_FILE)
    to_delete = []
    preserved = 0
    cat_counts = {}
    with gzip.open(AUDIT_FILE, "rt", encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            cat = rec.get("category", "UNKNOWN")
            cat_counts[cat] = cat_counts.get(cat, 0) + 1
            if cat in PRESERVE:
                preserved += 1
                continue
            to_delete.append(rec["point_id"])

    logger.info("Audit breakdown:")
    for k, v in sorted(cat_counts.items(), key=lambda x: -x[1]):
        logger.info("  %-24s %10d", k, v)
    logger.info("Preserved (%s): %d", ",".join(PRESERVE), preserved)
    logger.info("To delete: %d (in batches of %d)", len(to_delete), args.batch_size)
    if args.dry_run:
        logger.info("[DRY RUN] Exiting without deletion.")
        return

    t0 = time.time()
    deleted = 0
    for i in range(0, len(to_delete), args.batch_size):
        batch = to_delete[i:i+args.batch_size]
        try:
            q.delete(collection_name=COLLECTION, points_selector=PointIdsList(points=batch))
            deleted += len(batch)
            if (i // args.batch_size) % 10 == 0:
                logger.info("  %d / %d deleted (%.1f%%)", deleted, len(to_delete), 100*deleted/len(to_delete))
        except Exception as e:
            logger.error("Batch delete failed at offset %d: %s", i, e)

    elapsed = time.time() - t0
    logger.info("Done. Deleted %d orphan points in %.1f sec (%.1f/sec)", deleted, elapsed, deleted/max(elapsed, 0.01))
    count = q.count(collection_name=COLLECTION, exact=True)
    logger.info("Qdrant collection now: %d points", count.count)

if __name__ == "__main__":
    main()
