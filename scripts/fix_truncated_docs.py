#!/usr/bin/env python3
"""
fix_truncated_docs.py — Fix all document content quality issues
═══════════════════════════════════════════════════════════════

Root causes addressed:
  A. bb-id stubs WITH a richer ORI sibling → delete stub (duplicate)
  B. bb-id stubs WITHOUT a sibling → refetch full text from ORI by name search
  C. UUID iBabs stubs (empty, have URL) → OCR from stored iBabs download URL

Strategy C details:
  iBabs scraper inserts UUID-id docs with a download URL but no content.
  These are typically recent meeting docs (agendas, bijlagen, notulen) that
  iBabs serves as PDFs. We OCR them directly from the stored URL.
  If OCR yields < MIN_CHARS, try ORI name search as fallback.

Usage:
    python scripts/fix_truncated_docs.py --dry-run       # show scope only
    python scripts/fix_truncated_docs.py                 # fix all (A+B+C)
    python scripts/fix_truncated_docs.py --strategy ABC  # explicit
    python scripts/fix_truncated_docs.py --strategy C    # only UUID stubs
"""

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import subprocess

import httpx
import psycopg2

sys.path.insert(0, os.getcwd())

DB_URL     = "postgresql://postgres:postgres@localhost:5432/neodemos"
ORI_BASE   = "https://api.openraadsinformatie.nl/v1/elastic"
ORI_INDEX  = "ori_rotterdam_20250629013104"
ORI_URL    = f"{ORI_BASE}/{ORI_INDEX}"
OCR_TOOL   = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "ocr_pdf")
STATE_FILE = "data/pipeline_state/fix_truncated_checkpoint.json"
MIN_CHARS  = 500

os.makedirs("logs", exist_ok=True)
os.makedirs("data/pipeline_state", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/fix_truncated_docs.log", mode="a"),
    ]
)
log = logging.getLogger("fix_truncated")


def load_checkpoint() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f).get("done_ids", []))
    return set()


def save_checkpoint(done: set):
    with open(STATE_FILE, "w") as f:
        json.dump({"done_ids": list(done)}, f)


def get_stubs_with_siblings(conn) -> list[dict]:
    """Stubs that have a richer sibling — safe to delete."""
    cur = conn.cursor()
    cur.execute("""
        SELECT d1.id as stub_id, d1.name,
               d2.id as rich_id, LENGTH(d2.content) as rich_chars
        FROM documents d1
        JOIN documents d2
          ON lower(d2.name) = lower(d1.name) AND d2.id != d1.id
        WHERE d1.id ~ '^[0-9]{2}bb' AND LENGTH(d1.content) = 1000
        AND LENGTH(d2.content) > 1000
        ORDER BY d1.id
    """)
    rows = cur.fetchall()
    cur.close()
    return [{"stub_id": r[0], "name": r[1], "rich_id": r[2], "rich_chars": r[3]} for r in rows]


def get_truly_truncated(conn) -> list[dict]:
    """bb-id stubs with no richer sibling — need ORI fetch or OCR."""
    cur = conn.cursor()
    cur.execute("""
        SELECT d.id, d.name, d.url, d.meeting_id, d.agenda_item_id
        FROM documents d
        WHERE d.id ~ '^[0-9]{2}bb' AND LENGTH(d.content) = 1000
        AND NOT EXISTS (
            SELECT 1 FROM documents d2
            WHERE d2.id != d.id
            AND lower(d2.name) = lower(d.name)
            AND LENGTH(d2.content) > 1000
        )
        ORDER BY d.id
    """)
    rows = cur.fetchall()
    cur.close()
    return [{"id": r[0], "name": r[1], "url": r[2],
             "meeting_id": r[3], "agenda_item_id": r[4]} for r in rows]


def get_uuid_stubs(conn) -> list[dict]:
    """UUID iBabs stubs with empty content but a download URL."""
    cur = conn.cursor()
    cur.execute("""
        SELECT d.id, d.name, d.url, d.meeting_id, d.agenda_item_id
        FROM documents d
        WHERE d.id ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        AND (d.content IS NULL OR LENGTH(d.content) <= 50)
        AND d.url IS NOT NULL AND d.url != ''
        ORDER BY d.id
    """)
    rows = cur.fetchall()
    cur.close()
    return [{"id": r[0], "name": r[1], "url": r[2],
             "meeting_id": r[3], "agenda_item_id": r[4]} for r in rows]


