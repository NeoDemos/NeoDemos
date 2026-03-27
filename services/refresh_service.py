"""
Auto-refresh service for NeoDemos
Checks for new meetings and downloads documents automatically
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional
from services.storage import StorageService
from services.open_raad import OpenRaadService
from services.ibabs_service import IBabsService
from services.ai_service import AIService
from services.email_service import EmailService

logger = logging.getLogger(__name__)

class RefreshService:
    """Handles automatic refresh of meeting data"""
    
    def __init__(self, storage: StorageService, raad_service: OpenRaadService, ai_service: AIService):
        self.storage = storage
        self.raad_service = raad_service
        self.ibabs_service = IBabsService()
        self.ai_service = ai_service
        self.email_service = EmailService()
        self.last_refresh_date = None
    
    async def check_and_download(self):
        """
        Check for new meetings:
        1. History Sweep: From last ingestion until now (downloads docs/analysis).
        2. Calendar Sweep: From now until now + 60 days (inserts future meetings).
        """
        try:
            logger.info("Starting daily refresh...")
            now = datetime.now()
            
            # --- PHASE 1: History Sweep ---
            last_refresh = self.storage.get_last_ingestion_date()
            if last_refresh:
                start_history = datetime.fromisoformat(last_refresh)
            else:
                start_history = now - timedelta(days=7) # Default to last week if first run
            
            logger.info(f"PHASE 1 (History): Checking for meetings between {start_history} and {now}")
            history_meetings = await self._fetch_new_meetings(start_history, now)
            
            meetings_inserted = 0
            meetings_updated = 0
            documents_downloaded = 0
            errors = []

            for meeting in history_meetings:
                try:
                    if self.storage.insert_meeting(meeting):
                        meetings_inserted += 1
                    else:
                        meetings_updated += 1
                    
                    # Full details for history meetings (docs, agenda)
                    meeting_details = await self.raad_service.get_meeting_details(meeting['id'])
                    if meeting_details and meeting_details.get('agenda'):
                        for agenda_item in meeting_details['agenda']:
                            self.storage.insert_agenda_item(agenda_item)
                            if agenda_item.get('documents'):
                                for doc in agenda_item['documents']:
                                    if not self.storage.document_exists(doc['id']):
                                        self.storage.insert_document(doc)
                                        documents_downloaded += 1
                                        # Only analyze if substantive
                                        if self.storage.is_substantive_item(agenda_item):
                                            try:
                                                await self._analyze_item(agenda_item, [doc])
                                            except Exception as e:
                                                logger.warning(f"Failed to analyze item {agenda_item['id']}: {e}")
                except Exception as e:
                    errors.append(f"Error in history meeting {meeting.get('id')}: {e}")

            # --- PHASE 2: Calendar Sweep (Future) ---
            start_future = now
            end_future = now + timedelta(days=60)
            logger.info(f"PHASE 2 (Calendar): Checking for future meetings between {start_future} and {end_future}")
            
            future_meetings = await self._fetch_new_meetings(start_future, end_future)
            future_inserted = 0
            for meeting in future_meetings:
                try:
                    # Just insert/update meeting metadata for the calendar
                    if self.storage.insert_meeting(meeting):
                        future_inserted += 1
                    
                    # Document Watchdog: For upcoming meetings, check if documents are available
                    # Especially if ORI is falling behind
                    if meeting.get('id'):
                        ibabs_details = await self.ibabs_service.get_meeting_agenda(meeting['id'])
                        if ibabs_details and ibabs_details.get('agenda'):
                            for item in ibabs_details['agenda']:
                                self.storage.insert_agenda_item(item)
                                if item.get('documents'):
                                    for doc in item['documents']:
                                        if not self.storage.document_exists(doc['id']):
                                            self.storage.insert_document(doc)
                                            documents_downloaded += 1
                                            logger.info(f"Watchdog found new document: {doc['name']} for meeting {meeting['id']}")
                except Exception as e:
                    errors.append(f"Error in future meeting {meeting.get('id')}: {e}")

            # --- WRAP UP ---
            # Log the refresh based on HISTORY sweep progress
            self.storage.log_ingestion(
                date_range_start=start_history.date().isoformat(),
                date_range_end=now.date().isoformat(),
                meetings_found=len(history_meetings) + len(future_meetings),
                meetings_inserted=meetings_inserted + future_inserted,
                meetings_updated=meetings_updated,
                documents_downloaded=documents_downloaded,
                errors='\n'.join(errors) if errors else None
            )
            
            logger.info(
                f"Refresh complete: {len(history_meetings)} history meetings, "
                f"{len(future_meetings)} future meetings found."
            )
            self.last_refresh_date = now
            
        except Exception as e:
            logger.error(f"Fatal error in refresh service: {e}")
            await self.email_service.send_error_notification(
                subject="NeoDemos Refresh Critical Error",
                error_message=str(e),
                timestamp=datetime.now()
            )
    
    async def _fetch_new_meetings(self, start_date: datetime, end_date: datetime) -> list:
        """
        Fetch meetings from OpenRaadsinformatie API
        Falls back to direct iBabs scraping for upcoming 2026/2027 meetings.
        """
        # 1. Try ORI (Standard index)
        meetings = await self.raad_service.get_meetings_by_date(start_date, end_date)
        
        # 2. If ORI returns nothing and we're looking at 2026+, try iBabs direct
        if not meetings and (start_date.year >= 2026 or end_date.year >= 2026):
            logger.info("ORI returned 0 meetings for 2026+. Falling back to direct iBabs scrape...")
            ibabs_meetings = await self.ibabs_service.get_meetings_for_year(start_date.year)
            
            # Filter ibabs_meetings by date range manually since iBabs API is by year
            # Note: _parse_meeting_list would need to parse dates for precise filtering
            # For now, we'll return the ones for that year, storage.insert_meeting handles deduplication
            meetings = ibabs_meetings

        return meetings or []
    
    async def _analyze_item(self, agenda_item: dict, documents: list):
        """Run Gemini analysis on an agenda item"""
        try:
            # Prepare documents for analysis
            documents_for_analysis = [
                {
                    'name': doc.get('name', 'Document'),
                    'content': doc.get('content', '')
                }
                for doc in documents
                if doc.get('content')
            ]
            
            if not documents_for_analysis:
                return
            
            # Run analysis
            analysis = await self.ai_service.analyze_agenda_item(
                item_name=agenda_item.get('name', 'Unknown'),
                documents=documents_for_analysis
            )
            
            # Store analysis result (if we add summary_json support)
            logger.info(f"Analysis complete for item {agenda_item['id']}")
            
        except Exception as e:
            logger.warning(f"Failed to analyze item: {e}")
