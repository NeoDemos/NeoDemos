#!/usr/bin/env python3
"""
NeoDemos Data Ingestion Script
Fetches meetings, agenda items, and documents from OpenRaadsinformatie
Supports flexible date ranges and includes notulen (meeting minutes) retrieval
"""

import asyncio
import sys
import os
import argparse
from datetime import datetime
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.open_raad import OpenRaadService
from services.storage import StorageService
from services.scraper import ScraperService

# Load environment variables
load_dotenv()

class IngestionService:
    """Service for ingesting meeting data from OpenRaadsinformatie"""
    
    def __init__(self):
        self.raad_service = OpenRaadService()
        self.storage = StorageService()
        self.scraper = ScraperService()
        self.stats = {
            'meetings_found': 0,
            'meetings_inserted': 0,
            'meetings_updated': 0,
            'agenda_items': 0,
            'documents_downloaded': 0,
            'errors': []
        }
    
    async def ingest_meetings(self, start_date: str, end_date: str, include_notulen: bool = False):
        """
        Ingest all meetings between start_date and end_date
        
        Args:
            start_date: ISO format date string (YYYY-MM-DD)
            end_date: ISO format date string (YYYY-MM-DD)
            include_notulen: Whether to search for and ingest notulen documents
        """
        try:
            print(f"\n{'='*60}")
            print(f"NeoDemos Data Ingestion")
            print(f"Period: {start_date} to {end_date}")
            print(f"Include notulen: {include_notulen}")
            print(f"{'='*60}\n")
            
            # 1. Fetch all meetings for the date range
            print(f"[1/4] Fetching meetings from {start_date} to {end_date}...")
            meetings = await self.raad_service.get_meetings(
                start_date=start_date,
                end_date=end_date
            )
            
            if not meetings:
                print("⚠️  No meetings found for the specified date range.")
                return self.stats
            
            self.stats['meetings_found'] = len(meetings)
            print(f"✓ Found {len(meetings)} meetings\n")
            
            # 2. Process each meeting
            print(f"[2/4] Processing meetings and documents...")
            for idx, meeting in enumerate(meetings, 1):
                try:
                    meeting_name = meeting.get('name', 'Unknown')
                    meeting_date = meeting.get('start_date', 'Unknown')
                    
                    print(f"  [{idx}/{len(meetings)}] {meeting_name} ({meeting_date})")
                    
                    # Insert meeting (will skip if already exists)
                    if self.storage.insert_meeting(meeting):
                        self.stats['meetings_inserted'] += 1
                    else:
                        self.stats['meetings_updated'] += 1
                    
                    # 3. Fetch and process meeting details
                    details = await self.raad_service.get_meeting_details(meeting['id'])
                    
                    if not details or not details.get('agenda'):
                        print(f"    ⚠️  No agenda items found")
                        continue
                    
                    # Process agenda items
                    for agenda_item in details.get('agenda', []):
                        # Add meeting_id to agenda_item for storage
                        agenda_item['meeting_id'] = meeting['id']
                        self.storage.insert_agenda_item(agenda_item)
                        self.stats['agenda_items'] += 1
                        
                        # Process documents
                        for doc in agenda_item.get('documents', []):
                            if self.storage.document_exists(doc['id']):
                                continue
                            
                            # Download document content
                            if doc.get('url'):
                                try:
                                    full_content = await self.scraper.extract_text_from_url(doc['url'])
                                    if full_content:
                                        # Compress content for storage (15KB max)
                                        compressed = self.scraper.compress_text(full_content)
                                        doc['content'] = compressed
                                        doc['agenda_item_id'] = agenda_item['id']
                                        doc['meeting_id'] = meeting['id']
                                        
                                        self.storage.insert_document(doc)
                                        self.stats['documents_downloaded'] += 1
                                
                                except Exception as e:
                                    error_msg = f"Failed to download {doc['name']}: {str(e)}"
                                    self.stats['errors'].append(error_msg)
                                    print(f"    ✗ {error_msg}")
                
                except Exception as e:
                    error_msg = f"Error processing meeting {meeting.get('id')}: {str(e)}"
                    self.stats['errors'].append(error_msg)
                    print(f"  ✗ {error_msg}")
            
            # 4. Search for notulen (meeting minutes) if requested
            if include_notulen:
                print(f"\n[3/4] Searching for notulen (meeting minutes)...")
                await self._ingest_notulen(start_date, end_date)
            else:
                print(f"\n[3/4] Skipping notulen (use --notulen flag to include)")
            
            # Log ingestion
            self._log_ingestion(start_date, end_date)
            
            # Print summary
            self._print_summary()
            
            return self.stats
        
        except Exception as e:
            error_msg = f"Fatal error during ingestion: {str(e)}"
            self.stats['errors'].append(error_msg)
            print(f"\n✗ {error_msg}")
            self._log_ingestion(start_date, end_date)
            self._print_summary()
            raise
    
    async def _ingest_notulen(self, start_date: str, end_date: str):
        """
        Search for and ingest notulen (meeting minutes) documents
        These are typically much less numerous than other documents
        """
        try:
            # Query for notulen documents in the date range
            notulen_docs = await self.raad_service.get_documents_by_type(
                doc_type='notulen',
                start_date=start_date,
                end_date=end_date
            )
            
            if not notulen_docs:
                print("  No notulen found for this period")
                return
            
            print(f"  Found {len(notulen_docs)} notulen documents")
            
            for doc in notulen_docs:
                try:
                    if self.storage.document_exists(doc['id']):
                        continue
                    
                    # Download notulen content
                    if doc.get('url'):
                        full_content = await self.scraper.extract_text_from_url(doc['url'])
                        if full_content:
                            compressed = self.scraper.compress_text(full_content)
                            doc['content'] = compressed
                            self.storage.insert_document(doc)
                            self.stats['documents_downloaded'] += 1
                            print(f"    ✓ {doc['name']}")
                
                except Exception as e:
                    error_msg = f"Failed to process notulen {doc.get('name')}: {str(e)}"
                    self.stats['errors'].append(error_msg)
                    print(f"    ✗ {error_msg}")
        
        except Exception as e:
            error_msg = f"Error searching for notulen: {str(e)}"
            self.stats['errors'].append(error_msg)
            print(f"  ✗ {error_msg}")
    
    def _log_ingestion(self, start_date: str, end_date: str):
        """Log the ingestion results to database"""
        self.storage.log_ingestion(
            date_range_start=start_date,
            date_range_end=end_date,
            meetings_found=self.stats['meetings_found'],
            meetings_inserted=self.stats['meetings_inserted'],
            meetings_updated=self.stats['meetings_updated'],
            documents_downloaded=self.stats['documents_downloaded'],
            errors='\n'.join(self.stats['errors']) if self.stats['errors'] else None
        )
    
    def _print_summary(self):
        """Print ingestion summary"""
        print(f"\n{'='*60}")
        print("INGESTION SUMMARY")
        print(f"{'='*60}")
        print(f"Meetings found:       {self.stats['meetings_found']}")
        print(f"Meetings inserted:    {self.stats['meetings_inserted']}")
        print(f"Meetings updated:     {self.stats['meetings_updated']}")
        print(f"Agenda items:         {self.stats['agenda_items']}")
        print(f"Documents downloaded: {self.stats['documents_downloaded']}")
        print(f"Errors:               {len(self.stats['errors'])}")
        
        if self.stats['errors']:
            print(f"\nError details:")
            for error in self.stats['errors'][:10]:  # Show first 10 errors
                print(f"  - {error}")
            if len(self.stats['errors']) > 10:
                print(f"  ... and {len(self.stats['errors']) - 10} more")
        
        print(f"{'='*60}\n")

