import asyncio
import os
import sys
import logging
import psycopg2
from datetime import datetime

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.ibabs_service import IBabsService

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"

async def heal_meetings():
    ibabs = IBabsService()
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    
    for year in [2024, 2025, 2026]:
        log(f"\n--- Healing Meetings for {year} ---")
        scraped_meetings = await ibabs.get_meetings_for_year(year)
        log(f"Scraped {len(scraped_meetings)} meetings from iBabs.")
        
        for sm in scraped_meetings:
            guid_id = sm['id']
            m_date = sm.get('start_date')
            m_name = sm['name']
            
            if not m_date:
                continue
                
            day_only = m_date.split('T')[0]
            
            # Find candidate in DB with numeric ID but same date and similar name
            cur.execute("""
                SELECT id, name FROM meetings 
                WHERE (start_date::date = %s OR start_date::date = (%s::date + interval '1 day') OR start_date::date = (%s::date - interval '1 day'))
                  AND id !~ '^[0-9a-f]{8}-'
                  AND id != %s
            """, (day_only, day_only, day_only, guid_id))
            
            candidates = cur.fetchall()
            for old_id, old_name in candidates:
                # Fuzzy match name or just trust the date if it's unique
                log(f"  ? Found Numeric Candidate: {old_id} ('{old_name}')")
                log(f"    Match with GUID: {guid_id} ('{m_name}') on {day_only}")
                
                # HEAL: Reparent and replace
                try:
                    # 1. Insert/Update the GUID meeting record
                    cur.execute("""
                        INSERT INTO meetings (id, name, start_date, committee, last_updated)
                        SELECT %s, name, start_date, committee, CURRENT_TIMESTAMP
                        FROM meetings WHERE id = %s
                        ON CONFLICT (id) DO UPDATE SET last_updated = CURRENT_TIMESTAMP
                    """, (guid_id, old_id))
                    
                    # 2. Update Agenda Items
                    cur.execute("UPDATE agenda_items SET meeting_id = %s WHERE meeting_id = %s", (guid_id, old_id))
                    
                    # 3. Update Documents
                    cur.execute("UPDATE documents SET meeting_id = %s WHERE meeting_id = %s", (guid_id, old_id))
                    
                    # 4. Delete old meeting
                    cur.execute("DELETE FROM meetings WHERE id = %s", (old_id,))
                    
                    conn.commit()
                    log(f"    ✓ HEALED: {old_id} -> {guid_id}")
                except Exception as e:
                    conn.rollback()
                    log(f"    ✗ FAILED to heal {old_id}: {e}")
                    
    cur.close()
    conn.close()

def log(msg: str):
    print(msg, flush=True)

if __name__ == "__main__":
    asyncio.run(heal_meetings())
