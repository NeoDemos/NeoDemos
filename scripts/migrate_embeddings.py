import sys
import os
import json
import logging
import psycopg2
import numpy as np
from tqdm import tqdm
from qdrant_client import QdrantClient
from qdrant_client.http import models
from dotenv import load_dotenv

load_dotenv()

# Add project root to sys.path
sys.path.insert(0, os.getcwd())
sys.stdout.reconfigure(line_buffering=True)

from services.embedding import create_embedder, compute_point_id
from scripts.audit_vector_gaps import compute_missing_ids

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/neodemos")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY")
COLLECTION_NAME = "notulen_chunks"
CHECKPOINT_FILE = "data/pipeline_state/migration_checkpoint.json"

# Initialize logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)


def process_and_upsert_batch(batch, embedder, qdrant, error_logger):
    """Embed a batch via API (batch call), upsert to Qdrant in bulk.
    Returns the number of points successfully upserted."""
    if not batch:
        return 0

    try:
        rows_data = []
        texts = []
        for r in batch:
            db_id, doc_id, title, content, c_type, child_id, start_date, doc_name, url, doc_classification, municipality, chunk_index = r
            rows_data.append(r)
            texts.append(content[:50000])

        # Batch embed via Nebius API (or local fallback)
        embeddings = embedder.embed_batch(texts)

        points = []
        skipped = 0
        for r, embedding in zip(rows_data, embeddings):
            db_id, doc_id, title, content, c_type, child_id, start_date, doc_name, url, doc_classification, municipality, chunk_index = r

            if embedding is None:
                error_logger.error(f"Skipping chunk {db_id}: embedding returned None.")
                skipped += 1
                continue

            if any(np.isnan(embedding)) or any(np.isinf(embedding)):
                error_logger.error(f"Skipping chunk {db_id}: Vector contains NaN or Inf.")
                skipped += 1
                continue

            if len(embedding) != 4096:
                error_logger.error(f"Skipping chunk {db_id}: Vector size {len(embedding)} != 4096")
                skipped += 1
                continue

            point_id = compute_point_id(str(doc_id), db_id)

            points.append(models.PointStruct(
                id=point_id,
                vector=embedding,
                payload={
                    "document_id": str(doc_id),
                    "database_id": db_id,
                    "title": title,
                    "content": content,
                    "chunk_type": c_type,
                    "child_id": child_id,
                    "start_date": start_date.isoformat() if start_date else None,
                    "doc_name": doc_name,
                    "url": url,
                    "doc_classification": doc_classification,
                    "municipality": municipality,
                    "chunk_index": chunk_index,
                }
            ))

        if points:
            try:
                qdrant.upsert(collection_name=COLLECTION_NAME, points=points, wait=False)
            except Exception as e_batch:
                logger.warning(f"Batch upsert failed ({e_batch}), falling back to individual upserts...")
                for point in points:
                    try:
                        qdrant.upsert(collection_name=COLLECTION_NAME, points=[point], wait=False)
                    except Exception as e_point:
                        error_logger.error(f"Failed to upsert point {point.id} (DocID: {point.payload['document_id']}): {e_point}")
                        skipped += 1

            # Checkpoint after batch completes
            last_db_id = batch[-1][0]
            with open(CHECKPOINT_FILE, "w") as f:
                json.dump({"last_processed_id": last_db_id}, f)

        return len(points) - skipped

    except Exception as e:
        logger.error(f"Batch processing failed for range {batch[0][0]}-{batch[-1][0]}: {e}")
        return 0


