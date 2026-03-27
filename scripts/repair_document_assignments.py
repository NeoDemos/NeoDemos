import asyncio
import os
import sys
import logging
import psycopg2
from datetime import datetime

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.ibabs_service import IBabsService

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"

async def repair_assignments():
    ibabs = IBabsService()
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    
    # 1. Get all unique meetings (by name/date) from 2024 onwards
    # We use GUIDs for the portal where possible
    cur.execute("""
        SELECT DISTINCT ON (name, start_date::date) id, name, start_date 
        FROM meetings 
        WHERE start_date >= '2024-01-01' 
        ORDER BY start_date::date DESC, name, (CASE WHEN id ~ '^[a-f0-9-]{36}$' THEN 0 ELSE 1 END)
    """)
    meetings = cur.fetchall()
    print(f"Repairing assignments for {len(meetings)} unique meetings...")
    
    total_repaired = 0
    
    for m_id, m_name, m_date in meetings:
        print(f"\nProcessing {m_name} ({m_date}) [Portal ID: {m_id}]")
        
        # Find ALL meeting IDs in DB representing this meeting (matching by date, not exact time)
        cur.execute("SELECT id FROM meetings WHERE name = %s AND start_date::date = %s::date", (m_name, m_date))
        all_db_ids = [r[0] for r in cur.fetchall()]
        print(f"   DB IDs to link: {all_db_ids}")

        try:
            agenda_data = await ibabs.get_meeting_agenda(m_id, resolve_references=True)
            
            for item in agenda_data.get("agenda", []):
                item_name = item.get("name")
                item_number = item.get("number")
                
                # Find corresponding agenda item IDs in DB for ALL meeting records
                cur.execute("""
                    SELECT id, meeting_id 
                    FROM agenda_items 
                    WHERE meeting_id = ANY(%s) AND (name = %s OR number = %s)
                """, (all_db_ids, item_name, str(item_number)))
                db_item_mapping = {(r[1], r[0]) for r in cur.fetchall()} # (meeting_id, item_id)
                
                for doc in item.get("documents", []):
                    doc_id = doc.get("id")
                    if not doc_id: continue
                    
                    # Ensure document record exists with proper name/url
                    cur.execute("""
                        INSERT INTO documents (id, name, url, category)
                        VALUES (%s, %s, %s, 'municipal_doc')
                        ON CONFLICT (id) DO UPDATE SET
                            name = EXCLUDED.name,
                            url = COALESCE(documents.url, EXCLUDED.url)
                        WHERE documents.name LIKE '%' || documents.id || '%' AND length(documents.name) < length(EXCLUDED.name)
                           OR documents.url IS NULL
                    """, (doc_id, doc.get('name', 'Document'), doc.get('url')))

                    # Link to every relevant meeting and item
                    for db_m_id in all_db_ids:
                        # Find the specific item ID for this meeting record
                        target_item_id = next((it_id for m_id_ref, it_id in db_item_mapping if m_id_ref == db_m_id), None)
                        
                        cur.execute("""
                            INSERT INTO document_assignments (document_id, meeting_id, agenda_item_id)
                            VALUES (%s, %s, %s)
                            ON CONFLICT (document_id, meeting_id, agenda_item_id) DO NOTHING
                        """, (doc_id, db_m_id, target_item_id))
                        
                        if cur.rowcount > 0:
                            print(f"   + Linked {doc_id} to {db_m_id} (Item: {target_item_id})")
                            total_repaired += 1
            
            conn.commit()
        except Exception as e:
            print(f"   Error processing {m_id}: {e}")
            conn.rollback()
            
    print(f"\nDONE! Created {total_repaired} new assignments.")
    cur.close()
    conn.close()

if __name__ == "__main__":
    asyncio.run(repair_assignments())