def delete_stub(conn, stub_id: str) -> bool:
    """Delete a stub document and its chunks."""
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM document_chunks WHERE document_id = %s", (stub_id,))
        cur.execute("DELETE FROM document_children WHERE document_id = %s", (stub_id,))
        cur.execute("DELETE FROM documents WHERE id = %s", (stub_id,))
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        conn.rollback()
        log.error(f"  Failed to delete stub {stub_id}: {e}")
        cur.close()
        return False


async def fetch_from_ori(client: httpx.AsyncClient, name: str) -> str:
    """Search ORI for a document by name, return best text found."""
    # Search by name — ORI numeric-id version may have the full text
    query = {
        "query": {"match": {"name": name}},
        "_source": ["name", "text", "md_text", "original_url"],
        "size": 3,
    }
    try:
        resp = await client.post(f"{ORI_URL}/_search", json=query, timeout=30)
        if resp.status_code != 200:
            return ""
        hits = resp.json().get("hits", {}).get("hits", [])
        best_text = ""
        best_url = ""
        for h in hits:
            src = h["_source"]
            for field in ("text", "md_text"):
                raw = src.get(field, "")
                if isinstance(raw, list):
                    raw = "\n\n".join(str(x) for x in raw if x)
                if len(raw) > len(best_text):
                    best_text = raw
            if not best_url:
                best_url = src.get("original_url", "") or src.get("url", "")
        return best_text.replace("\x00", "").strip(), best_url
    except Exception as e:
        log.debug(f"  ORI fetch error for '{name[:40]}': {e}")
        return "", ""


async def ocr_from_url(client: httpx.AsyncClient, url: str) -> str:
    """Download PDF and run native macOS OCR."""
    if not url or not os.path.exists(OCR_TOOL):
        return ""
    try:
        resp = await client.get(url, follow_redirects=True, timeout=60)
        if resp.status_code != 200:
            return ""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path = tmp.name
        result = subprocess.run([OCR_TOOL, tmp_path], capture_output=True,
                                text=True, timeout=120)
        if result.returncode == 0 and result.stdout:
            text = result.stdout
            if "--- OCR RESULT START ---" in text:
                text = text.split("--- OCR RESULT START ---")[1].split("--- OCR RESULT END ---")[0]
            return text.strip()
    except Exception as e:
        log.debug(f"  OCR error for {url}: {e}")
    finally:
        if "tmp_path" in locals() and os.path.exists(tmp_path):
            os.remove(tmp_path)
    return ""


def update_doc_content(conn, doc_id: str, content: str) -> bool:
    """Update document content in place."""
    cur = conn.cursor()
    try:
        clean = content.replace("\x00", "").strip()
        cur.execute(
            "UPDATE documents SET content = %s WHERE id = %s",
            (clean, doc_id)
        )
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        conn.rollback()
        log.error(f"  Update failed for {doc_id}: {e}")
        cur.close()
        return False


