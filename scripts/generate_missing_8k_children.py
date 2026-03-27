import psycopg2
from psycopg2.extras import execute_values
import sys
import os
import time

# DB Connection
DB_URL = 'postgresql://dennistak:@localhost/neodemos'

def get_db_connection():
    return psycopg2.connect(DB_URL)

def generate_missing_8k_children(doc_limit=None):
    conn = get_db_connection()
    cur = conn.cursor()

    # 1. Identify document_ids that have chunks but NO children
    print("Finding documents missing 8K semantic children...")
    query = """
    SELECT DISTINCT document_id 
    FROM document_chunks 
    WHERE document_id NOT IN (SELECT DISTINCT document_id FROM document_children)
    """
    cur.execute(query)
    doc_ids = [r[0] for r in cur.fetchall()]
    
    if doc_limit:
        doc_ids = doc_ids[:doc_limit]
        print(f"Processing {len(doc_ids)} documents (Limited).")
    else:
        print(f"Total documents missing 8K children: {len(doc_ids)}")

    total_children_created = 0
    total_chunks_linked = 0
    start_time = time.time()

    for i, doc_id in enumerate(doc_ids):
        # 2. Get full content for the document
        # Note: We need to find where the full text is stored. 
        # Usually it's in a 'documents' table or we can reconstruct it from the largest chunks.
        # However, looking at previous tools, storage_service.get_document_full_content(doc_id) was used.
        # Since I'm in a standalone script, I'll try to fetch from a 'documents' table if it exists.
        
        cur.execute("SELECT content FROM documents WHERE id = %s", (doc_id,))
        res = cur.fetchone()
        if not res or not res[0]:
            # Try to see if there's a different table name
            print(f"[{i+1}/{len(doc_ids)}] Doc {doc_id}: Full content not found in 'documents' table. Skipping.")
            continue
            
        full_text = res[0]
        
        # 3. Split into ~8K chunks (Child tier)
        # We'll use a simple character-based split for now, overlapping slightly
        child_size = 8000
        overlap = 500
        children_content = []
        for start in range(0, len(full_text), child_size - overlap):
            chunk = full_text[start:start + child_size]
            children_content.append(chunk)

        # 4. Insert into document_children
        child_ids = []
        for idx, content in enumerate(children_content):
            cur.execute(
                "INSERT INTO document_children (document_id, chunk_index, content) VALUES (%s, %s, %s) RETURNING id",
                (doc_id, idx, content)
            )
            child_ids.append((cur.fetchone()[0], content))
            total_children_created += 1

        # 5. Link 1K Grandchildren to these new 8K Children
        cur.execute("SELECT id, content FROM document_chunks WHERE document_id = %s", (doc_id,))
        grand_chunks = cur.fetchall()
        
        updates = []
        for gc_id, gc_content in grand_chunks:
            # Find the first child that contains this grandchild
            clean_gc = gc_content.strip()
            for c_id, c_content in child_ids:
                if clean_gc in c_content:
                    updates.append((c_id, gc_id))
                    break
        
        if updates:
            execute_values(cur,
                "UPDATE document_chunks AS c SET child_id = data.p_id FROM (VALUES %s) AS data(p_id, c_id) WHERE c.id = data.c_id",
                updates
            )
            total_chunks_linked += len(updates)

        if (i + 1) % 50 == 0:
            elapsed = time.time() - start_time
            print(f"[{i+1}/{len(doc_ids)}] Created {total_children_created} parents. Linked {total_chunks_linked} chunks. Elapsed: {elapsed:.1f}s")
            conn.commit()

    conn.commit()
    cur.close()
    conn.close()
    print(f"Process complete. Created {total_children_created} children. Linked {total_chunks_linked} grandchildren.")

if __name__ == "__main__":
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else None
    generate_missing_8k_children(limit)
