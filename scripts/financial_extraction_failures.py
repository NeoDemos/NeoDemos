#!/usr/bin/env python3
"""
WS2 Financial Extraction Failure Log
=====================================

Queries pipeline_failures for job_name='ws2_financial_lines_extraction' and
prints a detailed, grouped failure report. Read-only; no writes.

Usage:
    python scripts/financial_extraction_failures.py
    python scripts/financial_extraction_failures.py --document fin_jaarstukken_2019
    python scripts/financial_extraction_failures.py --error-class empty_cell
    python scripts/financial_extraction_failures.py --limit 50
    python scripts/financial_extraction_failures.py --json
    python scripts/financial_extraction_failures.py --summary
"""

import argparse
import json as json_mod
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DB connection (same pattern as services/db_pool.py)
# ---------------------------------------------------------------------------

JOB_NAME = "ws2_financial_lines_extraction"


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


def _get_conn():
    return psycopg2.connect(DB_URL)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def query_failure_summary(cur) -> list[dict]:
    """Aggregate failure counts by error_class and item_type."""
    cur.execute(
        """
        SELECT
            pf.error_class,
            pf.item_type,
            count(*) AS cnt,
            min(pf.failed_at) AS first_seen,
            max(pf.failed_at) AS last_seen
        FROM pipeline_failures pf
        WHERE pf.job_name = %s
        GROUP BY pf.error_class, pf.item_type
        ORDER BY cnt DESC
        """,
        (JOB_NAME,),
    )
    return [dict(r) for r in cur.fetchall()]


def query_failures_by_document(cur, document_id: str | None,
                               error_class: str | None,
                               limit: int) -> list[dict]:
    """
    Retrieve individual failure rows, optionally filtered by document
    and/or error_class, grouped by document for display.
    """
    conditions = ["pf.job_name = %s"]
    params: list = [JOB_NAME]

    if document_id:
        # item_id may contain the document_id as a prefix or the raw_payload
        # has the document_id. Use flexible matching.
        conditions.append(
            "(pf.item_id LIKE %s OR pf.raw_payload->>'document_id' = %s)"
        )
        params.extend([f"{document_id}%", document_id])

    if error_class:
        conditions.append("pf.error_class = %s")
        params.append(error_class)

    params.append(limit)

    where = " AND ".join(conditions)
    cur.execute(
        f"""
        SELECT
            pf.id,
            pf.item_id,
            pf.item_type,
            pf.error_class,
            pf.error_message,
            pf.failed_at,
            pf.retry_count,
            pf.raw_payload
        FROM pipeline_failures pf
        WHERE {where}
        ORDER BY pf.item_id, pf.failed_at
        LIMIT %s
        """,
        params,
    )
    return [dict(r) for r in cur.fetchall()]


def query_document_failure_counts(cur) -> list[dict]:
    """Per-document failure counts for overview."""
    cur.execute(
        """
        SELECT
            coalesce(pf.raw_payload->>'document_id', split_part(pf.item_id, ':', 1)) AS doc_id,
            count(*) AS failures,
            count(DISTINCT pf.error_class) AS distinct_errors
        FROM pipeline_failures pf
        WHERE pf.job_name = %s
        GROUP BY 1
        ORDER BY failures DESC
        """,
        (JOB_NAME,),
    )
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Render: plain text
# ---------------------------------------------------------------------------


