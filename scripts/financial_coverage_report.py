#!/usr/bin/env python3
"""
WS2 Financial Lines Coverage Report
====================================

Checks extraction coverage of financial_lines against expected data in
staging.financial_documents, prints a human-readable summary (or JSON for CI).

Usage:
    python scripts/financial_coverage_report.py
    python scripts/financial_coverage_report.py --json
    python scripts/financial_coverage_report.py --year 2024
    python scripts/financial_coverage_report.py --gemeente rotterdam
    python scripts/financial_coverage_report.py --year 2024 --json
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
# DB connection (matches services/db_pool.py + scripts/promote_financial_docs.py)
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


def _get_conn():
    return psycopg2.connect(DB_URL)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def _fmt_eur(val) -> str:
    """Format a numeric value with thousands separators."""
    if val is None:
        return "-"
    return f"{val:,.2f}"


def _fmt_int(val) -> str:
    if val is None:
        return "-"
    return f"{val:,}"


def _year_range(years: list[int]) -> str:
    """Compact representation: [2018,2019,2020,2022] -> '2018-2020, 2022'."""
    if not years:
        return "-"
    years = sorted(y for y in years if y is not None)
    ranges = []
    start = years[0]
    prev = years[0]
    for y in years[1:]:
        if y == prev + 1:
            prev = y
        else:
            ranges.append(f"{start}-{prev}" if start != prev else str(start))
            start = y
            prev = y
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    return ", ".join(ranges)


# ---------------------------------------------------------------------------
# Section 1: Document coverage
# ---------------------------------------------------------------------------


def query_document_coverage(cur, gemeente: str, year: int | None) -> list[dict]:
    """
    Cross-reference staging.financial_documents (the inventory) against
    financial_lines to find which docs have been extracted.
    """
    year_filter_fd = "AND fd.fiscal_year = %s" if year else ""
    year_filter_fl = "AND fl.jaar = %s" if year else ""

    # Build params in positional order matching the SQL placeholders:
    # 1. year_filter_fd (optional)  2. gemeente  3. year_filter_fl (optional)
    params: list = []
    if year:
        params.append(year)
    params.append(gemeente)
    if year:
        params.append(year)

    cur.execute(
        f"""
        WITH inventory AS (
            SELECT
                fd.doc_type,
                fd.fiscal_year,
                fd.id AS doc_id,
                fd.review_status,
                fd.promoted_at
            FROM staging.financial_documents fd
            WHERE 1=1
                {year_filter_fd}
        ),
        promoted AS (
            SELECT doc_type, fiscal_year, doc_id
            FROM inventory
            WHERE promoted_at IS NOT NULL
        ),
        with_lines AS (
            SELECT DISTINCT fl.document_id
            FROM financial_lines fl
            WHERE fl.gemeente = %s
                {year_filter_fl}
        ),
        summary AS (
            SELECT
                i.doc_type,
                array_agg(DISTINCT i.fiscal_year ORDER BY i.fiscal_year) AS years_available,
                count(DISTINCT p.doc_id) AS docs_promoted,
                count(DISTINCT CASE WHEN wl.document_id IS NOT NULL THEN p.doc_id END) AS docs_with_lines
            FROM inventory i
            LEFT JOIN promoted p
                ON i.doc_type = p.doc_type AND i.fiscal_year = p.fiscal_year
            LEFT JOIN with_lines wl
                ON p.doc_id = wl.document_id
            GROUP BY i.doc_type
            ORDER BY i.doc_type
        )
        SELECT * FROM summary
        """,
        params,
    )
    return [dict(r) for r in cur.fetchall()]


def _find_gaps(years_available: list[int], docs_promoted: int, docs_with_lines: int,
               cur, doc_type: str, gemeente: str) -> list[int]:
    """Find fiscal years that are promoted but have no financial_lines."""
    cur.execute(
        """
        SELECT fd.fiscal_year
        FROM staging.financial_documents fd
        LEFT JOIN financial_lines fl
            ON fl.document_id = fd.id AND fl.gemeente = %s
        WHERE fd.doc_type = %s
          AND fd.promoted_at IS NOT NULL
          AND fl.id IS NULL
        ORDER BY fd.fiscal_year
        """,
        (gemeente, doc_type),
    )
    return [r["fiscal_year"] for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Section 2: Programma coverage per year
# ---------------------------------------------------------------------------


def query_programma_coverage(cur, gemeente: str, year: int | None) -> list[dict]:
    year_filter = "AND fl.jaar = %s" if year else ""
    params: list = [gemeente]
    if year:
        params.append(year)

    cur.execute(
        f"""
        SELECT
            fl.jaar,
            count(DISTINCT fl.programma) AS programmas_found,
            count(*) AS lines,
            coalesce(sum(fl.bedrag_eur) FILTER (WHERE lower(fl.bedrag_label) = 'lasten'), 0) AS sum_lasten,
            coalesce(sum(fl.bedrag_eur) FILTER (WHERE lower(fl.bedrag_label) = 'baten'), 0) AS sum_baten
        FROM financial_lines fl
        WHERE fl.gemeente = %s
            {year_filter}
        GROUP BY fl.jaar
        ORDER BY fl.jaar
        """,
        params,
    )
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Section 3: Top programmas
# ---------------------------------------------------------------------------


def query_top_programmas(cur, gemeente: str, year: int | None, limit: int = 20) -> list[dict]:
    year_filter = "AND fl.jaar = %s" if year else ""
    params: list = [gemeente]
    if year:
        params.append(year)
    params.append(limit)

    cur.execute(
        f"""
        SELECT
            fl.programma,
            count(DISTINCT fl.jaar) AS years,
            count(*) AS total_lines,
            coalesce(
                avg(fl.bedrag_eur) FILTER (WHERE lower(fl.bedrag_label) = 'lasten'),
                0
            ) AS avg_lasten_per_line
        FROM financial_lines fl
        WHERE fl.gemeente = %s
            AND fl.programma IS NOT NULL
            {year_filter}
        GROUP BY fl.programma
        ORDER BY total_lines DESC
        LIMIT %s
        """,
        params,
    )
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Section 4: Extraction quality
# ---------------------------------------------------------------------------


def query_extraction_quality(cur, gemeente: str, year: int | None) -> dict:
    """
    Compare total table_json chunks against extracted financial_lines.
    Also count failures from pipeline_failures.
    """
    year_filter_dc = ""
    year_filter_fl = ""
    params_dc: list = []
    params_fl: list = [gemeente]

    if year:
        # For chunks we approximate by matching fiscal_year in the document
        year_filter_dc = """
            AND EXISTS (
                SELECT 1 FROM staging.financial_documents fd
                WHERE fd.id = dc.document_id AND fd.fiscal_year = %s
            )
        """
        params_dc.append(year)
        year_filter_fl = "AND fl.jaar = %s"
        params_fl.append(year)

    # Total chunks with table_json
    cur.execute(
        f"""
        SELECT count(*) AS total_table_chunks
        FROM document_chunks dc
        WHERE dc.chunk_type = 'table'
            AND dc.metadata::text LIKE '%%table_json%%'
            {year_filter_dc}
        """,
        params_dc,
    )
    total_chunks = cur.fetchone()["total_table_chunks"] or 0

    # Total financial_lines
    cur.execute(
        f"""
        SELECT count(*) AS total_lines
        FROM financial_lines fl
        WHERE fl.gemeente = %s
            {year_filter_fl}
        """,
        params_fl,
    )
    total_lines = cur.fetchone()["total_lines"] or 0

    # Pipeline failures
    cur.execute(
        """
        SELECT count(*) AS total_failures
        FROM pipeline_failures pf
        WHERE pf.job_name = 'ws2_financial_lines_extraction'
        """,
    )
    total_failures = cur.fetchone()["total_failures"] or 0

    # Top failure reasons
    cur.execute(
        """
        SELECT
            pf.error_class,
            count(*) AS cnt
        FROM pipeline_failures pf
        WHERE pf.job_name = 'ws2_financial_lines_extraction'
        GROUP BY pf.error_class
        ORDER BY cnt DESC
        LIMIT 10
        """,
    )
    failure_reasons = [{"reason": r["error_class"] or "unknown", "count": r["cnt"]} for r in cur.fetchall()]

    extraction_rate = (total_lines / total_chunks * 100) if total_chunks > 0 else 0.0
    failure_rate = (total_failures / (total_lines + total_failures) * 100) if (total_lines + total_failures) > 0 else 0.0

    return {
        "total_table_chunks": total_chunks,
        "total_financial_lines": total_lines,
        "extraction_rate_pct": round(extraction_rate, 1),
        "total_failures": total_failures,
        "failure_rate_pct": round(failure_rate, 1),
        "top_failure_reasons": failure_reasons,
    }


# ---------------------------------------------------------------------------
# Section 5: Entity scope distribution
# ---------------------------------------------------------------------------


def query_entity_scope(cur, gemeente: str, year: int | None) -> list[dict]:
    year_filter = "AND fl.jaar = %s" if year else ""
    params: list = [gemeente]
    if year:
        params.append(year)

    cur.execute(
        f"""
        SELECT
            fl.entity_id,
            fl.scope,
            count(*) AS lines,
            min(fl.jaar) AS year_min,
            max(fl.jaar) AS year_max
        FROM financial_lines fl
        WHERE fl.gemeente = %s
            {year_filter}
        GROUP BY fl.entity_id, fl.scope
        ORDER BY lines DESC
        """,
        params,
    )
    return [dict(r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Render: plain text
# ---------------------------------------------------------------------------


def render_text(data: dict) -> str:
    lines: list[str] = []

    lines.append("=== NeoDemos WS2 Financial Lines Coverage Report ===")
    lines.append(f"Generated: {data['generated_at']}")
    if data.get("filters"):
        lines.append(f"Filters: {data['filters']}")
    lines.append("")

    # -- Document coverage --
    lines.append("--- Document Coverage ---")
    header = f"{'Doc Type':<18}| {'Years Available':<22}| {'Docs Promoted':>14} | {'Docs w/ Lines':>14} | Gap"
    lines.append(header)
    lines.append("-" * len(header))
    for row in data.get("document_coverage", []):
        yr = _year_range(row.get("years_available", []))
        gap_years = row.get("gap_years", [])
        gap = ", ".join(str(y) for y in gap_years) if gap_years else "-"
        lines.append(
            f"{row['doc_type']:<18}| {yr:<22}| {row['docs_promoted']:>14} | {row['docs_with_lines']:>14} | {gap}"
        )
    lines.append("")

    # -- Programma coverage per year --
    lines.append("--- Programma Coverage per Year ---")
    header = f"{'Year':>4} | {'Programmas Found':>16} | {'Lines':>8} | {'Sum EUR (Lasten)':>20} | {'Sum EUR (Baten)':>20}"
    lines.append(header)
    lines.append("-" * len(header))
    for row in data.get("programma_coverage", []):
        lines.append(
            f"{row['jaar']:>4} | {row['programmas_found']:>16} | {_fmt_int(row['lines']):>8} "
            f"| {_fmt_eur(row['sum_lasten']):>20} | {_fmt_eur(row['sum_baten']):>20}"
        )
    lines.append("")

    # -- Top programmas --
    lines.append("--- Top Programmas (all years combined) ---")
    header = f"{'Programma':<35}| {'Years':>5} | {'Total Lines':>11} | {'Avg Lasten/line':>18}"
    lines.append(header)
    lines.append("-" * len(header))
    for row in data.get("top_programmas", []):
        prog = (row["programma"] or "(null)")[:34]
        lines.append(
            f"{prog:<35}| {row['years']:>5} | {_fmt_int(row['total_lines']):>11} "
            f"| {_fmt_eur(row['avg_lasten_per_line']):>18}"
        )
    lines.append("")

    # -- Extraction quality --
    eq = data.get("extraction_quality", {})
    lines.append("--- Extraction Quality ---")
    lines.append(f"Total chunks with table_json:  {_fmt_int(eq.get('total_table_chunks'))}")
    lines.append(f"Total financial_lines extracted: {_fmt_int(eq.get('total_financial_lines'))}")
    lines.append(f"Extraction rate: {eq.get('extraction_rate_pct', 0):.1f}%")
    lines.append(f"Failures logged: {_fmt_int(eq.get('total_failures'))} ({eq.get('failure_rate_pct', 0):.1f}%)")
    reasons = eq.get("top_failure_reasons", [])
    if reasons:
        lines.append("Top failure reasons:")
        for r in reasons:
            lines.append(f"  - {r['reason']}: {r['count']}")
    lines.append("")

    # -- Entity scope distribution --
    lines.append("--- Entity Scope Distribution ---")
    header = f"{'Entity':<14}| {'Scope':<35}| {'Lines':>8} | Years"
    lines.append(header)
    lines.append("-" * len(header))
    for row in data.get("entity_scope", []):
        yr = f"{row['year_min']}-{row['year_max']}" if row["year_min"] != row["year_max"] else str(row["year_min"])
        lines.append(
            f"{row['entity_id']:<14}| {row['scope']:<35}| {_fmt_int(row['lines']):>8} | {yr}"
        )
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def collect_report(gemeente: str, year: int | None) -> dict:
    """Run all queries, return a structured dict."""
    conn = _get_conn()
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        # 1. Document coverage
        doc_cov_raw = query_document_coverage(cur, gemeente, year)

        # Enrich with gap info (separate query per doc_type)
        doc_coverage = []
        for row in doc_cov_raw:
            gaps = _find_gaps(
                row["years_available"], row["docs_promoted"],
                row["docs_with_lines"], cur, row["doc_type"], gemeente,
            )
            doc_coverage.append({**row, "gap_years": gaps})

        # 2. Programma coverage
        prog_cov = query_programma_coverage(cur, gemeente, year)

        # 3. Top programmas
        top_progs = query_top_programmas(cur, gemeente, year)

        # 4. Extraction quality
        extr_quality = query_extraction_quality(cur, gemeente, year)

        # 5. Entity scope
        entity_scope = query_entity_scope(cur, gemeente, year)

        cur.close()
    finally:
        conn.close()

    filters_parts = [f"gemeente={gemeente}"]
    if year:
        filters_parts.append(f"year={year}")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filters": ", ".join(filters_parts),
        "document_coverage": doc_coverage,
        "programma_coverage": prog_cov,
        "top_programmas": top_progs,
        "extraction_quality": extr_quality,
        "entity_scope": entity_scope,
    }


def _json_serializer(obj):
    """Handle types that json.dumps cannot serialize by default."""
    from decimal import Decimal

    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def main():
    parser = argparse.ArgumentParser(
        description="WS2 Financial Lines Coverage Report"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output as JSON (for CI integration)",
    )
    parser.add_argument(
        "--year", type=int, default=None,
        help="Filter to a single fiscal year (e.g. 2024)",
    )
    parser.add_argument(
        "--gemeente", type=str, default="rotterdam",
        help="Municipality filter (default: rotterdam)",
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

    logger.debug("Collecting coverage report (gemeente=%s, year=%s)", args.gemeente, args.year)
    data = collect_report(args.gemeente, args.year)

    if args.json:
        print(json_mod.dumps(data, indent=2, default=_json_serializer, ensure_ascii=False))
    else:
        print(render_text(data))


if __name__ == "__main__":
    main()
