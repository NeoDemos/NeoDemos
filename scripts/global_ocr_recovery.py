#!/usr/bin/env python3
"""
global_ocr_recovery.py — Phase 1: Global OCR Healing for 2018-2026
══════════════════════════════════════════════════════════════════
Heals documents with low character counts (<500 chars) using:
Pass 1: ORI Sync (OpenRaadsinformatie indexed text)
Pass 2: Local macOS Native OCR (Swift ocr_pdf tool fallback)

Target: 853 suspect documents identified in the 2018-2026 audit.
"""

import asyncio
import io
import os
import subprocess
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

# Thresholds
MIN_USEFUL_CHARS = 500
IMPROVEMENT_THRESHOLD = 200

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def get_hungry_candidates():
    """Fetch the 853 documents with < 500 characters from 2018-2026, excluding transcripts."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    query = """
        SELECT d.id, d.name, d.url, COALESCE(length(d.content), 0) as clen
        FROM documents d
        JOIN meetings m ON d.meeting_id = m.id
        WHERE m.start_date >= '2018-01-01' AND m.start_date <= '2026-12-31'
        AND LENGTH(COALESCE(d.content, '')) < %s
        AND d.id NOT LIKE 'transcript_%%'
        ORDER BY m.start_date DESC
    """
    cur.execute(query, (MIN_USEFUL_CHARS,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

async def fetch_ori_text(client: httpx.AsyncClient, doc_id: str):
    """Pass 1: Try to fetch indexed text from ORI."""
    # Attempt 1: Direct ID search
    url = f"{ORI_BASE_URL}/{ORI_INDEX}/_search"
    query = { "query": { "term": { "_id": doc_id } }, "size": 1 }
    
    # Attempt 2: Search by identifier (BB-number) if ID is not numeric
    if not doc_id.isdigit():
        query = {
            "query": { "term": { "identifier": doc_id } },
            "size": 1
        }

    try:
        resp = await client.post(url, json=query, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            hits = data.get("hits", {}).get("hits", [])
            if hits:
                source = hits[0].get("_source", {})
                return source.get("text") or ""
    except Exception as e:
        log(f"  ORI fetch error for {doc_id}: {e}")
    return ""

def extract_text_ocr(pdf_bytes: bytes) -> str:
    """Pass 2: Run native macOS OCR via the compiled Swift tool."""
    if not os.path.exists(OCR_TOOL):
        return ""
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
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            os.remove(tmp_path)
    return ""

def update_db(doc_id: str, content: str):
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    # Clean null bytes and leading/trailing whitespace
    clean_content = content.replace('\x00', '').strip()
    cur.execute("UPDATE documents SET content = %s WHERE id = %s", (clean_content, doc_id))
    
    # Reset chunking queue
    cur.execute("DELETE FROM chunking_metadata WHERE document_id = %s", (doc_id,))
    cur.execute("""
        INSERT INTO chunking_queue (document_id, status)
        VALUES (%s, 'pending')
        ON CONFLICT (document_id) DO UPDATE SET status = 'pending', claimed_by = NULL, claimed_at = NULL
    """, (doc_id,))
    
    conn.commit()
    cur.close()
    conn.close()

async def process_doc(client: httpx.AsyncClient, doc_id: str, name: str, url: str, current_len: int):
    # Pass 1: ORI Sync
    ori_text = await fetch_ori_text(client, doc_id)
    if len(ori_text) >= current_len + IMPROVEMENT_THRESHOLD:
        update_db(doc_id, ori_text)
        return "success_ori", f"ORI Sync: {current_len} -> {len(ori_text)} chars"

    # Pass 2: Local OCR Fallback
    if not url:
        return "skipped", "No URL for OCR fallback"

    try:
        resp = await client.get(url, follow_redirects=True, timeout=60)
        resp.raise_for_status()
        pdf_bytes = resp.content
        ocr_text = extract_text_ocr(pdf_bytes)
        if len(ocr_text) >= current_len + IMPROVEMENT_THRESHOLD:
            update_db(doc_id, ocr_text)
            return "success_ocr", f"Local OCR: {current_len} -> {len(ocr_text)} chars"
    except Exception as e:
        return "error", f"Download/OCR failed: {e}"

    return "skipped", f"No significant improvement ({current_len} -> ORI:{len(ori_text)})"

async def main():
    log("🚀 Starting Global OCR Recovery for 2018-2026...")
    candidates = get_hungry_candidates()
    total = len(candidates)
    log(f"Found {total} 'hungry' documents (<500 chars).")

    if total == 0:
        log("No candidates found. Done.")
        return

    success_ori = 0
    success_ocr = 0
    skipped = 0
    errors = 0

    async with httpx.AsyncClient(headers={"User-Agent": "NeoDemos-Recovery/1.0"}) as client:
        for idx, (doc_id, name, url, clen) in enumerate(candidates, 1):
            log(f"[{idx}/{total}] Processing: {str(name)[:50]} (ID: {doc_id}, current: {clen} chars)")
            status, detail = await process_doc(client, doc_id, name, url, clen)
            
            if status == "success_ori": success_ori += 1
            elif status == "success_ocr": success_ocr += 1
            elif status == "skipped": skipped += 1
            else: errors += 1
            
            log(f"  {status.upper()}: {detail}")
            
            # Rate limiting
            if idx % 10 == 0:
                await asyncio.sleep(1)

    log("\n" + "="*40)
    log("RECOVERY COMPLETED")
    log("="*40)
    log(f"Total:         {total}")
    log(f"ORI Sync:      {success_ori}")
    log(f"Local OCR:     {success_ocr}")
    log(f"Skipped:       {skipped}")
    log(f"Errors:        {errors}")
    log("="*40)

if __name__ == "__main__":
    asyncio.run(main())