def render_summary(summary: list[dict], doc_counts: list[dict]) -> str:
    lines: list[str] = []
    lines.append("=== WS2 Financial Extraction Failures — Summary ===")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}")
    lines.append("")

    total = sum(r["cnt"] for r in summary)
    lines.append(f"Total failures: {total:,}")
    lines.append("")

    # By error class
    lines.append("--- By Error Class ---")
    header = f"{'Error Class':<30}| {'Item Type':<20}| {'Count':>8} | {'First Seen':<20} | Last Seen"
    lines.append(header)
    lines.append("-" * len(header))
    for r in summary:
        lines.append(
            f"{(r['error_class'] or 'unknown'):<30}| {(r['item_type'] or '-'):<20}"
            f"| {r['cnt']:>8,} | {str(r['first_seen'])[:19]:<20} | {str(r['last_seen'])[:19]}"
        )
    lines.append("")

    # By document
    lines.append("--- By Document ---")
    header = f"{'Document ID':<45}| {'Failures':>10} | {'Distinct Errors':>15}"
    lines.append(header)
    lines.append("-" * len(header))
    for r in doc_counts:
        lines.append(
            f"{(r['doc_id'] or '-'):<45}| {r['failures']:>10,} | {r['distinct_errors']:>15}"
        )
    lines.append("")

    return "\n".join(lines)


def render_detail(failures: list[dict], document_filter: str | None) -> str:
    lines: list[str] = []

    if not failures:
        lines.append("No failures found matching the given filters.")
        return "\n".join(lines)

    # Group by document
    from collections import defaultdict

    by_doc: dict[str, list[dict]] = defaultdict(list)
    for f in failures:
        doc_id = (
            (f.get("raw_payload") or {}).get("document_id")
            or (f["item_id"] or "unknown").split(":")[0]
        )
        by_doc[doc_id].append(f)

    for doc_id, doc_failures in by_doc.items():
        lines.append(f"--- Extraction Failures for document {doc_id} ---")
        for f in doc_failures:
            payload = f.get("raw_payload") or {}
            table_id = payload.get("table_id", "?")
            row_idx = payload.get("row_idx", "?")
            col_idx = payload.get("col_idx", "?")
            raw_value = payload.get("raw_value", "")
            chunk_id = f.get("item_id", "?")
            error_class = f.get("error_class", "unknown")
            error_msg = f.get("error_message", "")

            location = f"Table {table_id}, Row {row_idx}, Col {col_idx}"
            raw_display = f'Raw: "{raw_value}"' if raw_value else 'Raw: ""'
            error_display = f"Error: {error_class}"
            if error_msg and error_msg != error_class:
                error_display += f" ({error_msg})"

            lines.append(f"Chunk {chunk_id} | {location} | {raw_display} | {error_display}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def _json_serializer(obj):
    """Handle types that json.dumps cannot serialize by default."""
    from decimal import Decimal

    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (datetime,)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def main():
    parser = argparse.ArgumentParser(
        description="WS2 Financial Extraction Failure Log",
    )
    parser.add_argument(
        "--document", type=str, default=None,
        help="Filter to a specific document ID (e.g. fin_jaarstukken_2019)",
    )
    parser.add_argument(
        "--error-class", type=str, default=None,
        help="Filter to a specific error class (e.g. empty_cell, unparseable_amount)",
    )
    parser.add_argument(
        "--limit", type=int, default=200,
        help="Max number of individual failure rows to display (default: 200)",
    )
    parser.add_argument(
        "--summary", action="store_true",
        help="Show only the summary (error counts), not individual rows",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output as JSON (for CI integration)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging to stderr",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    conn = _get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        summary = query_failure_summary(cur)
        doc_counts = query_document_failure_counts(cur)

        if args.summary:
            if args.json:
                data = {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "summary": summary,
                    "by_document": doc_counts,
                }
                print(json_mod.dumps(data, indent=2, default=_json_serializer, ensure_ascii=False))
            else:
                print(render_summary(summary, doc_counts))
            return

        # Detailed view
        failures = query_failures_by_document(cur, args.document, args.error_class, args.limit)
        cur.close()

        if args.json:
            data = {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "filters": {
                    "document": args.document,
                    "error_class": args.error_class,
                    "limit": args.limit,
                },
                "summary": summary,
                "by_document": doc_counts,
                "failures": failures,
            }
            print(json_mod.dumps(data, indent=2, default=_json_serializer, ensure_ascii=False))
        else:
            # Print summary first, then details
            print(render_summary(summary, doc_counts))
            print(render_detail(failures, args.document))

    finally:
        conn.close()


if __name__ == "__main__":
    main()
