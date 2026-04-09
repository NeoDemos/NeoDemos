#!/usr/bin/env python3
"""
Docling Table Extraction Test — Compare Docling vs. pypdf for financial PDFs.

Usage:
    python scripts/test_docling_tables.py                                # Use default test PDF
    python scripts/test_docling_tables.py path/to/financial.pdf          # Local PDF
    python scripts/test_docling_tables.py --url https://example.com/x.pdf  # Download + test

Outputs a quality comparison report:
  - Tables found by Docling (structured) vs. pypdf (raw text)
  - Converted table_json in our DB-compatible format
  - Financial calc integration check (parse_dutch_number, extract_table_numbers)

Does NOT modify any database or production data.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

# ── project imports ──────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from services.financial_calc import (
    extract_table_numbers,
    compute_financial_summary,
    parse_dutch_number,
)

# ── constants ────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PDF = PROJECT_ROOT / "docs" / "investigations" / "Onderhandelingen" / "Onderhandeldossier 4_ Economie.pdf"

SEPARATOR = "=" * 78


# ── helpers ──────────────────────────────────────────────────────────
def download_pdf(url: str) -> str:
    """Download a PDF to a temp file and return its path."""
    import requests

    print(f"Downloading: {url}")
    resp = requests.get(url, timeout=60, headers={"User-Agent": "NeoDemos-Docling-Test/1.0"})
    resp.raise_for_status()
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.write(resp.content)
    tmp.close()
    print(f"  → saved to {tmp.name} ({len(resp.content) / 1024:.0f} KB)")
    return tmp.name


# ── pypdf baseline ───────────────────────────────────────────────────
def extract_with_pypdf(pdf_path: str) -> dict:
    """Extract raw text from a PDF using pypdf (our current approach)."""
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    pages = []
    full_text = ""
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        pages.append(text)
        full_text += text + "\n"

    # Naive table detection: look for lines with multiple numbers/tabs
    table_like_lines = []
    for line in full_text.splitlines():
        nums = len([w for w in line.split() if parse_dutch_number(w) is not None])
        if nums >= 3:
            table_like_lines.append(line.strip())

    return {
        "page_count": len(reader.pages),
        "char_count": len(full_text),
        "table_like_lines": table_like_lines,
        "raw_text_preview": full_text[:2000],
    }


# ── docling extraction ──────────────────────────────────────────────
def extract_with_docling(pdf_path: str) -> dict:
    """Extract structured tables from a PDF using Docling."""
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.datamodel.base_models import InputFormat

    pipeline_options = PdfPipelineOptions(do_table_structure=True)
    pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE

    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
        }
    )

    result = converter.convert(pdf_path)
    doc = result.document

    tables = []
    for table_ix, table in enumerate(doc.tables):
        table_data = table.export_to_dataframe()
        if table_data is not None and not table_data.empty:
            headers = list(table_data.columns)
            rows = table_data.values.tolist()
            # Convert all cells to strings for consistency
            rows = [[str(cell) if cell is not None else "" for cell in row] for row in rows]
            tables.append({
                "index": table_ix,
                "headers": headers,
                "rows": rows,
                "num_rows": len(rows),
                "num_cols": len(headers),
                "page": getattr(table, "page_no", None),
            })

    # Also extract full markdown for reference
    md_text = result.document.export_to_markdown()

    return {
        "tables": tables,
        "table_count": len(tables),
        "markdown_preview": md_text[:3000] if md_text else "",
        "full_markdown_chars": len(md_text) if md_text else 0,
    }


def docling_table_to_table_json(docling_table: dict) -> dict:
    """Convert a Docling table to our DB-compatible table_json format."""
    return {
        "headers": docling_table["headers"],
        "rows": docling_table["rows"],
    }


# ── comparison report ────────────────────────────────────────────────
def print_report(pdf_path: str, pypdf_result: dict, docling_result: dict):
    """Print a side-by-side quality comparison."""
    print(f"\n{SEPARATOR}")
    print(f"  DOCLING vs. PYPDF TABLE EXTRACTION COMPARISON")
    print(f"  PDF: {pdf_path}")
    print(SEPARATOR)

    # ── Section 1: Overview ──
    print(f"\n{'─' * 40}")
    print(f"  1. OVERVIEW")
    print(f"{'─' * 40}")
    print(f"  pypdf pages:        {pypdf_result['page_count']}")
    print(f"  pypdf raw chars:    {pypdf_result['char_count']:,}")
    print(f"  pypdf table-lines:  {len(pypdf_result['table_like_lines'])} (heuristic: lines with ≥3 numbers)")
    print(f"  docling tables:     {docling_result['table_count']}")
    print(f"  docling markdown:   {docling_result['full_markdown_chars']:,} chars")

    # ── Section 2: Docling tables detail ──
    print(f"\n{'─' * 40}")
    print(f"  2. DOCLING EXTRACTED TABLES")
    print(f"{'─' * 40}")

    if not docling_result["tables"]:
        print("  (No tables found by Docling)")
    else:
        for t in docling_result["tables"]:
            print(f"\n  Table #{t['index']} — {t['num_rows']} rows × {t['num_cols']} cols (page {t.get('page', '?')})")
            print(f"  Headers: {t['headers']}")
            for row in t["rows"][:5]:
                print(f"    {row}")
            if t["num_rows"] > 5:
                print(f"    ... ({t['num_rows'] - 5} more rows)")

    # ── Section 3: table_json conversion + financial_calc integration ──
    print(f"\n{'─' * 40}")
    print(f"  3. FINANCIAL CALC INTEGRATION TEST")
    print(f"{'─' * 40}")

    total_numbers_extracted = 0
    for t in docling_result["tables"]:
        tj = docling_table_to_table_json(t)
        tj_str = json.dumps(tj, ensure_ascii=False)
        numbers = extract_table_numbers(tj_str)
        total_numbers_extracted += len(numbers)

        print(f"\n  Table #{t['index']} → table_json ({len(tj_str)} chars)")
        print(f"  extract_table_numbers() found {len(numbers)} labelled values")
        for n in numbers[:8]:
            year_str = n.get('year') or n.get('column') or '-'
            print(f"    {n['label']:<40} {year_str:>6}  {n['value']:>14,.0f}  (raw: {n['raw']})")
        if len(numbers) > 8:
            print(f"    ... ({len(numbers) - 8} more)")

    print(f"\n  Total labelled numbers extracted: {total_numbers_extracted}")

    # Test compute_financial_summary with a mock chunk
    if docling_result["tables"]:
        from dataclasses import dataclass, field
        from typing import Optional

        @dataclass
        class MockChunk:
            table_json: str
            title: str = "Docling Test"
            content: str = "[FINANCIAL]"

        mock_chunks = []
        for t in docling_result["tables"]:
            tj = json.dumps(docling_table_to_table_json(t), ensure_ascii=False)
            mock_chunks.append(MockChunk(table_json=tj))

        summary = compute_financial_summary(mock_chunks)
        if summary:
            print(f"\n  compute_financial_summary() output:")
            for line in summary.splitlines():
                print(f"    {line}")
        else:
            print("\n  compute_financial_summary() → no year-over-year comparisons found")

    # ── Section 4: pypdf raw comparison ──
    print(f"\n{'─' * 40}")
    print(f"  4. PYPDF RAW TABLE-LIKE LINES (first 15)")
    print(f"{'─' * 40}")
    for line in pypdf_result["table_like_lines"][:15]:
        print(f"    {line[:120]}")
    if not pypdf_result["table_like_lines"]:
        print("  (No table-like lines detected)")

    # ── Section 5: Verdict ──
    print(f"\n{'─' * 40}")
    print(f"  5. VERDICT")
    print(f"{'─' * 40}")
    dt = docling_result["table_count"]
    pt = len(pypdf_result["table_like_lines"])
    if dt > 0 and total_numbers_extracted > 0:
        print(f"  ✓ Docling found {dt} structured table(s) with {total_numbers_extracted} labelled numbers")
        print(f"  ✓ All tables are compatible with our table_json schema")
        print(f"  → Docling provides STRUCTURED extraction vs. pypdf's raw text ({pt} noisy lines)")
        print(f"  → Recommendation: USE DOCLING for financial PDFs with complex tables")
    elif dt > 0:
        print(f"  ~ Docling found {dt} table(s) but no parseable Dutch numbers")
        print(f"  → Tables may need header/row cleaning for financial_calc compatibility")
    else:
        print(f"  ✗ Docling found no tables in this PDF")
        print(f"  → This PDF may not contain tabular data, or Docling needs OCR mode")

    print(f"\n{SEPARATOR}\n")


# ── main ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Test Docling table extraction on financial PDFs"
    )
    parser.add_argument("pdf", nargs="?", default=str(DEFAULT_PDF), help="Path to a local PDF")
    parser.add_argument("--url", help="Download a PDF from URL instead of using a local file")
    args = parser.parse_args()

    pdf_path = args.pdf
    if args.url:
        pdf_path = download_pdf(args.url)

    if not os.path.exists(pdf_path):
        print(f"ERROR: PDF not found: {pdf_path}")
        sys.exit(1)

    print(f"Testing: {pdf_path}")
    print(f"Size: {os.path.getsize(pdf_path) / 1024:.0f} KB\n")

    # Step 1: pypdf baseline
    print("Step 1/2: Extracting with pypdf (baseline)...")
    pypdf_result = extract_with_pypdf(pdf_path)
    print(f"  → {pypdf_result['page_count']} pages, {pypdf_result['char_count']:,} chars")

    # Step 2: Docling extraction
    print("Step 2/2: Extracting with Docling (structured)...")
    try:
        docling_result = extract_with_docling(pdf_path)
        print(f"  → {docling_result['table_count']} tables found")
    except ImportError:
        print("\n  ERROR: docling not installed. Run:")
        print("    pip install docling")
        print("  Then re-run this script.")
        sys.exit(1)
    except Exception as e:
        print(f"\n  ERROR: Docling extraction failed: {e}")
        print("  This may be due to missing system dependencies (poppler, tesseract).")
        sys.exit(1)

    # Step 3: comparison report
    print_report(pdf_path, pypdf_result, docling_result)

    # Dump raw table_json for manual inspection
    if docling_result["tables"]:
        out_path = Path(pdf_path).stem + "_docling_tables.json"
        out_full = PROJECT_ROOT / "scripts" / out_path
        tables_json = [docling_table_to_table_json(t) for t in docling_result["tables"]]
        with open(out_full, "w") as f:
            json.dump(tables_json, f, ensure_ascii=False, indent=2)
        print(f"Raw table_json dumped to: {out_full}")


if __name__ == "__main__":
    main()
