"""
Recover missing documents from ORI (OpenRaad) and iBabs for meetings in NeoDemos.

Strategy:
- For Gemeenteraad (raadsvergaderingen): fetch missing notulen + attached documents
- For Commissie meetings: fetch missing attached documents (moties, brieven, raadsvoorstellen, etc.)
  NOTE: Notulen do NOT exist for committee meetings — only annotaties.
- Works year-by-year starting from 2026, going backwards
- Checkpoint-based: can resume after interruption
- Writes only to Postgres (documents table) — does NOT touch document_chunks or Qdrant

Safety: This script only writes to the `documents` and `meetings` tables.
The running embedding process reads from `document_chunks` only, so there is zero conflict.
New documents need to be chunked (pipeline/ingestion.py) and then embedded separately.
"""

import os
import sys
import json
import asyncio
import logging
import hashlib
from datetime import datetime
from pathlib import Path

import httpx
import psycopg2
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.getcwd())

# --- Configuration ---
DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
ORI_BASE = "https://api.openraadsinformatie.nl/v1/elastic"
ORI_INDEX_FALLBACK = "ori_rotterdam_20250629013104"
STATE_DIR = "data/pipeline_state"
STATE_FILE = os.path.join(STATE_DIR, "doc_recovery_state.json")

# --- Logging ---
os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/doc_recovery.log", mode="a"),
    ]
)
logger = logging.getLogger("doc_recovery")
logging.getLogger("httpx").setLevel(logging.WARNING)


def load_state() -> dict:
    """Load recovery state: which years are done, per-meeting progress."""
    os.makedirs(STATE_DIR, exist_ok=True)
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"completed_years": [], "current_year": None, "processed_meeting_ids": [], "stats": {}}


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


async def discover_ori_index(client: httpx.AsyncClient) -> str:
    """Find the latest Rotterdam ORI index."""
    try:
        resp = await client.get(f"{ORI_BASE}/_cat/indices?format=json", timeout=15)
        resp.raise_for_status()
        indices = [i["index"] for i in resp.json()
                   if "rotterdam" in i["index"].lower() and i["index"].startswith("ori_")]
        if indices:
            idx = sorted(indices)[-1]
            logger.info(f"Discovered ORI index: {idx}")
            return idx
    except Exception as e:
        logger.warning(f"Index discovery failed ({e}), using fallback")
    return ORI_INDEX_FALLBACK


async def fetch_ori_meetings(client: httpx.AsyncClient, index: str, year: int) -> list:
    """Fetch all Rotterdam meetings for a given year from ORI."""
    query = {
        "query": {"bool": {"must": [
            {"term": {"_index": index}},
            {"term": {"@type": "Meeting"}},
            {"range": {"start_date": {
                "gte": f"{year}-01-01T00:00:00Z",
                "lte": f"{year}-12-31T23:59:59Z"
            }}}
        ]}},
        "size": 1000,
        "sort": [{"start_date": "desc"}]
    }
    resp = await client.post(f"{ORI_BASE}/{index}/_search", json=query, timeout=30)
    resp.raise_for_status()
    return resp.json().get("hits", {}).get("hits", [])


async def fetch_ori_documents_for_meeting(client: httpx.AsyncClient, index: str, meeting_id: str) -> list:
    """Fetch all documents attached to a meeting's agenda items from ORI."""
    # Step 1: Get agenda items
    agenda_q = {
        "query": {"bool": {"must": [
            {"term": {"_index": index}},
            {"term": {"@type": "AgendaItem"}},
            {"term": {"parent": meeting_id}}
        ]}},
        "size": 200
    }
    resp = await client.post(f"{ORI_BASE}/{index}/_search", json=agenda_q, timeout=30)
    resp.raise_for_status()
    agenda_hits = resp.json().get("hits", {}).get("hits", [])

    # Collect all attachment IDs and agenda item mappings
    attachment_to_agenda = {}
    for hit in agenda_hits:
        source = hit.get("_source", {})
        agenda_id = hit["_id"]
        attachments = source.get("attachment", [])
        if isinstance(attachments, str):
            attachments = [attachments]
        for att_id in attachments:
            attachment_to_agenda[att_id] = agenda_id

    if not attachment_to_agenda:
        return []

    # Step 2: Fetch document details
    doc_q = {
        "query": {"bool": {"must": [
            {"term": {"_index": index}},
            {"terms": {"_id": list(attachment_to_agenda.keys())}}
        ]}},
        "size": 500
    }
    resp = await client.post(f"{ORI_BASE}/{index}/_search", json=doc_q, timeout=30)
    resp.raise_for_status()
    doc_hits = resp.json().get("hits", {}).get("hits", [])

    docs = []
    for hit in doc_hits:
        source = hit.get("_source", {})
        docs.append({
            "id": hit["_id"],
            "name": source.get("name") or source.get("title") or "Unnamed",
            "url": source.get("original_url") or source.get("url"),
            "text": source.get("text", ""),
            "text_pages": source.get("text_pages", ""),
            "agenda_item_id": attachment_to_agenda.get(hit["_id"]),
        })
    return docs


