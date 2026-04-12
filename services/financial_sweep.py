"""
Financial Document Sweep Service
================================

Periodic job that:
1. Detects documents with financial table_json chunks not yet extracted
2. Runs FinancialLinesExtractor on each
3. Logs every action to document_events for operator visibility

Designed to run as an APScheduler job (hourly) or manually:
    python -m services.financial_sweep
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Financial signal detection — lightweight regex on raw document text
# ---------------------------------------------------------------------------

_FINANCIAL_KEYWORDS = re.compile(
    r"""
    (?:
        begrot(?:ing|e)                     # begroting, begrote
        | jaarstukken | jaarrekening
        | voorjaarsnota | najaarsnota
        | 10[\s-]?maands
        | lasten\s+en\s+baten
        | saldo\b
        | reserves?\b
        | programma(?:rekening|begroting)
        | taakveld(?:en)?
        | x\s*€?\s*1\.000                   # "bedragen x € 1.000"
        | dekkingspercentage
        | kostendekkend
        | gemeentefonds
        | OZB\b | ozb\b
        | afvalstoffenheffing
        | rioolheffing
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Minimum number of keyword hits to classify as "likely financial"
_MIN_KEYWORD_HITS = 3


def detect_financial_signals(text: str) -> dict:
    """Check if document text contains financial table signals.

    Returns a dict with:
        - is_financial: bool
        - keyword_hits: int
        - matched_keywords: list[str]  (deduplicated, lowered)
    """
    if not text or len(text) < 200:
        return {"is_financial": False, "keyword_hits": 0, "matched_keywords": []}

    matches = _FINANCIAL_KEYWORDS.findall(text)
    unique = sorted(set(m.lower().strip() for m in matches))
    return {
        "is_financial": len(matches) >= _MIN_KEYWORD_HITS,
        "keyword_hits": len(matches),
        "matched_keywords": unique,
    }


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def _build_db_url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if url:
        return url
    h = os.getenv("DB_HOST", "localhost")
    p = os.getenv("DB_PORT", "5432")
    d = os.getenv("DB_NAME", "neodemos")
    u = os.getenv("DB_USER", "postgres")
    pw = os.getenv("DB_PASSWORD", "")
    return f"postgresql://{u}:{pw}@{h}:{p}/{d}"


def _log_event(conn, document_id: str, event_type: str, details: dict = None,
               triggered_by: str = "financial_sweep"):
    """Insert a row into document_events."""
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO document_events (document_id, event_type, details, triggered_by)
           VALUES (%s, %s, %s, %s)""",
        (document_id, event_type,
         json.dumps(details, ensure_ascii=False, default=str) if details else None,
         triggered_by),
    )
    cur.close()
    conn.commit()


def _log_pipeline_run(conn, job_name: str, discovered: int, processed: int,
                      failed: int, triggered_by: str = "apscheduler"):
    """Insert a summary row into pipeline_runs."""
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO pipeline_runs
               (job_name, started_at, finished_at, status,
                items_discovered, items_processed, items_failed, triggered_by)
           VALUES (%s, NOW(), NOW(), %s, %s, %s, %s, %s)""",
        (job_name,
         "success" if failed == 0 else "failure",
         discovered, processed, failed,
         "cron" if triggered_by == "apscheduler" else "manual"),
    )
    cur.close()
    conn.commit()


# ---------------------------------------------------------------------------
# Core sweep
# ---------------------------------------------------------------------------

def find_unprocessed_table_docs(conn) -> list[dict]:
    """Find documents that have table_json chunks but no financial_lines."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT dc.document_id,
               COUNT(*) AS table_chunks,
               d.name AS doc_name
        FROM document_chunks dc
        JOIN documents d ON d.id = dc.document_id
        LEFT JOIN financial_lines fl ON fl.document_id = dc.document_id
        WHERE dc.table_json IS NOT NULL
          AND fl.id IS NULL
        GROUP BY dc.document_id, d.name
        ORDER BY table_chunks DESC
    """)
    rows = cur.fetchall()
    cur.close()
    return [dict(r) for r in rows]


def run_sweep(triggered_by: str = "apscheduler") -> dict:
    """Main entry point: find unprocessed docs, extract financial_lines, log everything.

    Returns summary dict: {discovered, processed, failed, details}.
    """
    from pipeline.financial_lines_extractor import FinancialLinesExtractor

    conn = psycopg2.connect(_build_db_url())
    summary = {"discovered": 0, "processed": 0, "failed": 0, "details": []}

    try:
        # Ensure document_events table exists (idempotent)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS document_events (
                id BIGSERIAL PRIMARY KEY,
                document_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                event_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                details JSONB,
                triggered_by TEXT NOT NULL DEFAULT 'system'
            )
        """)
        conn.commit()
        cur.close()

        docs = find_unprocessed_table_docs(conn)
        summary["discovered"] = len(docs)

        if not docs:
            logger.info("[financial_sweep] No unprocessed table docs found")
            _log_pipeline_run(conn, "financial_sweep", 0, 0, 0, triggered_by)
            return summary

        logger.info("[financial_sweep] Found %d docs with table_json but no financial_lines", len(docs))

        for doc in docs:
            doc_id = doc["document_id"]
            table_chunks = doc["table_chunks"]
            doc_name = doc.get("doc_name", doc_id)

            try:
                logger.info("[financial_sweep] Extracting %s (%d table chunks)", doc_id, table_chunks)

                extractor = FinancialLinesExtractor(conn)
                result = extractor.extract_from_document(doc_id)

                detail = {
                    "document_id": doc_id,
                    "doc_name": doc_name,
                    "table_chunks": table_chunks,
                    "lines_extracted": result.lines_extracted,
                    "failures": len(result.failures),
                    "tables_processed": result.tables_processed,
                }
                summary["details"].append(detail)
                summary["processed"] += 1

                _log_event(conn, doc_id, "financial_extracted", detail, triggered_by)
                logger.info(
                    "[financial_sweep] %s: %d lines extracted, %d failures",
                    doc_id, result.lines_extracted, len(result.failures),
                )
            except Exception as e:
                summary["failed"] += 1
                err_detail = {"document_id": doc_id, "error": str(e)}
                summary["details"].append(err_detail)
                _log_event(conn, doc_id, "financial_extraction_failed", err_detail, triggered_by)
                logger.warning("[financial_sweep] Failed on %s: %s", doc_id, e)
                # Continue to next doc — don't abort the sweep
                conn.rollback()

        _log_pipeline_run(
            conn, "financial_sweep",
            summary["discovered"], summary["processed"],
            summary["failed"], triggered_by,
        )

    finally:
        conn.close()

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if "--detect" in sys.argv:
        # Run detection on all documents, report which look financial
        conn = psycopg2.connect(_build_db_url())
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT id, name, LEFT(content, 5000) AS content_head
            FROM documents
            WHERE content IS NOT NULL AND LENGTH(content) > 500
            ORDER BY id DESC LIMIT 100
        """)
        for row in cur.fetchall():
            sig = detect_financial_signals(row["content_head"])
            if sig["is_financial"]:
                print(f"  FINANCIAL: {row['id'][:40]:<40} hits={sig['keyword_hits']:>3} keywords={sig['matched_keywords'][:5]}")
        conn.close()
    else:
        result = run_sweep(triggered_by="cli")
        print(f"\nSweep complete: {result['discovered']} discovered, "
              f"{result['processed']} processed, {result['failed']} failed")
        for d in result["details"]:
            if "error" in d:
                print(f"  FAIL: {d['document_id']} — {d['error']}")
            else:
                print(f"  OK: {d['document_id']} — {d['lines_extracted']} lines")
