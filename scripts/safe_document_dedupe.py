import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def safe_dedupe(dry_run=True):
    db_name = os.getenv("DB_NAME", "neodemos")
    conn = psycopg2.connect(dbname=db_name, user=os.getenv("DB_USER"), host=os.getenv("DB_HOST"))
    cur = conn.cursor()
    
    print(f"--- DOCUMENT DEDUPLICATION (Step 3) (Dry-Run: {dry_run}) ---")
    
    # Fuzzy Deduplication: Merge documents with the same name attached to the same meeting.
    cur.execute("""
        SELECT name, meeting_id, array_agg(id), array_agg(COALESCE(length(content), 0))
        FROM documents
        WHERE meeting_id IS NOT NULL AND name IS NOT NULL AND name != ''
        GROUP BY name, meeting_id
        HAVING COUNT(*) > 1
    """)
    name_meeting_dups = cur.fetchall()
    print(f"Detected {len(name_meeting_dups)} Name+Meeting overlapping groups.")
    
    total_merges = 0
    for name, m_id, ids, lengths in name_meeting_dups:
        # Sort by length descending to pick the record with the most text as the "Master"
        pairs = sorted(zip(lengths, ids), reverse=True)
        master_id = pairs[0][1]
        dupe_ids = [p[1] for p in pairs[1:]]
        
        if not dry_run:
            for dupe_id in dupe_ids:
                # Merge logic: update references in document_assignments
                cur.execute("UPDATE document_assignments SET document_id = %s WHERE document_id = %s", (master_id, dupe_id))
                # Update references in document_chunks
                cur.execute("UPDATE document_chunks SET document_id = %s WHERE document_id = %s", (master_id, dupe_id))
                # Delete the ghost document
                cur.execute("DELETE FROM documents WHERE id = %s", (dupe_id,))
            total_merges += len(dupe_ids)
            
    if not dry_run:
        conn.commit()
        print(f"SUCCESS: Committed {total_merges} document merges across {len(name_meeting_dups)} groups.")
    else:
        print("Dry-run complete. No changes made.")

    cur.close()
    conn.close()

if __name__ == "__main__":
    # USER APPROVED: Running with dry_run=False
    safe_dedupe(dry_run=False)
