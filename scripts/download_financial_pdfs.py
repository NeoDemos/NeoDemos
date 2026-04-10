#!/usr/bin/env python3
"""
Download Rotterdam Financial PDFs from watdoetdegemeente.rotterdam.nl
=====================================================================

Scrapes the homepage for PDF links, merges with known URLs, downloads
to data/financial_pdfs/, and registers each in staging.financial_documents.

Usage:
    python scripts/download_financial_pdfs.py              # Download all
    python scripts/download_financial_pdfs.py --type jaarstukken  # Only one type
    python scripts/download_financial_pdfs.py --list        # List known PDFs without downloading
"""

import argparse
import os
import re
import socket
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import requests
from bs4 import BeautifulSoup
import psycopg2
from psycopg2.extras import execute_values

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

BASE_URL = "https://www.watdoetdegemeente.rotterdam.nl"
HOMEPAGE = BASE_URL + "/"
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
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOWNLOAD_DIR = PROJECT_ROOT / "data" / "financial_pdfs"

DOC_TYPES = ["jaarstukken", "begroting", "voorjaarsnota", "eindejaarsbrief"]

# Curated list — URLs that cannot be guessed from naming patterns.
KNOWN_PDFS = {
    # Jaarstukken
    ("jaarstukken", 2024): "/media/ydonzihe/jaarstukken-2024.pdf",
    ("jaarstukken", 2023): "/media/fmwbvijv/definitieve-jaarstukken-rotterdam-2023-gestempeld.pdf",
    ("jaarstukken", 2022): "/media/3w4dh2ie/jaarstukken-2022.pdf",
    ("jaarstukken", 2021): "/media/mp2o35tz/jaarstukken-2021.pdf",
    ("jaarstukken", 2020): "/media/5kfmunpm/jaarstukken-2020.pdf",
    ("jaarstukken", 2019): "/media/4buaqgd3/jaarstukken-2019.pdf",
    ("jaarstukken", 2018): "/media/nhul3qar/jaarstukken-2018.pdf",
    # Begroting
    ("begroting", 2026): "/media/0enptyz4/begroting-2026.pdf",
    ("begroting", 2025): "/media/wcxihfdd/begroting-2025.pdf",
    ("begroting", 2024): "/media/h10hfyhz/begroting-2024.pdf",
    ("begroting", 2023): "/media/gq1b5vsd/begroting-2023.pdf",
    ("begroting", 2022): "/media/cmah10nt/begroting-2022.pdf",
    ("begroting", 2021): "/media/lx2aqzgm/begroting-2021.pdf",
    ("begroting", 2020): "/media/kbhh0zzd/begroting-2020.pdf",
    # Voorjaarsnota
    ("voorjaarsnota", 2025): "/media/d0geym2y/voorjaarsnota-2025.pdf",
    ("voorjaarsnota", 2024): "/media/0japbqnh/voorjaarsnota-2024.pdf",
    ("voorjaarsnota", 2023): "/media/kmhflpcd/voorjaarsnota-2023.pdf",
    ("voorjaarsnota", 2022): "/media/verjlcau/eerste-herziening-2022.pdf",
    ("voorjaarsnota", 2021): "/media/25nnsjj0/voorjaarsnota-2021.pdf",
    ("voorjaarsnota", 2020): "/media/eymdcgqg/eerste-herziening-2020.pdf",
    ("voorjaarsnota", 2019): "/media/35vbtdl4/voorjaarsnota-2019.pdf",
    # Eindejaarsbrief / 10-maands
    ("eindejaarsbrief", 2025): "/media/zz2fyiwf/eindejaarsbrief-2025.pdf",
    ("eindejaarsbrief", 2024): "/media/35md3glf/eindejaarsbrief-2024.pdf",
    ("eindejaarsbrief", 2023): "/media/id1bewkn/eindejaarsbrief2023pdfbrief.pdf",
    ("eindejaarsbrief", 2022): "/media/hmvmx0r3/10-maands-2022.pdf",
    ("eindejaarsbrief", 2021): "/media/vcvmilhc/10-maands-2021.pdf",
    ("eindejaarsbrief", 2020): "/media/gj4djvej/10-maands-2020.pdf",
    ("eindejaarsbrief", 2019): "/media/waclqyt0/10-maands-2019.pdf",
}

