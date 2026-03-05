#!/usr/bin/env python3
"""
NeoDemos Comprehensive Historical Ingestion
==========================================
Ingests ALL meeting data from openraadsinformatie.nl from 2018 to present.
Runs year-by-year to handle API limits. Skips already-ingested data.
Run overnight: nohup python3 scripts/ingest_all_history.py > ingest_history.log 2>&1 &
"""

import asyncio
import sys
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from services.open_raad import OpenRaadService
from services.storage import StorageService
from services.scraper import ScraperService

class ComprehensiveHistoricalIngestor:
    def __init__(self):
        self.raad = OpenRaadService()
        self.storage = StorageService()
        self.scraper = ScraperService()
        self.global_stats = {
            "meetings_found": 0, "meetings_new": 0,
            "docs_downloaded": 0, "docs_skipped": 0, "errors": 0
        }

    async def ingest_year(self, year: int):
        """Ingest all meetings and documents for a given year."""
        start = f"{year}-01-01"
        end = f"{year}-12-31"
        print(f"\n{'='*60}")
        print(f"  YEAR: {year}")
        print(f"{'='*60}")

        # Fetch in quarterly chunks since OpenRaad API paginates at 500
        year_start = datetime(year, 1, 1)
        year_end = min(datetime(year, 12, 31), datetime.now())
        chunk = timedelta(days=90)  # ~3 months at a time
        current = year_start

        while current <= year_end:
            chunk_end = min(current + chunk, year_end)
            await self._ingest_period(
                current.strftime('%Y-%m-%d'),
                chunk_end.strftime('%Y-%m-%d')
            )
            current = chunk_end + timedelta(days=1)

    async def _ingest_period(self, start_date: str, end_date: str):
        """Ingest a specific date period."""
        print(f"\n  → Period: {start_date} to {end_date}")

        meetings = await self.raad.get_meetings(start_date=start_date, end_date=end_date)
        if not meetings:
            print("    No meetings found.")
            return

        print(f"    Found {len(meetings)} meetings")
        self.global_stats["meetings_found"] += len(meetings)

        for idx, meeting in enumerate(meetings, 1):
            meeting_id = meeting.get('id')
            meeting_name = meeting.get('name', 'Unknown')
            print(f"    [{idx}/{len(meetings)}] {meeting_name} ({meeting.get('start_date', '')[:10]})")

            try:
                self.storage.insert_meeting(meeting)
                if not self._meeting_already_fully_ingested(meeting_id):
                    self.global_stats["meetings_new"] += 1
                else:
                    print("      (already fully ingested, skipping docs)")
                    continue

                # Get all agenda items and docs
                details = await self.raad.get_meeting_details(meeting_id)
                for item in details.get('agenda', []):
                    item['meeting_id'] = meeting_id
                    self.storage.insert_agenda_item(item)

                    for doc in item.get('documents', []):
                        if self.storage.document_exists(doc['id']):
                            self.global_stats["docs_skipped"] += 1
                            continue

                        if doc.get('url'):
                            try:
                                text = await self.scraper.extract_text_from_url(doc['url'])
                                if text:
                                    # Store FULL content (no character limit) for proper RAG chunking
                                    doc['content'] = self.scraper.preserve_notulen_text(text)
                                    doc['agenda_item_id'] = item['id']
                                    doc['meeting_id'] = meeting_id
                                    self.storage.insert_document(doc)
                                    self.global_stats["docs_downloaded"] += 1
                            except Exception as e:
                                self.global_stats["errors"] += 1
                                print(f"      ✗ Doc download failed: {e}")

            except Exception as e:
                self.global_stats["errors"] += 1
                print(f"      ✗ Meeting failed: {e}")

    def _meeting_already_fully_ingested(self, meeting_id: str) -> bool:
        """Check if this meeting has at least one document already ingested."""
        try:
            with self.storage._get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM documents WHERE meeting_id = %s",
                        (meeting_id,)
                    )
                    count = cur.fetchone()[0]
                    return count > 0
        except Exception:
            return False

    def print_global_summary(self):
        print(f"\n{'='*60}")
        print("GLOBAL INGESTION SUMMARY")
        print(f"{'='*60}")
        print(f"Meetings found:      {self.global_stats['meetings_found']}")
        print(f"Meetings new:        {self.global_stats['meetings_new']}")
        print(f"Docs downloaded:     {self.global_stats['docs_downloaded']}")
        print(f"Docs skipped:        {self.global_stats['docs_skipped']}")
        print(f"Errors:              {self.global_stats['errors']}")
        print(f"{'='*60}\n")


async def main():
    ingestor = ComprehensiveHistoricalIngestor()
    
    start_year = 2018
    end_year = datetime.now().year

    print(f"Starting comprehensive ingestion: {start_year} - {end_year}")
    print(f"Started at: {datetime.now().isoformat()}")
    
    for year in range(start_year, end_year + 1):
        await ingestor.ingest_year(year)
        ingestor.print_global_summary()
        # Small pause between years to be gentle on the API
        await asyncio.sleep(2)

    print(f"Completed at: {datetime.now().isoformat()}")
    ingestor.print_global_summary()

if __name__ == "__main__":
    asyncio.run(main())