async def main(dry_run=False, strategy="AB"):
    conn = psycopg2.connect(DB_URL)
    done = load_checkpoint()
    stats = {"deleted": 0, "updated": 0, "no_content": 0, "errors": 0}

    # ── Strategy A: Delete stubs that have a richer sibling ──────────────────
    if "A" in strategy:
        stubs_with_siblings = get_stubs_with_siblings(conn)
        stubs_with_siblings = [s for s in stubs_with_siblings if s["stub_id"] not in done]
        log.info(f"Strategy A: {len(stubs_with_siblings)} stubs with richer siblings to delete")

        for s in stubs_with_siblings:
            if dry_run:
                log.info(f"  [DRY] Would delete stub {s['stub_id']} (richer: {s['rich_id']} {s['rich_chars']}c) — {s['name'][:60]}")
                continue
            if delete_stub(conn, s["stub_id"]):
                stats["deleted"] += 1
                done.add(s["stub_id"])
                log.info(f"  ✓ Deleted stub {s['stub_id']} → kept {s['rich_id']} ({s['rich_chars']}c)")
            else:
                stats["errors"] += 1

            if stats["deleted"] % 50 == 0:
                save_checkpoint(done)

        save_checkpoint(done)
        log.info(f"Strategy A done: {stats['deleted']} stubs deleted")

    # ── Strategy B: Refetch truly truncated docs from ORI or OCR ─────────────
    if "B" in strategy:
        truncated = get_truly_truncated(conn)
        truncated = [d for d in truncated if d["id"] not in done]
        log.info(f"Strategy B: {len(truncated)} truly truncated docs to refetch")

        STEALTH = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://rotterdam.raadsinformatie.nl/",
        }

        async with httpx.AsyncClient(headers=STEALTH, verify=False, timeout=60) as client:
            for i, doc in enumerate(truncated):
                doc_id = doc["id"]
                name   = doc["name"] or ""

                if dry_run:
                    log.info(f"  [DRY] Would refetch {doc_id}: {name[:60]}")
                    continue

                # 1. Try ORI by name search
                text, ori_url = await fetch_from_ori(client, name)

                # 2. If ORI text still thin, try OCR from ORI url or stored url
                if len(text) < MIN_CHARS:
                    fetch_url = ori_url or doc["url"] or ""
                    if fetch_url:
                        log.info(f"  [{doc_id}] ORI text {len(text)}c — trying OCR")
                        text = await ocr_from_url(client, fetch_url)

                if len(text) >= MIN_CHARS:
                    if update_doc_content(conn, doc_id, text):
                        stats["updated"] += 1
                        done.add(doc_id)
                        log.info(f"  ✓ Updated {doc_id}: {len(text)}c — {name[:55]}")
                    else:
                        stats["errors"] += 1
                else:
                    stats["no_content"] += 1
                    done.add(doc_id)  # mark done so we don't retry
                    log.info(f"  – No content found for {doc_id}: {name[:55]}")

                if (i + 1) % 50 == 0:
                    save_checkpoint(done)
                    log.info(f"  Progress: {i+1}/{len(truncated)} | "
                             f"updated={stats['updated']} no_content={stats['no_content']}")

        save_checkpoint(done)
        log.info(f"Strategy B done: {stats['updated']} updated, "
                 f"{stats['no_content']} no content found, {stats['errors']} errors")

    # ── Strategy C: OCR UUID iBabs stubs with stored download URLs ────────────
    if "C" in strategy:
        uuid_stubs = get_uuid_stubs(conn)
        uuid_stubs = [d for d in uuid_stubs if d["id"] not in done]
        log.info(f"Strategy C: {len(uuid_stubs)} UUID iBabs stubs to OCR")

        STEALTH = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://rotterdam.raadsinformatie.nl/",
        }

        c_updated = c_no_content = 0
        async with httpx.AsyncClient(headers=STEALTH, verify=False, timeout=120) as client:
            for i, doc in enumerate(uuid_stubs):
                doc_id = doc["id"]
                name   = doc["name"] or ""
                url    = doc["url"] or ""

                if dry_run:
                    log.info(f"  [DRY] Would OCR {doc_id[:20]}... | {name[:60]}")
                    continue

                text = ""

                # 1. OCR from stored iBabs URL (primary — most reliable for UUID docs)
                if url:
                    text = await ocr_from_url(client, url)

                # 2. Fallback: ORI name search
                if len(text) < MIN_CHARS:
                    clean_name = re.sub(r'\s*\d[\d,.]*\s*(KB|MB|GB)\s*$', '', name, flags=re.IGNORECASE).strip()
                    if clean_name:
                        ori_text, ori_url = await fetch_from_ori(client, clean_name)
                        if len(ori_text) > len(text):
                            text = ori_text

                if len(text) >= MIN_CHARS:
                    if update_doc_content(conn, doc_id, text):
                        c_updated += 1
                        done.add(doc_id)
                        log.info(f"  ✓ OCR {doc_id[:20]}... {len(text)}c — {name[:55]}")
                    else:
                        stats["errors"] += 1
                else:
                    c_no_content += 1
                    done.add(doc_id)
                    log.info(f"  – No content for {doc_id[:20]}... ({len(text)}c) — {name[:55]}")

                if (i + 1) % 50 == 0:
                    save_checkpoint(done)
                    log.info(f"  C progress: {i+1}/{len(uuid_stubs)} | "
                             f"updated={c_updated} no_content={c_no_content}")

        stats["updated"] += c_updated
        stats["no_content"] += c_no_content
        save_checkpoint(done)
        log.info(f"Strategy C done: {c_updated} updated, {c_no_content} no content")

    conn.close()
    log.info(f"\nAll done: {stats}")
    if not dry_run:
        log.info("Next step: run chunk_unchunked_documents.py for newly updated docs, "
                 "then migrate_embeddings.py --recovery-mode")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--strategy", default="ABC",
                        help="A=delete stubs with siblings, B=refetch bb truncated, C=OCR UUID stubs")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run, strategy=args.strategy.upper()))