async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='NeoDemos Data Ingestion - Fetch meetings from OpenRaadsinformatie'
    )
    parser.add_argument(
        '--start-date',
        type=str,
        default='2024-01-01',
        help='Start date (YYYY-MM-DD). Default: 2024-01-01'
    )
    parser.add_argument(
        '--end-date',
        type=str,
        default=datetime.now().strftime('%Y-%m-%d'),
        help='End date (YYYY-MM-DD). Default: today'
    )
    parser.add_argument(
        '--notulen',
        action='store_true',
        help='Include notulen (meeting minutes) in ingestion'
    )
    
    args = parser.parse_args()
    
    # Validate dates
    try:
        start = datetime.fromisoformat(args.start_date)
        end = datetime.fromisoformat(args.end_date)
        if start > end:
            print("Error: start-date must be before end-date")
            sys.exit(1)
    except ValueError as e:
        print(f"Error: Invalid date format. Use YYYY-MM-DD. {e}")
        sys.exit(1)
    
    # Run ingestion
    service = IngestionService()
    stats = await service.ingest_meetings(
        start_date=args.start_date,
        end_date=args.end_date,
        include_notulen=args.notulen
    )
    
    # Exit with appropriate code
    sys.exit(0 if not stats['errors'] else 1)

if __name__ == "__main__":
    asyncio.run(main())
