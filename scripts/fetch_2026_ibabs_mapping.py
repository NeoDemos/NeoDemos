import os
import sys
import asyncio
import json
import logging

# Ensure we can import from the root directory
sys.path.append(os.getcwd())

from services.open_raad import OpenRaadService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ibabs_mapping")

async def main():
    service = OpenRaadService()
    await service.ensure_index()
    
    meetings = await service.get_meetings(start_date="2026-01-01", end_date="2026-12-31")
    mapping = {}
    
    for m in meetings:
        meeting_id = m.get("id")
        gen_by = m.get("was_generated_by", {})
        original_id = gen_by.get("original_identifier")
        
        if not original_id:
            # Full fetch if not in list
            logger.info(f"Fetching details for {meeting_id}...")
            details = await service.get_meeting_details(meeting_id)
            original_id = details.get("was_generated_by", {}).get("original_identifier")
            
        if original_id:
            mapping[meeting_id] = original_id
            
    with open("data/ibabs_2026_mapping.json", "w") as f:
        json.dump(mapping, f, indent=2)
    
    logger.info(f"Saved {len(mapping)} mappings to data/ibabs_2026_mapping.json")

if __name__ == "__main__":
    asyncio.run(main())
