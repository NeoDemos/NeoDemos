import os
import re
import sys
import json
import logging
import asyncio
import psycopg2
from bs4 import BeautifulSoup

# We can reuse the scraping logic from IBabsService or just write a small sync one here
import requests
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
MAPPING_FILE = "ibabs_uuid_mapping.json"

def fetch_ibabs_meetings(year: int):
    """Fetch meetings for a year from iBabs RetrieveAgendasForYear endpoint."""
    url = "https://rotterdamraad.bestuurlijkeinformatie.nl/Agenda/RetrieveAgendasForYear"
    params = {
        "agendatypeId": "100002367",
        "year": str(year)
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "X-Requested-With": "XMLHttpRequest"
    }
    
    meetings = []
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        items = soup.find_all('li', class_='agenda-link')
        
        months = {
            'januari': 1, 'februari': 2, 'maart': 3, 'april': 4, 'mei': 5, 'juni': 6,
            'juli': 7, 'augustus': 8, 'september': 9, 'oktober': 10, 'november': 11, 'december': 12
        }
        
        for item in items:
            link = item.find('a', href=re.compile(r'/Agenda/Index/'))
            if not link: continue
            
            href = link['href']
            meeting_uuid = href.split('/')[-1]
            
            title_elem = link.find('div', class_='agenda-link-title')
            subtitle_elem = link.find('div', class_='agenda-link-subtitle')
            
            name = title_elem.get_text(separator=' ', strip=True) if title_elem else ""
            subtitle = subtitle_elem.get_text(separator=' ', strip=True) if subtitle_elem else ""
            
            # Extract date
            date_str = subtitle.lower() if subtitle else name.lower()
            start_date = None
            match = re.search(r'(\d+)\s+([a-z]+)\s+(\d{4})(?:,\s+(\d{2}):(\d{2}))?', date_str)
            if match:
                day, month_name, yr, hour, minute = match.groups()
                month = months.get(month_name, 1)
                h = int(hour) if hour else 0
                m = int(minute) if minute else 0
                start_date = datetime(int(yr), month, int(day), h, m)
            
            if start_date:
                meetings.append({
                    "uuid": meeting_uuid,
                    "name": name,
                    "date": start_date
                })
                
        return meetings
            
    except Exception as e:
        logger.error(f"Error fetching iBabs meetings for {year}: {e}")
        return []

def get_db_meetings(year: int):
    """Fetch meetings from the local DB for a given year."""
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, start_date 
                FROM meetings 
                WHERE EXTRACT(YEAR FROM start_date) = %s
            """, (year,))
            return cur.fetchall()
    finally:
        conn.close()

def main():
    years = [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026]
    
    mapping = {}
    if os.path.exists(MAPPING_FILE):
        with open(MAPPING_FILE, "r") as f:
            mapping = json.load(f)
            
    total_new_mapped = 0
            
    for year in years:
        logger.info(f"Processing year {year}...")
        ibabs_meetings = fetch_ibabs_meetings(year)
        db_meetings = get_db_meetings(year)
        
        logger.info(f"  Found {len(ibabs_meetings)} meetings in iBabs.")
        logger.info(f"  Found {len(db_meetings)} meetings in DB.")
        
        # Create a lookup dictionary by date string (YYYY-MM-DD)
        ibabs_lookup = {}
        for m in ibabs_meetings:
            d_str = m["date"].strftime("%Y-%m-%d")
            n = m["name"].lower()[:15] # first 15 chars for fuzziness
            key = f"{d_str}_{n}"
            # Also just date as fallback
            if d_str not in ibabs_lookup: ibabs_lookup[d_str] = []
            ibabs_lookup[d_str].append(m)
            
            ibabs_lookup[key] = m
            
        mapped_in_year = 0
            
        for db_id, name, start_date in db_meetings:
            db_id_str = str(db_id)
            if "-" in db_id_str and len(db_id_str) >= 36:
                # It's already a UUID, add self mapping
                continue
                
            if db_id_str in mapping: continue
            
            if not start_date: continue
            
            d_str = start_date.strftime("%Y-%m-%d")
            n = (name or "").lower()[:15]
            key = f"{d_str}_{n}"
            
            target_uuid = None
            
            # Try specific match
            if key in ibabs_lookup:
                target_uuid = ibabs_lookup[key]["uuid"]
            elif d_str in ibabs_lookup:
                # Try fallback, find best match in the day
                candidates = ibabs_lookup[d_str]
                if len(candidates) == 1:
                    target_uuid = candidates[0]["uuid"]
                else:
                    # Could happen if multiple committees on same day
                    # Check if there is an exact name match
                    for c in candidates:
                        if c["name"].lower() == (name or "").lower():
                            target_uuid = c["uuid"]
                            break
                    if not target_uuid:
                        logger.warning(f"  Ambiguous match for DB ID {db_id_str} ({d_str} {name}). Options: {[c['name'] for c in candidates]}")
            
            if target_uuid:
                mapping[db_id_str] = target_uuid
                mapped_in_year += 1
                total_new_mapped += 1
                
        logger.info(f"  Newly mapped for {year}: {mapped_in_year}")

    with open(MAPPING_FILE, "w") as f:
        json.dump(mapping, f, indent=2)
        
    logger.info(f"Done! Total mappings saved: {len(mapping)} (New: {total_new_mapped})")

if __name__ == "__main__":
    main()
