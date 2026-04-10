#!/usr/bin/env python3
"""
Discover financial PDFs from the ORI (Open Raadsinformatie) API for Rotterdam.

Queries the ORI Elasticsearch endpoint for financial MediaObjects across
Tier 1-3 categories, registers them in staging.financial_documents, and
optionally downloads the PDFs for Docling processing.

Usage:
    python scripts/discover_ori_financial.py                          # Discover all tiers
    python scripts/discover_ori_financial.py --tier 1                 # Only Tier 1
    python scripts/discover_ori_financial.py --category grondexploitatie  # Single category
    python scripts/discover_ori_financial.py --list                   # List without registering
    python scripts/discover_ori_financial.py --download               # Download PDFs after discovery
"""

import argparse
import asyncio
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import httpx
import psycopg2

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ORI_BASE_URL = "https://api.openraadsinformatie.nl/v1/elastic"
FALLBACK_INDEX = "ori_rotterdam_20250629013104"

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
PDF_BASE_DIR = Path("data/financial_pdfs/ori")

# Max results per scroll page
SCROLL_PAGE_SIZE = 500
# httpx timeout for large downloads
HTTP_TIMEOUT = 60.0
DOWNLOAD_TIMEOUT = 300.0

# ---------------------------------------------------------------------------
# Tier definitions
# ---------------------------------------------------------------------------

TIER1_QUERIES = [
    {"name": "grondexploitatie", "query": "grondexploitatie", "est_count": 266},
    {"name": "gr_begroting", "query": "zienswijze begroting gemeenschappelijke regeling", "est_count": 200},
    {"name": "monitor_werk_inkomen", "query": "monitor werk inkomen", "est_count": 93},
    {"name": "accountantsverslag", "query": "accountantsverslag accountantsrapport", "est_count": 76},
    {"name": "kredietvoorstel", "query": "krediet investeringsvoorstel", "est_count": 56},
]

TIER2_QUERIES = [
    {"name": "begrotingswijziging", "query": "begrotingswijziging", "est_count": 128},
    {"name": "voortgangsrapportage", "query": "voortgangsrapportage financieel", "est_count": 200},
    {"name": "belastingverordening", "query": "belastingverordening legesverordening", "est_count": 116},
]

TIERS: Dict[int, List[dict]] = {
    1: TIER1_QUERIES,
    2: TIER2_QUERIES,
}


# ---------------------------------------------------------------------------
# ORI index discovery
# ---------------------------------------------------------------------------

async def discover_index(client: httpx.AsyncClient) -> str:
    """Auto-discover the latest Rotterdam ORI index."""
    try:
        resp = await client.get(
            f"{ORI_BASE_URL}/_cat/indices?format=json",
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        indices = resp.json()
        rotterdam = sorted(
            [
                idx["index"]
                for idx in indices
                if "rotterdam" in idx["index"].lower() and idx["index"].startswith("ori_")
            ]
        )
        if rotterdam:
            index = rotterdam[-1]
            print(f"Discovered ORI index: {index}")
            return index
    except Exception as e:
        print(f"Index discovery failed ({e}), using fallback")
    print(f"Using fallback index: {FALLBACK_INDEX}")
    return FALLBACK_INDEX


# ---------------------------------------------------------------------------
# Elasticsearch query helpers
# ---------------------------------------------------------------------------

def build_search_body(search_query: str, size: int = SCROLL_PAGE_SIZE, search_after: Optional[list] = None) -> dict:
    """Build an ES query body for financial MediaObject discovery."""
    body: dict = {
        "query": {
            "bool": {
                "must": [
                    {"term": {"@type": "MediaObject"}},
                    {
                        "multi_match": {
                            "query": search_query,
                            "fields": ["name^3", "description"],
                            "type": "best_fields",
                        }
                    },
                ]
            }
        },
        "size": size,
        "sort": [{"last_discussed_at": "desc"}, {"_id": "asc"}],
        "_source": [
            "name",
            "original_url",
            "url",
            "last_discussed_at",
            "description",
            "size_in_bytes",
        ],
    }
    if search_after is not None:
        body["search_after"] = search_after
    return body


async def search_category(
    client: httpx.AsyncClient,
    index: str,
    category: dict,
) -> List[dict]:
    """Fetch all MediaObject hits for a single category using search_after pagination."""
    search_url = f"{ORI_BASE_URL}/{index}/_search"
    all_hits: List[dict] = []
    search_after = None

    while True:
        body = build_search_body(category["query"], search_after=search_after)
        resp = await client.post(search_url, json=body, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])

        if not hits:
            break

        for hit in hits:
            source = hit.get("_source", {})
            original_url = source.get("original_url") or source.get("url")
            if not original_url:
                continue

            all_hits.append({
                "ori_id": hit["_id"],
                "name": source.get("name", ""),
                "original_url": original_url,
                "last_discussed_at": source.get("last_discussed_at"),
                "description": source.get("description", ""),
                "size_in_bytes": source.get("size_in_bytes"),
                "category": category["name"],
            })

        # Pagination: use sort values from the last hit
        last_sort = hits[-1].get("sort")
        if last_sort is None or len(hits) < SCROLL_PAGE_SIZE:
            break
        search_after = last_sort

    return all_hits


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def deduplicate_hits(hits: List[dict]) -> List[dict]:
    """Remove duplicates by original_url, keeping the first occurrence."""
    seen_urls: set = set()
    unique: List[dict] = []
    for h in hits:
        url = h["original_url"]
        if url not in seen_urls:
            seen_urls.add(url)
            unique.append(h)
    return unique


