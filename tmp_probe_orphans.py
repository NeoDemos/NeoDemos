#!/usr/bin/env python3
"""
Probe a sample of UUID orphan stubs to see if ORI has richer content for them.
- Searches ORI by bb-number from doc name
- Checks if text/original_url exists
- Cross-checks our DB: does any chunk contain content from this document?
- Writes results to docs/audits/uuid_orphan_probe_results.json
"""
import asyncio
import json
import os
import re
import sys
import psycopg2
import httpx

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
ORI_URL = "https://api.openraadsinformatie.nl/v1/elastic/ori_rotterdam_20250629013104/_search"

STEALTH = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

async def probe_doc(client: httpx.AsyncClient, doc: dict, conn) -> dict:
    bb = doc.get("bb_number", "")
    name = doc.get("doc_name_clean", "") or doc.get("name", "")
    result = {
        "id": doc["id"],
        "bb_number": bb,
        "year": doc.get("year"),
        "name": name[:100],
        "ori_found": False,
        "ori_id": None,
        "ori_text_len": 0,
        "ori_url": None,
        "in_db_as_numeric": False,
        "db_numeric_id": None,
        "db_content_len": 0,
        "chunks_in_qdrant": 0,
    }

    if not bb:
        return result

    # 1. Search ORI by bb-number
    query = {
        "query": {"match": {"name": bb}},
        "_source": ["name", "text", "md_text", "original_url"],
        "size": 3,
    }
    try:
        resp = await client.post(ORI_URL, json=query, timeout=20)
        if resp.status_code == 200:
            hits = resp.json().get("hits", {}).get("hits", [])
            for h in hits:
                src = h["_source"]
                text = src.get("text") or src.get("md_text") or ""
                if isinstance(text, list):
                    text = "\n".join(str(x) for x in text if x)
                url = src.get("original_url", "")
                if len(text) > result["ori_text_len"] or (url and not result["ori_url"]):
                    result["ori_found"] = True
                    result["ori_id"] = h["_id"]
                    result["ori_text_len"] = len(text)
                    result["ori_url"] = url or None
    except Exception as e:
        result["ori_error"] = str(e)[:80]

    # 2. Check our DB for a numeric-id version of same doc
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, LENGTH(content) FROM documents WHERE name ILIKE %s AND id ~ '^[0-9]' ORDER BY LENGTH(content) DESC LIMIT 1",
            (f"%{bb}%",)
        )
        row = cur.fetchone()
        if row:
            result["in_db_as_numeric"] = True
            result["db_numeric_id"] = row[0]
            result["db_content_len"] = row[1] or 0

        # 3. Count chunks for the numeric doc
        if result["db_numeric_id"]:
            cur.execute(
                "SELECT COUNT(*) FROM document_chunks WHERE document_id = %s",
                (result["db_numeric_id"],)
            )
            result["chunks_in_qdrant"] = cur.fetchone()[0]
    except Exception:
        pass
    finally:
        cur.close()

    return result


async def main():
    with open("docs/audits/uuid_stub_orphans_2026-04-05.json") as f:
        all_docs = json.load(f)

    # Sample strategy: first 5 from each year group for a diverse probe
    from collections import defaultdict
    by_year = defaultdict(list)
    for d in all_docs:
        by_year[d.get("year") or "unknown"].append(d)

    sample = []
    for year in sorted(by_year.keys(), reverse=True):
        sample.extend(by_year[year][:5])

    print(f"Probing {len(sample)} docs (sample from {len(all_docs)} total)...")

    conn = psycopg2.connect(DB_URL)
    results = []

    async with httpx.AsyncClient(headers=STEALTH, verify=False, timeout=30) as client:
        for i, doc in enumerate(sample):
            r = await probe_doc(client, doc, conn)
            results.append(r)
            status = "ORI✓" if r["ori_found"] else "ORI✗"
            db_status = f"DB✓({r['db_content_len']}c,{r['chunks_in_qdrant']}chunks)" if r["in_db_as_numeric"] else "DB✗"
            print(f"  [{i+1}/{len(sample)}] {r['bb_number']} {status} {db_status} — {r['name'][:50]}")

    conn.close()

    # Summary
    ori_found = sum(1 for r in results if r["ori_found"])
    db_found = sum(1 for r in results if r["in_db_as_numeric"])
    has_url = sum(1 for r in results if r["ori_url"])
    print(f"\nSummary: {ori_found}/{len(results)} found in ORI, {db_found}/{len(results)} already in DB as numeric ID")
    print(f"         {has_url}/{len(results)} have an ORI original_url (downloadable PDF)")

    os.makedirs("docs/audits", exist_ok=True)
    with open("docs/audits/uuid_orphan_probe_results.json", "w") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\nResults written to docs/audits/uuid_orphan_probe_results.json")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    sys.path.insert(0, ".")
    asyncio.run(main())
