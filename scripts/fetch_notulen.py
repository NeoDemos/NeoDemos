#!/usr/bin/env python3
"""
Fetch Notulen for Gemeenteraad Meetings
Retrieves meeting minutes (notulen) for all Gemeenteraad meetings between 2024-2025
and stores them in the database for later analysis.

Strategy:
1. Query for all Gemeenteraad meetings in the date range
2. For each meeting, fetch its agenda items and attachments
3. Identify documents that appear to be notulen based on name patterns
4. Store them with proper classification for later analysis
"""

import asyncio
import sys
import os
from datetime import datetime
from typing import List, Dict, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.open_raad import OpenRaadService
from services.storage import StorageService
from services.scraper import ScraperService

class NotulenFetchService:
    """Service for fetching and ingesting notulen (meeting minutes)"""
    
    def __init__(self):
        self.raad_service = OpenRaadService()
        self.storage = StorageService()
        self.scraper = ScraperService()
        self.stats = {
            'gemeenteraad_meetings_found': 0,
            'notulen_documents_found': 0,
            'notulen_downloaded': 0,
            'errors': []
        }
    
    async def fetch_notulen(self, start_date: str = "2024-01-01", end_date: str = "2025-12-31"):
        """
        Fetch all notulen for Gemeenteraad meetings in the date range
        
        Args:
            start_date: ISO format date string (YYYY-MM-DD)
            end_date: ISO format date string (YYYY-MM-DD)
        """
        try:
            print(f"\n{'='*70}")
            print(f"NeoDemos Notulen Fetcher - Gemeenteraad (2024-2025)")
            print(f"{'='*70}\n")
            
            # 1. Fetch all Gemeenteraad meetings (across all date ranges due to API pagination)
            print(f"[1/3] Fetching Gemeenteraad meetings from {start_date} to {end_date}...")
            gemeenteraad_meetings = await self._fetch_all_gemeenteraad_meetings(start_date, end_date)
            self.stats['gemeenteraad_meetings_found'] = len(gemeenteraad_meetings)
            
            if not gemeenteraad_meetings:
                print("⚠️  No Gemeenteraad meetings found for the specified date range.")
                self._print_summary()
                return self.stats
            
            print(f"✓ Found {len(gemeenteraad_meetings)} Gemeenteraad meetings\n")
            
            # 2. Process each meeting to find notulen
            print(f"[2/3] Processing meetings to identify notulen documents...")
            for idx, meeting in enumerate(gemeenteraad_meetings, 1):
                try:
                    meeting_id = meeting.get('id')
                    if not meeting_id:
                        continue
                    
                    meeting_date = meeting.get('start_date', 'Unknown')
                    
                    print(f"  [{idx}/{len(gemeenteraad_meetings)}] Meeting from {meeting_date}")
                    
                    # Fetch meeting details and documents
                    details = await self.raad_service.get_meeting_details(str(meeting_id))
                    
                    if not details or not details.get('agenda'):
                        print(f"    ⚠️  No agenda items found")
                        continue
                    
                    # Look for notulen in the documents
                    notulen_found = 0
                    for agenda_item in details.get('agenda', []):
                        for doc in agenda_item.get('documents', []):
                            if self._is_notulen_document(doc):
                                # Download and store the notulen
                                if await self._store_notulen(
                                    doc=doc,
                                    meeting_id=str(meeting_id),
                                    meeting_date=meeting_date,
                                    agenda_item=agenda_item
                                ):
                                    notulen_found += 1
                    
                    if notulen_found > 0:
                        print(f"    ✓ Found {notulen_found} notulen document(s)")
                    else:
                        print(f"    ⊘ No notulen found for this meeting")
                
                except Exception as e:
                    error_msg = f"Error processing meeting {str(meeting.get('id'))}: {str(e)}"
                    self.stats['errors'].append(error_msg)
                    print(f"  ✗ {error_msg}")
            
            # Log ingestion
            self._log_ingestion(start_date, end_date)
            
            # Print summary
            self._print_summary()
            
            return self.stats
        
        except Exception as e:
            error_msg = f"Fatal error during notulen fetching: {str(e)}"
            self.stats['errors'].append(error_msg)
            print(f"\n✗ {error_msg}")
            self._log_ingestion(start_date, end_date)
            self._print_summary()
            raise
    
    async def _fetch_all_gemeenteraad_meetings(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        """
        Fetch all Gemeenteraad meetings, handling pagination by querying date ranges
        The API returns max 50 results per query, so we need to split the date range
        """
        from datetime import datetime, timedelta
        
        gemeenteraad_meetings = []
        
        # Split into 3-month quarters to handle pagination
        start = datetime.fromisoformat(start_date)
        end = datetime.fromisoformat(end_date)
        current = start
        
        while current < end:
            quarter_end = min(current + timedelta(days=90), end)
            quarter_start = current.strftime('%Y-%m-%d')
            quarter_end_str = quarter_end.strftime('%Y-%m-%d')
            
            meetings = await self.raad_service.get_meetings(
                start_date=quarter_start,
                end_date=quarter_end_str
            )
            
            gemeenteraad = [m for m in meetings if m.get('name') == 'Gemeenteraad']
            gemeenteraad_meetings.extend(gemeenteraad)
            
            current = quarter_end
        
        return gemeenteraad_meetings
    
    def _is_notulen_document(self, doc: Dict[str, Any]) -> bool:
        """
        Determine if a document is likely a notulen (meeting minutes) document
        based on its name and metadata
        """
        doc_name = (doc.get('name') or '').lower()
        
        # Notulen typically have these keywords in their names
        notulen_keywords = [
            'notulen',
            'minutes',
            'verslag',
            'aantekeningen',
            'zitting'
        ]
        
        return any(keyword in doc_name for keyword in notulen_keywords)
    
    async def _store_notulen(self, doc: Dict[str, Any], meeting_id: str, 
                            meeting_date: str, agenda_item: Dict[str, Any]) -> bool:
        """
        Download and store a notulen document in the database
        
        Returns True if successfully stored, False otherwise
        """
        try:
            doc_id = doc.get('id')
            if not doc_id:
                return False
            
            # Check if already exists
            if self.storage.document_exists(str(doc_id)):
                return False
            
            # Download document content
            if not doc.get('url'):
                return False
            
            full_content = await self.scraper.extract_text_from_url(doc['url'])
            if not full_content:
                return False
            
            # Store the notulen document
            compressed = self.scraper.compress_text(full_content)
            
            doc_data = {
                'id': doc_id,
                'name': doc.get('name'),
                'url': doc.get('url'),
                'content': compressed,
                'agenda_item_id': agenda_item.get('id'),
                'meeting_id': meeting_id
            }
            
            # Insert document
            if self.storage.insert_document(doc_data):
                # Also classify this document as notulen in the new table
                self._classify_as_notulen(str(doc_id), meeting_id)
                self.stats['notulen_downloaded'] += 1
                return True
            
            return False
        
        except Exception as e:
            error_msg = f"Failed to store notulen {doc.get('name')}: {str(e)}"
            self.stats['errors'].append(error_msg)
            return False
    
    def _classify_as_notulen(self, doc_id: str, meeting_id: str):
        """Mark a document as notulen in the document_classifications table"""
        try:
            import psycopg2
            
            conn = psycopg2.connect(
                "postgresql://postgres:postgres@localhost:5432/neodemos"
            )
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO document_classifications (document_id, is_notulen, meeting_id, document_type)
                VALUES (%s, TRUE, %s, 'notulen')
                ON CONFLICT (document_id) DO UPDATE SET
                    is_notulen = TRUE,
                    document_type = 'notulen'
            """, (doc_id, meeting_id))
            
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            self.stats['errors'].append(f"Failed to classify notulen {doc_id}: {str(e)}")
    
    def _log_ingestion(self, start_date: str, end_date: str):
        """Log the notulen ingestion operation"""
        self.storage.log_ingestion(
            date_range_start=start_date,
            date_range_end=end_date,
            meetings_found=self.stats['gemeenteraad_meetings_found'],
            meetings_inserted=0,  # We're not inserting meetings, just documents
            meetings_updated=0,
            documents_downloaded=self.stats['notulen_downloaded'],
            errors='\n'.join(self.stats['errors']) if self.stats['errors'] else None
        )
    
    def _print_summary(self):
        """Print notulen fetching summary"""
        print(f"\n{'='*70}")
        print("NOTULEN FETCHING SUMMARY")
        print(f"{'='*70}")
        print(f"Gemeenteraad meetings found: {self.stats['gemeenteraad_meetings_found']}")
        print(f"Notulen documents found:     {self.stats['notulen_documents_found']}")
        print(f"Notulen downloaded:          {self.stats['notulen_downloaded']}")
        print(f"Errors:                      {len(self.stats['errors'])}")
        
        if self.stats['errors']:
            print(f"\nError details (first 5):")
            for error in self.stats['errors'][:5]:
                print(f"  - {error}")
            if len(self.stats['errors']) > 5:
                print(f"  ... and {len(self.stats['errors']) - 5} more")
        
        print(f"{'='*70}\n")

async def main():
    """Main entry point"""
    service = NotulenFetchService()
    stats = await service.fetch_notulen(
        start_date="2024-01-01",
        end_date="2025-12-31"
    )
    
    sys.exit(0 if not stats['errors'] else 1)

if __name__ == "__main__":
    asyncio.run(main())
