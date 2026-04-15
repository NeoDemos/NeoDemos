#!/usr/bin/env python3
"""
Backfill full content + URL for ORI Report stubs by fetching their paired MediaObject.

Background
----------
Open Raadsinformatie (ORI) stores each document as two separate records:
  - @type=Report       -> metadata only (title, no URL, no text)
  - @type=MediaObject  -> the actual PDF (original_url, text field with full content)

Our ingestion pulled the Report into `documents` rows but never traversed the link
to fetch the MediaObject's content + URL. Result: ~3,300 documents corpus-wide
exist as 80-char title stubs — unusable for retrieval.

Discovery query for stubs:
  SELECT id, name, LENGTH(content) FROM documents
  WHERE source='ori'
    AND (url IS NULL OR url='')
    AND LENGTH(COALESCE(content,'')) < 500;

For each stub, this script queries ORI via:
  {match_phrase: {name: "<stub name>"}} AND {term: {"@type": "MediaObject"}}

MediaObjects use the pattern:  "[NNbbNNNNNN] <Report name>"
so the phrase match finds exactly one MediaObject per Report in ~95% of cases.

Usage (in order of safety)
--------------------------
  # 1. Single-doc dry-run — Tommy Tomato only
  python scripts/ws4_backfill_ori_mediaobjects.py --ids 7779452 --dry-run

  # 2. Apply to Tommy Tomato only (verify end-to-end)
  python scripts/ws4_backfill_ori_mediaobjects.py --ids 7779452 --apply

  # 3. Dry-run 10 random stubs to check pattern
  python scripts/ws4_backfill_ori_mediaobjects.py --limit 10 --dry-run

  # 4. Scale: apply to 50 schriftelijke_vraag stubs
  python scripts/ws4_backfill_ori_mediaobjects.py \
      --classification schriftelijke_vraag --limit 50 --apply

  # After applying, re-chunk + re-embed the updated docs:
  python -m services.document_processor --limit 200

Safety
------
* --dry-run is the default; --apply must be explicit.
* Uses pg_advisory_lock(42) to coordinate with other writers.
* Never modifies docs with LENGTH(content) >= 500 or url set.
* Only updates docs where exactly 1 MediaObject matches the Report name.
* Logs every action into document_events for auditability.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import psycopg2
from psycopg2.extras import RealDictCursor

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("ws4_backfill")

ORI_BASE = "https://api.openraadsinformatie.nl/v1/elastic"
ADVISORY_LOCK = 42  # shared with pipeline writers — see CLAUDE.md house rules


def db_connect():
    url = os.getenv("DATABASE_URL")
    if not url:
        pw = os.getenv("DB_PASSWORD", "")
        host = os.getenv("DB_HOST", "127.0.0.1")
        port = os.getenv("DB_PORT", "5432")
        name = os.getenv("DB_NAME", "neodemos")
        user = os.getenv("DB_USER", "postgres")
        url = f"postgresql://{user}:{pw}@{host}:{port}/{name}"
    return psycopg2.connect(url)


def discover_ori_index(client: httpx.Client) -> str:
    resp = client.get(f"{ORI_BASE}/_cat/indices?format=json", timeout=30)
    resp.raise_for_status()
    indices = resp.json()
    rotterdam = [i["index"] for i in indices if "rotterdam" in i.get("index", "").lower()]
    if not rotterdam:
        raise RuntimeError("No Rotterdam ORI index found")
    # Pick the newest (highest timestamp suffix)
    rotterdam.sort(reverse=True)
    return rotterdam[0]


def find_stubs(conn, ids: list[str] | None, classification: str | None,
               limit: int, exclude_ids: set[str] | None = None) -> list[dict]:
    cur = conn.cursor(cursor_factory=RealDictCursor)
    if ids:
        cur.execute("""
            SELECT id, name, doc_classification, category, content,
                   LENGTH(COALESCE(content,'')) AS content_len, url
            FROM documents
            WHERE id = ANY(%s)
        """, (ids,))
    else:
        params: list[Any] = []
        q = """
            SELECT id, name, doc_classification, category, content,
                   LENGTH(COALESCE(content,'')) AS content_len, url
            FROM documents
            WHERE source = 'ori'
              AND (url IS NULL OR url = '')
              AND LENGTH(COALESCE(content,'')) < 500
        """
        if classification:
            q += " AND doc_classification = %s"
            params.append(classification)
        if exclude_ids:
            q += " AND id <> ALL(%s)"
            params.append(list(exclude_ids))
        q += " ORDER BY id LIMIT %s"
        params.append(limit)
        cur.execute(q, params)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    return rows


def find_mediaobject(client: httpx.Client, index: str, stub_name: str) -> dict | None:
    """Return the single matching MediaObject for a Report name, or None.

    Strict: requires exactly 1 hit to avoid merging the wrong content.
    """
    query = {
        "size": 3,
        "query": {
            "bool": {
                "must": [
                    {"match_phrase": {"name": stub_name}},
                    {"term": {"@type": "MediaObject"}},
                ]
            }
        },
        "_source": [
            "@id", "name", "url", "original_url",
            "last_discussed_at", "text", "content_type",
        ],
    }
    try:
        resp = client.post(f"{ORI_BASE}/{index}/_search", json=query, timeout=30)
        resp.raise_for_status()
    except Exception as e:
        log.warning("ORI search failed for %r: %s", stub_name[:60], e)
        return None

    hits = resp.json().get("hits", {}).get("hits", [])
    if not hits:
        return None

    # Only accept if exactly one hit OR one hit clearly matches the Report name
    # suffix (after the "[NNbbNNNNNN] " prefix). This avoids ambiguous merges.
    exact_matches = []
    for h in hits:
        src = h.get("_source", {})
        nm = (src.get("name") or "").strip()
        # Strip leading bracketed BB-code prefix if present
        if nm.startswith("[") and "]" in nm:
            tail = nm.split("]", 1)[1].lstrip()
        else:
            tail = nm
        if tail == stub_name:
            exact_matches.append(src)

    if len(exact_matches) == 1:
        return exact_matches[0]

    # Fallback: single hit overall, accept it
    if len(hits) == 1:
        return hits[0].get("_source") or {}

    log.warning("Ambiguous MediaObject match for %r: %d hits, %d exact",
                stub_name[:60], len(hits), len(exact_matches))
    return None


def extract_content(media: dict) -> tuple[str, str | None, str | None]:
    """Return (text, original_url, last_discussed_at) from a MediaObject source."""
    text_parts = media.get("text", [])
    if isinstance(text_parts, list):
        text = "\n\n".join(t for t in text_parts if t)
    else:
        text = text_parts or ""
    url = media.get("original_url") or media.get("url") or None
    date = media.get("last_discussed_at") or None
    return text, url, date


def log_event(cur, doc_id: str, event_type: str, details: dict, triggered_by: str) -> None:
    cur.execute(
        """INSERT INTO document_events (document_id, event_type, details, triggered_by)
           VALUES (%s, %s, %s::jsonb, %s)""",
        (doc_id, event_type, json.dumps(details), triggered_by),
    )


def apply_update(conn, doc_id: str, new_content: str, new_url: str | None,
                 old_content_len: int, new_content_len: int,
                 media_id: str, triggered_by: str) -> None:
    """Update document content+url and drop existing chunks for re-processing."""
    cur = conn.cursor()
    try:
        cur.execute("UPDATE documents SET content = %s, url = %s WHERE id = %s",
                    (new_content, new_url, doc_id))
        cur.execute("DELETE FROM document_chunks WHERE document_id = %s", (doc_id,))
        chunks_deleted = cur.rowcount
        log_event(cur, doc_id, "ori_mediaobject_backfilled", {
            "ori_media_id": media_id,
            "old_content_len": old_content_len,
            "new_content_len": new_content_len,
            "url_captured": bool(new_url),
            "chunks_deleted": chunks_deleted,
        }, triggered_by)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def acquire_advisory_lock(conn) -> bool:
    """Try to acquire shared advisory lock — refuse to run if another writer holds it."""
    cur = conn.cursor()
    cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK,))
    got = cur.fetchone()[0]
    cur.close()
    return bool(got)


def release_advisory_lock(conn) -> None:
    cur = conn.cursor()
    cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK,))
    cur.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ids", default="", help="Comma-separated document IDs to target (overrides --limit/--classification)")
    parser.add_argument("--classification", default=None, help="Only target this doc_classification (e.g. schriftelijke_vraag)")
    parser.add_argument("--limit", type=int, default=10, help="Max docs to process (default 10 for safety)")
    parser.add_argument("--apply", action="store_true", help="Actually write (default is dry-run)")
    parser.add_argument("--triggered-by", default="cli", help="Label for document_events")
    args = parser.parse_args()

    ids = [i.strip() for i in args.ids.split(",") if i.strip()] or None
    dry_run = not args.apply

    mode = "DRY-RUN" if dry_run else "APPLY"
    log.info("Mode: %s", mode)
    log.info("Filter: ids=%s classification=%s limit=%s",
             ids, args.classification, args.limit)

    conn = db_connect()
    http = httpx.Client()

    try:
        index = discover_ori_index(http)
        log.info("ORI index: %s", index)

        if not dry_run:
            if not acquire_advisory_lock(conn):
                log.error("Advisory lock %s is held by another writer — refusing to apply. Rerun later.",
                          ADVISORY_LOCK)
                return 2

        stubs = find_stubs(conn, ids=ids, classification=args.classification, limit=args.limit)
        log.info("Found %d stub(s) to process", len(stubs))

        stats = {"matched": 0, "no_match": 0, "ambiguous": 0, "applied": 0, "errors": 0, "already_full": 0}

        for s in stubs:
            doc_id = s["id"]
            name = s["name"] or ""
            content_len = s["content_len"] or 0
            existing_url = s.get("url") or ""

            # Safety: skip docs that already have content or url (shouldn't match our query but defense-in-depth)
            if content_len >= 500 and existing_url:
                log.info("[SKIP %s] already healthy (len=%d, url set)", doc_id, content_len)
                stats["already_full"] += 1
                continue

            media = find_mediaobject(http, index, name)
            if not media:
                log.info("[MISS %s] no MediaObject match for %r", doc_id, name[:80])
                stats["no_match"] += 1
                continue

            text, new_url, new_date = extract_content(media)
            new_len = len(text)
            if new_len < 200:
                log.info("[WEAK %s] MediaObject text too short (%d chars) — skipping", doc_id, new_len)
                stats["no_match"] += 1
                continue

            stats["matched"] += 1
            log.info("[MATCH %s] %r | old_len=%d -> new_len=%d | url=%s",
                     doc_id, name[:60], content_len, new_len, bool(new_url))

            if dry_run:
                continue

            try:
                apply_update(
                    conn,
                    doc_id=doc_id,
                    new_content=text,
                    new_url=new_url,
                    old_content_len=content_len,
                    new_content_len=new_len,
                    media_id=str(media.get("@id") or ""),
                    triggered_by=args.triggered_by,
                )
                stats["applied"] += 1
                log.info("[APPLIED %s]", doc_id)
            except Exception as e:
                stats["errors"] += 1
                log.error("[ERROR %s] %s", doc_id, e)

        log.info("Summary: %s", stats)
        if not dry_run and stats["applied"] > 0:
            log.info("Next step: re-chunk + re-embed with `python -m services.document_processor` (or wait for APScheduler).")
        return 0 if stats["errors"] == 0 else 1

    finally:
        if not dry_run:
            try:
                release_advisory_lock(conn)
            except Exception:
                pass
        conn.close()
        http.close()


if __name__ == "__main__":
    sys.exit(main())
