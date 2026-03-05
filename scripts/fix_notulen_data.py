#!/usr/bin/env python3
"""
Fix Notulen Data - NeoDemos Data Quality Repair Script

This script fixes critical data quality issues:
1. Unlinks non-target-city notulen from target-city meetings
2. Re-fetches truncated notulen with full content
3. Searches ORI API for additional target-city notulen
4. Reports on cleaned data state

Architecture: City-agnostic. Configure via NEODEMOS_CITY env var or --city argument.

Run from project root: 
  python scripts/fix_notulen_data.py                    # Uses NEODEMOS_CITY or defaults to 'rotterdam'
  python scripts/fix_notulen_data.py --city amsterdam   # Explicit city
"""

import asyncio
import sys
import os
import json
import argparse
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from psycopg2.extras import RealDictCursor
from services.scraper import ScraperService
from services.open_raad import OpenRaadService


class CityConfig:
    """
    Configuration for different Dutch cities.
    
    Extend this dict to support new cities:
    - city_name: Official Dutch city name
    - keywords: List of keywords found in notulen/meeting names
    - mayors: List of known mayors (for period-specific recognition)
    - committees: List of typical committee names
    - ori_index: ORI API index name (if different from default)
    """
    
    CITIES = {
        'rotterdam': {
            'official_name': 'Rotterdam',
            'keywords': ['rotterdam', 'stationsplein'],
            'mayors': ['Aboutaleb', 'Schouten'],
            'committees': [
                'Gemeenteraad',
                'Commissie Mobiliteit',
                'Commissie Zorg',
                'Commissie Wonen',
                'Commissie Bestuur',
                'Commissie Economie',
                'Commissie Onderwijs',
                'Commissie Samenleven'
            ],
            'ori_index': 'ori_rotterdam_20250629013104',
            'known_wrong_docs': [
                '216305', '230325', '230725', '236218',  # Amsterdam
                '1301595',                                 # Steenbergen
                '3302027', '3304347',                      # Spijkenisse
                '3416055',                                 # Nuenen
                '3909950',                                 # Badhoevedorp
                '4852336',                                 # Voorne aan Zee
                '5394161'                                  # Zuidplas
            ]
        },
        'amsterdam': {
            'official_name': 'Amsterdam',
            'keywords': ['amsterdam', 'gemeente amsterdam'],
            'mayors': ['Femke van den Driessche'],
            'committees': ['Gemeenteraad'],
            'ori_index': 'ori_amsterdam_20250629013104',
            'known_wrong_docs': []
        },
        'den_haag': {
            'official_name': 'Den Haag',
            'keywords': ['den haag', 'the hague'],
            'mayors': ['Pauline Krikke'],
            'committees': ['Gemeenteraad'],
            'ori_index': 'ori_den_haag_20250629013104',
            'known_wrong_docs': []
        }
    }
    
    @classmethod
    def get(cls, city_name):
        """Get config for a city, raising error if not found"""
        city_key = city_name.lower().replace(' ', '_')
        if city_key not in cls.CITIES:
            available = ', '.join(cls.CITIES.keys())
            raise ValueError(f"Unknown city '{city_name}'. Available: {available}")
        return cls.CITIES[city_key]
    
    @classmethod
    def is_city_content(cls, text, city_name):
        """
        Detect if text content is from a specific city.
        Returns confidence 0.0-1.0
        """
        if not text:
            return 0.0
        
        config = cls.get(city_name)
        text_lower = text.lower()
        
        # Check keywords
        keyword_matches = sum(1 for kw in config['keywords'] if kw in text_lower)
        keyword_score = min(1.0, keyword_matches * 0.3)
        
        # Check mayors
        mayor_matches = sum(1 for m in config['mayors'] if m.lower() in text_lower)
        mayor_score = min(1.0, mayor_matches * 0.5)
        
        # Check committees
        committee_matches = sum(1 for c in config['committees'] if c.lower() in text_lower)
        committee_score = min(1.0, committee_matches * 0.2)
        
        # Combined confidence (mayor is strongest signal)
        confidence = max(keyword_score, mayor_score, committee_score)
        return confidence


