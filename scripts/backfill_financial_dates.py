#!/usr/bin/env python3
"""
Backfill document_date + budget_years on staging.financial_documents.

Why: classify_fiscal_year() in discover_ori_financial.py used last_discussed_at,
but for begroting docs the meaningful year is the TARGET fiscal year embedded
in the document name (e.g. "Begroting 2025" discussed in 2024).

This script:
  1. Sets document_date = last_discussed_at::date for ORI docs
  2. Re-fetches the original ORI name and extracts target budget years from
     patterns like "Begroting 2025", "Begroting 2025-2028", "Jaarstukken 2024"
  3. Falls back to scanning the first 5KB of document content for the same patterns
  4. Updates staging.financial_documents with both columns
"""

import asyncio
import os
import re
import sys
from pathlib import Path

import httpx
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

load_dotenv()


def _build_db_url():
    url = os.getenv("DATABASE_URL", "")
    if url:
        return url
    h = os.getenv("DB_HOST", "localhost")
    p = os.getenv("DB_PORT", "5432")
    d = os.getenv("DB_NAME", "neodemos")
    u = os.getenv("DB_USER", "postgres")
    pw = os.getenv("DB_PASSWORD", "postgres")
    return f"postgresql://{u}:{pw}@{h}:{p}/{d}"


DB_URL = _build_db_url()
ORI_BASE = "https://api.openraadsinformatie.nl/v1/elastic"
ORI_INDEX = "ori_rotterdam_20250629013104"

# Patterns matched against document names to extract budget years
# - "Begroting 2025"            → [2025]
# - "Begroting 2025-2028"       → [2025, 2026, 2027, 2028]
# - "Jaarstukken 2024"          → [2024]
# - "Voorjaarsnota 2024"        → [2024]
# - "10-maandsrapportage 2023"  → [2023]
# - "Eindejaarsbrief 2024"      → [2024]
DOC_KEYWORDS = (
    r"begroting|jaarstukken|jaarrekening|voorjaarsnota|"
    r"eerste herziening|tweede herziening|"
    r"eindejaarsbrief|10-maandsrapportage|10\s*-?\s*maandsrapportage|"
    r"meerjaren\w*|kadernota|tussenrapportage"
)
YEAR_RE = re.compile(
    rf"\b(?:{DOC_KEYWORDS})\s*[:\-]?\s*"
    r"(?P<year1>(?:19|20)\d{2})"
    r"(?:\s*[-–/]\s*(?P<year2>(?:19|20)\d{2}))?",
    re.IGNORECASE,
)
# Standalone year-range like "2025-2028" near the keywords
RANGE_RE = re.compile(r"\b((?:19|20)\d{2})\s*[-–/]\s*((?:19|20)\d{2})\b")


def parse_budget_years(text: str) -> list[int]:
    """Extract budget target years from a document name or first page of content."""
    if not text:
        return []
    years: set[int] = set()
    for m in YEAR_RE.finditer(text):
        y1 = int(m.group("year1"))
        y2 = m.group("year2")
        if y2:
            y2 = int(y2)
            if y1 <= y2 and y2 - y1 < 10:  # safety: cap at 10-year ranges
                years.update(range(y1, y2 + 1))
            else:
                years.add(y1)
        else:
            years.add(y1)
    return sorted(years)


async def fetch_ori_metadata(doc_ids: list[str]) -> dict[str, dict]:
    """Bulk-fetch ORI metadata (name + last_discussed_at) for a list of doc IDs."""
    out: dict[str, dict] = {}
    # ORI batch size
    BATCH = 100
    async with httpx.AsyncClient(timeout=60) as client:
        for i in range(0, len(doc_ids), BATCH):
            chunk = doc_ids[i : i + BATCH]
            query = {
                "query": {"terms": {"_id": chunk}},
                "_source": ["name", "last_discussed_at"],
                "size": BATCH,
            }
            try:
                r = await client.post(
                    f"{ORI_BASE}/{ORI_INDEX}/_search", json=query
                )
                r.raise_for_status()
                for hit in r.json().get("hits", {}).get("hits", []):
                    src = hit.get("_source", {})
                    out[hit["_id"]] = {
                        "name": src.get("name", ""),
                        "last_discussed_at": src.get("last_discussed_at"),
                    }
            except Exception as e:
                print(f"  ORI fetch error on batch {i}: {e}")
            print(f"  fetched {len(out)}/{len(doc_ids)} ORI metadata", flush=True)
    return out


def main():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Pull all ORI docs that need backfill
    cur.execute(
        """
        SELECT s.id, s.doc_type, s.fiscal_year, s.document_date, s.budget_years,
               d.name AS prod_name,
               substring(d.content, 1, 5000) AS content_head
        FROM staging.financial_documents s
        LEFT JOIN documents d ON d.id = s.id
        WHERE s.source = 'ori'
        ORDER BY s.id
        """
    )
    rows = cur.fetchall()
    print(f"Loaded {len(rows)} ORI staging rows", flush=True)

    # Fetch original ORI names so we have the authoritative source
    doc_ids = [r["id"] for r in rows]
    ori_meta = asyncio.run(fetch_ori_metadata(doc_ids))
    print(f"Got {len(ori_meta)} ORI metadata records", flush=True)

    updates = []  # (doc_id, document_date, budget_years, primary_year)
    parsed_from_name = 0
    parsed_from_content = 0
    no_years = 0

    for row in rows:
        doc_id = row["id"]
        meta = ori_meta.get(doc_id, {})
        ori_name = meta.get("name") or row["prod_name"] or ""
        last_discussed = meta.get("last_discussed_at")

        # 1. document_date from last_discussed_at
        document_date = None
        if last_discussed:
            document_date = last_discussed[:10]  # YYYY-MM-DD

        # 2. budget_years: try ORI name first, then content
        years = parse_budget_years(ori_name)
        if years:
            parsed_from_name += 1
        else:
            # Fall back to scanning first 5KB of content for the doc-type keywords + year
            content_head = row["content_head"] or ""
            years = parse_budget_years(content_head)
            if years:
                parsed_from_content += 1
            else:
                no_years += 1

        # primary fiscal_year = first (earliest) year if we found any
        primary_year = years[0] if years else row["fiscal_year"]

        updates.append((doc_id, document_date, years if years else None, primary_year))

    print(
        f"\nParsed from ORI name: {parsed_from_name}\n"
        f"Parsed from content:  {parsed_from_content}\n"
        f"No years found:       {no_years}\n"
        f"Total to update:      {len(updates)}\n",
        flush=True,
    )

    # Bulk update
    p_cur = conn.cursor()
    BATCH = 500
    written = 0
    for i in range(0, len(updates), BATCH):
        batch = updates[i : i + BATCH]
        execute_values(
            p_cur,
            """
            UPDATE staging.financial_documents AS sf
            SET document_date = v.document_date::date,
                budget_years = v.budget_years::int[],
                fiscal_year = v.primary_year::int
            FROM (VALUES %s) AS v(id, document_date, budget_years, primary_year)
            WHERE sf.id = v.id
            """,
            batch,
            template="(%s, %s, %s::int[], %s)",
        )
        written += len(batch)
        print(f"  updated {written}/{len(updates)}", flush=True)
    conn.commit()

    p_cur.close()
    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
