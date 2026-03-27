import psycopg2
from psycopg2.extras import execute_values
import sys
import os
import time
import re

# DB Connection
DB_URL = 'postgresql://dennistak:@localhost/neodemos'

def norm(s):
    if not s: return ""
    return re.sub(r'\s+', '', s).lower()

def repair_links(doc_limit=None):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    # 1. Get unique document_ids with NULL child_id in chunks
    print("Fetching unique document IDs with missing links...")
    cur.execute("SELECT DISTINCT document_id FROM document_chunks WHERE child_id IS NULL")
    doc_ids = [r[0] for r in cur.fetchall()]
    
    if doc_limit:
        doc_ids = doc_ids[:doc_limit]
        print(f"Testing with {len(doc_ids)} document(s).")
    else:
        print(f"Found {len(doc_ids)} documents with unlinked chunks.")

    total_updated = 0
    start_time = time.time()

    for i, doc_id in enumerate(doc_ids):
        # 2. Get all 8K Child Parents for this doc
        cur.execute("SELECT id, content FROM document_children WHERE document_id = %s", (doc_id,))
        parents = cur.fetchall() # List of (id, content)
        
        if not parents:
            if (i + 1) % 100 == 0:
                print(f"[{i+1}/{len(doc_ids)}] Doc {doc_id}: No parents found. Skipping.")
            continue

        # Pre-normalize parents for this document
        norm_parents = [(p_id, norm(p_content), p_content) for p_id, p_content in parents]

        # 3. Get all 1K Grandchild Chunks for this doc with NULL child_id
        cur.execute("SELECT id, content FROM document_chunks WHERE document_id = %s AND child_id IS NULL", (doc_id,))
        chunks = cur.fetchall() # List of (id, content)
        
        updates = []
        for chunk_id, chunk_content in chunks:
            match_found = False
            clean_chunk = chunk_content.strip()
            
            # Tier 1: Exact match (Fastest)
            for p_id, np_content, p_content in norm_parents:
                if clean_chunk in p_content:
                    updates.append((p_id, chunk_id))
                    match_found = True
                    break
            
            # Tier 2: Normalized match (Robust)
            if not match_found:
                norm_chunk = norm(clean_chunk)
                if not norm_chunk: continue
                
                for p_id, np_content, p_content in norm_parents:
                    if norm_chunk in np_content:
                        updates.append((p_id, chunk_id))
                        match_found = True
                        break
            
            # Tier 3: Snippet Normalized (Deep fallback)
            if not match_found and len(clean_chunk) > 100:
                mid = len(clean_chunk) // 2
                snippet = norm(clean_chunk[mid-50:mid+50])
                if snippet:
                    for p_id, np_content, p_content in norm_parents:
                        if snippet in np_content:
                            updates.append((p_id, chunk_id))
                            match_found = True
                            break

        # 4. Bulk Update
        if updates:
            execute_values(cur, 
                "UPDATE document_chunks AS c SET child_id = data.p_id FROM (VALUES %s) AS data(p_id, c_id) WHERE c.id = data.c_id",
                updates
            )
            total_updated += len(updates)
            
        if (i + 1) % 50 == 0 or doc_limit:
            elapsed = time.time() - start_time
            print(f"[{i+1}/{len(doc_ids)}] Processed {i+1} docs. Total updated: {total_updated}. Elapsed: {elapsed:.1f}s")
            conn.commit()

    conn.commit()
    cur.close()
    conn.close()
    print(f"Repair complete. Total chunks linked: {total_updated}")

if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    repair_links(limit)