# ---------------------------------------------------------------------------
# Fiscal year classification
# ---------------------------------------------------------------------------

def classify_fiscal_year(last_discussed_at: Optional[str]) -> Optional[int]:
    """Derive fiscal_year from last_discussed_at ISO timestamp."""
    if not last_discussed_at:
        return None
    try:
        dt = datetime.fromisoformat(last_discussed_at.replace("Z", "+00:00"))
        return dt.year
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Staging database operations
# ---------------------------------------------------------------------------

def ensure_staging_table(conn) -> None:
    """Create staging.financial_documents if it does not exist."""
    cur = conn.cursor()
    cur.execute("CREATE SCHEMA IF NOT EXISTS staging;")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS staging.financial_documents (
            id TEXT PRIMARY KEY,
            doc_type TEXT NOT NULL,
            fiscal_year INTEGER,
            document_date DATE,
            budget_years INTEGER[],
            source_url TEXT,
            source TEXT DEFAULT 'ori',
            pdf_path TEXT,
            page_count INTEGER,
            docling_tables_found INTEGER,
            docling_chunks_created INTEGER,
            review_status TEXT DEFAULT 'pending',
            quality_score FLOAT,
            promoted_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW()
        );
    """)
    conn.commit()
    cur.close()


def load_existing_ids(conn) -> Tuple[set, set]:
    """Return sets of (existing_ids, existing_urls) from staging.financial_documents."""
    cur = conn.cursor()
    cur.execute("SELECT id, source_url FROM staging.financial_documents WHERE source = 'ori'")
    rows = cur.fetchall()
    cur.close()
    ids = {r[0] for r in rows}
    urls = {r[1] for r in rows if r[1]}
    return ids, urls


def parse_budget_years_from_name(name: str) -> List[int]:
    """Extract target fiscal years from a doc name like 'Begroting 2025-2028'."""
    if not name:
        return []
    keywords = (
        r"begroting|jaarstukken|jaarrekening|voorjaarsnota|"
        r"eerste herziening|tweede herziening|"
        r"eindejaarsbrief|10-maandsrapportage|10\s*-?\s*maandsrapportage|"
        r"meerjaren\w*|kadernota|tussenrapportage"
    )
    pattern = re.compile(
        rf"\b(?:{keywords})\s*[:\-]?\s*"
        r"(?P<y1>(?:19|20)\d{2})"
        r"(?:\s*[-–/]\s*(?P<y2>(?:19|20)\d{2}))?",
        re.IGNORECASE,
    )
    years: set = set()
    for m in pattern.finditer(name):
        y1 = int(m.group("y1"))
        y2 = m.group("y2")
        if y2:
            y2 = int(y2)
            if y1 <= y2 and y2 - y1 < 10:
                years.update(range(y1, y2 + 1))
            else:
                years.add(y1)
        else:
            years.add(y1)
    return sorted(years)


def register_documents(conn, docs: List[dict]) -> int:
    """Insert discovered documents into staging.financial_documents. Returns count of newly inserted rows."""
    if not docs:
        return 0

    inserted = 0
    for doc in docs:
        # document_date = the date the doc was discussed in council
        last_discussed = doc.get("last_discussed_at")
        document_date = last_discussed[:10] if last_discussed else None

        # budget_years = target fiscal years extracted from the ORI document name
        budget_years = parse_budget_years_from_name(doc.get("name", ""))

        # primary fiscal_year: prefer the FIRST budget year, fall back to the discussion year
        if budget_years:
            fiscal_year = budget_years[0]
        else:
            fiscal_year = classify_fiscal_year(last_discussed)

        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO staging.financial_documents
                    (id, doc_type, fiscal_year, document_date, budget_years,
                     source_url, source, review_status, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, 'ori', 'pending', NOW())
                ON CONFLICT (id) DO NOTHING
                """,
                (
                    doc["ori_id"],
                    doc["category"],
                    fiscal_year,
                    document_date,
                    budget_years if budget_years else None,
                    doc["original_url"],
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
            conn.commit()
            cur.close()
        except Exception as e:
            conn.rollback()
            print(f"  DB error for {doc['ori_id']}: {e}")
            continue
    return inserted


# ---------------------------------------------------------------------------
# PDF download
# ---------------------------------------------------------------------------

async def download_pdf(
    client: httpx.AsyncClient,
    doc: dict,
    base_dir: Path,
) -> Optional[str]:
    """Download a single PDF. Returns local path on success, None on failure."""
    category_dir = base_dir / doc["category"]
    category_dir.mkdir(parents=True, exist_ok=True)

    # Sanitise filename from document name, fall back to ORI id
    safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in doc["name"][:120])
    safe_name = safe_name.strip() or doc["ori_id"]
    filename = f"{doc['ori_id']}_{safe_name}.pdf"
    filepath = category_dir / filename

    if filepath.exists():
        return str(filepath)

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/pdf,*/*",
    }
    try:
        resp = await client.get(
            doc["original_url"],
            timeout=DOWNLOAD_TIMEOUT,
            follow_redirects=True,
            headers=headers,
        )
        resp.raise_for_status()
        filepath.write_bytes(resp.content)
        return str(filepath)
    except Exception as e:
        print(f"  Download failed for {doc['ori_id']}: {e}")
        return None


async def download_batch(
    docs: List[dict],
    base_dir: Path,
    concurrency: int = 5,
) -> Dict[str, str]:
    """Download PDFs with bounded concurrency. Returns {ori_id: local_path}."""
    sem = asyncio.Semaphore(concurrency)
    results: Dict[str, str] = {}

    async def _download_one(client: httpx.AsyncClient, doc: dict):
        async with sem:
            path = await download_pdf(client, doc, base_dir)
            if path:
                results[doc["ori_id"]] = path

    async with httpx.AsyncClient() as client:
        tasks = [_download_one(client, doc) for doc in docs]
        await asyncio.gather(*tasks, return_exceptions=True)

    return results


def update_pdf_paths(conn, paths: Dict[str, str]) -> None:
    """Update pdf_path in staging for downloaded files."""
    if not paths:
        return
    cur = conn.cursor()
    for ori_id, path in paths.items():
        cur.execute(
            "UPDATE staging.financial_documents SET pdf_path = %s WHERE id = %s",
            (path, ori_id),
        )
    conn.commit()
    cur.close()


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

def print_summary(
    results: Dict[str, List[dict]],
    existing_ids: set,
    existing_urls: set,
    newly_registered: Dict[str, int],
) -> None:
    """Print a formatted summary table."""
    header = f"{'Category':<28} {'Found':>6} {'Staged':>7} {'New':>5} {'Est. Size':>10}"
    print()
    print(header)
    print("-" * len(header))

    total_found = 0
    total_staged = 0
    total_new = 0
    total_bytes = 0

    for category_name, docs in results.items():
        found = len(docs)
        already = sum(
            1
            for d in docs
            if d["ori_id"] in existing_ids or d["original_url"] in existing_urls
        )
        new = newly_registered.get(category_name, 0)
        size_bytes = sum(d.get("size_in_bytes") or 0 for d in docs)

        total_found += found
        total_staged += already
        total_new += new
        total_bytes += size_bytes

        size_str = _fmt_bytes(size_bytes)
        print(f"{category_name:<28} {found:>6} {already:>7} {new:>5} {size_str:>10}")

    print("-" * len(header))
    print(f"{'TOTAL':<28} {total_found:>6} {total_staged:>7} {total_new:>5} {_fmt_bytes(total_bytes):>10}")
    print()


def _fmt_bytes(b: int) -> str:
    if b == 0:
        return "-"
    if b < 1024:
        return f"{b} B"
    if b < 1024 * 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b / (1024 * 1024):.1f} MB"


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def run(
    tiers: Optional[List[int]] = None,
    categories: Optional[List[str]] = None,
    list_only: bool = False,
    do_download: bool = False,
) -> None:
    # Determine which category queries to run
    queries: List[dict] = []
    if categories:
        all_queries = TIER1_QUERIES + TIER2_QUERIES
        for cat in categories:
            match = [q for q in all_queries if q["name"] == cat]
            if match:
                queries.extend(match)
            else:
                print(f"Unknown category: {cat}  (available: {[q['name'] for q in all_queries]})")
                sys.exit(1)
    elif tiers:
        for t in tiers:
            if t not in TIERS:
                print(f"Unknown tier: {t}  (available: {list(TIERS.keys())})")
                sys.exit(1)
            queries.extend(TIERS[t])
    else:
        # All tiers
        for tier_queries in TIERS.values():
            queries.extend(tier_queries)

    print(f"Discovering {len(queries)} categories from ORI API...")

    # Discover index and search
    async with httpx.AsyncClient() as client:
        index = await discover_index(client)

        results: Dict[str, List[dict]] = {}
        for q in queries:
            print(f"  Searching: {q['name']} ...", end=" ", flush=True)
            hits = await search_category(client, index, q)
            hits = deduplicate_hits(hits)
            results[q["name"]] = hits
            print(f"{len(hits)} docs")

    # Connect to staging DB (unless list-only)
    existing_ids: set = set()
    existing_urls: set = set()
    newly_registered: Dict[str, int] = {}

    if not list_only:
        conn = psycopg2.connect(DB_URL)
        try:
            ensure_staging_table(conn)
            existing_ids, existing_urls = load_existing_ids(conn)

            for category_name, docs in results.items():
                # Filter out already-known documents
                new_docs = [
                    d
                    for d in docs
                    if d["ori_id"] not in existing_ids and d["original_url"] not in existing_urls
                ]
                count = register_documents(conn, new_docs)
                newly_registered[category_name] = count

            # Reload after registration
            existing_ids, existing_urls = load_existing_ids(conn)
        finally:
            conn.close()

    # Summary
    print_summary(results, existing_ids, existing_urls, newly_registered)

    if list_only:
        print("(--list mode: no documents were registered in staging)")
        return

    total_new = sum(newly_registered.values())
    print(f"Registered {total_new} new documents in staging.financial_documents")

    # Optional download
    if do_download:
        all_docs = [d for docs in results.values() for d in docs]
        print(f"\nDownloading {len(all_docs)} PDFs to {PDF_BASE_DIR}/ ...")
        paths = await download_batch(all_docs, PDF_BASE_DIR)
        print(f"Downloaded {len(paths)} PDFs")

        if paths:
            conn = psycopg2.connect(DB_URL)
            try:
                update_pdf_paths(conn, paths)
            finally:
                conn.close()
            print(f"Updated pdf_path for {len(paths)} documents in staging")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Discover financial PDFs from the ORI API for Rotterdam"
    )
    parser.add_argument(
        "--tier",
        type=int,
        choices=list(TIERS.keys()),
        help="Only search a specific tier (1 or 2)",
    )
    parser.add_argument(
        "--category",
        type=str,
        help="Only search a specific category (e.g. grondexploitatie)",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        dest="list_only",
        help="List discovered documents without registering in staging",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download PDFs after discovery",
    )
    args = parser.parse_args()

    tiers = [args.tier] if args.tier else None
    categories = [args.category] if args.category else None

    asyncio.run(run(tiers=tiers, categories=categories, list_only=args.list_only, do_download=args.download))


if __name__ == "__main__":
    main()
