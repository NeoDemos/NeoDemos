#!/usr/bin/env python3
"""
surgical_healer.py — Phase 1.3: The Surgical Healer
══════════════════════════════════════════════════════════════════
Fills meeting document gaps and recovers 'ghost' document content
using ORI Text Sync and Stealth Local OCR.

Targets:
1. Meetings with only 'annotaties' (missing 'notulen')
2. Documents with NULL or <500 chars (Ghost Documents)
"""

import asyncio
import io
import os
import subprocess
import json
import sys
import tempfile
import time
import httpx
import psycopg2
from pypdf import PdfReader
from dotenv import load_dotenv

load_dotenv()

# Config
DB_URL = os.getenv("DB_URL") or "postgresql://postgres:postgres@localhost:5432/neodemos"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OCR_TOOL = os.path.join(BASE_DIR, "scripts", "ocr_pdf")
ORI_BASE_URL = "https://api.openraadsinformatie.nl/v1/elastic"
ORI_INDEX = "ori_rotterdam_20250629013104"

# Stealth Headers
STEALTH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/pdf,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "nl-NL,nl;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://rotterdam.raadsinformatie.nl/",
    "Connection": "keep-alive"
}

# Thresholds
MIN_USEFUL_CHARS = 500
IMPROVEMENT_THRESHOLD = 200
CONCURRENCY = 4

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

async def fetch_ori_details(client: httpx.AsyncClient, ori_id: str, field: str = "_id"):
    """Fetch doc details from ORI by _id or identifier."""
    # ORI often uses numeric IDs for _id but municipal IDs for identifier
    query = { "query": { "term": { field: ori_id } }, "size": 1 }
    try:
        resp = await client.post(f"{ORI_BASE_URL}/{ORI_INDEX}/_search", json=query, timeout=10)
        if resp.status_code == 200:
            hits = resp.json().get("hits", {}).get("hits", [])
            if hits: return hits[0].get("_source", {})
    except Exception as e:
        log(f"  ORI fetch error for {ori_id}: {e}")
    return None

async def find_missing_notulen(client: httpx.AsyncClient, meeting_id: str):
    """Find missing 'notulen' or 'verslag' documents in ORI for a meeting."""
    # Search for AgendaItems under this meeting to find attachments
    query = {
        "query": {
            "bool": {
                "must": [
                    { "term": { "@type": "AgendaItem" } },
                    { "term": { "parent": meeting_id } }
                ]
            }
        },
        "size": 100
    }
    try:
        resp = await client.post(f"{ORI_BASE_URL}/{ORI_INDEX}/_search", json=query, timeout=10)
        hits = resp.json().get("hits", {}).get("hits", [])
        
        all_doc_ids = []
        for hit in hits:
            attachments = hit.get("_source", {}).get("attachment", [])
            if isinstance(attachments, str): attachments = [attachments]
            all_doc_ids.extend(attachments)
            
        if not all_doc_ids: return []
        
        # Now find the documents from those attachments
        doc_query = {
            "query": {
                "bool": {
                    "must": [
                        { "terms": { "_id": all_doc_ids } },
                        { "bool": { 
                            "should": [
                                { "match": { "name": "notulen" } },
                                { "match": { "name": "verslag" } },
                                { "match": { "name": "besluitenlijst" } }
                            ]
                        }}
                    ]
                }
            },
            "size": 20
        }
        resp = await client.post(f"{ORI_BASE_URL}/{ORI_INDEX}/_search", json=doc_query, timeout=10)
        doc_hits = resp.json().get("hits", {}).get("hits", [])
        
        results = []
        for d in doc_hits:
            source = d.get("_source", {})
            results.append({
                "id": d.get("_id"),
                "name": source.get("name"),
                "url": source.get("original_url") or source.get("url"),
                "text": source.get("text")
            })
        return results
    except Exception as e:
        log(f"  Gap audit error for meeting {meeting_id}: {e}")
    return []

