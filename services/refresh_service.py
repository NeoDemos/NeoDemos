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
from services.ai_service import AIService
from services.email_service import EmailService

logger = logging.getLogger(__name__)

class RefreshService:
    """Handles automatic refresh of meeting data"""
    
    def __init__(self, storage: StorageService, raad_service: OpenRaadService, ai_service: AIService):
        self.storage = storage
        self.raad_service = raad_service
        self.ai_service = ai_service
        self.email_service = EmailService()
        self.last_refresh_date = None
    
    async def check_and_download(self):
        """
        Check for new meetings since last refresh and download them.
        This method is called daily by APScheduler.
        """
        try:
            logger.info("Starting daily refresh...")
            
            # Determine date range for this refresh
            last_refresh = self.storage.get_last_ingestion_date()
            if last_refresh:
                start_date = datetime.fromisoformat(last_refresh)
            else:
                # If no previous refresh, start from yesterday
                start_date = datetime.now() - timedelta(days=1)
            
            end_date = datetime.now()
            
            logger.info(f"Checking for meetings between {start_date} and {end_date}")
            
            # Query OpenRaadsinformatie for new meetings
            meetings_found = 0
            meetings_inserted = 0
            meetings_updated = 0
            documents_downloaded = 0
            errors = []
            
            try:
                # Get meetings from API
                new_meetings = await self._fetch_new_meetings(start_date, end_date)
                meetings_found = len(new_meetings)
                logger.info(f"Found {meetings_found} new meetings")
                
                # Process each meeting
                for meeting in new_meetings:
                    try:
                        # Insert/update meeting
                        if self.storage.insert_meeting(meeting):
                            meetings_inserted += 1
                        else:
                            meetings_updated += 1
                        
                        # Get agenda items and documents
                        meeting_details = await self.raad_service.get_meeting_details(meeting['id'])
                        
                        if meeting_details and meeting_details.get('agenda'):
                            for agenda_item in meeting_details['agenda']:
                                self.storage.insert_agenda_item(agenda_item)
                                
                                # Download and store documents
                                if agenda_item.get('documents'):
                                    for doc in agenda_item['documents']:
                                        if not self.storage.document_exists(doc['id']):
                                            self.storage.insert_document(doc)
                                            documents_downloaded += 1
                                            
                                            # Run analysis on substantive items
                                            if self.storage.is_substantive_item(agenda_item):
                                                try:
                                                    await self._analyze_item(agenda_item, [doc])
                                                except Exception as e:
                                                    logger.warning(f"Failed to analyze item {agenda_item['id']}: {e}")
                    
                    except Exception as e:
                        error_msg = f"Error processing meeting {meeting.get('id')}: {str(e)}"
                        logger.error(error_msg)
                        errors.append(error_msg)
                
                # Log the refresh
                self.storage.log_ingestion(
                    date_range_start=start_date.date().isoformat(),
                    date_range_end=end_date.date().isoformat(),
                    meetings_found=meetings_found,
                    meetings_inserted=meetings_inserted,
                    meetings_updated=meetings_updated,
                    documents_downloaded=documents_downloaded,
                    errors='\n'.join(errors) if errors else None
                )
                
                logger.info(
                    f"Refresh complete: {meetings_found} meetings, "
                    f"{documents_downloaded} documents downloaded"
                )
                
                self.last_refresh_date = end_date
                
            except Exception as e:
                error_msg = f"Failed to fetch meetings from API: {str(e)}"
                logger.error(error_msg)
                
                # Log failed refresh
                self.storage.log_ingestion(
                    date_range_start=start_date.date().isoformat(),
                    date_range_end=end_date.date().isoformat(),
                    meetings_found=0,
                    meetings_inserted=0,
                    meetings_updated=0,
                    documents_downloaded=0,
                    errors=error_msg
                )
                
                # Send error email
                await self.email_service.send_error_notification(
                    subject="NeoDemos Refresh Failed",
                    error_message=error_msg,
                    timestamp=datetime.now()
                )
                raise
        
        except Exception as e:
            logger.error(f"Fatal error in refresh service: {e}")
            # Send error email
            await self.email_service.send_error_notification(
                subject="NeoDemos Refresh Critical Error",
                error_message=str(e),
                timestamp=datetime.now()
            )
    
    async def _fetch_new_meetings(self, start_date: datetime, end_date: datetime) -> list:
        """
        Fetch meetings from OpenRaadsinformatie API
        Only returns meetings newer than start_date
        """
        # This would query the ORI API for meetings in the date range
        # For now, returning empty list as this needs API integration
        # This will be filled in based on the existing open_raad.py
        meetings = await self.raad_service.get_meetings_by_date(start_date, end_date)
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
