#!/usr/bin/env python3
"""
Recover 2026 Committee Meetings
================================
Fetches 2026 committee meetings from Openraadsinformatie,
validates them, and runs the transcription pipeline with
RAM-safe settings (Whisper Tiny) and test tagging.
"""

import asyncio
import logging
import argparse
import os
import sys
from datetime import datetime, timedelta
from typing import List, Dict, Any

# Ensure we can import from the root directory
sys.path.append(os.getcwd())

from services.open_raad import OpenRaadService
from pipeline.main_pipeline import run_pipeline

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("recovery_2026.log"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("recover_2026")

def convert_utc_to_local(utc_str):
    """Convert ORI UTC string to local Rotterdam time (CET/CEST)."""
    if not utc_str: return None
    dt_utc = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
    # Minimal fix: Rotterdam is UTC+1 or UTC+2. 
    # March 25 is currently CEST (UTC+2) since late March.
    # Actually, Sunday March 29 2026 is when it shifts. So it's still CET (+1).
    return dt_utc + timedelta(hours=1)

async def main():
    parser = argparse.ArgumentParser(description="Recover 2026 Committee Meetings.")
    parser.add_argument("--limit", type=int, default=3, help="Number of meetings to process")
    parser.add_argument("--apply", action="store_true", help="Actually run the pipeline (default is dry-run)")
    parser.add_argument("--model", default="mlx-community/whisper-large-v3-turbo", help="Whisper model to use")
    parser.add_argument("--category", default="committee_transcript_test", help="Category tag for ingestion")
    args = parser.parse_args()

    service = OpenRaadService()
    logger.info("Initializing OpenRaadService and discovering index...")
    await service.ensure_index()

    logger.info("Fetching 2026 meetings...")
    # Fetch meetings for 2026
    meetings = await service.get_meetings(start_date="2026-01-01", end_date="2026-12-31")
    
    # Filter for committee meetings
    committee_meetings = [
        m for m in meetings 
        if m.get("name") and "commissie" in m.get("name", "").lower()
    ]
    
    logger.info(f"Found {len(committee_meetings)} committee meetings for 2026.")
    
    # Sort by date (most recent first)
    committee_meetings.sort(key=lambda x: x.get("start_date", ""), reverse=True)
    
    targets = committee_meetings[:args.limit]
    
    logger.info(f"Targeting top {len(targets)} meetings for recovery:")
    for i, m in enumerate(targets):
        local_time = convert_utc_to_local(m.get("start_date"))
        logger.info(f"  {i+1}. [{local_time}] {m.get('committee')} - {m.get('name')} (ID: {m.get('id')})")

    if not args.apply:
        logger.info("\nDRY RUN complete. Use --apply to process these meetings.")
        return

    logger.info("\n🚀 Starting recovery phase...")
    
    for m in targets:
        meeting_id = m.get("id")
        meeting_name = m.get("name")
        committee = m.get("committee")
        local_start = convert_utc_to_local(m.get("start_date"))
        
        # Extract original iBabs identifier
        original_id = None
        gen_by = m.get("was_generated_by", {})
        if gen_by:
            original_id = gen_by.get("original_identifier")
        
        if not original_id:
            # Fallback to fetching full details if not in list metadata
            logger.info(f"Fetching details for {meeting_id} to find original identifier...")
            details = await service.get_meeting_details(meeting_id)
            original_id = details.get("was_generated_by", {}).get("original_identifier")

        if not original_id:
            logger.warning(f"Could not find original iBabs identifier for {meeting_id}. Skipping.")
            continue

        ibabs_url = f"https://rotterdamraad.bestuurlijkeinformatie.nl/Agenda/Index/{original_id}"
        
        logger.info(f"\n--- Processing: {meeting_name} ---")
        logger.info(f"Date:      {local_start}")
        logger.info(f"Committee: {committee}")
        logger.info(f"ORI ID:    {meeting_id}")
        logger.info(f"iBabs URL: {ibabs_url}")
        
        try:
            # Run the pipeline
            result = run_pipeline(
                ibabs_url=ibabs_url,
                numeric_id=meeting_id,
                use_whisper=True,
                whisper_model=args.model,
                category=args.category,
                no_normalize=False,
                no_ingest=False
            )
            
            logger.info(f"✅ Successfully processed meeting {meeting_id}")
            logger.info(f"   Transcript: {result.get('meeting_name')}")
            
        except Exception as e:
            logger.error(f"❌ Failed to process meeting {meeting_id}: {str(e)}")

    logger.info("\nRecovery run finished.")

if __name__ == "__main__":
    asyncio.run(main())
