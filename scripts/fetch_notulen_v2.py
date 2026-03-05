#!/usr/bin/env python3
"""
Fetch Notulen for Gemeenteraad Meetings - Version 2
Direct search approach: queries for documents with "notulen" in name
"""

import asyncio
import httpx
import psycopg2
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.scraper import ScraperService
from services.storage import StorageService

class NotulenFetcherV2:
    """Direct notulen fetcher using document search"""
    
    BASE_URL = "https://api.openraadsinformatie.nl/v1/elastic"
    
    def __init__(self):
        self.scraper = ScraperService()
        self.storage = StorageService()
        self.stats = {
            'notulen_found': 0,
            'notulen_downloaded': 0,
            'errors': []
        }
    
    async def fetch_notulen(self):
        """Fetch notulen documents directly by searching for keyword"""
        try:
            print(f"\n{'='*70}")
            print(f"NeoDemos Notulen Fetcher V2 - Direct Search")
            print(f"{'='*70}\n")
            
            print("[1/2] Searching for notulen documents...")
            notulen_docs = await self._search_notulen_documents()
            
            self.stats['notulen_found'] = len(notulen_docs)
            print(f"✓ Found {len(notulen_docs)} notulen documents\n")
            
            if notulen_docs:
                print("[2/2] Downloading and storing notulen...")
                await self._store_notulen_documents(notulen_docs)
            
            self._print_summary()
            return self.stats
        
        except Exception as e:
            error_msg = f"Fatal error: {str(e)}"
            self.stats['errors'].append(error_msg)
            print(f"\n✗ {error_msg}")
            self._print_summary()
            raise
    
    async def _search_notulen_documents(self):
        """Search for documents with 'notulen' in name"""
        query = {
            "query": {
                "match": {
                    "name": "notulen"
                }
            },
            "size": 200
        }
        
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(f"{self.BASE_URL}/_search", json=query, timeout=30)
                response.raise_for_status()
                data = response.json()
                
                hits = data.get("hits", {}).get("hits", [])
                documents = []
                
                for hit in hits:
                    source = hit.get("_source", {})
                    # Only process MediaObjects (actual document files)
                    if source.get("@type") == "MediaObject":
                        documents.append({
                            "id": hit.get("_id"),
                            "name": source.get("name") or source.get("title"),
                            "url": source.get("original_url") or source.get("url"),
                            "date": source.get("date"),
                            "content_type": source.get("content_type")
                        })
                
                return documents
        
        except Exception as e:
            error_msg = f"Error searching for notulen: {str(e)}"
            self.stats['errors'].append(error_msg)
            return []
    
    async def _store_notulen_documents(self, notulen_docs):
        """Download and store notulen documents"""
        for idx, doc in enumerate(notulen_docs, 1):
            try:
                doc_id = doc.get('id')
                if not doc_id:
                    continue
                
                # Check if already exists
                if self.storage.document_exists(str(doc_id)):
                    print(f"  [{idx}/{len(notulen_docs)}] {doc.get('name')} (already exists)")
                    continue
                
                # Download content
                if not doc.get('url'):
                    print(f"  [{idx}/{len(notulen_docs)}] {doc.get('name')} (no URL)")
                    continue
                
                print(f"  [{idx}/{len(notulen_docs)}] Downloading: {doc.get('name')}...")
                
                full_content = await self.scraper.extract_text_from_url(doc['url'])
                if not full_content:
                    print(f"    ⚠️  Could not extract text")
                    continue
                
                # Preserve full notulen content (no truncation)
                preserved = self.scraper.preserve_notulen_text(full_content)
                
                doc_data = {
                    'id': str(doc_id),
                    'name': doc.get('name'),
                    'url': doc.get('url'),
                    'content': preserved,
                    'agenda_item_id': None,
                    'meeting_id': None
                }
                
                # Store in database
                if self.storage.insert_document(doc_data):
                    self._classify_as_notulen(str(doc_id))
                    self.stats['notulen_downloaded'] += 1
                    print(f"    ✓ Stored")
                else:
                    print(f"    ✗ Failed to store")
            
            except Exception as e:
                error_msg = f"Error storing notulen {doc.get('name')}: {str(e)}"
                self.stats['errors'].append(error_msg)
                print(f"    ✗ {error_msg}")
    
    def _classify_as_notulen(self, doc_id: str):
        """Mark document as notulen"""
        try:
            conn = psycopg2.connect(
                "postgresql://postgres:postgres@localhost:5432/neodemos"
            )
            cursor = conn.cursor()
            
            cursor.execute("""
                INSERT INTO document_classifications (document_id, is_notulen, document_type)
                VALUES (%s, TRUE, 'notulen')
                ON CONFLICT (document_id) DO UPDATE SET
                    is_notulen = TRUE,
                    document_type = 'notulen'
            """, (doc_id,))
            
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            self.stats['errors'].append(f"Classification error for {doc_id}: {str(e)}")
    
    def _print_summary(self):
        """Print summary"""
        print(f"\n{'='*70}")
        print("NOTULEN FETCH SUMMARY")
        print(f"{'='*70}")
        print(f"Documents found:    {self.stats['notulen_found']}")
        print(f"Documents stored:   {self.stats['notulen_downloaded']}")
        print(f"Errors:             {len(self.stats['errors'])}")
        
        if self.stats['errors']:
            print(f"\nFirst 3 errors:")
            for error in self.stats['errors'][:3]:
                print(f"  - {error}")
        
        print(f"{'='*70}\n")

async def main():
    """Main entry point"""
    fetcher = NotulenFetcherV2()
    stats = await fetcher.fetch_notulen()
    sys.exit(0 if not stats['errors'] else 1)

if __name__ == "__main__":
    asyncio.run(main())
