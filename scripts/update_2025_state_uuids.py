import json
import requests
import psycopg2
from datetime import datetime, timedelta
import logging
import os

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
STATE_FILE = "pipeline_state_2025.json"
API_URL = "https://rotterdamraad.bestuurlijkeinformatie.nl/Calendar/GetAgendasForCalendar"

def fetch_api_meetings(year=2025):
    all_meetings = []
    # Fetch month by month to avoid potential limits
    for month in range(1, 13):
        start_date = f"{year}-{month:02d}-01T00:00:00Z"
        if month == 12:
            end_date = f"{year}-12-31T23:59:59Z"
        else:
            end_date = f"{year}-{month+1:02d}-01T00:00:00Z"
        
        try:
            r = requests.get(API_URL, params={'start': start_date, 'end': end_date}, timeout=15)
            r.raise_for_status()
            data = r.json()
            all_meetings.extend(data)
            logger.info(f"Fetched {len(data)} meetings for {year}-{month:02d}")
        except Exception as e:
            logger.error(f"Error fetching meetings for {year}-{month:02d}: {e}")
            
    return all_meetings

def get_db_meetings():
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("SELECT id, name, start_date FROM meetings WHERE start_date >= '2025-01-01' AND start_date < '2026-01-01'")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return {str(row[0]): {'name': row[1], 'date': row[2]} for row in rows}
    except Exception as e:
        logger.error(f"Error fetching DB meetings: {e}")
        return {}

def update_state():
    if not os.path.exists(STATE_FILE):
        logger.error(f"{STATE_FILE} not found")
        return

    with open(STATE_FILE, "r") as f:
        state = json.load(f)

    api_meetings = fetch_api_meetings()
    db_meetings = get_db_meetings()
    
    # Create an API mapping by date and title
    # Normalize titles: remove "- Reguliere commissievergadering" etc.
    def normalize(t):
        return t.split(' - ')[0].strip().lower()

    api_map = {}
    for m in api_meetings:
        date_str = m.get('start').split('T')[0]
        title = normalize(m.get('title', ''))
        key = (date_str, title)
        if key not in api_map:
            api_map[key] = []
        api_map[key].append(m.get('id'))

    new_meetings_state = {}
    mapped_count = 0
    failed_count = 0

    for m_id, m_info in state.get('meetings', {}).items():
        db_info = db_meetings.get(m_id)
        if not db_info:
            logger.warning(f"Meeting {m_id} from state file not found in DB")
            continue
            
        date_str = db_info['date'].strftime('%Y-%m-%d')
        title = normalize(db_info['name'])
        key = (date_str, title)
        
        uuids = api_map.get(key)
        if uuids:
            new_id = uuids[0] # Take the first match
            # Copy existing state but update URL and ID
            meeting_state = m_info.copy()
            meeting_state['url'] = f"https://rotterdamraad.bestuurlijkeinformatie.nl/Agenda/Index/{new_id}"
            new_meetings_state[new_id] = meeting_state
            mapped_count += 1
            if len(uuids) > 1:
                logger.info(f"Multiple matches for {key}: {uuids}")
        else:
            logger.warning(f"Could not map meeting {m_id} ({date_str} | {title})")
            failed_count += 1

    state['meetings'] = new_meetings_state
    
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
        
    logger.info(f"Update complete. Mapped: {mapped_count}, Failed: {failed_count}")

if __name__ == "__main__":
    update_state()
