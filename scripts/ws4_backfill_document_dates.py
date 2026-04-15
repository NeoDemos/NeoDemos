#!/usr/bin/env python3
"""
Backfill document_date for ORI docs healed before migration 0017.

Fetches start_date / last_discussed_at from ORI in batches of 100 IDs
(ORI @id == our documents.id), then sets document_date on any row
that still has NULL.

Usage:
    python scripts/ws4_backfill_document_dates.py            # dry-run
    python scripts/ws4_backfill_document_dates.py --apply    # write
"""
from __future__ import annotations
import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

import httpx
import psycopg2
from psycopg2.extras import RealDictCursor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("ws4_dates")

ORI_BASE = "https://api.openraadsinformatie.nl/v1/elastic"
BATCH = 100


def db_connect():
    url = os.getenv("DATABASE_URL", "")
    if not url:
        pw = os.getenv("DB_PASSWORD", "")
        url = f"postgresql://postgres:{pw}@127.0.0.1:5432/neodemos"
    return psycopg2.connect(url)


def discover_index(client: httpx.Client) -> str:
    resp = client.get(f"{ORI_BASE}/_cat/indices?format=json", timeout=30)
    resp.raise_for_status()
    indices = [i["index"] for i in resp.json() if "rotterdam" in i.get("index", "").lower()]
    if not indices:
        raise RuntimeError("No Rotterdam ORI index found")
    return sorted(indices, reverse=True)[0]


def fetch_dates_batch(client: httpx.Client, index: str, ids: list[str]) -> dict[str, str]:
    """Return {ori_id: date_str} for a batch of IDs using ORI start_date/last_discussed_at."""
    query = {
        "size": len(ids),
        "query": {"terms": {"@id": ids}},
        "_source": ["@id", "start_date", "last_discussed_at", "@type"],
    }
    try:
        resp = client.post(f"{ORI_BASE}/{index}/_search", json=query, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        log.warning("ORI batch fetch failed: %s", e)
        return {}

    result: dict[str, str] = {}
    for hit in resp.json().get("hits", {}).get("hits", []):
        src = hit.get("_source", {})
        ori_id = src.get("@id") or hit.get("_id")
        date = src.get("start_date") or src.get("last_discussed_at")
        if ori_id and date:
            result[str(ori_id)] = date[:10]  # YYYY-MM-DD only
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write dates (default: dry-run)")
    args = parser.parse_args()
    dry_run = not args.apply

    log.info("Mode: %s", "DRY-RUN" if dry_run else "APPLY")

    conn = db_connect()
    client = httpx.Client()

    try:
        index = discover_index(client)
        log.info("ORI index: %s", index)

        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id FROM documents
            WHERE source = 'ori'
              AND document_date IS NULL
              AND (url IS NOT NULL AND url != '')
            ORDER BY id
        """)
        rows = [r["id"] for r in cur.fetchall()]
        cur.close()
        log.info("Found %d ORI docs with url but no document_date", len(rows))

        updated = 0
        missed = 0

        for i in range(0, len(rows), BATCH):
            batch = rows[i:i + BATCH]
            dates = fetch_dates_batch(client, index, batch)

            if dry_run:
                log.info("Batch %d-%d: %d/%d dates found from ORI",
                         i, i + len(batch), len(dates), len(batch))
                updated += len(dates)
                missed += len(batch) - len(dates)
                continue

            cur2 = conn.cursor()
            for doc_id in batch:
                date = dates.get(doc_id)
                if date:
                    cur2.execute(
                        "UPDATE documents SET document_date = %s::date WHERE id = %s AND document_date IS NULL",
                        (date, doc_id),
                    )
                    if cur2.rowcount:
                        updated += 1
                else:
                    missed += 1
            conn.commit()
            cur2.close()

            if (i // BATCH) % 5 == 0:
                log.info("Progress: %d updated, %d missed so far", updated, missed)

        log.info("Done — updated: %d, missed: %d", updated, missed)
        return 0

    finally:
        conn.close()
        client.close()


if __name__ == "__main__":
    sys.exit(main())