def get_local_meeting_and_docs(conn, ori_meeting_id: str, meeting_name: str, meeting_date: str):
    """Find the local meeting (by ORI ID or name+date fuzzy match) and its existing doc IDs."""
    cur = conn.cursor()

    # Try exact ORI ID match first
    cur.execute("SELECT id FROM meetings WHERE id = %s", (ori_meeting_id,))
    row = cur.fetchone()
    local_meeting_id = row[0] if row else None

    if not local_meeting_id and meeting_date:
        # Fuzzy: match by name + date (within 1 day)
        cur.execute("""
            SELECT id FROM meetings
            WHERE name = %s AND ABS(EXTRACT(EPOCH FROM start_date - %s::timestamp)) < 86400
            LIMIT 1
        """, (meeting_name, meeting_date))
        row = cur.fetchone()
        local_meeting_id = row[0] if row else None

    existing_doc_ids = set()
    if local_meeting_id:
        cur.execute("SELECT id FROM documents WHERE meeting_id = %s", (local_meeting_id,))
        existing_doc_ids = {r[0] for r in cur.fetchall()}

    cur.close()
    return local_meeting_id, existing_doc_ids


def insert_meeting(conn, ori_id: str, name: str, start_date: str, committee: str = None):
    """Insert a meeting if it doesn't exist. Returns the meeting ID."""
    cur = conn.cursor()
    cur.execute("SELECT id FROM meetings WHERE id = %s", (ori_id,))
    if cur.fetchone():
        cur.close()
        return ori_id

    cur.execute(
        "INSERT INTO meetings (id, name, start_date, committee, last_updated) VALUES (%s, %s, %s, %s, NOW())",
        (ori_id, name, start_date, committee)
    )
    conn.commit()
    cur.close()
    logger.info(f"  + Inserted new meeting: {name} ({start_date[:10]})")
    return ori_id


