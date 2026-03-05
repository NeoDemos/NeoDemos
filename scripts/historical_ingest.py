import asyncio
import os
import sys
import logging
from datetime import datetime
from typing import List, Dict, Any

# Add the project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.raadsinformatie_scraper import RaadsinformatieScraperService
from services.storage import StorageService
from services.scraper import ScraperService

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("historical_ingest.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("historical_ingest")

class HistoricalIngestor:
    def __init__(self):
        self.scraper_service = RaadsinformatieScraperService()
        self.storage = StorageService()
        self.pdf_scraper = ScraperService()
        self.stats = {
            "meetings_found": 0,
            "meetings_processed": 0,
            "documents_downloaded": 0,
            "errors": 0
        }

    async def ingest_range(self, keywords: str = "notulen", max_pages: int = 20):
        """Search and ingest documents based on keywords"""
        logger.info(f"Starting historical ingestion for keywords: '{keywords}'")
        
        for page in range(1, max_pages + 1):
            logger.info(f"Processing search results page {page}...")
            results = await self.scraper_service.search_documents(keywords, page=page)
            
            if not results:
                logger.info("No more results found.")
                break
                
            for res in results:
                meeting_id = res.get('meeting_id')
                if not meeting_id:
                    continue
                    
                self.stats["meetings_found"] += 1
                
                try:
                    # Process meeting
                    await self.process_meeting(meeting_id)
                    self.stats["meetings_processed"] += 1
                except Exception as e:
                    logger.error(f"Error processing meeting {meeting_id}: {e}")
                    self.stats["errors"] += 1
                    
                # Small delay to be polite to the server
                await asyncio.sleep(1)

        self.print_summary()

    async def process_meeting(self, meeting_id: str):
        """Fetch and store all data for a specific meeting"""
        logger.info(f"Processing meeting: {meeting_id}")
        
        details = await self.scraper_service.get_meeting_details(meeting_id)
        if not details:
            return
            
        # 1. Insert Meeting
        meeting_data = {
            "id": meeting_id,
            "name": details.get("name"),
            "start_date": self._parse_date(details.get("meeting_date")),
            "committee": details.get("meeting_name"),
            "location": None,
            "organization_id": "rotterdam"
        }
        self.storage.insert_meeting(meeting_data)
        
        # 2. Process Agenda Items
        for item in details.get("agenda", []):
            item_id = item.get("id")
            agenda_item_data = {
                "id": item_id,
                "meeting_id": meeting_id,
                "number": None, # Could extract from name if needed
                "name": item.get("name")
            }
            self.storage.insert_agenda_item(agenda_item_data)
            
            # 3. Process Documents
            for doc in item.get("documents", []):
                doc_url = doc.get("url")
                # Create a unique ID for the document based on URL or remote ID
                doc_id = self._extract_doc_id(doc_url)
                
                if self.storage.document_exists(doc_id):
                    logger.debug(f"Document {doc_id} already exists, skipping.")
                    continue
                    
                logger.info(f"Downloading document: {doc.get('name')} ({doc_id})")
                content = await self.pdf_scraper.extract_text_from_url(doc_url)
                
                if content:
                    doc_data = {
                        "id": doc_id,
                        "agenda_item_id": item_id,
                        "meeting_id": meeting_id,
                        "name": doc.get("name"),
                        "url": doc_url,
                        "content": self.pdf_scraper.preserve_notulen_text(content)
                    }
                    if self.storage.insert_document(doc_data):
                        self.stats["documents_downloaded"] += 1
                else:
                    logger.warning(f"Could not extract text from {doc_url}")

    def _parse_date(self, date_str: str) -> str:
        """Heuristic to parse Dutch date strings or returns current if fails"""
        # Example: "28 januari 2026"
        # For now, return current or attempt simple split if format matches
        try:
            # Simple placeholder logic or actual parser
            return datetime.now().isoformat() # Needs better parsing for production
        except:
            return datetime.now().isoformat()

    def _extract_doc_id(self, url: str) -> str:
        """Extract numeric ID from document URL"""
        match = re.search(r'/document/(\d+)', url)
        if match:
            return match.group(1)
        import hashlib
        return hashlib.md5(url.encode()).hexdigest()

    def print_summary(self):
        logger.info("=" * 40)
        logger.info("INGESTION SUMMARY")
        logger.info("=" * 40)
        logger.info(f"Meetings found:     {self.stats['meetings_found']}")
        logger.info(f"Meetings processed: {self.stats['meetings_processed']}")
        logger.info(f"Documents ingested: {self.stats['documents_downloaded']}")
        logger.info(f"Errors encountered: {self.stats['errors']}")
        logger.info("=" * 40)

import re

if __name__ == "__main__":
    ingestor = HistoricalIngestor()
    asyncio.run(ingestor.ingest_range(max_pages=5)) # Start with 5 pages for testing