class NotulenDataFixer:
    """Fix notulen data quality issues for a specific city"""
    
    def __init__(self, city_name='rotterdam'):
        self.city_name = city_name.lower()
        self.city_config = CityConfig.get(self.city_name)
        
        self.db_config = {
            'host': os.getenv('DB_HOST', 'localhost'),
            'port': int(os.getenv('DB_PORT', '5432')),
            'dbname': os.getenv('DB_NAME', 'neodemos'),
            'user': os.getenv('DB_USER', 'postgres'),
            'password': os.getenv('DB_PASSWORD', 'postgres')
        }
        self.scraper = ScraperService()
        self.ori = OpenRaadService()
        # Override ORI index if city-specific
        if self.city_config.get('ori_index'):
            self.ori.INDEX = self.city_config['ori_index']
        
        self.stats = {
            'city': self.city_name,
            'non_target_unlinked': 0,
            'target_refetched': 0,
            'new_notulen_found': 0,
            'new_notulen_ingested': 0,
            'errors': []
        }
    
    def _get_connection(self):
        return psycopg2.connect(**self.db_config)
    
    async def run_full_fix(self):
        """Run the complete data fix pipeline for the configured city"""
        
        print("\n" + "=" * 70)
        print(f"NeoDemos Notulen Data Repair - {self.city_config['official_name']}")
        print("=" * 70 + "\n")
        
        # Step 1: Identify and unlink non-target-city notulen
        print(f"[STEP 1/4] Identifying and unlinking non-{self.city_name} notulen...")
        await self._unlink_non_target_city_notulen()
        
        # Step 2: Re-fetch truncated target-city notulen
        print(f"\n[STEP 2/4] Re-fetching truncated {self.city_name} notulen with full content...")
        await self._refetch_truncated_target_notulen()
        
        # Step 3: Search ORI API for additional target-city notulen
        print(f"\n[STEP 3/4] Searching ORI API for additional {self.city_name} notulen...")
        await self._fetch_new_target_notulen()
        
        # Step 4: Verify and report
        print(f"\n[STEP 4/4] Verifying cleaned data...")
        await self._verify_and_report()
        
        self._print_summary()
        return self.stats
    
    async def _unlink_non_target_city_notulen(self):
        """Unlink notulen from other cities that were incorrectly linked to target-city meetings"""
        
        non_target_ids = self.city_config.get('known_wrong_docs', [])
        
        if not non_target_ids:
            print(f"  No known non-{self.city_name} documents to unlink")
            return
        
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            
            for doc_id in non_target_ids:
                cur.execute(
                    "UPDATE documents SET meeting_id = NULL WHERE id = %s AND meeting_id IS NOT NULL",
                    (doc_id,)
                )
                if cur.rowcount > 0:
                    self.stats['non_target_unlinked'] += 1
                    print(f"  Unlinked doc {doc_id}")
            
            conn.commit()
            print(f"  Total unlinked: {self.stats['non_target_unlinked']} non-{self.city_name} notulen")
            cur.close()
            conn.close()
            
        except Exception as e:
            error = f"Error unlinking non-{self.city_name} notulen: {e}"
            self.stats['errors'].append(error)
            print(f"  ERROR: {error}")
    
    async def _refetch_truncated_target_notulen(self):
        """Re-fetch target-city notulen that were truncated to 15000 chars"""
        
        conn = self._get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Find truncated notulen linked to meetings (not arbitrary, but by actual detection)
        try:
            cur.execute("""
                SELECT d.id, d.name, d.url, LENGTH(d.content) as content_length, m.start_date
                FROM documents d
                JOIN meetings m ON d.meeting_id = m.id
                WHERE LOWER(d.name) LIKE '%notulen%'
                AND LENGTH(d.content) = 15000
                ORDER BY m.start_date DESC
            """)
            truncated_docs = cur.fetchall()
            
            print(f"  Found {len(truncated_docs)} truncated notulen to re-fetch")
            
            for doc in truncated_docs:
                try:
                    doc_id = doc['id']
                    url = doc.get('url')
                    
                    if not url:
                        print(f"  Doc {doc_id}: no URL, skipping")
                        continue
                    
                    old_length = doc.get('content_length', 0)
                    print(f"  Re-fetching {doc_id} ({doc['name'][:50]}), {old_length} chars...", end=" ")
                    
                    # Download full content
                    full_content = await self.scraper.extract_text_from_url(url)
                    
                    if not full_content:
                        print("FAILED (no content)")
                        self.stats['errors'].append(f"Failed to re-fetch doc {doc_id}")
                        continue
                    
                    # Preserve full content (no truncation)
                    preserved = self.scraper.preserve_notulen_text(full_content)
                    
                    # Verify it's actually target-city content
                    confidence = CityConfig.is_city_content(preserved, self.city_name)
                    if confidence < 0.3:
                        print(f"SKIP (not {self.city_name} content, confidence {confidence:.1%})")
                        # Unlink if it's not actually target-city
                        cur.execute("UPDATE documents SET meeting_id = NULL WHERE id = %s", (doc_id,))
                        conn.commit()
                        continue
                    
                    new_length = len(preserved)
                    
                    # Clean NUL characters
                    preserved = preserved.replace('\x00', '')
                    
                    # Update in database
                    cur2 = conn.cursor()
                    cur2.execute("UPDATE documents SET content = %s WHERE id = %s", (preserved, doc_id))
                    conn.commit()
                    cur2.close()
                    
                    self.stats['target_refetched'] += 1
                    improvement = ((new_length - old_length) / max(old_length, 1)) * 100
                    print(f"OK ({old_length} -> {new_length} chars, {improvement:+.0f}%)")
                    
                except Exception as e:
                    error = f"Error re-fetching doc {doc_id}: {e}"
                    self.stats['errors'].append(error)
                    print(f"ERROR: {error}")
            
        except Exception as e:
            error = f"Error querying truncated documents: {e}"
            self.stats['errors'].append(error)
            print(f"  ERROR: {error}")
        
        finally:
            cur.close()
            conn.close()
    
    async def _fetch_new_target_notulen(self):
        """Search ORI API for additional target-city notulen we might be missing"""
        
        try:
            import httpx
            
            # Search for notulen documents from ORI index
            query = {
                "query": {
                    "bool": {
                        "must": [
                            {"term": {"_index": self.ori.INDEX}},
                            {"match": {"name": "notulen"}},
                        ]
                    }
                },
                "size": 100,
                "sort": [{"date": {"order": "desc", "unmapped_type": "date"}}]
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.ori.BASE_URL}/_search",
                    json=query
                )
                response.raise_for_status()
                data = response.json()
            
            hits = data.get("hits", {}).get("hits", [])
            print(f"  Found {len(hits)} notulen-type documents in ORI {self.city_name} index")
            
            # Get existing doc IDs from our DB
            conn = self._get_connection()
            cur = conn.cursor(cursor_factory=RealDictCursor)
            cur.execute("SELECT id FROM documents WHERE LOWER(name) LIKE '%notulen%'")
            existing_ids = {row['id'] for row in cur.fetchall()}
            
            new_docs = []
            for hit in hits:
                doc_id = hit.get("_id")
                if doc_id not in existing_ids:
                    source = hit.get("_source", {})
                    new_docs.append({
                        'id': doc_id,
                        'name': source.get('name', 'Unknown'),
                        'url': source.get('original_url') or source.get('url'),
                        'date': source.get('date')
                    })
            
            self.stats['new_notulen_found'] = len(new_docs)
            print(f"  New notulen not yet in DB: {len(new_docs)}")
            
            # Ingest new notulen
            for doc in new_docs:
                try:
                    if not doc.get('url'):
                        print(f"    Skipping {doc['id']} (no URL)")
                        continue
                    
                    print(f"    Fetching {doc['name'][:50]}...", end=" ")
                    
                    content = await self.scraper.extract_text_from_url(doc['url'])
                    if not content:
                        print("FAILED")
                        continue
                    
                    # Check if it's actually target-city content
                    confidence = CityConfig.is_city_content(content, self.city_name)
                    if confidence < 0.3:
                        print(f"SKIP (not {self.city_name}, confidence {confidence:.1%})")
                        continue
                    
                    # Preserve full content
                    preserved = self.scraper.preserve_notulen_text(content)
                    preserved = preserved.replace('\x00', '')
                    
                    # Find a matching meeting to link to (by date)
                    meeting_id = None
                    if doc.get('date'):
                        # Try to find a Gemeenteraad meeting on this date
                        date_prefix = doc['date'][:10] if doc['date'] else None
                        if date_prefix:
                            cur.execute(
                                "SELECT id FROM meetings WHERE CAST(start_date AS TEXT) LIKE %s AND name LIKE '%Gemeenteraad%' LIMIT 1",
                                (f"{date_prefix}%",)
                            )
                            row = cur.fetchone()
                            if row:
                                meeting_id = row['id']
                    
                    # Insert into database
                    cur.execute("""
                        INSERT INTO documents (id, name, url, content, meeting_id)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content, meeting_id = EXCLUDED.meeting_id
                    """, (doc['id'], doc['name'], doc['url'], preserved, meeting_id))
                    conn.commit()
                    
                    self.stats['new_notulen_ingested'] += 1
                    linked = f"linked to meeting {meeting_id}" if meeting_id else "unlinked"
                    print(f"OK ({len(preserved)} chars, {linked})")
                    
                except Exception as e:
                    print(f"ERROR: {e}")
                    self.stats['errors'].append(f"Error ingesting {doc['id']}: {e}")
            
            cur.close()
            conn.close()
            
        except Exception as e:
            error = f"Error searching ORI API: {e}"
            self.stats['errors'].append(error)
            print(f"  ERROR: {error}")
    
    async def _verify_and_report(self):
        """Verify the cleaned data state"""
        
        conn = self._get_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        
        # Count target-city notulen now linked to meetings
        cur.execute("""
            SELECT d.id, d.name, LENGTH(d.content) as content_length, m.start_date, m.name as meeting_name,
                LEFT(d.content, 200) as preview
            FROM documents d
            JOIN meetings m ON d.meeting_id = m.id
            WHERE LOWER(d.name) LIKE '%notulen%'
            ORDER BY m.start_date DESC
        """)
        linked = cur.fetchall()
        
        print(f"\n  Notulen linked to meetings: {len(linked)}")
        
        total_gl_mentions = 0
        total_pvda_mentions = 0
        total_chars = 0
        target_city_count = 0
        
        for doc in linked:
            # Check if actually target-city content
            preview = (doc['preview'] or '').replace('\n', ' ')
            confidence = CityConfig.is_city_content(preview, self.city_name)
            is_target = confidence >= 0.3
            
            if is_target:
                target_city_count += 1
                
                # Count GL-PvdA mentions
                cur.execute("""
                    SELECT 
                        (LENGTH(content) - LENGTH(REPLACE(LOWER(content), 'groenlinks', ''))) / LENGTH('groenlinks') as gl,
                        (LENGTH(content) - LENGTH(REPLACE(LOWER(content), 'pvda', ''))) / LENGTH('pvda') as pvda
                    FROM documents WHERE id = %s
                """, (doc['id'],))
                mentions = cur.fetchone()
                gl = mentions['gl'] or 0
                pvda = mentions['pvda'] or 0
                total_gl_mentions += gl
                total_pvda_mentions += pvda
                total_chars += doc['content_length']
                
                preview_short = preview[:80]
                print(f"    ✓ Doc {doc['id']} | {doc['start_date']} | {doc['content_length']:>7} chars | GL={gl} PvdA={pvda}")
            else:
                marker = "OTHER"
                print(f"    [{marker}] Doc {doc['id']} (not {self.city_name}, will be unlinked)")
                cur.execute("UPDATE documents SET meeting_id = NULL WHERE id = %s", (doc['id'],))
                conn.commit()
        
        print(f"\n  CLEANED DATA SUMMARY:")
        print(f"    Target-city notulen linked: {target_city_count}")
        print(f"    Total content: {total_chars:,} chars")
        print(f"    GroenLinks mentions: {total_gl_mentions}")
        print(f"    PvdA mentions: {total_pvda_mentions}")
        print(f"    Combined GL-PvdA mentions: {total_gl_mentions + total_pvda_mentions}")
        
        cur.close()
        conn.close()
    
    def _print_summary(self):
        """Print final summary"""
        
        print("\n" + "=" * 70)
        print("DATA REPAIR SUMMARY")
        print("=" * 70)
        print(f"  City: {self.city_config['official_name']}")
        print(f"  Non-target city notulen unlinked: {self.stats['non_target_unlinked']}")
        print(f"  Target city notulen refetched:    {self.stats['target_refetched']}")
        print(f"  New notulen found in ORI:         {self.stats['new_notulen_found']}")
        print(f"  New notulen ingested:             {self.stats['new_notulen_ingested']}")
        
        if self.stats['errors']:
            print(f"\n  Errors ({len(self.stats['errors'])}):")
            for err in self.stats['errors'][:10]:
                print(f"    - {err[:80]}")
        else:
            print(f"\n  ✓ No errors!")
        
        print("=" * 70 + "\n")


async def main():
    from dotenv import load_dotenv
    load_dotenv()
    
    # Parse arguments
    parser = argparse.ArgumentParser(description='Fix notulen data quality for a Dutch city')
    parser.add_argument('--city', default=os.getenv('NEODEMOS_CITY', 'rotterdam'),
                       help='City name (rotterdam, amsterdam, den_haag, etc.)')
    args = parser.parse_args()
    
    try:
        fixer = NotulenDataFixer(city_name=args.city)
        stats = await fixer.run_full_fix()
        
        # Save stats
        os.makedirs('output/test_results', exist_ok=True)
        with open(f'output/test_results/notulen_data_fix_{args.city.lower()}.json', 'w') as f:
            json.dump(stats, f, indent=2, ensure_ascii=False, default=str)
        
        print(f"Results saved to output/test_results/notulen_data_fix_{args.city.lower()}.json")
        
        # Exit with error code if there were errors
        sys.exit(1 if stats['errors'] else 0)
        
    except ValueError as e:
        print(f"ERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
