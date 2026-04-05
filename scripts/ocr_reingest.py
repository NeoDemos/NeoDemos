#!/usr/bin/env python3
"""
ocr_reingest.py — Phase B2: OCR Re-ingestion of Scanned Documents
══════════════════════════════════════════════════════════════════
Downloads PDFs for documents with suspiciously low text content,
runs OCR via the native macOS Swift tool, and updates the database.

After completion, resets affected document IDs in chunking_queue
so the swarm controller can re-chunk them in a mop-up pass.

Scope: ~1,361 documents (empty, <500 chars, or still truncated at 15k)

Run:
  .venv/bin/python3 -u scripts/ocr_reingest.py 2>&1 | tee logs/ocr_reingest.log
"""

import asyncio
import io
import os
import subprocess
import sys
import tempfile
import time

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import psycopg2
from pypdf import PdfReader

DB_URL = os.getenv("DB_URL") or "postgresql://postgres:postgres@localhost:5432/neodemos"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OCR_TOOL = os.path.join(BASE_DIR, "scripts", "ocr_pdf")
LOG_DIR = os.path.join(BASE_DIR, "logs")

# Thresholds
MIN_USEFUL_CHARS = 500          # Below this, we consider the text insufficient
IMPROVEMENT_THRESHOLD = 200     # OCR must produce at least this many MORE chars than current


def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)


def get_candidates(scope: str = "all"):
    """Fetch documents needing OCR re-ingestion.

    scope: 'all'  — all documents with <500 chars (default, broadened from ZWCS-only)
           'zwcs' — original ZWCS 2021-2024 scope only
    """
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    if scope == "zwcs":
        log("Scanning for ZWCS OCR candidates (2021-2024) [legacy scope]...")
        query = """
            SELECT d.id, d.name, d.url, COALESCE(length(d.content), 0) as clen
            FROM documents d
            JOIN meetings m ON d.meeting_id = m.id
            WHERE d.url IS NOT NULL AND d.url != ''
              AND m.name ILIKE '%%Commissie Zorg, Welzijn, Cultuur en Sport%%'
              AND m.start_date >= '2021-01-01'
              AND (d.content IS NULL OR length(d.content) < %s)
            ORDER BY clen ASC
        """
        cur.execute(query, (MIN_USEFUL_CHARS,))
    else:
        log("Scanning ALL documents for OCR candidates (content < 500 chars)...")
        query = """
            SELECT d.id, d.name, d.url, COALESCE(length(d.content), 0) as clen
            FROM documents d
            WHERE d.url IS NOT NULL AND d.url != ''
              AND (d.content IS NULL OR length(d.content) < %s)
            ORDER BY clen ASC
        """
        cur.execute(query, (MIN_USEFUL_CHARS,))

    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows


def extract_text_pypdf(pdf_bytes: bytes) -> str:
    """Try standard pypdf text extraction."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        full_text = ""
        for page in reader.pages:
            full_text += (page.extract_text() or "") + "\n"
        return full_text.strip()
    except Exception as e:
        log(f"  pypdf error: {e}")
        return ""


def extract_text_ocr(pdf_bytes: bytes) -> str:
    """Run native macOS OCR via the compiled Swift tool."""
    if not os.path.exists(OCR_TOOL):
        log(f"  ⚠ OCR tool not found at {OCR_TOOL}")
        return ""

    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(pdf_bytes)
            tmp_path = tmp.name

        result = subprocess.run(
            [OCR_TOOL, tmp_path],
            capture_output=True, text=True, timeout=120
        )

        if result.returncode == 0 and result.stdout:
            ocr_text = result.stdout
            # Clean up debug prints from Swift output
            if "--- OCR RESULT START ---" in ocr_text:
                ocr_text = ocr_text.split("--- OCR RESULT START ---")[1]
                if "--- OCR RESULT END ---" in ocr_text:
                    ocr_text = ocr_text.split("--- OCR RESULT END ---")[0]
            return ocr_text.strip()
        else:
            if result.stderr:
                log(f"  OCR stderr: {result.stderr[:200]}")
            return ""
    except subprocess.TimeoutExpired:
        log(f"  OCR timeout (120s)")
        return ""
    except Exception as e:
        log(f"  OCR error: {e}")
        return ""
    finally:
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            os.remove(tmp_path)


def update_document(doc_id: str, new_content: str):
    """Update the document content in the database."""
    conn = psycopg2.connect(DB_URL)
    try:
        cur = conn.cursor()
        clean = new_content.replace('\x00', '')
        cur.execute("UPDATE documents SET content = %s WHERE id = %s", (clean, doc_id))
        conn.commit()
        cur.close()
    finally:
        conn.close()


def reset_chunking_queue(doc_ids: list):
    """Reset updated documents in chunking_queue to 'pending' for re-chunking."""
    if not doc_ids:
        return 0

    conn = psycopg2.connect(DB_URL)
    try:
        cur = conn.cursor()

        # Delete existing chunking metadata so swarm doesn't skip them
        cur.execute(
            "DELETE FROM chunking_metadata WHERE document_id = ANY(%s)",
            (doc_ids,)
        )
        deleted_meta = cur.rowcount

        # Reset queue status to pending
        cur.execute(
            "UPDATE chunking_queue SET status = 'pending', claimed_by = NULL, claimed_at = NULL WHERE document_id = ANY(%s)",
            (doc_ids,)
        )
        reset_count = cur.rowcount

        # Insert queue entries for any docs that don't have one yet
        cur.execute("""
            INSERT INTO chunking_queue (document_id, status)
            SELECT unnest(%s::text[]), 'pending'
            ON CONFLICT (document_id) DO UPDATE SET status = 'pending', claimed_by = NULL, claimed_at = NULL
        """, (doc_ids,))

        conn.commit()
        cur.close()
        log(f"Reset {reset_count} queue entries, deleted {deleted_meta} metadata rows.")
        return reset_count
    finally:
        conn.close()


async def process_document(client: httpx.AsyncClient, doc_id: str, name: str, url: str, current_len: int):
    """Download PDF, extract text (pypdf then OCR), and update if improved."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/pdf,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    }
    
    pdf_bytes = None
    for attempt in range(1, 4):
        try:
            response = await client.get(url, headers=headers, follow_redirects=True, timeout=60)
            response.raise_for_status()
            pdf_bytes = response.content
            break
        except Exception as e:
            if attempt < 3:
                wait = attempt * 2
                log(f"  Download attempt {attempt} failed ({e}) - retrying in {wait}s...")
                await asyncio.sleep(wait)
            else:
                return "error", f"Download failed after 3 attempts: {e}"

    if not pdf_bytes:
        return "error", "Download failed: Empty content"

    # Step 1: Try pypdf extraction
    text = extract_text_pypdf(pdf_bytes)

    # Step 2: If pypdf didn't produce enough, try OCR
    if len(text) < MIN_USEFUL_CHARS:
        log(f"  pypdf: {len(text)} chars → trying OCR...")
        ocr_text = extract_text_ocr(pdf_bytes)
        if len(ocr_text) > len(text):
            text = ocr_text

    # Step 3: Check if we got a meaningful improvement
    if len(text) <= current_len + IMPROVEMENT_THRESHOLD:
        return "skipped", f"No improvement ({current_len} → {len(text)} chars)"

    # Step 4: Update the database
    update_document(doc_id, text)
    return "success", f"{current_len} → {len(text)} chars"


