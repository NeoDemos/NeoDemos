import sys
import os
import hashlib
import json
import gc
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

try:
    import mlx.core as mx
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
QDRANT_PATH = "./data/qdrant_storage"
COLLECTION_NAME = "notulen_chunks"
CHECKPOINT_FILE = "data/pipeline_state/migration_checkpoint.json"

def get_system_ram_used():
    try:
        # On macOS, 'top' reports 'used' which includes 'Cached Files'.
        # We want to know how much is 'actually' tied up in apps.
        output = subprocess.check_output(['top', '-l', '1', '-s', '0', '-n', '0']).decode()
        for line in output.split('\n'):
            if 'PhysMem:' in line:
                # Example: PhysMem: 12G used (6G wired), 32G cached, 20G unused.
                # We want the 'used' but we should be aware that 'cached' is reclaimable.
                parts = line.split(',')
                used_part = parts[0].split('PhysMem:')[1].split('used')[0].strip()
                
                if 'G' in used_part: return float(used_part.replace('G', ''))
                if 'M' in used_part: return float(used_part.replace('M', '')) / 1024
    except: pass
    return 0

def get_process_rss_gb():
    try:
        pid = os.getpid()
        output = subprocess.check_output(['ps', '-o', 'rss=', '-p', str(pid)]).decode()
        return float(output.strip()) / (1024 * 1024)
    except: return 0

def migrate():
    # RAM Guard - Rule #2 Technical Operations
    # Note: 50GB threshold is safe because 'top' includes reclaimable cache in 'used'
    mem_used_gb = get_system_ram_used()
    if mem_used_gb > 50:
        print(f"❌ RAM Usage too high: {mem_used_gb:.1f}GB / 64GB. Cleanup processes before starting.")
        return

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
    cur = conn.cursor()
    
    # Manual Pagination Loop
    # Load checkpoint
    last_processed_id = 0
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, "r") as f:
                checkpoint = json.load(f)
                # Handle legacy checkpoints by taking latest ID or reset
                last_processed_id = checkpoint.get("last_processed_id", 0)
                if not last_processed_id and "last_index" in checkpoint:
                     print("Converting legacy index-based checkpoint to ID-based (assuming start from scratch for safety).")
                     last_processed_id = 0
        except:
            print("Could not load checkpoint, starting from scratch.")

    current_offset_id = last_processed_id
    batch_size = 100 
    print(f"Resuming from Database ID {current_offset_id} (True-Streaming Mode)...")
    total_processed = 0
    
    # Accurate count for the progress bar (restores user trust)
    count_cur = conn.cursor()
    count_cur.execute("SELECT COUNT(*) FROM document_chunks WHERE id > %s", (current_offset_id,))
    total_remaining = count_cur.fetchone()[0]
    count_cur.close()

    print(f"Total chunks remaining in Database: {total_remaining}")

    # Use a NAMED CURSOR for true server-side streaming (Rule #2: 0GB Buffering)
    # The name triggers psycopg2 into "stream" mode.
    stream_cur = conn.cursor(name='migration_stream')
    stream_cur.itersize = 500  # Only pull 500 rows at a time from Postgres
    
    stream_cur.execute("SELECT id, document_id, title, content, chunk_type, child_id FROM document_chunks WHERE id > %s ORDER BY id ASC", (current_offset_id,))

    with tqdm(total=total_remaining, desc="Processing Chunks", unit="chunk") as pbar:
        points = []
        # In named cursor mode, we iterate over the cursor directly.
        # No 'while True' loop needed for pagination; Postgres handles it.
        for row in stream_cur:
            db_id, doc_id, title, content, c_type, child_id = row
            current_offset_id = db_id
                
            # Skip empty content to avoid NaN
            if not content or len(content.strip()) < 2:
                pbar.update(1)
                continue

            # Batching Logic
            # Use a safe doc name
            doc_name = title if title else f"Doc_{doc_id}"
            embedding_text = f"[Document: {doc_name} | Section: {title}]\n" + content
            
            try:
                embedding = local_ai.generate_embedding(embedding_text)
                
                # NaN and None check
                if embedding is None or np.isnan(embedding).any():
                    pbar.update(1)
                    continue
            except Exception as e:
                pbar.update(1)
                continue
                
            hash_str = hashlib.md5(f"{doc_id}_{db_id}".encode()).hexdigest()
            point_id = int(hash_str[:15], 16)
            
            points.append(models.PointStruct(
                id=point_id,
                vector=embedding,
                payload={
                    "document_id": str(doc_id),
                    "title": title,
                    "content": content,
                    "chunk_type": c_type,
                    "child_id": child_id
                }
            ))
            
            if len(points) >= batch_size:
                qdrant.upsert(collection_name=COLLECTION_NAME, points=points)
                points = []
                # Save checkpoint
                with open(CHECKPOINT_FILE, "w") as f:
                    json.dump({"last_processed_id": db_id}, f)
            
            # Memory Guard: Clear MLX cache and Python GC every 20 chunks (Optimal for 8B model)
            if total_processed % 20 == 0:
                if MLX_AVAILABLE:
                    mx.clear_cache()
                gc.collect()
                
                # Proactive Cleanup if RAM exceeds 25GB for this process
                mem_gb = get_process_rss_gb()
                if mem_gb > 25:
                     print(f"⚠️  RSS Memory at {mem_gb:.2f} GB. Aggressive GC...")
                     gc.collect()
            
            pbar.update(1)
            total_processed += 1
            
        # Ensure final batch is sent
        if points:
            qdrant.upsert(collection_name=COLLECTION_NAME, points=points)
            with open(CHECKPOINT_FILE, "w") as f:
                json.dump({"last_processed_id": current_offset_id}, f)

    stream_cur.close()

    print(f"✅ Migration complete. Processed {total_processed} chunks.")
    conn.close()

if __name__ == "__main__":
    migrate()
