#!/usr/bin/env python3
"""
Financial Document Batch Processor — runs Docling extraction on all pending PDFs.

Must be launched via nohup to avoid sandbox child-process kills:
    nohup python scripts/run_financial_batch.py > /tmp/docling_batch.log 2>&1 &

Usage:
    python scripts/run_financial_batch.py                # Extract all pending (4 workers)
    python scripts/run_financial_batch.py --workers 2    # Control parallelism
    python scripts/run_financial_batch.py --status       # Check progress
    python scripts/run_financial_batch.py --doc fin_jaarstukken_2023  # Single doc
"""

import argparse
import json
import os
import sys
import time
from multiprocessing import Pool
from pathlib import Path

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


def get_pending_pdfs(doc_filter=None):
    """Query staging for PDFs that need Docling extraction."""
    import psycopg2

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    if doc_filter:
        cur.execute(
            """SELECT id, doc_type, fiscal_year, pdf_path, source_url
               FROM staging.financial_documents
               WHERE id = %s AND pdf_path IS NOT NULL""",
            (doc_filter,),
        )
    else:
        cur.execute(
            """SELECT id, doc_type, fiscal_year, pdf_path, source_url
               FROM staging.financial_documents
               WHERE pdf_path IS NOT NULL
                 AND docling_tables_found IS NULL
                 AND review_status = 'pending'
               ORDER BY fiscal_year DESC"""
        )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [
        {
            "id": r[0],
            "doc_type": r[1],
            "fiscal_year": r[2],
            "pdf_path": r[3],
            "source_url": r[4] or "",
        }
        for r in rows
    ]


def process_single_pdf(item):
    """Worker function — each process gets its own Docling instance."""
    doc_id = item["id"]
    doc_type = item["doc_type"]
    fiscal_year = item["fiscal_year"]
    pdf_path = item["pdf_path"]
    source_url = item["source_url"]
    doc_name = f"{doc_type.replace('_', ' ').title()} {fiscal_year}"

    print(f"[START] {doc_id} ({doc_name})", flush=True)
    start = time.time()

    try:
        from pipeline.financial_ingestor import FinancialDocumentIngestor

        ingestor = FinancialDocumentIngestor(db_url=DB_URL)
        result = ingestor.process_pdf(
            pdf_path=pdf_path,
            doc_id=doc_id,
            doc_name=doc_name,
            doc_type=doc_type,
            fiscal_year=fiscal_year,
            source_url=source_url,
        )
        elapsed = time.time() - start
        print(
            f"[DONE] {doc_id}: {result['tables_found']} tables, "
            f"{result['chunks_created']} chunks, {elapsed:.0f}s",
            flush=True,
        )
        return {"id": doc_id, "status": "ok", **result, "time_s": elapsed}
    except Exception as e:
        elapsed = time.time() - start
        print(f"[FAIL] {doc_id}: {e} ({elapsed:.0f}s)", flush=True)
        return {"id": doc_id, "status": "error", "error": str(e), "time_s": elapsed}


def show_status():
    """Show current staging status."""
    import psycopg2

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute(
        """SELECT id, doc_type, fiscal_year, docling_tables_found,
                  docling_chunks_created, review_status, promoted_at
           FROM staging.financial_documents
           ORDER BY doc_type, fiscal_year"""
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()

    print(f"\n{'ID':<30} {'Type':<15} {'Year':>4} {'Tables':>7} {'Chunks':>7} {'Status':<15} {'Promoted'}")
    print("-" * 100)
    for r in rows:
        tables = r[3] if r[3] is not None else "-"
        chunks = r[4] if r[4] is not None else "-"
        promoted = "yes" if r[5] else "-"
        print(f"{r[0]:<30} {r[1]:<15} {r[2]:>4} {tables:>7} {chunks:>7} {r[5]:<15} {promoted}")

    pending = sum(1 for r in rows if r[3] is None)
    extracted = sum(1 for r in rows if r[3] is not None and r[6] is None)
    promoted = sum(1 for r in rows if r[6] is not None)
    print(f"\nPending: {pending} | Extracted: {extracted} | Promoted: {promoted} | Total: {len(rows)}")


def main():
    parser = argparse.ArgumentParser(description="Batch Docling extraction for financial PDFs")
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel workers")
    parser.add_argument("--status", action="store_true", help="Show staging status")
    parser.add_argument("--doc", help="Process a single document by ID")
    args = parser.parse_args()

    if args.status:
        show_status()
        return

    pending = get_pending_pdfs(doc_filter=args.doc)
    if not pending:
        print("No pending PDFs to process.")
        show_status()
        return

    print(f"=== Financial Document Batch Extraction ===")
    print(f"Pending: {len(pending)} PDFs")
    print(f"Workers: {args.workers}")
    print(f"", flush=True)

    start = time.time()

    if args.workers == 1 or len(pending) == 1:
        results = [process_single_pdf(item) for item in pending]
    else:
        with Pool(processes=min(args.workers, len(pending))) as pool:
            results = pool.map(process_single_pdf, pending)

    elapsed = time.time() - start

    # Summary
    ok = [r for r in results if r["status"] == "ok"]
    failed = [r for r in results if r["status"] == "error"]
    total_tables = sum(r.get("tables_found", 0) for r in ok)
    total_chunks = sum(r.get("chunks_created", 0) for r in ok)

    print(f"\n{'=' * 60}")
    print(f"Batch complete in {elapsed / 60:.1f} minutes")
    print(f"  Succeeded: {len(ok)}")
    print(f"  Failed:    {len(failed)}")
    print(f"  Tables:    {total_tables}")
    print(f"  Chunks:    {total_chunks}")
    if failed:
        print(f"\nFailed documents:")
        for f in failed:
            print(f"  {f['id']}: {f.get('error', 'unknown')}")
    print()
    show_status()


if __name__ == "__main__":
    main()
