import os
import sys
import json
import logging
import requests
import psycopg2
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
MAPPING_FILE = "ibabs_uuid_mapping.json"
API_URL = "https://rotterdamraad.bestuurlijkeinformatie.nl/Calendar/GetAgendasForCalendar"

def fetch_ibabs_meetings(year: int):
    all_meetings = []
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"}
    for month in range(1, 13):
        start_date = f"{year}-{month:02d}-01T00:00:00Z"
        if month == 12:
            end_date = f"{year}-12-31T23:59:59Z"
        else:
            end_date = f"{year}-{month+1:02d}-01T00:00:00Z"
        
        try:
            r = requests.get(API_URL, params={'start': start_date, 'end': end_date}, headers=headers, timeout=15)
            r.raise_for_status()
            data = r.json()
            for m in data:
                date_str = m.get('start', '').split('T')[0]
                if date_str:
                    d = datetime.strptime(date_str, "%Y-%m-%d")
                    all_meetings.append({
                        "uuid": m.get("id"),
                        "name": m.get("title", ""),
                        "date": d
                    })
        except Exception as e:
            logger.error(f"Error fetching meetings for {year}-{month:02d}: {e}")
            
    return all_meetings

def get_db_meetings(year: int):
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
        
        ibabs_lookup = {}
        for m in ibabs_meetings:
            d_str = m["date"].strftime("%Y-%m-%d")
            n = m["name"].lower()[:15]
            key = f"{d_str}_{n}"
            if d_str not in ibabs_lookup: ibabs_lookup[d_str] = []
            ibabs_lookup[d_str].append(m)
            ibabs_lookup[key] = m
            
        mapped_in_year = 0
            
        for db_id, name, start_date in db_meetings:
            db_id_str = str(db_id)
            if "-" in db_id_str and len(db_id_str) >= 36:
                continue
                
            if db_id_str in mapping: continue
            if not start_date: continue
            
            d_str = start_date.strftime("%Y-%m-%d")
            n = (name or "").lower()[:15]
            key = f"{d_str}_{n}"
            
            target_uuid = None
            if key in ibabs_lookup:
                target_uuid = ibabs_lookup[key]["uuid"]
            elif d_str in ibabs_lookup:
                candidates = ibabs_lookup[d_str]
                if len(candidates) == 1:
                    target_uuid = candidates[0]["uuid"]
                else:
                    for c in candidates:
                        if c["name"].lower() == (name or "").lower():
                            target_uuid = c["uuid"]
                            break
            
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
