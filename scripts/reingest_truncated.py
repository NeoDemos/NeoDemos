#!/usr/bin/env python3
"""
NeoDemos Re-Ingestion Script for Truncated Documents
=====================================================
Re-downloads and re-stores all documents that were stored with the 15k char
limit (content at or near 15,000 chars). These documents had their content
truncated and need to be re-fetched and stored with the full content.

Run: python3 -u scripts/reingest_truncated.py > reingest_truncated.log 2>&1 &
"""

import asyncio
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv()

import psycopg2
from psycopg2.extras import RealDictCursor
from services.scraper import ScraperService
from services.storage import StorageService

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"

async def reingest_truncated():
    scraper = ScraperService()
    storage = StorageService()

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Get all documents that appear to be truncated (stored content >= 14,999 chars)
    cur.execute("""
        SELECT id, url, name, meeting_id, agenda_item_id
        FROM documents
        WHERE length(content) >= 14999
          AND url IS NOT NULL AND url != ''
        ORDER BY id
    """)
    docs = cur.fetchall()
    cur.close()
    conn.close()

    total = len(docs)
    print(f"Starting re-ingestion of {total} truncated documents...")
    success = 0
    errors = 0
    skipped = 0

    for idx, doc in enumerate(docs, 1):
        doc_id = doc['id']
        url = doc['url']
        name = doc['name']
        print(f"[{idx}/{total}] {name[:60]} (id: {doc_id})")

        try:
            text = await scraper.extract_text_from_url(url)
            if not text:
                print(f"  ✗ No text extracted")
                skipped += 1
                continue

            full_content = scraper.preserve_notulen_text(text)

            # Only update if the new content is meaningfully larger
            if len(full_content) <= 15500:
                print(f"  ~ Content didn't grow significantly ({len(full_content)} chars), skipping update")
                skipped += 1
                continue

            # Update in database directly
            conn2 = psycopg2.connect(DB_URL)
            try:
                cur2 = conn2.cursor()
                # Clean NUL chars
                clean_content = full_content.replace('\x00', '')
                cur2.execute(
                    "UPDATE documents SET content = %s WHERE id = %s",
                    (clean_content, doc_id)
                )
                conn2.commit()
                cur2.close()
                success += 1
                print(f"  ✓ Updated: {len(clean_content)} chars (was 15k limit)")
            finally:
                conn2.close()

        except Exception as e:
            errors += 1
            print(f"  ✗ Error: {e}")

        # Polite delay every 10 docs
        if idx % 10 == 0:
            await asyncio.sleep(1)

    print(f"\n{'='*55}")
    print("RE-INGESTION COMPLETED")
    print(f"{'='*55}")
    print(f"Total truncated docs: {total}")
    print(f"Successfully updated: {success}")
    print(f"Skipped (no growth):  {skipped}")
    print(f"Errors:               {errors}")

if __name__ == "__main__":
    asyncio.run(reingest_truncated())
