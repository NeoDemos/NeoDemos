import psycopg2
import json
import glob
from pathlib import Path

# Try to import Qdrant; if not available, we can rely on Postgres cascade
try:
    from qdrant_client import QdrantClient
    from qdrant_client.http.models import Filter, FieldCondition, MatchValue
    HAS_QDRANT = True
except ImportError:
    HAS_QDRANT = False

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"

def purge_anonymous_meetings():
    print("🧹 Starting FINAL Purge of Anonymous Transcripts (2018-2026)...")
    
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    
    # Identify poor transcripts/stubs to purge across ALL years
    # Criteria: 
    # 1. Category is 'video_transcript' OR ID starts with 'transcript_'
    # 2. Has NO chunks OR has chunks but NONE have speaker markers '['
    cur.execute("""
        SELECT d.id, m.id
        FROM documents d
        JOIN meetings m ON d.meeting_id = m.id
        WHERE (d.category = 'video_transcript' OR d.id ILIKE 'transcript_%')
          AND NOT EXISTS (
              SELECT 1 FROM document_chunks c 
              WHERE c.document_id = d.id AND c.content LIKE '[%'
          );
    """)
    rows = cur.fetchall()
    doc_ids = [row[0] for row in rows]
    meeting_ids_involved = list(set([row[1] for row in rows]))
    
    if not doc_ids:
        print("✅ No anonymous or empty transcripts found to purge. You are clean!")
        return

    print(f"🗑️ Found {len(doc_ids)} transcripts/stubs to purge across all years.")
    
    # 1. Purge from PostgreSQL
    print("⏳ Purging from PostgreSQL...")
    
    placeholders = ', '.join(['%s'] * len(doc_ids))
    
    # Delete chunks
    cur.execute(f"DELETE FROM document_chunks WHERE document_id IN ({placeholders})", doc_ids)
    print(f"  - Deleted {cur.rowcount} chunks.")
    
    # Delete children
    cur.execute(f"DELETE FROM document_children WHERE document_id IN ({placeholders})", doc_ids)
    print(f"  - Deleted {cur.rowcount} child sections.")
    
    # Delete documents
    cur.execute(f"DELETE FROM documents WHERE id IN ({placeholders})", doc_ids)
    print(f"  - Deleted {cur.rowcount} transcript documents.")
    
    # Delete meetings ONLY IF they have no other documents left
    meeting_placeholders = ', '.join(['%s'] * len(meeting_ids_involved))
    cur.execute(f"""
        DELETE FROM meetings 
        WHERE id IN ({meeting_placeholders}) 
        AND NOT EXISTS (
            SELECT 1 FROM documents WHERE meeting_id = meetings.id
        )
    """, meeting_ids_involved)
    print(f"  - Deleted {cur.rowcount} meetings (kept meetings that had moties/minutes attached).")
    
    # NOTE: url_source table does not exist in this schema, skipping.
    
    conn.commit()
    print("✅ PostgreSQL Purge Complete (Committed).")
    
    # 2. Purge from Qdrant
    if HAS_QDRANT:
        try:
            print("⏳ Attempting to purge from local Qdrant Vector Store...")
            client = QdrantClient(path="./data/qdrant_storage")
            for d_id in doc_ids:
                client.delete(
                    collection_name="notulen_chunks",
                    points_selector=Filter(
                        must=[FieldCondition(key="document_id", match=MatchValue(value=d_id))],
                    ),
                )
            print("✅ Qdrant Purge Complete.")
        except Exception as e:
            print(f"⚠️ Could not complete Qdrant purge: {e}")

    # 3. Reset pipeline_state_*.json
    print("⏳ Resetting states in pipeline_state_*.json files...")
    state_files = glob.glob("pipeline_state_*.json")
    for s_file in state_files:
        with open(s_file, "r") as f:
            state = json.load(f)
        updated = False
        for m_id in meeting_ids_involved:
            if m_id in state.get("meetings", {}):
                state["meetings"][m_id]["status"] = "pending"
                state["meetings"][m_id]["last_error"] = None
                updated = True
        if updated:
            with open(s_file, "w") as f:
                json.dump(state, f, indent=2)
            print(f"  - Updated {s_file}")

    print("✅ Pipeline states reset. System is ready for M5 Pro!")

if __name__ == "__main__":
    purge_anonymous_meetings()