async def main(scope: str = "all"):
    os.makedirs(LOG_DIR, exist_ok=True)

    log("═" * 60)
    log("OCR Re-ingestion of Scanned/Low-content Documents")
    log("═" * 60)

    # Check OCR tool
    if os.path.exists(OCR_TOOL):
        log(f"✓ OCR tool found: {OCR_TOOL}")
    else:
        log(f"⚠ OCR tool NOT found at {OCR_TOOL} — will rely on pypdf only")

    # Get candidates
    candidates = get_candidates(scope=scope)
    total = len(candidates)
    log(f"Found {total} documents needing OCR re-ingestion")

    if total == 0:
        log("Nothing to do. Exiting.")
        return

    # Categorize for reporting
    empty = sum(1 for _, _, _, clen in candidates if clen == 0)
    under_200 = sum(1 for _, _, _, clen in candidates if 0 < clen < 200)
    under_500 = sum(1 for _, _, _, clen in candidates if 200 <= clen < 500)
    at_15k = sum(1 for _, _, _, clen in candidates if clen == 15000)
    log(f"  Empty: {empty} | <200 chars: {under_200} | 200-500 chars: {under_500} | =15k chars: {at_15k}")

    # Process
    success = 0
    skipped = 0
    errors = 0
    updated_ids = []

    async with httpx.AsyncClient() as client:
        for idx, (doc_id, name, url, clen) in enumerate(candidates, 1):
            display_name = (name or "Unnamed")[:55]
            log(f"[{idx}/{total}] {display_name} ({clen} chars)")

            status, detail = await process_document(client, doc_id, name, url, clen)

            if status == "success":
                success += 1
                updated_ids.append(doc_id)
                log(f"  ✓ {detail}")
            elif status == "skipped":
                skipped += 1
                log(f"  ~ {detail}")
            else:
                errors += 1
                log(f"  ✗ {detail}")

            # Polite delay every 5 docs to avoid hammering the server
            if idx % 5 == 0:
                await asyncio.sleep(0.5)

    # Summary
    log("")
    log("═" * 60)
    log("OCR RE-INGESTION COMPLETED")
    log("═" * 60)
    log(f"Total candidates:     {total}")
    log(f"Successfully updated: {success}")
    log(f"Skipped (no growth):  {skipped}")
    log(f"Errors:               {errors}")

    # Reset chunking queue for updated docs
    if updated_ids:
        log(f"\nResetting {len(updated_ids)} documents in chunking_queue for re-chunking...")
        reset_chunking_queue(updated_ids)
        log("✓ Done. Run smart_controller.py for the mop-up pass.")
    else:
        log("\nNo documents were updated — no mop-up needed.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--scope", choices=["all", "zwcs"], default="all",
                        help="'all' = all docs <500 chars (default), 'zwcs' = original ZWCS-only scope")
    args = parser.parse_args()
    asyncio.run(main(scope=args.scope))
