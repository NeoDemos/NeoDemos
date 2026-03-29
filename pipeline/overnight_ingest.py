import os
import logging
import time
from pipeline.bulk_orchestrator import BulkOrchestrator

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler("overnight_ingest.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("overnight")

def run_overnight():
    # Target years for the remaining historical ingestion
    years = [2022]
    
    logger.info(f"🚀 Starting overnight ingestion for years: {years}")
    
    for year in years:
        logger.info(f"\n{'='*60}")
        logger.info(f"📅 COMMENCING YEAR: {year}")
        logger.info(f"{'='*60}")
        
        state_file = f"data/pipeline_state/pipeline_state_{year}.json"
        
        try:
            orchestrator = BulkOrchestrator(state_file=state_file, reset_state=False)
            
            # Discover meetings if first time for this year
            if not orchestrator.state["meetings"]:
                logger.info(f"🔍 Discovering meetings for {year}...")
                orchestrator.discover_meetings(year=year)
            
            # Process sequential in download_only mode
            logger.info(f"⚙️ Running sequential audio download for {year} (Skipping DB/Whisper)...")
            orchestrator.run_sequential(download_only=True)
            
            logger.info(f"✅ Finished year: {year}")
            
        except Exception as e:
            logger.error(f"❌ Critical error in year {year}: {e}")
            # Continue to next year even if one fails
            continue

    logger.info("\n🏆 Overnight ingestion complete!")

if __name__ == "__main__":
    run_overnight()