# Patterns to classify scraped PDF filenames into doc types
TYPE_PATTERNS = [
    (r"jaarstukken", "jaarstukken"),
    (r"begroting", "begroting"),
    (r"voorjaarsnota|eerste.herziening", "voorjaarsnota"),
    (r"eindejaarsbrief|10.maands", "eindejaarsbrief"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def check_ssh_tunnel() -> bool:
    """Test if localhost:5432 is reachable (SSH tunnel or local PG)."""
    try:
        sock = socket.create_connection(("localhost", 5432), timeout=3)
        sock.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def classify_pdf_url(url_path: str) -> tuple[str | None, int | None]:
    """
    Given a URL path like /media/xxx/jaarstukken-2024.pdf, return
    (doc_type, year) or (None, None) if unclassifiable.
    """
    filename = url_path.rsplit("/", 1)[-1].lower()
    doc_type = None
    for pattern, dtype in TYPE_PATTERNS:
        if re.search(pattern, filename):
            doc_type = dtype
            break
    if doc_type is None:
        return None, None

    year_match = re.search(r"(20[12]\d)", filename)
    if year_match:
        return doc_type, int(year_match.group(1))
    return None, None


def scrape_homepage() -> dict[tuple[str, int], str]:
    """
    Scrape the homepage for PDF links under /media/ and classify them.
    Returns dict mapping (doc_type, year) -> url_path.
    """
    discovered = {}
    print(f"Scraping {HOMEPAGE} ...")
    try:
        resp = requests.get(HOMEPAGE, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  WARNING: Could not reach homepage: {e}")
        return discovered

    soup = BeautifulSoup(resp.text, "html.parser")
    links = soup.find_all("a", href=True)
    pdf_links = [a["href"] for a in links if a["href"].endswith(".pdf")]
    print(f"  Found {len(pdf_links)} PDF link(s) on homepage")

    for href in pdf_links:
        # Normalise: strip domain if full URL
        if href.startswith("http"):
            if BASE_URL in href:
                url_path = href.replace(BASE_URL, "")
            else:
                continue  # external link
        else:
            url_path = href

        doc_type, year = classify_pdf_url(url_path)
        if doc_type and year:
            key = (doc_type, year)
            if key not in discovered:
                discovered[key] = url_path
                print(f"  Discovered: {doc_type} {year} -> {url_path}")

    return discovered


def make_doc_id(doc_type: str, year: int) -> str:
    return f"fin_{doc_type}_{year}"


def download_pdf(url_path: str, doc_type: str, year: int) -> Path | None:
    """
    Download a PDF to DOWNLOAD_DIR/{doc_type}/{filename}.
    Returns the local path or None on failure.
    Skips if file already exists with size > 0.
    """
    filename = url_path.rsplit("/", 1)[-1]
    type_dir = DOWNLOAD_DIR / doc_type
    type_dir.mkdir(parents=True, exist_ok=True)
    local_path = type_dir / filename

    if local_path.exists() and local_path.stat().st_size > 0:
        print(f"  SKIP (exists): {local_path.relative_to(PROJECT_ROOT)}")
        return local_path

    full_url = BASE_URL + url_path
    print(f"  Downloading {full_url} ...")
    try:
        resp = requests.get(full_url, timeout=120, stream=True)
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                f.write(chunk)
        size_mb = local_path.stat().st_size / (1024 * 1024)
        print(f"  OK: {local_path.relative_to(PROJECT_ROOT)} ({size_mb:.1f} MB)")
        return local_path
    except requests.RequestException as e:
        print(f"  ERROR downloading {full_url}: {e}")
        if local_path.exists():
            local_path.unlink()
        return None


def get_page_count(pdf_path: Path) -> int | None:
    """Try to get page count using PyPDF2/pypdf if available."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        return len(reader.pages)
    except Exception:
        pass
    try:
        from PyPDF2 import PdfReader as PdfReader2
        reader = PdfReader2(str(pdf_path))
        return len(reader.pages)
    except Exception:
        pass
    return None


def upsert_staging(conn, records: list[dict]) -> int:
    """
    Upsert records into staging.financial_documents.
    Returns number of rows affected.
    """
    if not records:
        return 0

    cur = conn.cursor()
    # Ensure schema and table exist
    cur.execute("CREATE SCHEMA IF NOT EXISTS staging")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS staging.financial_documents (
            id TEXT PRIMARY KEY,
            doc_type TEXT NOT NULL,
            fiscal_year INTEGER NOT NULL,
            source_url TEXT,
            source TEXT DEFAULT 'watdoetdegemeente',
            pdf_path TEXT,
            page_count INTEGER,
            docling_tables_found INTEGER,
            docling_chunks_created INTEGER,
            review_status TEXT DEFAULT 'pending',
            quality_score FLOAT,
            promoted_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(doc_type, fiscal_year, source)
        )
    """)

    sql = """
        INSERT INTO staging.financial_documents
            (id, doc_type, fiscal_year, source_url, source, pdf_path, page_count, review_status)
        VALUES %s
        ON CONFLICT (id) DO UPDATE SET
            source_url = EXCLUDED.source_url,
            pdf_path   = EXCLUDED.pdf_path,
            page_count = COALESCE(EXCLUDED.page_count, staging.financial_documents.page_count)
    """
    values = [
        (
            r["id"],
            r["doc_type"],
            r["fiscal_year"],
            r["source_url"],
            "watdoetdegemeente",
            r["pdf_path"],
            r["page_count"],
            "pending",
        )
        for r in records
    ]
    execute_values(cur, sql, values)
    count = cur.rowcount
    conn.commit()
    cur.close()
    return count


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def list_known():
    """Print a table of all known PDFs."""
    print(f"\n{'Type':<18} {'Year':<6} {'URL Path'}")
    print("-" * 80)
    for (doc_type, year), url_path in sorted(KNOWN_PDFS.items()):
        print(f"{doc_type:<18} {year:<6} {url_path}")
    print(f"\nTotal: {len(KNOWN_PDFS)} known PDFs")


def run_download(type_filter: str | None = None):
    """Main download + register flow."""

    # 1. Merge known PDFs with scraped ones (scraped wins for new discoveries,
    #    known wins for existing keys since they are curated)
    scraped = scrape_homepage()
    merged = dict(KNOWN_PDFS)
    new_count = 0
    for key, url_path in scraped.items():
        if key not in merged:
            merged[key] = url_path
            new_count += 1
    if new_count:
        print(f"\nDiscovered {new_count} NEW PDF(s) not in KNOWN_PDFS — consider adding them to the script.")
    print(f"Total PDFs to process: {len(merged)}")

    # 2. Apply type filter
    if type_filter:
        if type_filter not in DOC_TYPES:
            print(f"ERROR: Unknown type '{type_filter}'. Must be one of: {', '.join(DOC_TYPES)}")
            sys.exit(1)
        merged = {k: v for k, v in merged.items() if k[0] == type_filter}
        print(f"Filtered to {len(merged)} PDFs of type '{type_filter}'")

    if not merged:
        print("Nothing to download.")
        return

    # 3. Download PDFs
    print(f"\nDownloading to {DOWNLOAD_DIR.relative_to(PROJECT_ROOT)}/ ...")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    downloaded = 0
    skipped = 0
    failed = 0

    for (doc_type, year), url_path in sorted(merged.items()):
        print(f"\n[{doc_type} {year}]")
        local_path = download_pdf(url_path, doc_type, year)
        if local_path and local_path.exists():
            was_new = local_path.stat().st_size > 0
            page_count = get_page_count(local_path)
            results.append({
                "id": make_doc_id(doc_type, year),
                "doc_type": doc_type,
                "fiscal_year": year,
                "source_url": BASE_URL + url_path,
                "pdf_path": str(local_path.relative_to(PROJECT_ROOT)),
                "page_count": page_count,
            })
            if was_new:
                downloaded += 1
            else:
                skipped += 1
        else:
            failed += 1

    # 4. Register in staging DB
    print("\n" + "=" * 60)
    print("Database registration")
    print("=" * 60)

    if not check_ssh_tunnel():
        print("WARNING: Cannot reach localhost:5432 — SSH tunnel may not be active.")
        print("Skipping database registration. Re-run once the tunnel is up.")
    else:
        try:
            conn = psycopg2.connect(DB_URL)
            upserted = upsert_staging(conn, results)
            conn.close()
            print(f"Upserted {upserted} record(s) into staging.financial_documents")
        except Exception as e:
            print(f"ERROR registering in database: {e}")

    # 5. Summary
    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  Downloaded : {downloaded}")
    print(f"  Skipped    : {skipped} (already on disk)")
    print(f"  Failed     : {failed}")
    print(f"  DB records : {len(results)}")

    if results:
        print(f"\n  Files in: {DOWNLOAD_DIR}/")
        for r in sorted(results, key=lambda x: (x["doc_type"], x["fiscal_year"])):
            pages = f" ({r['page_count']} pages)" if r["page_count"] else ""
            print(f"    {r['id']}: {r['pdf_path']}{pages}")


def main():
    parser = argparse.ArgumentParser(
        description="Download Rotterdam financial PDFs from watdoetdegemeente.rotterdam.nl"
    )
    parser.add_argument(
        "--type",
        choices=DOC_TYPES,
        help="Only download one document type",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List known PDFs without downloading",
    )
    args = parser.parse_args()

    if args.list:
        list_known()
        return

    run_download(type_filter=args.type)


if __name__ == "__main__":
    main()
