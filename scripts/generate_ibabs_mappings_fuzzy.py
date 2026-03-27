import os
import sys
import json
import logging
import requests
import psycopg2
from datetime import datetime
from difflib import SequenceMatcher

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
                SELECT id, name, committee, start_date 
                FROM meetings 
                WHERE EXTRACT(YEAR FROM start_date) = %s
            """, (year,))
            return cur.fetchall()
    finally:
        conn.close()

def clean_title(title):
    t = (title or "").lower()
    t = t.replace(' - ', ' ').replace('—', ' ').split(' (')[0].strip()
    return t

def similar(a, b):
    return SequenceMatcher(None, clean_title(a), clean_title(b)).ratio()

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
        
        ibabs_by_day = {}
        for m in ibabs_meetings:
            d_str = m["date"].strftime("%Y-%m-%d")
            if d_str not in ibabs_by_day: ibabs_by_day[d_str] = []
            ibabs_by_day[d_str].append(m)
            
        mapped_in_year = 0
            
        for db_id, name, committee, start_date in db_meetings:
            db_id_str = str(db_id)
            if "-" in db_id_str and len(db_id_str) >= 36:
                continue
            if db_id_str in mapping: 
                continue
            if not start_date: 
                continue
            
            d_str = start_date.strftime("%Y-%m-%d")
            db_title = name if (name and name != "Agendapunt") else committee
            if not db_title:
                continue
                
            candidates = ibabs_by_day.get(d_str, [])
            best_match = None
            best_score = 0
            
            for c in candidates:
                score = similar(db_title, c["name"])
                # Also check if committee matches
                score2 = similar(committee, c["name"]) if committee else 0
                max_score = max(score, score2)
                
                if max_score > best_score:
                    best_score = max_score
                    best_match = c
                    
            if best_match and best_score > 0.4:  # Adjust threshold reasonably
                mapping[db_id_str] = best_match["uuid"]
                mapped_in_year += 1
                total_new_mapped += 1
            else:
                pass # logger.debug(f"No match for {db_id_str} {d_str} {db_title}")
                
        logger.info(f"  Newly mapped for {year}: {mapped_in_year}")

    with open(MAPPING_FILE, "w") as f:
        json.dump(mapping, f, indent=2)
        
    logger.info(f"Done! Total mappings saved: {len(mapping)} (New: {total_new_mapped})")

if __name__ == "__main__":
    main()
