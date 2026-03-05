#!/usr/bin/env python3
"""
NeoDemos Pre-2018 Historical Ingestion (via rotterdam.raadsinformatie.nl)
=========================================================================
Ingests meeting minutes from rotterdam.raadsinformatie.nl (1993-2017).
Uses the Notubiz JSON search API with Rotterdam's organisation filter.
Paginates through all results filtering to pre-2018 dates from the API.

Run: python3 -u scripts/ingest_pre2018.py > ingest_pre2018.log 2>&1 &
"""

import asyncio
import sys
import os
import re
import hashlib
import logging
import json
from datetime import datetime

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import httpx
from bs4 import BeautifulSoup
from services.storage import StorageService
from services.scraper import ScraperService

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("ingest_pre2018.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("ingest_pre2018")

BASE_URL = "https://rotterdam.raadsinformatie.nl"
SEARCH_URL = f"{BASE_URL}/zoeken/result"
ORG_ID = "726"
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/html, */*",
    "Referer": f"{BASE_URL}/zoeken?keywords=notulen"
}


def make_doc_id(url: str) -> str:
    m = re.search(r'/document/(\d+)', url)
    return m.group(1) if m else hashlib.md5(url.encode()).hexdigest()


def make_meeting_id(notubiz_id) -> str:
    return f"rotterdam_raad_{notubiz_id}"


def parse_item_date(item: dict) -> str:
    """Extract ISO date from the JSON item"""
    try:
        date_obj = item.get("date", {})
        if isinstance(date_obj, dict):
            d = date_obj.get("date", "")[:10]
            return d
    except:
        pass
    return ""


async def fetch_meeting_page_docs(client: httpx.AsyncClient, meeting_id: str) -> list:
    """Scrape a /vergadering/{id} page for agenda items and document links."""
    url = f"{BASE_URL}/vergadering/{meeting_id}"
    full_req_headers = {
        **REQUEST_HEADERS,
        "X-Requested-With": "",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    try:
        resp = await client.get(url, headers=full_req_headers, follow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        items = []
        for li in soup.find_all('li', id=re.compile(r'^ai_')):
            item_id = li.get('id', '')
            title_el = li.find(['strong', 'h3', 'h2', 'span'], class_=re.compile(r'title|label|name'))
            if not title_el:
                title_el = li.find('strong') or li.find('h3')
            item_name = title_el.text.strip()[:150] if title_el else li.text.strip()[:100]
            item_docs = []
            for a in li.find_all('a', href=re.compile(r'/document/')):
                href = a.get('href', '')
                full = href if href.startswith('http') else f"{BASE_URL}{href}"
                item_docs.append({"name": a.text.strip() or "Document", "url": full})
            items.append({
                "agenda_item_id": f"{meeting_id}_{item_id}",
                "name": item_name,
                "documents": item_docs
            })
        return items
    except Exception as e:
        logger.warning(f"Could not load meeting page {meeting_id}: {e}")
        return []


async def ingest_pre2018():
    storage = StorageService()
    pdf_scraper = ScraperService()
    stats = {"meetings": 0, "docs": 0, "errors": 0, "skipped": 0}
    seen_meetings: set = set()

    async with httpx.AsyncClient(timeout=60.0, headers=REQUEST_HEADERS) as client:
        for keyword in ["notulen", "verslag", "besluitenlijst"]:
            logger.info(f"\n{'='*55}")
            logger.info(f"Searching keyword: '{keyword}' (1993-2017)")
            logger.info(f"{'='*55}")

            for page in range(1, 600):  # Up to 600 pages × 25 = 15,000 results
                params = {
                    "keywords": keyword,
                    "limit": 25,
                    "document_type": "",
                    "search": "send",
                    "filter[organisations][]": ORG_ID,
                    "page": page
                }
                try:
                    resp = await client.get(SEARCH_URL, params=params)
                    resp.raise_for_status()
                    
                    try:
                        data = resp.json()
                    except Exception:
                        logger.info(f"  Page {page}: non-JSON or empty response. Stopping keyword '{keyword}'.")
                        break

                    if data == "no results" or not data:
                        logger.info(f"  Page {page}: no results. Done with '{keyword}'.")
                        break
                    
                    items = data.get("items", []) if isinstance(data, dict) else []
                    if not items:
                        # Try treating the whole list as items
                        if isinstance(data, list):
                            items = data
                        else:
                            logger.info(f"  Page {page}: empty items. Done.")
                            break

                    logger.info(f"  Page {page}: {len(items)} items")
                    
                    # Filter to pre-2018 items only (the API has no reliable date filter)
                    pre2018_items = []
                    any_post2017 = False
                    for item in items:
                        item_date = parse_item_date(item)
                        if item_date and item_date >= "2018-01-01":
                            any_post2017 = True
                            continue  # skip - covered by ingest_all_history.py
                        pre2018_items.append(item)
                    
                    logger.info(f"    → {len(pre2018_items)} pre-2018 items to process")
                    
                    for item in pre2018_items:
                        # Extract meeting ID from the 'link' HTML field (contains vergadering URL)
                        link_html = item.get("link", "") or ""
                        m = re.search(r'rotterdam\.raadsinformatie\.nl/vergadering/(\d+)', link_html)
                        if not m:
                            continue
                        meeting_id_raw = m.group(1)

                        # Extract doc info
                        doc_url_raw = item.get("url", "") or ""  # api.notubiz.nl/document/ID/VER
                        item_date = parse_item_date(item)
                        item_title = item.get("title", "Document")
                        meeting_name = item.get("type_name", "Vergadering")
                        
                        # Try to get a richer meeting name from the breadcrumb or agenda_item
                        agenda_item_obj = item.get("agenda_item") or {}
                        if isinstance(agenda_item_obj, dict):
                            event_obj = agenda_item_obj.get("event") or {}
                            if isinstance(event_obj, dict):
                                meeting_name = event_obj.get("title") or meeting_name

                        full_meeting_id = make_meeting_id(meeting_id_raw)

                        if meeting_id_raw not in seen_meetings:
                            seen_meetings.add(meeting_id_raw)
                            meeting_data = {
                                "id": full_meeting_id,
                                "name": meeting_name,
                                "start_date": f"{item_date}T00:00:00" if item_date else None,
                                "committee": meeting_name,
                                "location": None,
                                "organization_id": "rotterdam"
                            }
                            storage.insert_meeting(meeting_data)
                            stats["meetings"] += 1
                            logger.info(f"    Meeting: {meeting_name} ({item_date})")

                            # Fetch full agenda and all docs from the meeting page
                            agenda_items = await fetch_meeting_page_docs(client, meeting_id_raw)
                            for ai in agenda_items:
                                storage.insert_agenda_item({
                                    "id": ai["agenda_item_id"],
                                    "meeting_id": full_meeting_id,
                                    "number": None,
                                    "name": ai["name"]
                                })
                                for doc in ai["documents"]:
                                    doc_id = make_doc_id(doc["url"])
                                    if storage.document_exists(doc_id):
                                        stats["skipped"] += 1
                                        continue
                                    try:
                                        text = await pdf_scraper.extract_text_from_url(doc["url"])
                                        if text:
                                            storage.insert_document({
                                                "id": doc_id,
                                                "agenda_item_id": ai["agenda_item_id"],
                                                "meeting_id": full_meeting_id,
                                                "name": doc["name"],
                                                "url": doc["url"],
                                                "content": pdf_scraper.preserve_notulen_text(text)
                                            })
                                            stats["docs"] += 1
                                            logger.info(f"      ✓ {doc['name']}")
                                    except Exception as e:
                                        stats["errors"] += 1
                                        logger.warning(f"      ✗ {e}")
                            await asyncio.sleep(0.5)
                    
                    # If the entire page was post-2017, we likely passed the historical data
                    if any_post2017 and not pre2018_items:
                        logger.info("  All items on this page are post-2017. Results are sorted by relevance not date — continuing.")

                except Exception as e:
                    stats["errors"] += 1
                    logger.error(f"  Error on page {page}: {e}")
                    await asyncio.sleep(5)

                await asyncio.sleep(1.5)  # polite delay between pages

    logger.info("\n" + "="*55)
    logger.info("PRE-2018 INGESTION COMPLETED")
    logger.info("="*55)
    logger.info(f"Meetings inserted:    {stats['meetings']}")
    logger.info(f"Documents downloaded: {stats['docs']}")
    logger.info(f"Documents skipped:    {stats['skipped']}")
    logger.info(f"Errors:               {stats['errors']}")
    logger.info(f"Completed at:         {datetime.now().isoformat()}")


if __name__ == "__main__":
    asyncio.run(ingest_pre2018())
