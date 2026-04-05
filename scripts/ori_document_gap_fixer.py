#!/usr/bin/env python3
"""
ori_document_gap_fixer.py — Comprehensive ORI gap discovery and ingestion
═══════════════════════════════════════════════════════════════════════════

Phases:
  1. AUDIT   — Scroll all MediaObjects from ORI, diff against our DB.
               Classifies gaps by type and year. Handles timing correctly:
               a doc indexed in ORI in 2025 may only appear on a 2026 agenda.
  2. SCOPE   — Prints a full breakdown before touching anything.
  3. INGEST  — Fetches missing docs: ORI text → OCR fallback if < MIN_CHARS.
               Resolves parent chain (MediaObject → AgendaItem → Meeting)
               to set correct meeting_id + agenda_item_id.
               Queues for chunking.

Usage:
    python scripts/ori_document_gap_fixer.py --audit-only     # scope report, no writes
    python scripts/ori_document_gap_fixer.py                  # full run
    python scripts/ori_document_gap_fixer.py --limit 200      # ingest first N gaps
    python scripts/ori_document_gap_fixer.py --min-year 2023  # only recent gaps
"""

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from collections import defaultdict

import httpx
import psycopg2
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.getcwd())

# ── Config ────────────────────────────────────────────────────────────────────
DB_URL       = os.getenv("DB_URL") or "postgresql://postgres:postgres@localhost:5432/neodemos"
ORI_BASE     = "https://api.openraadsinformatie.nl/v1/elastic"
ORI_INDEX    = "ori_rotterdam_20250629013104"
ORI_URL      = f"{ORI_BASE}/{ORI_INDEX}"
OCR_TOOL     = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "ocr_pdf")
STATE_FILE   = "data/pipeline_state/ori_gap_checkpoint.json"
MIN_CHARS    = 500      # below this, attempt OCR before giving up
SCROLL_SIZE  = 500      # ORI scroll page size
CONCURRENCY  = 6        # parallel ingest workers

