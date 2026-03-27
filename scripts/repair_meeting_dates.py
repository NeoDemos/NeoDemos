import json
import asyncio
import httpx
import psycopg2
from datetime import datetime, timedelta, timezone
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("date_repair_ori")

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
ORI_BASE_URL = "https://api.openraadsinformatie.nl/v1/elastic"

async def fetch_ori_meeting(meeting_id):
    """Fetch meeting metadata from ORI API."""
    query = {
        "query": {
            "bool": {
                "must": [
                    { "term": { "_id": str(meeting_id) } }
                ]
            }
        }
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{ORI_BASE_URL}/_search", json=query)
            if resp.status_code == 200:
                hits = resp.json().get("hits", {}).get("hits", [])
                if hits:
                    return hits[0]["_source"]
    except Exception as e:
        logger.error(f"Error fetching {meeting_id} from ORI: {e}")
    return None

def convert_utc_to_local(utc_str):
    """
    Convert ORI UTC string (e.g. 2026-02-03T18:00:00+00:00) to local datetime.
    Since pytz is missing, we use standard timezone handling.
    """
    if not utc_str: return None
    # Parse ISO format (handles +00:00)
    dt_utc = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
    
    # Rotterdam is CET (UTC+1) or CEST (UTC+2)
    # Simple heuristic for now: March is usually CET (+1) until last Sunday.
    # For a high-quality production fix, we should use zoneinfo if available.
    try:
        from zoneinfo import ZoneInfo
        return dt_utc.astimezone(ZoneInfo("Europe/Amsterdam"))
    except (ImportError, Exception):
        # Fallback to manual +1 for winter (standard time)
        return dt_utc + timedelta(hours=1)

async def repair_dates(dry_run=True, year=2026):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    
    # Fetch meetings for the specific year
    logger.info(f"Auditing meetings for year {year}...")
    cur.execute("""
        SELECT id, name, start_date 
        FROM meetings 
        WHERE EXTRACT(YEAR FROM start_date) = %s
    """, (year,))
    
    meetings = cur.fetchall()
    logger.info(f"Found {len(meetings)} meetings to audit.")
    
    mismatch_count = 0
    updated_count = 0
    
    for mid, name, db_start in meetings:
        ori_meta = await fetch_ori_meeting(mid)
        if not ori_meta:
            continue
            
        ori_start_utc = ori_meta.get("start_date")
        if not ori_start_utc:
            continue
            
        local_start = convert_utc_to_local(ori_start_utc)
        
        # Compare DB date (naive, assumed UTC) with local_start
        # We compare as naive for the check
        if db_start.replace(tzinfo=None) != local_start.replace(tzinfo=None):
            mismatch_count += 1
            logger.info(f"MISMATCH [{mid}]: {name}")
            logger.info(f"  DB:  {db_start}")
            logger.info(f"  ORI: {ori_start_utc} -> Local: {local_start}")
            
            if not dry_run:
                cur.execute("UPDATE meetings SET start_date = %s WHERE id = %s", (local_start, mid))
                updated_count += 1
        
        # Avoid hammering the API too hard
        await asyncio.sleep(0.05)

    if not dry_run:
        conn.commit()
        logger.info(f"Repair complete: Updated {updated_count} meetings.")
    else:
        logger.info(f"Dry run complete: Found {mismatch_count} mismatches to repair.")
    
    conn.close()

if __name__ == "__main__":
    import sys
    dry = "--apply" not in sys.argv
    asyncio.run(repair_dates(dry_run=dry))
