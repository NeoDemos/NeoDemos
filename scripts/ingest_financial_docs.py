#!/usr/bin/env python3
"""
Financial Document Ingest Orchestrator
=======================================

End-to-end pipeline: tunnel check -> download -> parallel Docling extraction
-> staging -> quality review -> promotion.

Components:
  - scripts/download_financial_pdfs.py   — download Tier 0 PDFs
  - pipeline/financial_ingestor.py       — Docling extraction into staging
  - scripts/discover_ori_financial.py    — discover ORI Tier 1-3 PDFs
  - scripts/promote_financial_docs.py    — promote staging -> production

Usage:
    python scripts/ingest_financial_docs.py                          # Full pipeline
    python scripts/ingest_financial_docs.py --step download          # Only download
    python scripts/ingest_financial_docs.py --step extract           # Only Docling extraction
    python scripts/ingest_financial_docs.py --step promote           # Only promote
    python scripts/ingest_financial_docs.py --workers 2              # Reduce parallelism
    python scripts/ingest_financial_docs.py --doc fin_jaarstukken_2024
    python scripts/ingest_financial_docs.py --tier 0                 # Tier 0 only
    python scripts/ingest_financial_docs.py --dry-run                # Show plan
    python scripts/ingest_financial_docs.py --status                 # Pipeline status
"""

import argparse
import logging
import os
import socket
import sys
import time
import traceback
from multiprocessing import Pool
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import psycopg2
from psycopg2.extras import RealDictCursor

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

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
DEFAULT_WORKERS = 4

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------


def check_tunnel():
    """Verify SSH tunnel is active (localhost:5432 and localhost:6333 reachable)."""
    for port, name in [(5432, "PostgreSQL"), (6333, "Qdrant")]:
        try:
            sock = socket.create_connection(("localhost", port), timeout=3)
            sock.close()
        except (ConnectionRefusedError, socket.timeout, OSError):
            return False, f"{name} not reachable on localhost:{port}"
    return True, "OK"


def check_db_connectivity():
    """Verify we can connect and query the staging schema."""
    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        return True, "OK"
    except Exception as e:
        return False, str(e)


def check_docling_installed():
    """Verify Docling is importable."""
    try:
        import docling  # noqa: F401
        return True, "OK"
    except ImportError:
        return False, "Docling not installed (pip install docling)"


def run_preflight():
    """Run all pre-flight checks. Returns True if all pass."""
    print("\n" + "=" * 60)
    print("Pre-flight Checks")
    print("=" * 60)

    checks = [
        ("SSH Tunnel", check_tunnel),
        ("DB Connectivity", check_db_connectivity),
        ("Docling Installed", check_docling_installed),
    ]

    all_ok = True
    for name, check_fn in checks:
        ok, msg = check_fn()
        status = "\033[92mOK\033[0m" if ok else f"\033[91mFAIL: {msg}\033[0m"
        print(f"  {name:<25} {status}")
        if not ok:
            all_ok = False

    # Environment summary
    print(f"\n  DB URL:         {DB_URL}")
    print(f"  Project root:   {PROJECT_ROOT}")
    print(f"  Python:         {sys.version.split()[0]}")

    if not all_ok:
        print("\nPre-flight checks failed. Fix the issues above before continuing.")

    return all_ok


# ---------------------------------------------------------------------------
# Step 1: Download (Tier 0)
# ---------------------------------------------------------------------------


def ensure_staging_table(conn):
    """Create staging.financial_documents if it does not exist."""
    cur = conn.cursor()
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
    conn.commit()
    cur.close()


def step_download(tier: int | None = None, dry_run: bool = False):
    """
    Download Tier 0 PDFs via the download_financial_pdfs module.
    Delegates to scripts/download_financial_pdfs.py logic.
    """
    print("\n" + "=" * 60)
    print("Step 1: Download Financial PDFs")
    print("=" * 60)

    if tier is not None and tier != 0:
        print("  Skipping download (--tier is not 0)")
        return

    from scripts.download_financial_pdfs import run_download

    if dry_run:
        from scripts.download_financial_pdfs import KNOWN_PDFS, scrape_homepage
        scraped = scrape_homepage()
        merged = dict(KNOWN_PDFS)
        for key, url_path in scraped.items():
            if key not in merged:
                merged[key] = url_path
        print(f"\n  DRY RUN: Would download {len(merged)} PDF(s)")
        for (doc_type, year), url_path in sorted(merged.items()):
            print(f"    fin_{doc_type}_{year}: {url_path}")
        return

    run_download(type_filter=None)


# ---------------------------------------------------------------------------
# Step 2: Extract (parallel Docling)
# ---------------------------------------------------------------------------


