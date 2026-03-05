#!/usr/bin/env python3
"""
Fetch ALL 2024 Rotterdam Gemeenteraad & Committee Notulen from ORI API
======================================================================
Focuses on notulen ONLY (no other agenda items to speed up process).
Downloads full content with no truncation.
Links to meetings where applicable.

Progress bar: Shows real-time stats during fetch
"""

import asyncio
import sys
import os
import json
from datetime import datetime
from typing import List, Dict, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.open_raad import OpenRaadService
from services.storage import StorageService
from services.scraper import ScraperService
import psycopg2
from psycopg2.extras import RealDictCursor

# Progress tracking
class ProgressTracker:
    def __init__(self):
        self.total_meetings = 0
        self.meetings_processed = 0
        self.notulen_found = 0
        self.notulen_downloaded = 0
        self.notulen_already_exists = 0
        self.errors = 0
        self.start_time = datetime.now()
    
    def progress_bar(self):
        if self.total_meetings > 0:
            pct = int((self.meetings_processed / self.total_meetings) * 100)
            bar_len = 40
            filled = int((pct / 100) * bar_len)
            bar = '█' * filled + '░' * (bar_len - filled)
            elapsed = (datetime.now() - self.start_time).total_seconds()
            rate = self.meetings_processed / elapsed if elapsed > 0 else 0
            eta = (self.total_meetings - self.meetings_processed) / rate if rate > 0 else 0
            
            print(f"\n[{bar}] {pct:3d}% | {self.meetings_processed}/{self.total_meetings} meetings | "
                  f"Notulen: {self.notulen_downloaded} ✓ {self.notulen_already_exists} ∃ {self.errors} ✗ | "
                  f"Rate: {rate:.1f}/s | ETA: {int(eta)}s")

async def fetch_all_2024_notulen():
    """Fetch all 2024 notulen for Gemeenteraad and committees"""
    
    print("=" * 80)
    print("FETCH ALL 2024 ROTTERDAM NOTULEN")
    print("=" * 80)
    
    raad_service = OpenRaadService()
    storage = StorageService()
    scraper = ScraperService()
    progress = ProgressTracker()
    
    try:
        # Step 1: Get all 2024 Gemeenteraad meetings
        print("\n[1/3] Fetching all 2024 Gemeenteraad meetings...")
        gr_meetings = await raad_service.search_meetings(
            query="*",
            filters={
                "index": "ori_rotterdam_20250629013104",
                "start_date": "2024-01-01T00:00:00",
                "end_date": "2024-12-31T23:59:59",
                "committee": "Gemeenteraad"
            }
        )
        print(f"✓ Found {len(gr_meetings)} Gemeenteraad meetings")
        progress.total_meetings += len(gr_meetings)
        
        # Step 2: Process each meeting, extract notulen
        print("\n[2/3] Downloading notulen with full content...")
        print("(Progress bar updates every 5 meetings)")
        
        notulen_by_id = {}  # Track unique notulen by ORI document ID
        
        for idx, meeting in enumerate(gr_meetings):
            progress.meetings_processed = idx + 1
            
            try:
                # Get meeting details (includes agenda items with attachments)
                meeting_details = await raad_service.get_meeting_details(meeting['id'])
                
                if not meeting_details.get('agenda'):
                    continue
                
                # Extract all notulen from this meeting's agenda items
                for agenda_item in meeting_details['agenda']:
                    if not agenda_item.get('documents'):
                        continue
                    
                    for doc in agenda_item['documents']:
                        doc_name = doc.get('name', '').lower()
                        
                        # Identify notulen by name pattern
                        if 'notulen' in doc_name and 'raadsvergadering' in doc_name:
                            doc_id = doc.get('id')
                            
                            # Skip if we've already seen this notulen
                            if doc_id in notulen_by_id:
                                progress.notulen_already_exists += 1
                                continue
                            
                            progress.notulen_found += 1
                            notulen_by_id[doc_id] = doc
                            
                            # Download full content
                            try:
                                url = doc.get('url')
                                if url:
                                    content = scraper.extract_text_from_url(url)
                                    
                                    # Store in database with full content (no truncation)
                                    with storage._get_connection() as conn:
                                        with conn.cursor() as cur:
                                            # Insert document
                                            cur.execute("""
                                                INSERT INTO documents (id, name, url, content, meeting_id, agenda_item_id)
                                                VALUES (%s, %s, %s, %s, %s, %s)
                                                ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content
                                            """, (
                                                doc_id,
                                                doc.get('name'),
                                                url,
                                                content,
                                                meeting['id'],
                                                agenda_item['id']
                                            ))
                                            
                                            # Classify as notulen
                                            cur.execute("""
                                                INSERT INTO document_classifications (document_id, is_notulen, meeting_id, extraction_status)
                                                VALUES (%s, %s, %s, %s)
                                                ON CONFLICT (document_id) DO UPDATE SET is_notulen = TRUE
                                            """, (
                                                doc_id,
                                                True,
                                                meeting['id'],
                                                'pending'
                                            ))
                                            
                                            conn.commit()
                                    
                                    progress.notulen_downloaded += 1
                            except Exception as e:
                                progress.errors += 1
                                print(f"  Error downloading {doc_id}: {e}")
            
            except Exception as e:
                progress.errors += 1
                print(f"  Error processing meeting {meeting['id']}: {e}")
            
            # Show progress every 5 meetings
            if (idx + 1) % 5 == 0:
                progress.progress_bar()
        
        progress.progress_bar()
        
        # Step 3: Verify data quality
        print("\n[3/3] Verifying data quality...")
        with storage._get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Count linked Gemeenteraad notulen
                cur.execute("""
                    SELECT COUNT(*) as total, 
                           AVG(LENGTH(content)) as avg_len,
                           MIN(LENGTH(content)) as min_len,
                           MAX(LENGTH(content)) as max_len
                    FROM documents d
                    JOIN document_classifications dc ON d.id = dc.document_id
                    JOIN meetings m ON d.meeting_id = m.id
                    WHERE dc.is_notulen = TRUE AND m.name = 'Gemeenteraad'
                """)
                stats = cur.fetchone()
                
                if stats:
                    print(f"✓ Total linked Gemeenteraad notulen: {stats['total']}")
                    print(f"✓ Average content length: {int(stats['avg_len'] or 0):,} chars")
                    print(f"✓ Min/Max: {int(stats['min_len'] or 0):,} / {int(stats['max_len'] or 0):,} chars")
        
        # Results
        print("\n" + "=" * 80)
        print("RESULTS")
        print("=" * 80)
        print(f"Meetings processed: {progress.meetings_processed}")
        print(f"Unique notulen found: {progress.notulen_found}")
        print(f"Notulen downloaded: {progress.notulen_downloaded} ✓")
        print(f"Already existed: {progress.notulen_already_exists} ∃")
        print(f"Errors: {progress.errors} ✗")
        print("=" * 80)
        
        return progress.notulen_downloaded == progress.notulen_found
    
    except Exception as e:
        print(f"✗ Fatal error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(fetch_all_2024_notulen())
    sys.exit(0 if success else 1)
