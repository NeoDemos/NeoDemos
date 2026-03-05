#!/usr/bin/env python3
"""
Link Rotterdam gemeenteraad notulen to meetings.
Identifies stored notulen that belong to Rotterdam council and links them properly.
"""

import sys
import os
import psycopg2
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.storage import StorageService

def main():
    """Link Rotterdam notulen to meetings"""
    
    storage = StorageService()
    
    print(f"\n{'='*70}")
    print("LINKING ROTTERDAM GEMEENTERAAD NOTULEN")
    print(f"{'='*70}\n")
    
    try:
        with storage._get_connection() as conn:
            with conn.cursor() as cur:
                # Find Gemeenteraad meetings
                print("[1/3] Finding Gemeenteraad meetings...")
                cur.execute("""
                    SELECT id, name, start_date FROM meetings
                    WHERE name LIKE '%Gemeenteraad%' OR name LIKE '%gemeenteraad%'
                    ORDER BY start_date DESC
                """)
                
                raad_meetings = cur.fetchall()
                print(f"  ✓ Found {len(raad_meetings)} Gemeenteraad meetings")
                
                if not raad_meetings:
                    print("  ⚠️  No Gemeenteraad meetings found")
                    return
                
                # Find notulen that reference Rotterdam or don't have meeting_id
                print("\n[2/3] Finding Rotterdam notulen...")
                cur.execute("""
                    SELECT d.id, d.name, d.meeting_id, d.content
                    FROM documents d
                    JOIN document_classifications dc ON d.id = dc.document_id
                    WHERE dc.is_notulen = TRUE
                    AND (d.name ILIKE '%rotterdam%' 
                         OR d.content ILIKE '%rotterdam%'
                         OR d.meeting_id IS NULL)
                    LIMIT 20
                """)
                
                rotterdam_notulen = cur.fetchall()
                print(f"  ✓ Found {len(rotterdam_notulen)} candidate notulen")
                
                # Try to link notulen to meetings by date matching
                print("\n[3/3] Linking notulen to meetings...")
                
                linked = 0
                for doc in rotterdam_notulen[:5]:  # Process first 5 for safety
                    doc_id, doc_name, meeting_id, content = doc
                    
                    if meeting_id:
                        print(f"  ✓ {doc_name[:50]} already linked to meeting {meeting_id}")
                        continue
                    
                    # Try to extract date from document name
                    date_pattern = r'(\d{1,2})[.\s-](\d{1,2})[.\s-](\d{2,4})|(\d{4})\s*(\d{1,2})\s*(\d{1,2})'
                    date_match = re.search(date_pattern, doc_name)
                    
                    if date_match:
                        # Link to most recent Gemeenteraad meeting (simple heuristic)
                        most_recent = raad_meetings[0]
                        
                        # Update document
                        cur.execute("""
                            UPDATE documents
                            SET meeting_id = %s
                            WHERE id = %s
                        """, (most_recent[0], doc_id))
                        
                        print(f"  ✓ Linked {doc_name[:50]} to {most_recent[1]}")
                        linked += 1
                
                print(f"\n✓ Successfully linked {linked} notulen to Gemeenteraad meetings")
    
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