def process_single_pdf(args_tuple):
    """
    Worker function for parallel Docling extraction.
    Each worker creates its own FinancialDocumentIngestor instance.
    Returns (doc_id, result_dict) on success or (doc_id, error_str) on failure.
    """
    pdf_path, doc_id, doc_name, doc_type, fiscal_year, source_url, db_url = args_tuple
    try:
        from pipeline.financial_ingestor import FinancialDocumentIngestor

        ingestor = FinancialDocumentIngestor(db_url=db_url)
        result = ingestor.process_pdf(
            pdf_path=pdf_path,
            doc_id=doc_id,
            doc_name=doc_name,
            doc_type=doc_type,
            fiscal_year=fiscal_year,
            source_url=source_url,
        )
        return (doc_id, result)
    except Exception as e:
        return (doc_id, f"ERROR: {e}\n{traceback.format_exc()}")


def step_extract(
    workers: int = DEFAULT_WORKERS,
    doc_filter: str | None = None,
    dry_run: bool = False,
):
    """
    Query staging.financial_documents for pending PDFs and extract
    with Docling in parallel.
    """
    print("\n" + "=" * 60)
    print("Step 2: Docling Extraction")
    print("=" * 60)

    conn = psycopg2.connect(DB_URL)
    ensure_staging_table(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Find PDFs that have been downloaded but not yet extracted
    query = """
        SELECT id, doc_type, fiscal_year, pdf_path, source_url
        FROM staging.financial_documents
        WHERE pdf_path IS NOT NULL
          AND docling_tables_found IS NULL
          AND review_status = 'pending'
    """
    params = []
    if doc_filter:
        query += " AND id = %s"
        params.append(doc_filter)

    query += " ORDER BY fiscal_year DESC, doc_type"
    cur.execute(query, params)
    pending = cur.fetchall()
    cur.close()
    conn.close()

    if not pending:
        print("  No pending PDFs to extract.")
        return

    print(f"  Found {len(pending)} PDF(s) to extract:")
    for row in pending:
        print(f"    {row['id']}: {row['pdf_path']}")

    if dry_run:
        print(f"\n  DRY RUN: Would extract {len(pending)} PDF(s) with {workers} workers")
        return

    # Build work list
    work_items = []
    for row in pending:
        pdf_path = str(PROJECT_ROOT / row["pdf_path"])
        doc_id = row["id"]
        # Construct human-readable document name
        doc_name = f"{row['doc_type'].replace('_', ' ').title()} {row['fiscal_year']}"
        doc_type = row["doc_type"]
        fiscal_year = row["fiscal_year"]
        source_url = row["source_url"] or ""
        work_items.append((pdf_path, doc_id, doc_name, doc_type, fiscal_year, source_url, DB_URL))

    # Run parallel extraction
    print(f"\n  Starting extraction with {workers} worker(s)...")
    t0 = time.time()

    try:
        from tqdm import tqdm

        if workers == 1:
            # Sequential — shows progress inline
            results = []
            for item in tqdm(work_items, desc="  Extracting"):
                results.append(process_single_pdf(item))
        else:
            with Pool(processes=workers) as pool:
                results = list(tqdm(
                    pool.imap_unordered(process_single_pdf, work_items),
                    total=len(work_items),
                    desc="  Extracting",
                ))
    except ImportError:
        # tqdm not available — plain execution
        logger.info("tqdm not installed; running without progress bar")
        if workers == 1:
            results = [process_single_pdf(item) for item in work_items]
        else:
            with Pool(processes=workers) as pool:
                results = pool.map(process_single_pdf, work_items)

    elapsed = time.time() - t0

    # Update staging with extraction stats and auto-approve
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    ok_count = 0
    fail_count = 0

    for doc_id, result in results:
        if isinstance(result, str) and result.startswith("ERROR"):
            fail_count += 1
            logger.error("Extraction failed for %s: %s", doc_id, result)
            print(f"  FAIL {doc_id}: {result.splitlines()[0]}")
        elif isinstance(result, dict):
            ok_count += 1
            tables = result.get("tables_found", 0)
            chunks = result.get("chunks_created", 0)
            pages = result.get("pages", 0)
            # Auto-approve documents with at least 1 table
            review_status = "auto_approved" if tables >= 1 else "pending"
            cur.execute("""
                UPDATE staging.financial_documents
                SET docling_tables_found = %s,
                    docling_chunks_created = %s,
                    page_count = COALESCE(page_count, %s),
                    review_status = %s
                WHERE id = %s
            """, (tables, chunks, pages, review_status, doc_id))
            print(f"  OK   {doc_id}: {tables} tables, {chunks} chunks, {pages} pages -> {review_status}")
        else:
            fail_count += 1
            print(f"  FAIL {doc_id}: Unexpected result type: {type(result)}")

    conn.commit()
    cur.close()
    conn.close()

    print(f"\n  Extraction complete in {elapsed:.1f}s")
    print(f"  Success: {ok_count}  |  Failed: {fail_count}")


# ---------------------------------------------------------------------------
# Step 3: Promote
# ---------------------------------------------------------------------------


def step_promote(doc_filter: str | None = None, dry_run: bool = False):
    """
    Promote auto_approved (or pending with tables) documents from staging
    to production.
    """
    print("\n" + "=" * 60)
    print("Step 3: Promote to Production")
    print("=" * 60)

    conn = psycopg2.connect(DB_URL)
    ensure_staging_table(conn)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    query = """
        SELECT id, doc_type, fiscal_year, docling_tables_found, docling_chunks_created
        FROM staging.financial_documents
        WHERE docling_tables_found IS NOT NULL
          AND promoted_at IS NULL
          AND review_status IN ('pending', 'auto_approved')
    """
    params = []
    if doc_filter:
        query += " AND id = %s"
        params.append(doc_filter)

    query += " ORDER BY fiscal_year DESC, doc_type"
    cur.execute(query, params)
    promotable = cur.fetchall()
    cur.close()
    conn.close()

    if not promotable:
        print("  No documents ready for promotion.")
        return

    print(f"  Found {len(promotable)} document(s) to promote:")
    for row in promotable:
        print(f"    {row['id']}: {row['docling_tables_found']} tables, "
              f"{row['docling_chunks_created']} chunks")

    if dry_run:
        print(f"\n  DRY RUN: Would promote {len(promotable)} document(s)")
        return

    # Import promotion logic
    try:
        from scripts.promote_financial_docs import promote_financial_doc
    except ImportError:
        logger.error("Cannot import promote_financial_docs — module not found")
        print("  ERROR: scripts/promote_financial_docs.py not found or not importable.")
        print("  Skipping promotion step.")
        return

    promoted = 0
    failed = 0

    for row in promotable:
        doc_id = row["id"]
        print(f"\n  Promoting {doc_id}...")
        try:
            success = promote_financial_doc(doc_id)
            if success:
                promoted += 1
                # Mark as promoted in staging
                conn = psycopg2.connect(DB_URL)
                cur = conn.cursor()
                cur.execute("""
                    UPDATE staging.financial_documents
                    SET promoted_at = NOW(), review_status = 'approved'
                    WHERE id = %s
                """, (doc_id,))
                conn.commit()
                cur.close()
                conn.close()
                print(f"  OK: {doc_id} promoted")
            else:
                failed += 1
                print(f"  FAIL: {doc_id} promotion returned False")
        except Exception as e:
            failed += 1
            logger.error("Promotion failed for %s: %s", doc_id, e)
            print(f"  FAIL: {doc_id}: {e}")

    print(f"\n  Promotion complete: {promoted} promoted, {failed} failed")


# ---------------------------------------------------------------------------
# Step 4: Verify
# ---------------------------------------------------------------------------


def step_verify():
    """Count table chunks and financial docs in production."""
    print("\n" + "=" * 60)
    print("Step 4: Production Verification")
    print("=" * 60)

    try:
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()

        # Count table chunks in production
        cur.execute("""
            SELECT count(*)
            FROM document_chunks
            WHERE chunk_type = 'table'
        """)
        table_chunks = cur.fetchone()[0]

        # Count financial docs
        cur.execute("""
            SELECT count(DISTINCT document_id)
            FROM document_chunks
            WHERE document_id LIKE 'fin_%%'
        """)
        fin_docs = cur.fetchone()[0]

        # Count financial chunks
        cur.execute("""
            SELECT count(*)
            FROM document_chunks
            WHERE document_id LIKE 'fin_%%'
        """)
        fin_chunks = cur.fetchone()[0]

        cur.close()
        conn.close()

        print(f"  Production table chunks (all):     {table_chunks}")
        print(f"  Financial documents (distinct):     {fin_docs}")
        print(f"  Financial chunks (total):           {fin_chunks}")

    except Exception as e:
        logger.error("Verification failed: %s", e)
        print(f"  ERROR: {e}")


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def show_status():
    """Show pipeline status from staging.financial_documents."""
    print("\n" + "=" * 60)
    print("Financial Document Pipeline Status")
    print("=" * 60)

    try:
        conn = psycopg2.connect(DB_URL)
        ensure_staging_table(conn)
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT
                count(*) as total,
                count(*) FILTER (WHERE pdf_path IS NOT NULL) as downloaded,
                count(*) FILTER (WHERE docling_tables_found IS NOT NULL) as extracted,
                count(*) FILTER (WHERE review_status = 'auto_approved') as auto_approved,
                count(*) FILTER (WHERE review_status = 'pending') as pending,
                count(*) FILTER (WHERE promoted_at IS NOT NULL) as promoted,
                coalesce(sum(docling_tables_found), 0) as total_tables,
                coalesce(sum(docling_chunks_created), 0) as total_chunks,
                coalesce(sum(page_count), 0) as total_pages
            FROM staging.financial_documents
        """)
        stats = cur.fetchone()

        print(f"\n  Total registered:    {stats['total']}")
        print(f"  Downloaded (PDF):    {stats['downloaded']}")
        print(f"  Extracted (Docling): {stats['extracted']}")
        print(f"  Auto-approved:       {stats['auto_approved']}")
        print(f"  Pending review:      {stats['pending']}")
        print(f"  Promoted:            {stats['promoted']}")
        print(f"\n  Total tables found:  {stats['total_tables']}")
        print(f"  Total chunks:        {stats['total_chunks']}")
        print(f"  Total pages:         {stats['total_pages']}")

        # Per-document breakdown
        cur.execute("""
            SELECT id, doc_type, fiscal_year, page_count,
                   docling_tables_found, docling_chunks_created,
                   review_status, promoted_at
            FROM staging.financial_documents
            ORDER BY doc_type, fiscal_year DESC
        """)
        rows = cur.fetchall()

        if rows:
            print(f"\n  {'Document':<30} {'Pages':>6} {'Tables':>7} {'Chunks':>7} {'Status':<15} {'Promoted'}")
            print("  " + "-" * 90)
            for r in rows:
                pages = str(r["page_count"] or "-").rjust(6)
                tables = str(r["docling_tables_found"] or "-").rjust(7)
                chunks = str(r["docling_chunks_created"] or "-").rjust(7)
                status = r["review_status"] or "?"
                promoted = str(r["promoted_at"])[:10] if r["promoted_at"] else "-"

                # Color code
                if status == "auto_approved":
                    status_display = f"\033[92m{status:<15}\033[0m"
                elif status == "pending":
                    status_display = f"\033[93m{status:<15}\033[0m"
                else:
                    status_display = f"{status:<15}"

                print(f"  {r['id']:<30} {pages} {tables} {chunks} {status_display} {promoted}")

        cur.close()
        conn.close()

    except Exception as e:
        logger.error("Status query failed: %s", e)
        print(f"  ERROR: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Financial Document Ingest Orchestrator: "
                    "tunnel check -> download -> Docling extraction -> staging -> promotion"
    )
    parser.add_argument(
        "--step",
        choices=["download", "extract", "promote"],
        help="Run only a specific step (default: run all steps)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Number of parallel Docling workers (default: {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--doc",
        type=str,
        help="Process only a single document by id (e.g. fin_jaarstukken_2024)",
    )
    parser.add_argument(
        "--tier",
        type=int,
        choices=[0, 1, 2, 3],
        help="Only process documents from a specific tier (0 = watdoetdegemeente)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show plan without executing any changes",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Show current pipeline status and exit",
    )

    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # --status: show and exit
    if args.status:
        # Quick tunnel check before querying DB
        ok, msg = check_tunnel()
        if not ok:
            print(f"\033[91mTunnel not available: {msg}\033[0m")
            sys.exit(1)
        show_status()
        step_verify()
        return

    # Pre-flight (always runs)
    if not run_preflight():
        if not args.dry_run:
            sys.exit(1)
        print("\n  (Continuing in dry-run mode despite failed pre-flight checks)")

    # Determine which steps to run
    run_all = args.step is None

    t0 = time.time()

    if run_all or args.step == "download":
        step_download(tier=args.tier, dry_run=args.dry_run)

    if run_all or args.step == "extract":
        step_extract(
            workers=args.workers,
            doc_filter=args.doc,
            dry_run=args.dry_run,
        )

    if run_all or args.step == "promote":
        step_promote(doc_filter=args.doc, dry_run=args.dry_run)

    # Verification (always runs unless --dry-run or single step)
    if run_all and not args.dry_run:
        step_verify()

    elapsed = time.time() - t0

    print("\n" + "=" * 60)
    print(f"Pipeline {'(DRY RUN) ' if args.dry_run else ''}complete in {elapsed:.1f}s")
    print("=" * 60)


if __name__ == "__main__":
    main()
