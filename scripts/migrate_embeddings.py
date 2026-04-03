import sys
import os
import hashlib
import json
import gc
import threading
import time
import subprocess
import logging
import psycopg2
import numpy as np
from tqdm import tqdm
from qdrant_client import QdrantClient
from qdrant_client.http import models

# Add project root to sys.path
sys.path.insert(0, os.getcwd())
sys.stdout.reconfigure(line_buffering=True)

from services.local_ai_service import LocalAIService
from scripts.audit_vector_gaps import compute_missing_ids

try:
    import mlx.core as mx
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
QDRANT_PATH = "./data/qdrant_storage"
COLLECTION_NAME = "notulen_chunks"
CHECKPOINT_FILE = "data/pipeline_state/migration_checkpoint.json"

# Initialize logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)  # suppress per-request noise

def get_system_ram_used():
    try:
        # On macOS, 'vm_stat' provides a more accurate picture of 'Memory Used'
        # (Active + Wired + Compressed) which excludes reclaimable Cached Files.
        output = subprocess.check_output(['vm_stat']).decode()
        stats = {}
        for line in output.split('\n'):
            if ':' in line:
                parts = line.split(':')
                key = parts[0].strip()
                val = parts[1].strip().replace('.', '')
                stats[key] = int(val)
        
        # macOS page size is 16384 bytes on ARM
        pagesize = 16384
        
        # Memory Used = (Pages active + Pages wired down + Pages occupied by compressor)
        active = stats.get('Pages active', 0)
        wired = stats.get('Pages wired down', 0)
        compressed = stats.get('Pages occupied by compressor', 0)
        
        used_gb = (active + wired + compressed) * pagesize / (1024**3)
        return used_gb
    except:
        return 0

def perform_cleanup():
    """Flush GPU pipeline and clear MLX caches. Prevents Metal memory fragmentation stalls."""
    if MLX_AVAILABLE:
        import mlx.core as mx
        mx.synchronize()  # drain the Metal command queue before clearing
        mx.clear_cache()
    gc.collect()

GPU_HANG_TIMEOUT = 120  # seconds — if one embedding takes longer, GPU is hung

def safe_generate_embedding(local_ai, text, timeout=GPU_HANG_TIMEOUT):
    """Run generate_embedding in a thread with a timeout to detect GPU hangs.
    Returns the embedding list, or raises TimeoutError if the GPU is stuck."""
    result = [None]
    error = [None]

    def _worker():
        try:
            result[0] = local_ai.generate_embedding(text)
        except Exception as e:
            error[0] = e

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=timeout)

    if t.is_alive():
        raise TimeoutError(f"GPU hung: embedding call exceeded {timeout}s")
    if error[0] is not None:
        raise error[0]
    return result[0]