def migrate(recovery_mode=False, limit=None):
    os.makedirs("logs", exist_ok=True)
    error_logger = logging.getLogger("migration_errors")
    error_logger.setLevel(logging.ERROR)
    handler = logging.FileHandler("logs/migration_errors.log", mode='a')
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    error_logger.addHandler(handler)

    print(f"Starting migration to collection: {COLLECTION_NAME}")

    embedder = create_embedder()  # auto-selects Nebius API if NEBIUS_API_KEY set

    qdrant = QdrantClient(url=QDRANT_URL, api_key=QDRANT_API_KEY if QDRANT_API_KEY else None, timeout=120)

    # Create collection if it doesn't exist or has wrong dimensions
    collections = qdrant.get_collections().collections
    exists = any(c.name == COLLECTION_NAME for c in collections)

    if exists:
        info = qdrant.get_collection(COLLECTION_NAME)
        current_dim = info.config.params.vectors.size
        is_on_disk = info.config.params.vectors.on_disk

        if current_dim != 4096 or not is_on_disk:
            reason = "Dimension mismatch" if current_dim != 4096 else "on_disk=False"
            print(f"⚠️  {reason} inside {COLLECTION_NAME}. Recreating for performance...")
            qdrant.delete_collection(COLLECTION_NAME)
            exists = False

    if not exists:
        print(f"Creating collection {COLLECTION_NAME} (4096 dims, on_disk=True, scalar quantization)...")
        qdrant.create_collection(
            collection_name=COLLECTION_NAME,
            vectors_config=models.VectorParams(
                size=4096,
                distance=models.Distance.COSINE,
                on_disk=True
            ),
            quantization_config=models.ScalarQuantization(
                scalar=models.ScalarQuantizationConfig(
                    type=models.ScalarType.INT8,
                    quantile=0.99,
                    always_ram=True
                )
            )
        )

    conn = psycopg2.connect(DB_URL)

    # Load checkpoint (standard mode only)
    last_processed_id = 0
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, "r") as f:
                checkpoint = json.load(f)
                last_processed_id = checkpoint.get("last_processed_id", 0)
        except Exception:
            print("Could not load checkpoint, starting from scratch.")

    current_offset_id = last_processed_id
    batch_size = 64  # Nebius API handles batches of 64 efficiently
    total_processed = 0
    total_upserted = 0

    # Recovery mode: audit live to find missing IDs
    recovery_ids = None
    if recovery_mode:
        recovery_ids = compute_missing_ids(qdrant_client=qdrant, pg_conn=conn)
        if not recovery_ids:
            print("Audit found no missing chunks. Nothing to do.")
            conn.close()
            return "DONE"

    if recovery_ids:
        total_remaining = len(recovery_ids)
    else:
        count_cur = conn.cursor()
        count_cur.execute("SELECT COUNT(*) FROM document_chunks WHERE id > %s", (current_offset_id,))
        total_remaining = count_cur.fetchone()[0]
        count_cur.close()

    effective_total = min(limit, total_remaining) if limit else total_remaining
    print(f"Resuming from Database ID {current_offset_id} | Remaining: {effective_total} | Batch Size: {batch_size}")

    # 1. Recovery Mode: Sliding Window over Audit IDs
    if recovery_ids:
        window_size = 1000
        pbar = tqdm(total=effective_total, desc="Recovery", unit="chunk")
        current_batch = []

        for w_start in range(0, len(recovery_ids), window_size):
            if limit and total_processed >= limit:
                break

            window = recovery_ids[w_start:w_start + window_size]
            win_cur = conn.cursor()
            win_cur.execute("""
                SELECT dc.id, dc.document_id, dc.title, dc.content, dc.chunk_type, dc.child_id, m.start_date,
                       d.name AS doc_name, d.url, d.doc_classification, d.municipality, dc.chunk_index
                FROM document_chunks dc
                LEFT JOIN documents d ON dc.document_id = d.id
                LEFT JOIN meetings m ON d.meeting_id = m.id
                WHERE dc.id = ANY(%s)
                ORDER BY dc.id ASC
            """, (window,))

            rows = win_cur.fetchall()
            win_cur.close()

            for row in rows:
                if limit and total_processed >= limit:
                    break

                db_id, doc_id, title, content, c_type, child_id, start_date, doc_name, url, doc_classification, municipality, chunk_index = row
                if not content or len(content.strip()) < 2:
                    pbar.update(1)
                    total_processed += 1
                    continue

                if len(content) > 50000:
                    pbar.update(1)
                    total_processed += 1
                    continue

                current_batch.append(row)

                if len(current_batch) >= batch_size:
                    upserted = process_and_upsert_batch(current_batch, embedder, qdrant, error_logger)
                    total_upserted += upserted
                    total_processed += len(current_batch)
                    pbar.update(len(current_batch))
                    pbar.set_postfix(upserted=total_upserted, id=db_id)
                    current_batch = []

        # Final partial batch
        if current_batch:
            upserted = process_and_upsert_batch(current_batch, embedder, qdrant, error_logger)
            total_upserted += upserted
            total_processed += len(current_batch)
            pbar.update(len(current_batch))
        pbar.close()

    # 2. Standard Mode: Full Streaming via Named Cursor
    else:
        stream_cur = conn.cursor(name='migration_stream')
        stream_cur.itersize = 1000
        stream_cur.execute("""
            SELECT dc.id, dc.document_id, dc.title, dc.content, dc.chunk_type, dc.child_id, m.start_date,
                   d.name AS doc_name, d.url, d.doc_classification, d.municipality, dc.chunk_index
            FROM document_chunks dc
            LEFT JOIN documents d ON dc.document_id = d.id
            LEFT JOIN meetings m ON d.meeting_id = m.id
            WHERE dc.id > %s
            ORDER BY dc.id ASC
        """, (current_offset_id,))

        pbar = tqdm(total=effective_total, desc="Standard", unit="chunk")
        current_batch = []

        while True:
            row = stream_cur.fetchone()
            if not row or (limit and total_processed >= limit):
                break

            db_id, doc_id, title, content, c_type, child_id, start_date, doc_name, url, doc_classification, municipality, chunk_index = row
            if not content or len(content.strip()) < 2:
                pbar.update(1)
                total_processed += 1
                continue

            if len(content) > 50000:
                pbar.update(1)
                total_processed += 1
                continue

            current_batch.append(row)

            if len(current_batch) >= batch_size:
                upserted = process_and_upsert_batch(current_batch, embedder, qdrant, error_logger)
                total_upserted += upserted
                total_processed += len(current_batch)
                pbar.update(len(current_batch))
                pbar.set_postfix(upserted=total_upserted, id=db_id)
                current_batch = []

        if current_batch:
            upserted = process_and_upsert_batch(current_batch, embedder, qdrant, error_logger)
            total_upserted += upserted
            total_processed += len(current_batch)
            pbar.update(len(current_batch))

        pbar.close()
        stream_cur.close()

    print(f"Migration complete. Processed {total_processed} chunks, upserted {total_upserted} vectors.")
    conn.close()
    return "DONE"


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--recovery-mode", action="store_true", help="Only process IDs missing from Qdrant")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of chunks to process")
    args = parser.parse_args()

    migrate(recovery_mode=args.recovery_mode, limit=args.limit)
