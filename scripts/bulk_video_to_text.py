import os
import sys
import json
import logging
import argparse
import psycopg2
from pathlib import Path
from datetime import datetime
from pipeline.main_pipeline import run_pipeline

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bulk_pipeline.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("bulk_video_to_text")

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"

def get_meetings_to_process(year: int):
    """Query DB for committee meetings in the given year without notulen/whisper transcripts."""
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            query = """
            SELECT m.id, m.name, m.committee, m.start_date
            FROM meetings m
            WHERE EXTRACT(YEAR FROM m.start_date) = %s
              AND (m.committee ILIKE '%%Commissie%%' OR (m.committee IS NOT NULL AND m.committee NOT IN ('None', 'Agendapunt')))
              AND NOT EXISTS (
                  SELECT 1 FROM documents d 
                  WHERE d.meeting_id = m.id 
                    AND (d.name ILIKE '%%notulen%%' OR d.name ILIKE '%%verslag%%')
              )
            ORDER BY m.start_date DESC;
            """
            cur.execute(query, (year,))
            return cur.fetchall()
    finally:
        conn.close()

def load_state(year: int):
    state_file = Path(f"pipeline_state_{year}.json")
    if state_file.exists():
        with open(state_file, "r") as f:
            state = json.load(f)
            # Ensure keys exist
            if "completed_meetings" not in state: state["completed_meetings"] = []
            if "failed_meetings" not in state: state["failed_meetings"] = {}
            return state
    return {"completed_meetings": [], "failed_meetings": {}}

def save_state(year: int, state: dict):
    state_file = Path(f"pipeline_state_{year}.json")
    # Ensure structure is consistent
    if "completed_meetings" not in state: state["completed_meetings"] = []
    if "failed_meetings" not in state: state["failed_meetings"] = {}
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)

def main():
    parser = argparse.ArgumentParser(description="Bulk Video-to-Text Transcription for a specific year.")
    parser.add_argument("--year", type=int, required=True, help="Year to process (e.g. 2026)")
    parser.add_argument("--limit", type=int, help="Limit number of meetings to process")
    parser.add_argument("--resume", action="store_true", help="Resume from state file")
    args = parser.parse_args()

    year = args.year
    logger.info(f"Starting bulk transcription for year {year}...")

    meetings = get_meetings_to_process(year)
    state = load_state(year) if args.resume else {"completed_meetings": [], "failed_meetings": {}}
    
    completed_ids = set(state["completed_meetings"])
    target_meetings = [m for m in meetings if str(m[0]) not in completed_ids]
    
    if args.limit:
        target_meetings = target_meetings[:args.limit]

    logger.info(f"Total target meetings for {year}: {len(meetings)}")
    logger.info(f"Remaining to process: {len(target_meetings)}")

    # Load UUID mapping
    mapping_file = Path("ibabs_uuid_mapping.json")
    uuid_mapping = {}
    if mapping_file.exists():
        with open(mapping_file, "r") as f:
            uuid_mapping = json.load(f)

    for meeting_id, name, committee, start_date in target_meetings:
        mid_str = str(meeting_id)
        logger.info(f"\n🚀 Processing: {start_date} | {committee} | {name} (ID: {mid_str})")
        
        try:
            # We don't have the webcast_code yet, so we pass ibabs_url or similar
            # Use the UUID from mapping if available to prevent iBabs 500 errors
            target_id = uuid_mapping.get(mid_str, mid_str)
            ibabs_url = f"https://rotterdamraad.bestuurlijkeinformatie.nl/Agenda/Index/{target_id}"
            
            # RUN PIPELINE
            result = run_pipeline(
                ibabs_url=ibabs_url,
                numeric_id=mid_str,
                use_whisper=True,  # Force whisper as these meetings lack notulen
                vtt_only=False     # We want OCR and full MLX processing
            )
            
            state["completed_meetings"].append(mid_str)
            logger.info(f"✅ Successfully processed {mid_str}")
            
        except Exception as e:
            logger.error(f"❌ Failed to process {mid_str}: {str(e)}")
            state["failed_meetings"][mid_str] = str(e)
            
        finally:
            save_state(year, state)

    logger.info(f"Bulk run for {year} finished.")

if __name__ == "__main__":
    main()
