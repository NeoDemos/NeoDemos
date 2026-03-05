#!/usr/bin/env python3
"""
Re-fetch existing notulen with full content (no truncation)
This script takes all existing notulen documents and re-downloads them
with full content preservation.
"""

import asyncio
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.scraper import ScraperService
from services.storage import StorageService
import psycopg2
from psycopg2.extras import RealDictCursor

class NotulenRefetcher:
    """Re-fetch notulen with full content"""
    
    def __init__(self):
        self.scraper = ScraperService()
        self.storage = StorageService()
        self.stats = {
            'total_notulen': 0,
            'refetched': 0,
            'failed': 0,
            'avg_length_before': 0,
            'avg_length_after': 0,
            'errors': []
        }
    
    async def refetch_all_notulen(self):
        """Re-fetch all existing notulen with full content"""
        try:
            print(f"\n{'='*70}")
            print(f"NeoDemos Notulen Refetcher - Full Content Preservation")
            print(f"{'='*70}\n")
            
            # Get all existing notulen
            print("[1/3] Fetching list of existing notulen...")
            notulen_docs = self._get_existing_notulen()
            self.stats['total_notulen'] = len(notulen_docs)
            print(f"✓ Found {len(notulen_docs)} existing notulen documents\n")
            
            if not notulen_docs:
                print("No notulen documents found to re-fetch")
                return self.stats
            
            # Calculate average length before
            total_length_before = sum(doc['content_length'] for doc in notulen_docs)
            self.stats['avg_length_before'] = total_length_before / len(notulen_docs) if notulen_docs else 0
            
            # Re-fetch with full content
            print("[2/3] Re-fetching notulen with full content...")
            await self._refetch_documents(notulen_docs)
            
            # Calculate average length after
            print("\n[3/3] Verifying refetched content...")
            self._verify_refetch()
            
            self._print_summary()
            return self.stats
        
        except Exception as e:
            error_msg = f"Fatal error: {str(e)}"
            self.stats['errors'].append(error_msg)
            print(f"\n✗ {error_msg}")
            self._print_summary()
            raise
    
    def _get_existing_notulen(self):
        """Get all existing notulen from database"""
        try:
            with self.storage._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT d.id, d.name, d.url, length(d.content) as content_length
                        FROM documents d
                        JOIN document_classifications dc ON d.id = dc.document_id
                        WHERE dc.is_notulen = TRUE
                        ORDER BY d.id
                    """)
                    
                    return [dict(row) for row in cur.fetchall()]
        
        except Exception as e:
            self.stats['errors'].append(f"Error fetching notulen list: {str(e)}")
            return []
    
    async def _refetch_documents(self, notulen_docs):
        """Re-fetch documents with full content"""
        for idx, doc in enumerate(notulen_docs, 1):
            try:
                doc_id = doc.get('id')
                if not doc_id or not doc.get('url'):
                    print(f"  [{idx}/{len(notulen_docs)}] {doc.get('name')} (no URL)")
                    continue
                
                print(f"  [{idx}/{len(notulen_docs)}] Re-fetching: {doc.get('name')[:50]}...", end=" ")
                
                # Download full content
                full_content = await self.scraper.extract_text_from_url(doc['url'])
                if not full_content:
                    print("⚠️  Could not extract text")
                    self.stats['failed'] += 1
                    continue
                
                # Preserve full content (no truncation)
                preserved = self.scraper.preserve_notulen_text(full_content)
                
                # Update in database
                if self._update_document_content(doc_id, preserved):
                    old_length = doc.get('content_length', 0)
                    new_length = len(preserved)
                    self.stats['refetched'] += 1
                    print(f"✓ ({old_length} → {new_length} chars)")
                else:
                    print("✗ Failed to update")
                    self.stats['failed'] += 1
            
            except Exception as e:
                error_msg = f"Error re-fetching {doc.get('name')}: {str(e)}"
                self.stats['errors'].append(error_msg)
                print(f"✗ {error_msg[:50]}")
                self.stats['failed'] += 1
    
    def _update_document_content(self, doc_id: str, content: str) -> bool:
        """Update document content in database"""
        try:
            with self.storage._get_connection() as conn:
                with conn.cursor() as cur:
                    # Clean NUL characters
                    content = content.replace('\x00', '')
                    
                    cur.execute("""
                        UPDATE documents
                        SET content = %s
                        WHERE id = %s
                    """, (content, str(doc_id)))
                    
                    return cur.rowcount > 0
        
        except Exception as e:
            self.stats['errors'].append(f"Update error for {doc_id}: {str(e)}")
            return False
    
    def _verify_refetch(self):
        """Verify refetch results"""
        try:
            with self.storage._get_connection() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("""
                        SELECT AVG(length(content)) as avg_length,
                               MAX(length(content)) as max_length,
                               MIN(length(content)) as min_length,
                               COUNT(*) as total
                        FROM documents d
                        JOIN document_classifications dc ON d.id = dc.document_id
                        WHERE dc.is_notulen = TRUE
                    """)
                    
                    result = cur.fetchone()
                    if result:
                        self.stats['avg_length_after'] = result['avg_length'] or 0
                        print(f"  Average content length: {int(self.stats['avg_length_after'])} chars")
                        print(f"  Max length: {result['max_length']} chars")
                        print(f"  Min length: {result['min_length']} chars")
        
        except Exception as e:
            self.stats['errors'].append(f"Verification error: {str(e)}")
    
    def _print_summary(self):
        """Print summary"""
        print(f"\n{'='*70}")
        print("NOTULEN REFETCH SUMMARY")
        print(f"{'='*70}")
        print(f"Total notulen processed:    {self.stats['total_notulen']}")
        print(f"Successfully refetched:     {self.stats['refetched']}")
        print(f"Failed:                     {self.stats['failed']}")
        
        if self.stats['avg_length_before'] > 0:
            print(f"\nContent Length Change:")
            print(f"  Before: avg {int(self.stats['avg_length_before'])} chars")
            print(f"  After:  avg {int(self.stats['avg_length_after'])} chars")
            avg_before = float(self.stats['avg_length_before'])
            avg_after = float(self.stats['avg_length_after'])
            improvement = ((avg_after - avg_before) / avg_before * 100)
            print(f"  Change: {improvement:+.1f}%")
        
        if self.stats['errors']:
            print(f"\nErrors ({len(self.stats['errors'])}):")
            for error in self.stats['errors'][:5]:
                print(f"  - {error[:60]}")
            if len(self.stats['errors']) > 5:
                print(f"  ... and {len(self.stats['errors']) - 5} more")
        
        print(f"{'='*70}\n")

async def main():
    """Main entry point"""
    refetcher = NotulenRefetcher()
    stats = await refetcher.refetch_all_notulen()
    sys.exit(0 if stats['failed'] == 0 else 1)

if __name__ == "__main__":
    asyncio.run(main())