def process_and_upsert_batch(batch, local_ai, qdrant, logger):
    """Process a batch of rows: embed one-by-one (no padding waste), upsert in bulk.
    Returns the number of points successfully upserted."""
    if not batch:
        return 0

    try:
        points = []
        skipped = 0
        for r in batch:
            r_db_id, r_doc_id, r_title, r_content, r_c_type, r_child_id, r_start_date = r

            # Single-item embedding with GPU hang detection
            try:
                embedding = safe_generate_embedding(local_ai, r_content[:50000])
            except TimeoutError as te:
                logger.error(f"GPU HANG detected at chunk {r_db_id}: {te}")
                raise  # bubble up to trigger process self-restart

            if embedding is None:
                skipped += 1
                continue

            if any(np.isnan(embedding)) or any(np.isinf(embedding)):
                logger.error(f"Skipping chunk {r_db_id}: Vector contains NaN or Inf.")
                skipped += 1
                continue

            if len(embedding) != 4096:
                logger.error(f"Skipping chunk {r_db_id}: Vector size {len(embedding)} != 4096")
                skipped += 1
                continue

            hash_str = hashlib.md5(f"{r_doc_id}_{r_db_id}".encode()).hexdigest()
            point_id = int(hash_str[:15], 16)

            points.append(models.PointStruct(
                id=point_id,
                vector=embedding,
                payload={
                    "document_id": str(r_doc_id),
                    "database_id": r_db_id,
                    "title": r_title,
                    "content": r_content,
                    "chunk_type": r_c_type,
                    "child_id": r_child_id,
                    "start_date": r_start_date.isoformat() if r_start_date else None
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
                        logger.error(f"Failed to upsert point {point.id} (DocID: {point.payload['document_id']}): {e_point}")
                        skipped += 1

            # Checkpoint after batch completes
            last_db_id = batch[-1][0]
            with open(CHECKPOINT_FILE, "w") as f:
                json.dump({"last_processed_id": last_db_id}, f)

        return len(points) - skipped

    except TimeoutError:
        raise  # GPU hang — must bubble up for process restart
    except Exception as e:
        logger.error(f"Batch processing failed for range {batch[0][0]}-{batch[-1][0]}: {e}")
        return 0

def migrate(recovery_mode=False, limit=None):
    # RAM Guard - Rule #2 Technical Operations
    # With vm_stat, 50GB is a firm threshold for a 64GB machine.
    mem_used_gb = get_system_ram_used()
    if mem_used_gb > 40: # Rule 2: 40GB limit on 64GB Mac
        print(f"❌ RAM Usage too high: {mem_used_gb:.1f}GB / 64GB. Cleanup processes before starting.")
        return

    # Setup Logging (Rule #2)
    os.makedirs("logs", exist_ok=True)
    error_logger = logging.getLogger("migration_errors")
    error_logger.setLevel(logging.ERROR)
    # Use mode='a' to ensure old logs are preserved
    handler = logging.FileHandler("logs/migration_errors.log", mode='a')
    handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    error_logger.addHandler(handler)

    print(f"Starting migration to local embedding collection: {COLLECTION_NAME}")

    local_ai = LocalAIService(skip_llm=True)
    if not local_ai.is_available():
        print("❌ Local AI (Embedding) service not available.")
        return

    # Connection to STANDALONE Qdrant Server (Solves the 93GB process bloat)
    qdrant = QdrantClient(url="http://localhost:6333")
    
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

    # Load checkpoint
    last_processed_id = 0
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, "r") as f:
                checkpoint = json.load(f)
                last_processed_id = checkpoint.get("last_processed_id", 0)
                if not last_processed_id and "last_index" in checkpoint:
                    print("Converting legacy index-based checkpoint to ID-based (assuming start from scratch for safety).")
                    last_processed_id = 0
        except:
            print("Could not load checkpoint, starting from scratch.")

    current_offset_id = last_processed_id
    batch_size = 16
    total_processed = 0
    total_upserted = 0

    # Recovery mode: run the audit live using already-open connections
    recovery_ids = None
    if recovery_mode:
        recovery_ids = compute_missing_ids(qdrant_client=qdrant, pg_conn=conn)
        if not recovery_ids:
            print("Audit found no missing chunks. Nothing to do.")
            conn.close()
            return

    # In recovery mode the live audit is the source of truth — it only returns IDs
    # genuinely missing from Qdrant right now, so no checkpoint filter is needed.
    # The checkpoint is still used in standard mode (WHERE dc.id > offset).

    if recovery_ids:
        total_remaining = len(recovery_ids)
    else:
        count_cur = conn.cursor()
        count_cur.execute("SELECT COUNT(*) FROM document_chunks WHERE id > %s", (current_offset_id,))
        total_remaining = count_cur.fetchone()[0]
        count_cur.close()

    effective_total = min(limit, total_remaining) if limit else total_remaining
    print(f"Resuming from Database ID {current_offset_id} | Remaining: {effective_total} | Batch Size: {batch_size}")
    print(f"RAM Guard Active: Skip-LLM Engine + 40GB Threshold")

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
                SELECT dc.id, dc.document_id, dc.title, dc.content, dc.chunk_type, dc.child_id, m.start_date
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

                db_id, doc_id, title, content, c_type, child_id, start_date = row
                if not content or len(content.strip()) < 2:
                    pbar.update(1)
                    total_processed += 1
                    continue

                # Skip massive parent documents (> 50,000 chars) — already represented by children
                if len(content) > 50000:
                    pbar.update(1)
                    total_processed += 1
                    continue

                current_batch.append(row)

                if len(current_batch) >= batch_size:
                    try:
                        upserted = process_and_upsert_batch(current_batch, local_ai, qdrant, error_logger)
                    except TimeoutError:
                        pbar.close()
                        conn.close()
                        print(f"\n⚡ GPU hang detected. Auto-restarting from checkpoint...")
                        return "GPU_HANG"
                    total_upserted += upserted
                    total_processed += len(current_batch)
                    pbar.update(len(current_batch))
                    pbar.set_postfix(upserted=total_upserted, id=db_id)

                    if total_processed % 64 == 0:
                        perform_cleanup()

                    current_batch = []

        # Final partial batch
        if current_batch:
            try:
                upserted = process_and_upsert_batch(current_batch, local_ai, qdrant, error_logger)
            except TimeoutError:
                pbar.close()
                conn.close()
                print(f"\n⚡ GPU hang detected. Auto-restarting from checkpoint...")
                return "GPU_HANG"
            total_upserted += upserted
            total_processed += len(current_batch)
            pbar.update(len(current_batch))
            pbar.set_postfix(upserted=total_upserted, id=db_id)
        pbar.close()

    # 2. Standard Mode: Full Streaming via Named Cursor
    else:
        stream_cur = conn.cursor(name='migration_stream')
        stream_cur.itersize = 1000
        stream_cur.execute("""
            SELECT dc.id, dc.document_id, dc.title, dc.content, dc.chunk_type, dc.child_id, m.start_date
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

            db_id, doc_id, title, content, c_type, child_id, start_date = row
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
                try:
                    upserted = process_and_upsert_batch(current_batch, local_ai, qdrant, error_logger)
                except TimeoutError:
                    pbar.close()
                    stream_cur.close()
                    conn.close()
                    print(f"\n⚡ GPU hang detected. Auto-restarting from checkpoint...")
                    return "GPU_HANG"
                total_upserted += upserted
                total_processed += len(current_batch)
                pbar.update(len(current_batch))
                pbar.set_postfix(upserted=total_upserted, id=db_id)

                if total_processed % 256 == 0:
                    perform_cleanup()

                current_batch = []

        if current_batch:
            try:
                upserted = process_and_upsert_batch(current_batch, local_ai, qdrant, error_logger)
            except TimeoutError:
                pbar.close()
                stream_cur.close()
                conn.close()
                print(f"\n⚡ GPU hang detected. Auto-restarting from checkpoint...")
                return "GPU_HANG"
            total_upserted += upserted
            total_processed += len(current_batch)
            pbar.update(len(current_batch))
            pbar.set_postfix(upserted=total_upserted, id=db_id)

        pbar.close()
        stream_cur.close()

    print(f"Migration complete. Processed {total_processed} chunks, upserted {total_upserted} vectors.")
    conn.close()
    return "DONE"

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--recovery-mode", action="store_true", help="Only process IDs in missing_ids_gap_audit.json")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of chunks to process")
    args = parser.parse_args()

    # Auto-restart loop: when GPU hangs, the process re-execs itself
    # to get a completely fresh Metal/GPU context.
    result = migrate(recovery_mode=args.recovery_mode, limit=args.limit)
    if result == "GPU_HANG":
        print("♻️  Re-execing process for fresh GPU context...")
        time.sleep(3)  # brief cooldown for GPU
        os.execv(sys.executable, [sys.executable] + sys.argv)
