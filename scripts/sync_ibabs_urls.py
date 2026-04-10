#!/usr/bin/env python3
"""
ORI → iBabs URL Alignment
==========================

Source of truth: ORI (Open Raadsinformatie) stores both the stable internal ID
(used as our public.meetings.id) and the current iBabs display UUID via
was_generated_by.original_identifier.

This script:
1. Queries ORI for committee meetings in a date range
2. Extracts the current iBabs UUID from each ORI record
3. UPDATES public.meetings.ibabs_url for matching rows (matched by ORI _id)
4. Logs every change to public.meeting_url_history (audit trail)
5. Refuses to delete or modify any other column

Usage:
    # Dry-run (default) — show proposed changes, no DB writes
    python scripts/sync_ibabs_urls.py --year 2026

    # Apply changes
    python scripts/sync_ibabs_urls.py --year 2026 --apply

    # Sync date range
    python scripts/sync_ibabs_urls.py --from 2026-01-01 --to 2026-04-30 --apply

    # All committee meetings ever recorded in ORI
    python scripts/sync_ibabs_urls.py --all --apply
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
import psycopg2
from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ORI_INDEX = "ori_rotterdam_20250629013104"
ORI_URL = f"https://api.openraadsinformatie.nl/v1/elastic/{ORI_INDEX}/_search"
IBABS_BASE = "https://rotterdamraad.bestuurlijkeinformatie.nl/Agenda/Index/"


def _build_db_url():
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    user = os.getenv("DB_USER", "postgres")
    pw = os.getenv("DB_PASSWORD", "postgres")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "neodemos")
    return f"postgresql://{user}:{pw}@{host}:{port}/{name}"


def fetch_ori_committee_meetings(date_from: str = None, date_to: str = None,
                                  size: int = 1000) -> list:
    """Fetch committee meetings from ORI with current iBabs UUIDs.

    Returns list of dicts: {ori_id, name, start_date, ibabs_uuid, committee_org}
    """
    must = [
        {"term": {"@type": "Meeting"}},
        {"match_phrase_prefix": {"name": "Commissie"}},
    ]
    if date_from or date_to:
        rng = {}
        if date_from:
            rng["gte"] = f"{date_from}T00:00:00+00:00"
        if date_to:
            rng["lte"] = f"{date_to}T23:59:59+00:00"
        must.append({"range": {"start_date": rng}})

    body = {
        "size": size,
        "query": {"bool": {"must": must}},
        "sort": [{"start_date": {"order": "desc"}}],
        "_source": ["name", "start_date", "committee", "organization", "was_generated_by"],
    }

    r = requests.post(ORI_URL, json=body, timeout=30)
    r.raise_for_status()
    data = r.json()
    hits = data.get("hits", {}).get("hits", [])
    total = data.get("hits", {}).get("total", {}).get("value", 0)
    logger.info(f"ORI returned {len(hits)} of {total} matching meetings")

    results = []
    for h in hits:
        src = h.get("_source", {})
        wgb = src.get("was_generated_by") or {}
        ibabs_uuid = wgb.get("original_identifier")
        if not ibabs_uuid or "-" not in str(ibabs_uuid):
            continue  # only keep meetings with valid UUID
        results.append({
            "ori_id": h["_id"],
            "name": src.get("name", ""),
            "start_date": src.get("start_date", "")[:10],
            "ibabs_uuid": ibabs_uuid,
            "ibabs_url": IBABS_BASE + ibabs_uuid,
            "committee": src.get("committee"),
        })
    return results


def get_existing_meetings(conn, ori_ids: list) -> dict:
    """Fetch current ibabs_url for the given meeting IDs."""
    if not ori_ids:
        return {}
    cur = conn.cursor()
    cur.execute(
        "SELECT id::text, name, start_date, ibabs_url FROM public.meetings WHERE id::text = ANY(%s)",
        (ori_ids,)
    )
    out = {}
    for r in cur.fetchall():
        out[r[0]] = {"name": r[1], "start_date": str(r[2])[:10] if r[2] else None, "ibabs_url": r[3]}
    cur.close()
    return out


def apply_alignment(conn, updates: list, source: str):
    """Apply UPDATEs and write audit log entries. Single transaction."""
    if not updates:
        return 0
    cur = conn.cursor()
    now = datetime.now()
    audit_rows = []
    for u in updates:
        cur.execute(
            "UPDATE public.meetings SET ibabs_url = %s, ibabs_url_verified_at = %s WHERE id::text = %s",
            (u["new_url"], now, u["meeting_id"])
        )
        audit_rows.append((
            u["meeting_id"],
            u["old_url"],
            u["new_url"],
            source,
            1.0,  # match_confidence — exact match by ORI ID
            "ori_id_exact",
        ))
    # Insert audit log
    from psycopg2.extras import execute_values
    execute_values(
        cur,
        "INSERT INTO public.meeting_url_history (meeting_id, old_url, new_url, source, match_confidence, match_method) VALUES %s",
        audit_rows
    )
    conn.commit()
    cur.close()
    return len(updates)


def main():
    parser = argparse.ArgumentParser(description="Sync iBabs URLs from ORI")
    parser.add_argument("--year", type=int, help="Filter by year")
    parser.add_argument("--from", dest="date_from", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--to", dest="date_to", help="End date (YYYY-MM-DD)")
    parser.add_argument("--all", action="store_true", help="Sync all years")
    parser.add_argument("--apply", action="store_true", help="Write changes to DB (default: dry-run)")
    parser.add_argument("--source", default="manual", help="Audit log source tag (cron_daily, cron_weekly, manual)")
    parser.add_argument("--limit", type=int, default=2000, help="Max meetings per ORI query")
    args = parser.parse_args()

    if args.year:
        date_from = f"{args.year}-01-01"
        date_to = f"{args.year}-12-31"
    elif args.all:
        date_from = "2014-01-01"
        date_to = "2030-12-31"
    else:
        date_from = args.date_from
        date_to = args.date_to

    if not date_from and not date_to and not args.all:
        parser.error("specify --year, --from/--to, or --all")

    logger.info(f"=== ORI → iBabs URL Alignment ===")
    logger.info(f"Date range: {date_from} to {date_to}")
    logger.info(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")

    # Fetch from ORI
    ori_meetings = fetch_ori_committee_meetings(date_from, date_to, size=args.limit)
    logger.info(f"ORI committee meetings with iBabs UUID: {len(ori_meetings)}")

    if not ori_meetings:
        logger.info("Nothing to sync.")
        return

    # Look up existing meetings in DB
    db_url = _build_db_url()
    conn = psycopg2.connect(db_url)
    existing = get_existing_meetings(conn, [m["ori_id"] for m in ori_meetings])
    logger.info(f"Matched in public.meetings: {len(existing)} / {len(ori_meetings)}")

    # Compute updates needed
    updates = []
    no_change = 0
    not_in_db = 0

    for m in ori_meetings:
        if m["ori_id"] not in existing:
            not_in_db += 1
            continue
        existing_url = existing[m["ori_id"]]["ibabs_url"]
        if existing_url == m["ibabs_url"]:
            no_change += 1
            continue
        updates.append({
            "meeting_id": m["ori_id"],
            "old_url": existing_url,
            "new_url": m["ibabs_url"],
            "name": m["name"],
            "date": m["start_date"],
        })

    logger.info(f"")
    logger.info(f"=== ALIGNMENT RESULTS ===")
    logger.info(f"  Meetings in ORI:        {len(ori_meetings)}")
    logger.info(f"  Matched in DB:          {len(existing)}")
    logger.info(f"  Already up-to-date:     {no_change}")
    logger.info(f"  Not in our DB:          {not_in_db}")
    logger.info(f"  Need URL update:        {len(updates)}")
    logger.info(f"")

    if updates:
        logger.info(f"=== PROPOSED CHANGES (showing first 20) ===")
        for u in updates[:20]:
            old = (u["old_url"] or "[NULL]")[-45:]
            new = u["new_url"][-45:]
            logger.info(f"  {u['date']} | {u['name'][:50]}")
            logger.info(f"    OLD: ...{old}")
            logger.info(f"    NEW: ...{new}")

    if not args.apply:
        logger.info(f"")
        logger.info(f"DRY-RUN — no changes written. Re-run with --apply to commit.")
        conn.close()
        return

    # Apply changes
    n = apply_alignment(conn, updates, source=args.source)
    logger.info(f"")
    logger.info(f"Applied {n} URL updates to public.meetings")
    logger.info(f"Audit log entries written to public.meeting_url_history")

    conn.close()


if __name__ == "__main__":
    main()