STEALTH_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/pdf,*/*;q=0.8",
    "Referer": "https://rotterdam.raadsinformatie.nl/",
}

os.makedirs("logs", exist_ok=True)
os.makedirs("data/pipeline_state", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/ori_gap_fixer.log", mode="a"),
    ]
)
log = logging.getLogger("gap_fixer")


# ── Checkpoint ────────────────────────────────────────────────────────────────

def load_checkpoint() -> set:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return set(json.load(f).get("ingested_ids", []))
    return set()

def save_checkpoint(ingested: set):
    with open(STATE_FILE, "w") as f:
        json.dump({"ingested_ids": list(ingested)}, f)


# ── ORI scroll: get ALL MediaObjects ─────────────────────────────────────────

def scroll_all_ori_mediaobjects() -> list[dict]:
    """
    Pages through the entire ORI index using search_after (cursor pagination).
    ORI blocks the /_search/scroll endpoint (403), so we use sort + search_after.
    Returns list of hit dicts with {_id, _source}.
    Handles the timing gap: ORI _id is our document ID regardless of when
    it was indexed vs when it appears on an agenda.
    """
    log.info("Fetching all ORI MediaObjects via search_after pagination...")
    query = {
        "query": {"term": {"@type": "MediaObject"}},
        "_source": ["name", "text", "md_text", "original_url", "url", "parent",
                    "is_referenced_by", "last_discussed_at",
                    "date_modified", "identifier", "@type"],
        "size": SCROLL_SIZE,
        "sort": [{"_id": "asc"}],  # stable sort required for search_after
    }

    docs = []
    search_after = None

    with httpx.Client(timeout=60) as client:
        while True:
            if search_after:
                query["search_after"] = search_after

            resp = client.post(f"{ORI_URL}/_search", json=query, timeout=60)
            if resp.status_code != 200:
                log.error(f"ORI search error {resp.status_code}: {resp.text[:200]}")
                break

            data = resp.json()
            hits = data["hits"]["hits"]
            if not hits:
                break

            docs.extend(hits)
            search_after = hits[-1]["sort"]

            total = data["hits"]["total"]["value"]
            if len(docs) % 5000 == 0 or len(docs) == len(hits):
                log.info(f"  Fetched {len(docs):,}/{total:,} MediaObjects...")

            if len(hits) < SCROLL_SIZE:
                break  # last page

    log.info(f"  Pagination complete: {len(docs):,} MediaObjects retrieved.")
    return docs


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_existing_doc_ids(conn) -> set:
    cur = conn.cursor()
    cur.execute("SELECT id FROM documents")
    ids = {str(r[0]) for r in cur.fetchall()}
    cur.close()
    return ids


def get_existing_meeting_ids(conn) -> set:
    cur = conn.cursor()
    cur.execute("SELECT id FROM meetings")
    ids = {str(r[0]) for r in cur.fetchall()}
    cur.close()
    return ids


def get_existing_agenda_item_ids(conn) -> set:
    cur = conn.cursor()
    cur.execute("SELECT id FROM agenda_items")
    ids = {str(r[0]) for r in cur.fetchall()}
    cur.close()
    return ids


def get_meeting_years(conn) -> dict:
    """Returns {meeting_id: year} from meetings.start_date."""
    cur = conn.cursor()
    cur.execute("SELECT id, start_date FROM meetings WHERE start_date IS NOT NULL")
    result = {}
    for row in cur.fetchall():
        mid, sd = str(row[0]), row[1]
        if sd:
            result[mid] = sd.year if hasattr(sd, 'year') else int(str(sd)[:4])
    cur.close()
    return result


def get_agenda_meeting_map(conn) -> dict:
    """Returns {agenda_item_id: meeting_id}."""
    cur = conn.cursor()
    cur.execute("SELECT id, meeting_id FROM agenda_items WHERE meeting_id IS NOT NULL")
    result = {str(r[0]): str(r[1]) for r in cur.fetchall()}
    cur.close()
    return result


def resolve_parent(ori_parent: str, meeting_ids: set, agenda_ids: set) -> tuple:
    """
    Resolve ORI parent ID to (meeting_id, agenda_item_id).
    ORI parent can be an AgendaItem or Event (meeting) ID.
    Timing-aware: we check both sets regardless of year.
    """
    if not ori_parent:
        return None, None
    p = str(ori_parent)
    if p in agenda_ids:
        # Parent is an agenda item — look up its meeting
        return None, p   # meeting_id resolved later via DB
    if p in meeting_ids:
        return p, None
    return None, None


def resolve_agenda_item_meeting(conn, agenda_item_id: str) -> str | None:
    """Look up meeting_id for an agenda item."""
    cur = conn.cursor()
    cur.execute("SELECT meeting_id FROM agenda_items WHERE id = %s", (agenda_item_id,))
    row = cur.fetchone()
    cur.close()
    return str(row[0]) if row else None


# ── OCR fallback ──────────────────────────────────────────────────────────────

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
        if 'tmp_path' in locals() and os.path.exists(tmp_path):
            os.remove(tmp_path)
    return ""


# ── Extract text from ORI source ─────────────────────────────────────────────

def extract_ori_text(source: dict) -> str:
    """Get text from ORI, checking text, md_text, and text_pages fields."""
    for field in ("text", "md_text"):
        raw = source.get(field, "")
        if isinstance(raw, list):
            raw = "\n\n".join([str(p) for p in raw if p])
        if raw and raw.strip():
            return raw.replace('\x00', '').strip()
    pages = source.get("text_pages", [])
    if isinstance(pages, list):
        text = "\n\n".join([p.get("text", "") for p in pages if isinstance(p, dict)])
        if text.strip():
            return text.replace('\x00', '').strip()
    return ""


# ── Document type classifier ──────────────────────────────────────────────────

def classify_doc(name: str) -> str:
    n = (name or "").lower()
    if any(x in n for x in ["notulen", "verslag"]):         return "Notulen/Verslag"
    if "motie" in n:                                         return "Motie"
    if "amendement" in n:                                    return "Amendement"
    if "raadsvoorstel" in n:                                 return "Raadsvoorstel"
    if "annotatie" in n:                                     return "Annotatie"
    if any(x in n for x in ["besluitenlijst","adviezenlijst"]): return "Besluitenlijst"
    if "bijlage" in n:                                       return "Bijlage"
    if any(x in n for x in ["brief","collegebrief","burgemeestersbrief"]): return "Brief B&W"
    if any(x in n for x in ["schriftelijke vrag","sv ", "raadsvraag"]): return "Schr. Vragen"
    if "inspreek" in n:                                      return "Inspreekbijdrage"
    if "toezegging" in n:                                    return "Toezegging"
    if any(x in n for x in ["rapport","onderzoek"]):         return "Rapport/Onderzoek"
    if "agenda" in n:                                        return "Agenda"
    return "Overig"


# ── Ingest a single missing document ─────────────────────────────────────────

async def ingest_missing_doc(
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    doc: dict,
    meeting_ids: set,
    agenda_ids: set,
) -> str | None:
    """Fetch, OCR if needed, and write a missing document to Postgres."""
    async with semaphore:
        source  = doc["_source"]
        doc_id  = doc["_id"]
        name    = source.get("name") or "Untitled"
        url     = source.get("original_url") or source.get("url") or ""
        parent  = str(source.get("parent", "")) if source.get("parent") else ""

        text = extract_ori_text(source)

        # OCR fallback if text too short
        if len(text) < MIN_CHARS and url:
            log.info(f"  [{doc_id}] ORI text {len(text)}c < {MIN_CHARS} — OCR from URL")
            text = await ocr_from_url(client, url)
            if len(text) < 50:
                log.info(f"  [{doc_id}] OCR also thin ({len(text)}c) — storing as-is")

        # Resolve parent: try 'parent' then 'is_referenced_by'
        meeting_id, agenda_item_id = resolve_parent(parent, meeting_ids, agenda_ids)
        if not meeting_id and not agenda_item_id:
            ref = str(source.get("is_referenced_by", "")) if source.get("is_referenced_by") else ""
            if ref:
                meeting_id, agenda_item_id = resolve_parent(ref, meeting_ids, agenda_ids)

        try:
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()

            # Resolve agenda_item → meeting_id
            if agenda_item_id and not meeting_id:
                meeting_id = resolve_agenda_item_meeting(conn, agenda_item_id)

            clean_text = text.replace('\x00', '').strip()

            cur.execute("""
                INSERT INTO documents (id, name, url, content, meeting_id, agenda_item_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    content   = CASE WHEN LENGTH(EXCLUDED.content) > LENGTH(documents.content)
                                     THEN EXCLUDED.content ELSE documents.content END,
                    name      = EXCLUDED.name,
                    url       = COALESCE(EXCLUDED.url, documents.url),
                    meeting_id = COALESCE(EXCLUDED.meeting_id, documents.meeting_id),
                    agenda_item_id = COALESCE(EXCLUDED.agenda_item_id, documents.agenda_item_id)
            """, (doc_id, name, url, clean_text, meeting_id, agenda_item_id))

            # Queue for chunking
            cur.execute("""
                INSERT INTO chunking_queue (document_id, status)
                VALUES (%s, 'pending')
                ON CONFLICT (document_id) DO UPDATE SET
                    status = 'pending', claimed_by = NULL, claimed_at = NULL
            """, (doc_id,))

            conn.commit()
            cur.close()
            conn.close()
            log.info(f"  ✓ [{doc_id}] {name[:60]} ({len(clean_text):,}c)")
            return doc_id

        except Exception as e:
            log.error(f"  ✗ [{doc_id}] {name[:50]}: {e}")
            try:
                conn.rollback()
                conn.close()
            except Exception:
                pass
            return None


# ── Main ──────────────────────────────────────────────────────────────────────

async def main(audit_only: bool = False, limit: int = None, min_year: int = None):
    conn = psycopg2.connect(DB_URL)
    log.info("Loading existing DB state...")
    existing_ids      = get_existing_doc_ids(conn)
    meeting_ids       = get_existing_meeting_ids(conn)
    agenda_ids        = get_existing_agenda_item_ids(conn)
    meeting_years     = get_meeting_years(conn)         # {meeting_id: year}
    agenda_meet_map   = get_agenda_meeting_map(conn)    # {agenda_item_id: meeting_id}
    conn.close()
    log.info(f"  DB has {len(existing_ids):,} documents, {len(meeting_ids):,} meetings, "
             f"{len(agenda_ids):,} agenda items")

    # ── Phase 1: Scroll ORI ──────────────────────────────────────────────────
    all_ori = scroll_all_ori_mediaobjects()

    # ── Phase 2: Diff ────────────────────────────────────────────────────────
    missing = []
    for doc in all_ori:
        oid = doc["_id"]
        if oid not in existing_ids:
            missing.append(doc)

    log.info(f"\nGap analysis: {len(all_ori):,} ORI docs, "
             f"{len(existing_ids):,} in DB → {len(missing):,} missing")

    # ── Phase 3: Scope report ────────────────────────────────────────────────
    # Classify missing by type and year
    # Year: resolve via parent meeting if possible, else from date_modified
    by_type = defaultdict(int)
    by_year = defaultdict(int)
    by_type_year = defaultdict(lambda: defaultdict(int))

    for doc in missing:
        s = doc["_source"]
        name = s.get("name", "")
        doc_type = classify_doc(name)

        # Resolve year: 1) last_discussed_at (most accurate — when it was on agenda)
        #               2) [YYbb...] prefix in document name (Rotterdam municipality ID)
        #               3) parent chain via meeting start_date
        #               4) date_modified fallback
        year = None
        date_str = s.get("last_discussed_at", "") or ""
        m = re.search(r'(20\d\d)', date_str)
        if m:
            year = int(m.group(1))
        if year is None:
            bb_m = re.search(r'\[(\d{2})bb', name)
            if bb_m:
                yr2 = int(bb_m.group(1))
                if 18 <= yr2 <= 30:
                    year = 2000 + yr2
        if year is None:
            for parent_field in ("parent", "is_referenced_by"):
                pid = str(s.get(parent_field, "")) if s.get(parent_field) else ""
                if pid:
                    mid = pid if pid in meeting_years else agenda_meet_map.get(pid)
                    if mid:
                        year = meeting_years.get(mid)
                        break
        if year is None:
            date_str = s.get("date_modified", "") or ""
            m = re.search(r'(20\d\d)', date_str)
            if m:
                year = int(m.group(1))

        by_type[doc_type] += 1
        if year:
            by_year[year] += 1
            by_type_year[doc_type][year] += 1

    print("\n" + "═" * 70)
    print(f"  ORI GAP REPORT — {len(missing):,} missing documents")
    print("═" * 70)
    print(f"\n{'Type':<28} {'Total':>7}  {'2022':>5} {'2023':>5} {'2024':>5} {'2025':>5} {'2026':>5}")
    print("-" * 65)
    for doc_type, total in sorted(by_type.items(), key=lambda x: -x[1]):
        yy = by_type_year[doc_type]
        print(f"  {doc_type:<26} {total:>7}  "
              f"{yy.get(2022,0):>5} {yy.get(2023,0):>5} "
              f"{yy.get(2024,0):>5} {yy.get(2025,0):>5} {yy.get(2026,0):>5}")
    print("-" * 65)
    print(f"  {'TOTAL':<26} {len(missing):>7}  "
          f"{by_year.get(2022,0):>5} {by_year.get(2023,0):>5} "
          f"{by_year.get(2024,0):>5} {by_year.get(2025,0):>5} {by_year.get(2026,0):>5}")
    print("═" * 70 + "\n")

    if audit_only:
        log.info("--audit-only: stopping before ingest.")
        return

    # ── Phase 4: Ingest ──────────────────────────────────────────────────────
    ingested = load_checkpoint()
    targets = [d for d in missing if d["_id"] not in ingested]

    if min_year:
        def doc_year(doc):
            s = doc["_source"]
            name_ = s.get("name", "")
            for date_field in ("last_discussed_at", "date_modified"):
                m = re.search(r'(20\d\d)', s.get(date_field, "") or "")
                if m:
                    return int(m.group(1))
            bb_m = re.search(r'\[(\d{2})bb', name_)
            if bb_m:
                yr2 = int(bb_m.group(1))
                if 18 <= yr2 <= 30:
                    return 2000 + yr2
            for parent_field in ("parent", "is_referenced_by"):
                pid = str(s.get(parent_field, "")) if s.get(parent_field) else ""
                if pid:
                    mid = pid if pid in meeting_years else agenda_meet_map.get(pid)
                    if mid and meeting_years.get(mid):
                        return meeting_years[mid]
            return 0
        targets = [d for d in targets if doc_year(d) >= min_year]
        log.info(f"Filtered to {len(targets):,} docs from {min_year}+")

    if limit:
        targets = targets[:limit]

    log.info(f"Ingesting {len(targets):,} missing documents ({CONCURRENCY} workers)...")

    semaphore = asyncio.Semaphore(CONCURRENCY)
    stats = {"ok": 0, "fail": 0}
    batch_size = 100

    async with httpx.AsyncClient(headers=STEALTH_HEADERS, verify=False, timeout=120) as client:
        for i in range(0, len(targets), batch_size):
            batch = targets[i:i + batch_size]
            tasks = [
                ingest_missing_doc(client, semaphore, doc, meeting_ids, agenda_ids)
                for doc in batch
            ]
            results = await asyncio.gather(*tasks)
            for r in results:
                if r:
                    stats["ok"] += 1
                    ingested.add(r)
                else:
                    stats["fail"] += 1

            save_checkpoint(ingested)
            log.info(f"  Batch {i//batch_size + 1}: ✓{stats['ok']} ✗{stats['fail']} "
                     f"(total {i + len(batch)}/{len(targets)})")

    log.info(f"\nIngest complete: {stats['ok']} succeeded, {stats['fail']} failed.")
    log.info("Next step: run chunk_unchunked_documents.py to chunk the new docs.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-only", action="store_true",
                        help="Only report scope, do not ingest")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max documents to ingest")
    parser.add_argument("--min-year", type=int, default=None,
                        help="Only ingest docs from this year onwards")
    args = parser.parse_args()

    asyncio.run(main(
        audit_only=args.audit_only,
        limit=args.limit,
        min_year=args.min_year,
    ))
