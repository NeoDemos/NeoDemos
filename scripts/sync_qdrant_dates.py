import os
import sys
import psycopg2
import json
import logging
import re
import gc
import subprocess
import time
from tqdm import tqdm
from qdrant_client import QdrantClient
from qdrant_client.http import models
from dotenv import load_dotenv

load_dotenv()

DB_URL = os.getenv("DB_URL") or "postgresql://postgres:postgres@localhost:5432/neodemos"
COLLECTION_NAME = "notulen_chunks"
CHECKPOINT_PATH = "data/pipeline_state/date_sync_checkpoint.json"
REVIEW_LOG_PATH = "logs/date_sync_review.json"

# RAM GUARD CONFIG (Rule #2 Technical Operations)
RAM_THRESHOLD_GB = 61.0 # High-Pressure Threshold for 64GB M5 Pro

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

def is_garbage(text):
    if not text: return True
    control_chars = len(re.findall(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', text))
    return (control_chars / len(text)) > 0.1 if len(text) > 20 else False

def sync_v3(limit=None, batch_size=500):
    print(f"--- GLOBAL DATE SYNC V3: HIGH-PERFORMANCE STREAMING ---")
    
    qdrant = QdrantClient(url="http://localhost:6333")
    conn = psycopg2.connect(DB_URL)
    
    # Load Checkpoint
    offset = None
    if os.path.exists(CHECKPOINT_PATH):
        try:
            with open(CHECKPOINT_PATH, "r") as f:
                checkpoint = json.load(f)
                offset = checkpoint.get("next_page_offset")
                print(f"Resuming from offset: {offset}")
        except: pass

    success_count = 0
    mismatch_count = 0
    processed_total = 0
    
    # NAMED CURSOR for Zero-Buffering (Lesson from Embedding Script)
    doc_cur = conn.cursor(name='sync_v3_server_cursor')
    doc_cur.itersize = 1000 
    
    # Pre-fetch all document metadata into a server-side stream
    doc_cur.execute("SELECT d.id::text, m.start_date, d.content, d.name FROM documents d JOIN meetings m ON d.meeting_id = m.id")
    
    # We iterate over Qdrant Scroll first to identify precisely what chunks need dates
    pbar = tqdm(total=limit or 1300000, desc="Syncing Chunks")
    
    while True:
        # RAM GUARD CHECK (Rule #2)
        current_ram = get_system_ram_used()
        if current_ram > RAM_THRESHOLD_GB:
             print(f"\n⚠️  RAM GUARD: {current_ram:.1f}GB used. Pausing for cleanup...")
             gc.collect()
             time.sleep(10)
             continue

        res, next_offset = qdrant.scroll(
            collection_name=COLLECTION_NAME,
            limit=batch_size,
            with_payload=True,
            offset=offset,
            with_vectors=False # Direct SSD Scroll
        )
        
        if not res: break
        
        # Build local batch for this scroll segment
        # In V3, we use a single query per batch to avoid loop latency
        doc_ids = [p.payload.get("document_id") for p in res if p.payload.get("document_id")]
        
        if doc_ids:
            # Quick batch fetch for this specific set of document IDs
            batch_cur = conn.cursor()
            batch_cur.execute("SELECT d.id::text, m.start_date, d.content, d.name FROM documents d JOIN meetings m ON d.meeting_id = m.id WHERE d.id::text IN %s", (tuple(doc_ids),))
            doc_map = {row[0]: {"date": row[1], "text": row[2], "name": row[3]} for row in batch_cur.fetchall()}
            batch_cur.close()

            for p in res:
                doc_id = p.payload.get("document_id")
                q_text = p.payload.get("content", "")
                
                if doc_id in doc_map:
                    doc = doc_map[doc_id]
                    if doc["date"] and not is_garbage(doc["text"]):
                        # SMART CONTAINMENT CHECK
                        match_snippet = q_text[:70].strip()
                        if match_snippet.lower() in (doc["text"] or "").lower():
                            qdrant.set_payload(COLLECTION_NAME, {"start_date": doc["date"].isoformat()}, [p.id], wait=False)
                            success_count += 1
                        else:
                            mismatch_count += 1
                
                pbar.update(1)
                processed_total += 1
                if limit and processed_total >= limit: break

        # BATCH CLEANUP (Lesson from Embedding Script)
        gc.collect()
        
        offset = next_offset
        with open(CHECKPOINT_PATH, "w") as f:
            json.dump({"next_page_offset": offset, "success_count": success_count}, f)
            
        if limit and processed_total >= limit: break
        if offset is None: break

    doc_cur.close()
    conn.close()
    pbar.close()
    print(f"✅ V3 BATCH DONE. Success: {success_count} | Mismatches: {mismatch_count}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    sync_v3(limit=args.limit)
