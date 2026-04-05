"""
Deletes oversized chunks (>50K chars) from document_chunks and re-chunks their
parent documents through SmartIngestor (Gemini hierarchical split).

These chunks are currently skipped by migrate_embeddings.py. After re-splitting,
the resulting grandchildren (≤8K chars each) will be picked up by the next
migrate_embeddings.py --recovery-mode run.

Safety: Runs after migrate_embeddings.py finishes (do NOT run concurrently
with an active migration — it writes to document_chunks).

Usage:
    python scripts/resplit_oversized_chunks.py --dry-run   # preview only
    python scripts/resplit_oversized_chunks.py             # execute
"""

import sys, os, json, logging
sys.path.insert(0, os.getcwd())

import psycopg2
from tqdm import tqdm

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/resplit_oversized.log", mode="a"),
    ]
)
logger = logging.getLogger("resplit")


def get_oversized_parent_documents(conn):
    """Find distinct parent documents that have at least one chunk >50K chars."""
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT dc.document_id, d.name, d.content, d.meeting_id,
               MAX(LENGTH(dc.content)) as max_chunk_len,
               COUNT(*) as oversized_count
        FROM document_chunks dc
        JOIN documents d ON dc.document_id = d.id
        WHERE LENGTH(dc.content) > 50000
        GROUP BY dc.document_id, d.name, d.content, d.meeting_id
        ORDER BY MAX(LENGTH(dc.content)) DESC
    """)
    rows = cur.fetchall()
    cur.close()
    return rows


def delete_all_chunks_for_document(conn, doc_id: str):
    """Remove all existing chunks and children for this document (clean slate)."""
    cur = conn.cursor()
    # Get child IDs first
    cur.execute("SELECT id FROM document_children WHERE document_id = %s", (doc_id,))
    child_ids = [r[0] for r in cur.fetchall()]
    if child_ids:
        cur.execute("DELETE FROM document_chunks WHERE child_id = ANY(%s)", (child_ids,))
        cur.execute("DELETE FROM document_children WHERE id = ANY(%s)", (child_ids,))
    cur.execute("DELETE FROM document_chunks WHERE document_id = %s AND child_id IS NULL", (doc_id,))
    conn.commit()
    cur.close()


def main(dry_run=False):
    conn = psycopg2.connect(DB_URL)
    parents = get_oversized_parent_documents(conn)
    conn.close()

    logger.info(f"Found {len(parents)} parent documents with oversized chunks (>50K chars)")
    if dry_run:
        for doc_id, name, content, meeting_id, max_len, count in parents:
            logger.info(f"  {name[:70]:<70}  max={max_len:>10}  oversized_chunks={count}")
        logger.info("DRY RUN — no changes made")
        return

    from pipeline.ingestion import SmartIngestor
    ingestor = SmartIngestor(chunk_only=True)

    stats = {"processed": 0, "errors": 0}
    pbar = tqdm(parents, desc="Re-splitting", unit="doc")

    for doc_id, name, content, meeting_id, max_len, count in pbar:
        pbar.set_postfix(doc=name[:40] if name else doc_id)
        try:
            conn = psycopg2.connect(DB_URL)
            delete_all_chunks_for_document(conn, doc_id)
            conn.close()

            ingestor.ingest_document(
                doc_id=doc_id,
                doc_name=name or "Unnamed",
                content=content or "",
                meeting_id=meeting_id,
            )
            stats["processed"] += 1
            logger.info(f"  Re-split: {name[:70]} (was max {max_len} chars, {count} oversized chunks)")
        except Exception as e:
            stats["errors"] += 1
            logger.error(f"  Failed: {doc_id} — {e}")

    pbar.close()
    logger.info(f"\nDone. Processed={stats['processed']}  Errors={stats['errors']}")
    logger.info("Next: run migrate_embeddings.py --recovery-mode to embed new chunks")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