def extract_text_ocr(pdf_bytes: bytes) -> str:
    """Run native macOS OCR via the compiled Swift tool."""
    if not os.path.exists(OCR_TOOL): return ""
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name
        result = subprocess.run([OCR_TOOL, tmp_path], capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and result.stdout:
            text = result.stdout
            if "--- OCR RESULT START ---" in text:
                text = text.split("--- OCR RESULT START ---")[1].split("--- OCR RESULT END ---")[0]
            return text.strip()
    except Exception as e:
        log(f"  OCR error: {e}")
    finally:
        if 'tmp_path' in locals() and os.path.exists(tmp_path): os.remove(tmp_path)
    return ""

def update_document(doc_id: str, content: str, meeting_id: str = None, name: str = None, url: str = None):
    """Upsert document and content, then reset queue."""
    # Resilience: Handle ORI returning text as a list of page strings
    if isinstance(content, list):
        content = "\n\n".join([str(p) for p in content if p])
    
    if not isinstance(content, str):
        content = str(content or "")

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    clean_content = content.replace('\x00', '').strip()
    
    # Surgical update or insert
    if meeting_id: # Missing document discovered
        cur.execute("""
            INSERT INTO documents (id, name, url, content, meeting_id)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content, url = EXCLUDED.url
        """, (doc_id, name, url, clean_content, meeting_id))
    else: # Ghost document healing
        cur.execute("UPDATE documents SET content = %s WHERE id = %s", (clean_content, doc_id))

    # Reset queue
    cur.execute("DELETE FROM chunking_metadata WHERE document_id = %s", (doc_id,))
    cur.execute("""
        INSERT INTO chunking_queue (document_id, status)
        VALUES (%s, 'pending')
        ON CONFLICT (document_id) DO UPDATE SET status = 'pending', claimed_by = NULL, claimed_at = NULL
    """, (doc_id,))
    
    conn.commit()
    cur.close()
    conn.close()

async def heal_ghost_doc(client: httpx.AsyncClient, semaphore: asyncio.Semaphore, doc: dict):
    async with semaphore:
        doc_id = doc["id"]
        current_len = doc.get("clen", 0)
        
        log(f"Healing Ghost: {doc['name'][:50]} (ID: {doc_id})")
        
        # Pass 1: ORI Sync
        lookup_field = "_id" if doc_id.isdigit() else "identifier"
        ori_source = await fetch_ori_details(client, doc_id, lookup_field)
        
        if ori_source and len(ori_source.get("text", "")) > 50:
            update_document(doc_id, ori_source["text"])
            log(f"  ✓ ORI Sync: {len(ori_source['text'])} chars")
            return

        # Pass 2: Stealth OCR Fallback with Auto-Retry
        url = doc.get("url") or (ori_source.get("original_url") if ori_source else None)
        if not url:
            log(f"  ~ Skipped: No URL found for {doc_id}")
            return

        max_retries = 3
        for attempt in range(max_retries):
            try:
                resp = await client.get(url, follow_redirects=True, timeout=60)
                if resp.status_code == 200:
                    ocr_text = extract_text_ocr(resp.content)
                    if len(ocr_text) > max(current_len, 50):
                        update_document(doc_id, ocr_text)
                        log(f"  ✓ Local OCR: {len(ocr_text)} chars")
                    else:
                        log(f"  ~ OCR produced only {len(ocr_text)} chars - skipping update")
                    break
                elif resp.status_code == 403:
                    wait_time = (attempt + 1) * 5 + (attempt ** 2) # 5s, 11s, 19s
                    log(f"  ⚠ 403 Forbidden on {doc_id} (Attempt {attempt+1}/{max_retries}). Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    log(f"  ✗ Download failed ({resp.status_code}) for {doc_id}")
                    break
            except Exception as e:
                log(f"  ✗ Connection error on {doc_id}: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2)
                else:
                    break

async def heal_gap_meeting(client: httpx.AsyncClient, semaphore: asyncio.Semaphore, meeting: dict):
    async with semaphore:
        mid = meeting["id"]
        log(f"Gap Audit: {meeting['name'][:50]} ({meeting['date']})")
        
        missing_docs = await find_missing_notulen(client, mid)
        if not missing_docs:
            log(f"  ~ No missing minutes found in ORI for {mid}")
            return
            
        for d in missing_docs:
            did = d["id"]
            dname = d["name"]
            url = d.get("url")
            
            # Pass 1: ORI Sync
            final_text = d.get("text") or ""
            
            # Pass 2: Stealth OCR Fallback with Retry
            if len(final_text) < 50 and url:
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        resp = await client.get(url, follow_redirects=True, timeout=60)
                        if resp.status_code == 200:
                            final_text = extract_text_ocr(resp.content)
                            break
                        elif resp.status_code == 403:
                            wait_time = (attempt + 1) * 5
                            log(f"  ⚠ 403 on {dname} (Gap). Retrying in {wait_time}s...")
                            await asyncio.sleep(wait_time)
                        else:
                            break
                    except:
                        if attempt < max_retries - 1: await asyncio.sleep(2)
                        else: break
            
            if len(final_text) > 50:
                update_document(did, final_text, meeting_id=mid, name=dname, url=url)
                log(f"  ✓ Healed/Ingested: {dname} ({len(final_text)} chars)")
            else:
                log(f"  ~ Failed to get text for {dname}")

async def main():
    try:
        with open('data/pipeline_state/surgical_targets.json', 'r') as f:
            targets = json.load(f)
        
        ghosts = targets['ghost_docs']
        meetings = targets['gap_meetings']
        
        log(f"🚀 Surgical Healer v1.6 (Real-time Batching): {len(ghosts)} ghosts, {len(meetings)} meetings.")
        
        semaphore = asyncio.Semaphore(CONCURRENCY)
        async with httpx.AsyncClient(headers=STEALTH_HEADERS, verify=False, timeout=120) as client:
            # 1. Process Ghosts
            batch_size = 20
            for i in range(0, len(ghosts), batch_size):
                batch_data = ghosts[i:i + batch_size]
                log(f"--- Processing Ghost Batch {i//batch_size + 1}/{(len(ghosts)//batch_size)+1} ---")
                batch_tasks = [heal_ghost_doc(client, semaphore, doc) for doc in batch_data]
                await asyncio.gather(*batch_tasks)
                await asyncio.sleep(0.1)

            # 2. Process Gap Meetings
            for i in range(0, len(meetings), batch_size):
                batch_data = meetings[i:i + batch_size]
                log(f"--- Processing Gap Batch {i//batch_size + 1}/{(len(meetings)//batch_size)+1} ---")
                batch_tasks = [heal_gap_meeting(client, semaphore, m) for m in batch_data]
                await asyncio.gather(*batch_tasks)
                await asyncio.sleep(0.1)

    except Exception as e:
        import traceback
        log(f"CRITICAL ERROR in main loop: {e}")
        log(traceback.format_exc())

if __name__ == "__main__":
    asyncio.run(main())
