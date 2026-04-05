"""
Batch chunking for documents that exist in Postgres but have no chunks yet.
Uses SmartIngestor in chunk_only mode (heuristic + Gemini for structural detection).
New chunks get picked up by migrate_embeddings.py --recovery-mode for embedding.

Safety: Writes only to document_chunks and document_children tables.
Does NOT touch Qdrant or load any MLX models.

Two-pass strategy for speed:
  Pass 1: Atomic/Compact/Recursive docs — no API calls, instant.
  Pass 2: Structural docs (50K+) — parallel Gemini calls for section detection.

Usage:
    python scripts/chunk_unchunked_documents.py                    # process all unchunked
    python scripts/chunk_unchunked_documents.py --limit 100        # process 100 docs
    python scripts/chunk_unchunked_documents.py --dry-run           # count only
    python scripts/chunk_unchunked_documents.py --workers 8         # parallel Gemini calls
"""

import sys
import os
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.getcwd())

import psycopg2
from tqdm import tqdm

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
STATE_FILE = "data/pipeline_state/chunking_checkpoint.json"
STRUCTURAL_THRESHOLD = 50000  # must match SmartIngestor.structural_threshold

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/chunking.log", mode="a"),
    ]
)
logger = logging.getLogger("chunking")


def load_checkpoint() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f).get("completed_ids", []))
    return set()


def save_checkpoint(completed_ids: set):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump({"completed_ids": list(completed_ids)}, f)


def get_unchunked_documents(conn, limit=None):
    """Find documents with content but no chunks, including meeting metadata."""
    cur = conn.cursor()
    query = """
        SELECT d.id, d.name, d.content, d.meeting_id,
               d.category,
               m.start_date as meeting_date,
               m.committee as meeting_committee
        FROM documents d
        LEFT JOIN meetings m ON d.meeting_id = m.id
        WHERE NOT EXISTS (SELECT 1 FROM document_chunks dc WHERE dc.document_id = d.id)
        AND d.content IS NOT NULL AND LENGTH(d.content) > 10
        ORDER BY LENGTH(d.content) ASC
    """
    if limit:
        query += f" LIMIT {int(limit)}"
    cur.execute(query)
    rows = cur.fetchall()
    cur.close()
    return rows


def build_metadata(category, meeting_date, meeting_committee):
    metadata = {}
    if meeting_date:
        metadata["meeting_date"] = str(meeting_date)
    if meeting_committee:
        metadata["committee"] = meeting_committee
    if category:
        metadata["category"] = category
    return metadata or None


def process_doc(ingestor, doc_id, doc_name, content, meeting_id, category, meeting_date, meeting_committee):
    """Process a single document. Thread-safe for structural docs."""
    metadata = build_metadata(category, meeting_date, meeting_committee)
    ingestor.ingest_document(
        doc_id=doc_id,
        doc_name=doc_name or "Unnamed",
        content=content,
        meeting_id=meeting_id,
        metadata=metadata,
        category=category or "municipal_doc",
    )
    return doc_id


def main(limit=None, dry_run=False, workers=6):
    conn = psycopg2.connect(DB_URL)
    docs = get_unchunked_documents(conn, limit=limit)
    conn.close()

    completed = load_checkpoint()
    # Filter already-completed
    docs = [d for d in docs if d[0] not in completed]

    # Split into fast (no API) and structural (Gemini needed)
    fast_docs = [d for d in docs if len(d[2]) <= STRUCTURAL_THRESHOLD]
    structural_docs = [d for d in docs if len(d[2]) > STRUCTURAL_THRESHOLD]

    logger.info(f"Found {len(docs)} unchunked documents ({len(fast_docs)} fast, {len(structural_docs)} structural)")
    if dry_run:
        logger.info("DRY RUN — exiting")
        return

    from pipeline.ingestion import SmartIngestor
    ingestor = SmartIngestor(chunk_only=True)

    stats = {"processed": 0, "errors": 0}

    # ── Pass 1: Fast docs (no API calls) ─────────────────────────────
    if fast_docs:
        logger.info(f"═══ Pass 1: {len(fast_docs)} fast docs (atomic/compact/recursive) ═══")
        pbar = tqdm(fast_docs, desc="Pass 1 (fast)", unit="doc")
        for doc_id, doc_name, content, meeting_id, category, meeting_date, meeting_committee in pbar:
            try:
                process_doc(ingestor, doc_id, doc_name, content, meeting_id, category, meeting_date, meeting_committee)
                stats["processed"] += 1
                completed.add(doc_id)
                if stats["processed"] % 100 == 0:
                    save_checkpoint(completed)
                    pbar.set_postfix(ok=stats["processed"], err=stats["errors"])
            except Exception as e:
                stats["errors"] += 1
                logger.error(f"Failed to chunk {doc_id} ({doc_name}): {e}")
        save_checkpoint(completed)
        pbar.close()

    # ── Pass 2: Structural docs (parallel Gemini calls) ──────────────
    if structural_docs:
        logger.info(f"═══ Pass 2: {len(structural_docs)} structural docs ({workers} workers) ═══")
        pbar = tqdm(total=len(structural_docs), desc="Pass 2 (structural)", unit="doc")

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for doc in structural_docs:
                doc_id, doc_name, content, meeting_id, category, meeting_date, meeting_committee = doc
                future = executor.submit(
                    process_doc, ingestor, doc_id, doc_name, content, meeting_id,
                    category, meeting_date, meeting_committee
                )
                futures[future] = (doc_id, doc_name)

            for future in as_completed(futures):
                doc_id, doc_name = futures[future]
                try:
                    future.result()
                    stats["processed"] += 1
                    completed.add(doc_id)
                except Exception as e:
                    stats["errors"] += 1
                    logger.error(f"Failed to chunk {doc_id} ({doc_name}): {e}")
                pbar.update(1)
                pbar.set_postfix(ok=stats["processed"], err=stats["errors"])

                # Checkpoint every 20 structural docs
                if stats["processed"] % 20 == 0:
                    save_checkpoint(completed)

        save_checkpoint(completed)
        pbar.close()

    # ── Summary ──────────────────────────────────────────────────────
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM document_chunks")
    total_chunks = cur.fetchone()[0]
    cur.close()
    conn.close()

    logger.info(f"\nChunking complete.")
    logger.info(f"  Processed: {stats['processed']}")
    logger.info(f"  Errors: {stats['errors']}")
    logger.info(f"  Total chunks in DB: {total_chunks}")
    logger.info(f"\nNext step: run migrate_embeddings.py --recovery-mode to embed new chunks")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--workers", type=int, default=6, help="Parallel Gemini workers for structural docs")
    args = parser.parse_args()
    main(limit=args.limit, dry_run=args.dry_run, workers=args.workers)
