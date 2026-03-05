#!/usr/bin/env python3
"""
Link notulen documents to Gemeenteraad meetings by date matching.
This is critical for multi-source profile extraction.
"""

import sys
import os
import psycopg2
import re
from datetime import datetime, timedelta
from typing import Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.storage import StorageService

class NotulenLinker:
    """Link notulen to Gemeenteraad meetings"""
    
    def __init__(self):
        self.storage = StorageService()
        self.stats = {
            'total': 0,
            'linked': 0,
            'skipped': 0,
            'errors': []
        }
    
    def link_all_notulen(self):
        """Link all unlinked notulen to Gemeenteraad meetings"""
        
        print(f"\n{'='*70}")
        print("LINKING NOTULEN TO GEMEENTERAAD MEETINGS")
        print(f"{'='*70}\n")
        
        try:
            # Get all Gemeenteraad meetings with dates
            with self.storage._get_connection() as conn:
                with conn.cursor() as cur:
                    print("[1/3] Loading Gemeenteraad meetings...")
                    cur.execute("""
                        SELECT id, name, start_date FROM meetings
                        WHERE name = 'Gemeenteraad'
                        ORDER BY start_date DESC
                    """)
                    
                    raad_meetings = cur.fetchall()
                    print(f"  ✓ Found {len(raad_meetings)} Gemeenteraad meetings")
                    
                    for id, name, date in raad_meetings:
                        print(f"    - {date.date()}: {name}")
                    
                    # Get all notulen without meeting_id
                    print("\n[2/3] Loading unlinked notulen...")
                    cur.execute("""
                        SELECT d.id, d.name
                        FROM documents d
                        JOIN document_classifications dc ON d.id = dc.document_id
                        WHERE dc.is_notulen = TRUE
                        AND d.meeting_id IS NULL
                        ORDER BY d.name
                    """)
                    
                    notulen_list = cur.fetchall()
                    self.stats['total'] = len(notulen_list)
                    print(f"  ✓ Found {len(notulen_list)} unlinked notulen")
                    
                    # Link each notulen to closest meeting
                    print("\n[3/3] Linking notulen to meetings...")
                    
                    for doc_id, doc_name in notulen_list:
                        # Extract date from document name
                        extracted_date = self._extract_date_from_name(doc_name)
                        
                        if not extracted_date:
                            self.stats['skipped'] += 1
                            continue
                        
                        # Find closest Gemeenteraad meeting
                        closest_meeting = self._find_closest_meeting(extracted_date, raad_meetings)
                        
                        if closest_meeting:
                            meeting_id, meeting_date = closest_meeting
                            
                            # Update document
                            cur.execute("""
                                UPDATE documents SET meeting_id = %s WHERE id = %s
                            """, (meeting_id, doc_id))
                            
                            date_diff = abs((extracted_date - meeting_date.date()).days)
                            print(f"  ✓ {doc_name[:60]:60s} → {meeting_date.date()} ({date_diff}d)")
                            self.stats['linked'] += 1
                        else:
                            self.stats['skipped'] += 1
            
            self._print_summary()
            return self.stats
        
        except Exception as e:
            print(f"✗ Error: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    
    def _extract_date_from_name(self, name: str) -> Optional[datetime]:
        """Extract date from notulen document name"""
        
        # Pattern 1: DD-MM-YYYY or DD.MM.YYYY
        match = re.search(r'(\d{1,2})[-./](\d{1,2})[-./](\d{4})', name)
        if match:
            try:
                day, month, year = int(match.group(1)), int(match.group(2)), int(match.group(3))
                return datetime(year, month, day).date()
            except ValueError:
                pass
        
        # Pattern 2: YYYY-MM-DD
        match = re.search(r'(\d{4})[-](\d{1,2})[-](\d{1,2})', name)
        if match:
            try:
                year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
                return datetime(year, month, day).date()
            except ValueError:
                pass
        
        # Pattern 3: DDMMMYYYY (e.g., 15sep2023)
        match = re.search(r'(\d{1,2})(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)(\d{4})', name.lower())
        if match:
            try:
                day = int(match.group(1))
                month_name = match.group(2)
                year = int(match.group(3))
                month = {
                    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
                    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
                }[month_name]
                return datetime(year, month, day).date()
            except (ValueError, KeyError):
                pass
        
        return None
    
    def _find_closest_meeting(self, notulen_date, meetings) -> Optional[Tuple[str, datetime]]:
        """Find closest Gemeenteraad meeting to notulen date"""
        
        closest = None
        min_diff = timedelta(days=365)  # Max 1 year difference
        
        for meeting_id, _, meeting_datetime in meetings:
            meeting_date = meeting_datetime.date() if hasattr(meeting_datetime, 'date') else meeting_datetime
            diff = abs((notulen_date - meeting_date).days)
            
            if diff < min_diff.days:
                min_diff = timedelta(days=diff)
                closest = (meeting_id, meeting_datetime)
        
        return closest
    
    def _print_summary(self):
        """Print linking summary"""
        print(f"\n{'='*70}")
        print("LINKING SUMMARY")
        print(f"{'='*70}")
        print(f"Total notulen processed:  {self.stats['total']}")
        print(f"Successfully linked:      {self.stats['linked']}")
        print(f"Skipped (no date found):  {self.stats['skipped']}")
        print(f"{'='*70}\n")

def main():
    linker = NotulenLinker()
    linker.link_all_notulen()

if __name__ == "__main__":
    main()
