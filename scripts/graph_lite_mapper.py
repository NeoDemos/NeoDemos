import json
import psycopg2
import sys
import os

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
CHUNKS_FILE = "data/knowledge_graph/child_chunks_for_extraction.jsonl"
ENTITIES_FILE = "data/knowledge_graph/gliner_entities.jsonl"
RESOLUTION_MAP_FILE = "data/knowledge_graph/entity_resolution_map.json"

def get_db_connection():
    return psycopg2.connect(DB_URL)

def load_resolution_map():
    with open(RESOLUTION_MAP_FILE, 'r') as f:
        return json.load(f)

def run_mapper(limit=None):
    if limit:
        print(f"🚀 Launching Graph-Lite Mapper (Limit: {limit})...")
    else:
        print("🚀 Launching Graph-Lite Mapper (UNLIMITED)...")
    conn = get_db_connection()
    cur = conn.cursor()
    
    # Load the resolution map for normalization
    res_map = load_resolution_map()
    
    # First, build a quick text-to-guid cache for the relevant chunks
    # We trust the IDs in the file match the DB IDs for this 'Lite' run.
    text_to_guid = {}
    print("🧠 Building chunk cache (Fast Mapping)...")
    with open(CHUNKS_FILE, 'r') as f:
        for i, line in enumerate(f):
            data = json.loads(line)
            cid = str(data.get("chunk_id"))
            if cid:
                text_to_guid[cid] = int(cid)
            if i % 100000 == 0:
                print(f"  Cache progress: {i} chunks mapped...")

    print(f"✅ Cache built with {len(text_to_guid)} entries.")
    
    processed = 0
    mentions_inserted = 0
    
    print("📥 Ingesting entities...")
    with open(ENTITIES_FILE, 'r') as f:
        for line in f:
            data = json.loads(line)
            shadow_chunk_id = str(data.get("parent_id") or data.get("chunk_id"))
            local_guid = text_to_guid.get(shadow_chunk_id)
            
            if not local_guid:
                continue
                
            entities = data.get("entities", [])
            for ent in entities:
                raw_name = ent.get("text")
                ent_type = ent.get("label") or ent.get("type") or "Unknown"
                
                # Resolve the name
                clean_name = res_map.get(raw_name, raw_name)
                
                cur.execute("""
                    INSERT INTO kg_entities (name, type) 
                    VALUES (%s, %s) 
                    ON CONFLICT (name, type) DO UPDATE SET name = EXCLUDED.name
                    RETURNING id
                """, (clean_name, ent_type))
                entity_id = cur.fetchone()[0]
                
                cur.execute("""
                    INSERT INTO kg_mentions (entity_id, chunk_id, raw_mention)
                    VALUES (%s, %s, %s)
                """, (entity_id, local_guid, raw_name))
                mentions_inserted += 1
                
            processed += 1
            if processed % 1000 == 0:
                print(f"  Batch complete: {processed} chunks, {mentions_inserted} mentions total.")
                conn.commit()
                
            if limit and processed >= limit:
                break
                
    conn.commit()
    print(f"🏁 Finished. Processed {processed} chunks, created {mentions_inserted} entity mentions.")
    cur.close()
    conn.close()

if __name__ == "__main__":
    # Lower CPU priority
    os.nice(19)
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run_mapper(limit=limit)