def insert_document(conn, doc_id: str, name: str, content: str, meeting_id: str,
                    agenda_item_id: str = None, url: str = None):
    """Insert a document if it doesn't exist. Returns True if inserted."""
    cur = conn.cursor()
    cur.execute("SELECT id FROM documents WHERE id = %s", (doc_id,))
    if cur.fetchone():
        cur.close()
        return False

    # Only set agenda_item_id if it actually exists in the agenda_items table
    safe_agenda_id = None
    if agenda_item_id:
        cur.execute("SELECT id FROM agenda_items WHERE id = %s", (agenda_item_id,))
        if cur.fetchone():
            safe_agenda_id = agenda_item_id

    cur.execute(
        """INSERT INTO documents (id, name, content, meeting_id, agenda_item_id, url)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (doc_id, name, content or "", meeting_id, safe_agenda_id, url)
    )
    conn.commit()
    cur.close()
    return True


async def process_year(year: int, state: dict, conn, client: httpx.AsyncClient, index: str, dry_run: bool = False):
    """Process all meetings for a single year. Returns stats dict."""
    logger.info(f"\n{'='*60}")
    logger.info(f"Processing year {year}")
    logger.info(f"{'='*60}")

    ori_meetings = await fetch_ori_meetings(client, index, year)
    logger.info(f"ORI has {len(ori_meetings)} meetings for {year}")

    processed_ids = set(state.get("processed_meeting_ids", []))
    stats = {"meetings_checked": 0, "meetings_new": 0, "docs_found": 0, "docs_new": 0, "docs_with_content": 0}

    for hit in ori_meetings:
        ori_id = hit["_id"]
        source = hit.get("_source", {})
        m_name = source.get("name", "Unknown")
        m_date = source.get("start_date", "")
        m_committee = source.get("committee")

        # Skip if already processed in a previous run
        if ori_id in processed_ids:
            continue

        stats["meetings_checked"] += 1

        # Find local match
        local_meeting_id, existing_doc_ids = get_local_meeting_and_docs(conn, ori_id, m_name, m_date)

        if not local_meeting_id:
            # Meeting doesn't exist locally — insert it
            if not dry_run:
                local_meeting_id = insert_meeting(conn, ori_id, m_name, m_date, m_committee)
            stats["meetings_new"] += 1
            logger.info(f"  NEW meeting: {m_name} ({m_date[:10] if m_date else '?'})")

        # Fetch documents from ORI for this meeting
        try:
            ori_docs = await fetch_ori_documents_for_meeting(client, index, ori_id)
        except Exception as e:
            logger.warning(f"  Failed to fetch docs for {ori_id}: {e}")
            processed_ids.add(ori_id)
            continue

        stats["docs_found"] += len(ori_docs)
        new_for_meeting = 0

        for doc in ori_docs:
            # Skip if we already have this document
            if doc["id"] in existing_doc_ids:
                continue

            content = doc.get("text") or doc.get("text_pages") or ""
            if content:
                stats["docs_with_content"] += 1

            if not dry_run:
                try:
                    inserted = insert_document(
                        conn,
                        doc_id=doc["id"],
                        name=doc["name"],
                        content=content,
                        meeting_id=local_meeting_id,
                        agenda_item_id=doc.get("agenda_item_id"),
                        url=doc.get("url")
                    )
                except Exception as e:
                    conn.rollback()
                    logger.warning(f"  Failed to insert doc {doc['id']}: {e}")
                    continue
                if inserted:
                    stats["docs_new"] += 1
                    new_for_meeting += 1
            else:
                stats["docs_new"] += 1
                new_for_meeting += 1

        if new_for_meeting > 0:
            logger.info(f"  {m_name} ({m_date[:10] if m_date else '?'}): +{new_for_meeting} new docs")

        # Checkpoint after each meeting
        processed_ids.add(ori_id)
        state["processed_meeting_ids"] = list(processed_ids)
        state["current_year"] = year
        save_state(state)

    return stats


async def main(start_year: int = 2026, end_year: int = 2018, dry_run: bool = False):
    state = load_state()

    if dry_run:
        logger.info("=== DRY RUN MODE — no database writes ===")

    conn = psycopg2.connect(DB_URL)

    async with httpx.AsyncClient(timeout=30) as client:
        index = await discover_ori_index(client)

        completed = set(state.get("completed_years", []))
        all_stats = {}

        for year in range(start_year, end_year - 1, -1):
            if year in completed:
                logger.info(f"Skipping {year} (already completed)")
                continue

            stats = await process_year(year, state, conn, client, index, dry_run=dry_run)
            all_stats[year] = stats

            # Mark year complete
            completed.add(year)
            state["completed_years"] = sorted(completed, reverse=True)
            # Reset per-meeting tracking for next year
            state["processed_meeting_ids"] = []
            state["stats"][str(year)] = stats
            save_state(state)

            logger.info(f"\n--- {year} Summary ---")
            logger.info(f"  Meetings checked: {stats['meetings_checked']}")
            logger.info(f"  New meetings inserted: {stats['meetings_new']}")
            logger.info(f"  Total docs found in ORI: {stats['docs_found']}")
            logger.info(f"  New docs inserted: {stats['docs_new']}")
            logger.info(f"  Docs with text content: {stats['docs_with_content']}")

    conn.close()

    # Final summary
    logger.info(f"\n{'='*60}")
    logger.info("RECOVERY COMPLETE")
    logger.info(f"{'='*60}")
    total_new = sum(s["docs_new"] for s in all_stats.values())
    total_meetings = sum(s["meetings_new"] for s in all_stats.values())
    total_with_content = sum(s["docs_with_content"] for s in all_stats.values())
    logger.info(f"Years processed: {start_year} -> {end_year}")
    logger.info(f"New meetings: {total_meetings}")
    logger.info(f"New documents: {total_new} ({total_with_content} with text content)")
    logger.info(f"\nNext step: chunk new documents with pipeline/ingestion.py, then run embedding recovery.")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Recover missing documents from ORI/iBabs")
    parser.add_argument("--start-year", type=int, default=2026, help="Start year (default: 2026)")
    parser.add_argument("--end-year", type=int, default=2018, help="End year inclusive (default: 2018)")
    parser.add_argument("--dry-run", action="store_true", help="Audit only, don't write to DB")
    args = parser.parse_args()

    asyncio.run(main(start_year=args.start_year, end_year=args.end_year, dry_run=args.dry_run))
