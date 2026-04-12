#!/usr/bin/env python3
"""
Tier A — Extract kruistabel (cross-reference table) from Rotterdam begroting PDFs
==================================================================================

BBV regulations require every municipality to include a kruistabel in their
begroting/jaarstukken showing how programmas map to IV3 taakvelden.  This script
mines the ``document_chunks`` table for tables that contain both programma names
and IV3 taakveld codes/names, then extracts the mapping.

Writes to ``programma_aliases`` with ``source='kruistabel'``, ``confidence=1.00``.

Usage:
    python scripts/extract_kruistabel.py                      # dry-run (default)
    python scripts/extract_kruistabel.py --commit             # write to DB
    python scripts/extract_kruistabel.py --year 2024          # single year
    python scripts/extract_kruistabel.py --year 2024 --commit
    python scripts/extract_kruistabel.py --output data/financial/kruistabel_proposed.yml
"""

import argparse
import json
import logging
import os
import re
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

import psycopg2
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


def _build_db_url() -> str:
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
# Load IV3 taakvelden reference data (from DB, fallback to JSON)
# ---------------------------------------------------------------------------


def load_iv3_taakvelden(conn) -> dict:
    """Return {code: omschrijving} for all IV3 taakvelden.

    Tries DB first; falls back to the JSON seed file.
    """
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT code, omschrijving FROM iv3_taakvelden")
            return {row[0]: row[1] for row in cur.fetchall()}
    except Exception:
        conn.rollback()
        logger.warning("iv3_taakvelden table not found in DB, loading from JSON seed")

    json_path = Path(__file__).resolve().parent.parent / "data" / "financial" / "iv3_taakvelden.json"
    with open(json_path) as f:
        data = json.load(f)
    return {tv["code"]: tv["omschrijving"] for tv in data["taakvelden"]}


# ---------------------------------------------------------------------------
# IV3 code pattern — matches "0.1", "6.71", "0.10", etc.
# ---------------------------------------------------------------------------

_IV3_CODE_PATTERN = re.compile(r"\b(\d\.\d{1,2})\b")

# Broader pattern to also match codes written without dot: "071", "062"
_IV3_CODE_NODOT = re.compile(r"\b(0\d{2}|[1-8]\d{1,2})\b")


# ---------------------------------------------------------------------------
# Kruistabel detection heuristics
# ---------------------------------------------------------------------------


def _is_kruistabel_candidate(headers: list[str], rows: list[list[str]]) -> bool:
    """Heuristic: a table is a kruistabel candidate if it references both
    programma names and IV3 taakveld codes or the word "taakveld".
    """
    all_text = " ".join(str(h) for h in headers).lower()
    all_row_text = " ".join(
        str(cell) for row in rows for cell in row
    ).lower()
    combined = all_text + " " + all_row_text

    has_programma_ref = bool(
        re.search(r"programma", combined)
    )
    has_taakveld_ref = bool(
        re.search(r"taakveld", combined)
    )
    has_iv3_codes = bool(
        _IV3_CODE_PATTERN.search(combined)
    )

    # Must have some programma reference AND either taakveld keyword or IV3 codes
    return has_programma_ref and (has_taakveld_ref or has_iv3_codes)


def _extract_mappings_from_table(
    headers: list[str],
    rows: list[list[str]],
    iv3_lookup: dict,
    jaar: int,
) -> list[dict]:
    """Parse a kruistabel and extract programma -> taakveld mappings.

    Rotterdam kruistabels typically have one of these layouts:
      A) Columns: Programma | Taakveld(en)   — one row per programma, taakveld cell has codes
      B) Rows: taakveld codes in first col, programma names across headers
      C) Matrix: rows=taakvelden, cols=programmas (or vice versa), cells have checkmarks/amounts

    Returns a list of {programma_label, iv3_taakveld, notes} dicts.
    """
    mappings = []
    h_lower = [str(h).strip().lower() for h in headers]

    # --- Detect column roles ---
    programma_col = -1
    taakveld_col = -1
    taakveld_code_col = -1

    for idx, h in enumerate(h_lower):
        if "programma" in h and programma_col == -1:
            programma_col = idx
        if "taakveld" in h:
            # Distinguish between code column and description column
            if "code" in h or "nr" in h:
                taakveld_code_col = idx
            else:
                taakveld_col = idx

    # If we found explicit programma + taakveld columns, use layout A
    if programma_col >= 0 and (taakveld_col >= 0 or taakveld_code_col >= 0):
        mappings = _extract_layout_a(
            rows, programma_col, taakveld_col, taakveld_code_col,
            iv3_lookup, jaar,
        )
        if mappings:
            return mappings

    # Fallback: scan all cells for IV3 codes alongside programma context
    mappings = _extract_layout_scan(headers, rows, iv3_lookup, jaar)
    return mappings


def _extract_layout_a(
    rows: list[list[str]],
    programma_col: int,
    taakveld_col: int,
    taakveld_code_col: int,
    iv3_lookup: dict,
    jaar: int,
) -> list[dict]:
    """Layout A: explicit Programma and Taakveld columns."""
    mappings = []
    current_programma = None

    for row in rows:
        padded = list(row) + [""] * 10  # safety padding

        raw_prog = str(padded[programma_col]).strip()
        if raw_prog and not re.match(r"^(totaal|subtotaal)\b", raw_prog, re.IGNORECASE):
            current_programma = _clean_programma_label(raw_prog)

        if not current_programma:
            continue

        # Extract IV3 codes from the taakveld column(s)
        codes_found = set()

        for col in [taakveld_code_col, taakveld_col]:
            if col < 0:
                continue
            cell_text = str(padded[col]).strip()
            # Find all IV3 code patterns
            for m in _IV3_CODE_PATTERN.finditer(cell_text):
                code = m.group(1)
                if code in iv3_lookup:
                    codes_found.add(code)

            # Also try matching taakveld descriptions to codes
            cell_lower = cell_text.lower()
            for code, omschrijving in iv3_lookup.items():
                if omschrijving.lower() in cell_lower and len(omschrijving) > 5:
                    codes_found.add(code)

        for code in sorted(codes_found):
            mappings.append({
                "gemeente": "rotterdam",
                "jaar": jaar,
                "programma_label": current_programma,
                "iv3_taakveld": code,
                "confidence": "1.00",
                "source": "kruistabel",
                "notes": f"IV3 {code} ({iv3_lookup.get(code, '?')})",
            })

    return mappings


def _extract_layout_scan(
    headers: list[str],
    rows: list[list[str]],
    iv3_lookup: dict,
    jaar: int,
) -> list[dict]:
    """Fallback layout: scan entire table for co-occurring programma labels
    and IV3 codes.  Less precise but catches non-standard layouts.
    """
    mappings = []

    # Build a reverse lookup: lowercase omschrijving -> code
    desc_to_code = {}
    for code, omschrijving in iv3_lookup.items():
        desc_to_code[omschrijving.lower()] = code

    # Check if any header looks like a programma name
    header_programmas = []
    for h in headers:
        h_clean = str(h).strip()
        if h_clean and len(h_clean) > 3 and not re.match(r"^\d", h_clean):
            # Skip headers that are clearly not programma names
            if h_clean.lower() not in ("taakveld", "code", "nr", "omschrijving",
                                        "totaal", "lasten", "baten", "saldo"):
                header_programmas.append(h_clean)

    # Scan rows: look for rows where col 0 has a taakveld code
    # and other columns indicate programma membership
    for row in rows:
        if not row:
            continue
        first_cell = str(row[0]).strip()
        iv3_codes = _IV3_CODE_PATTERN.findall(first_cell)

        if not iv3_codes:
            # Try matching the cell text against taakveld descriptions
            first_lower = first_cell.lower().strip()
            for desc, code in desc_to_code.items():
                if desc in first_lower or first_lower in desc:
                    iv3_codes = [code]
                    break

        if not iv3_codes:
            continue

        # This row has IV3 code(s) — check which programma columns are marked
        for col_idx, cell in enumerate(row[1:], start=1):
            cell_text = str(cell).strip()
            if not cell_text:
                continue
            # If cell has a checkmark, 'x', or a number > 0, and the header
            # looks like a programma name, record the mapping
            is_marked = bool(
                cell_text.lower() in ("x", "v", "\u2713", "\u2714", "ja", "yes")
                or re.match(r"^[\d.,]+$", cell_text.replace(" ", ""))
            )
            if is_marked and col_idx < len(headers):
                prog_name = _clean_programma_label(str(headers[col_idx]))
                if prog_name and len(prog_name) > 2:
                    for code in iv3_codes:
                        if code in iv3_lookup:
                            mappings.append({
                                "gemeente": "rotterdam",
                                "jaar": jaar,
                                "programma_label": prog_name,
                                "iv3_taakveld": code,
                                "confidence": "1.00",
                                "source": "kruistabel",
                                "notes": f"IV3 {code} ({iv3_lookup.get(code, '?')}) [matrix scan]",
                            })

    return mappings


def _clean_programma_label(text: str) -> str | None:
    """Normalise a programma label from a kruistabel cell."""
    if not text:
        return None
    text = text.strip()
    # Remove leading numbering like "1. " or "01 " or "P1: "
    text = re.sub(r"^[Pp]?\d{1,2}[\.\:\)\s]+\s*", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text or len(text) < 3:
        return None
    return text


# ---------------------------------------------------------------------------
# Fiscal year extraction from document IDs
# ---------------------------------------------------------------------------


def _extract_year_from_doc_id(doc_id: str) -> int | None:
    """Try to extract the fiscal year from a document_id like 'fin_begroting_2024'."""
    m = re.search(r"(\d{4})", doc_id)
    return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Main extraction logic
# ---------------------------------------------------------------------------


def find_kruistabel_mappings(
    conn,
    iv3_lookup: dict,
    year_filter: int | None = None,
) -> list[dict]:
    """Query the DB for begroting/jaarstukken table chunks and extract kruistabel
    mappings.

    Returns a list of mapping dicts ready for insertion or YAML output.
    """
    all_mappings = []

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        # Find begroting and jaarstukken documents with table chunks
        query = """
            SELECT dc.id AS chunk_id, dc.document_id, dc.title,
                   dc.table_json, dc.content, dc.chunk_type
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            WHERE dc.chunk_type = 'table'
              AND dc.table_json IS NOT NULL
              AND (d.id LIKE '%%begroting%%' OR d.id LIKE '%%jaarstukken%%')
        """
        params = []
        if year_filter:
            query += " AND d.id LIKE %s"
            params.append(f"%{year_filter}%")

        query += " ORDER BY dc.document_id, dc.chunk_index"
        cur.execute(query, params)
        chunks = cur.fetchall()

    logger.info("Found %d table chunks in begroting/jaarstukken documents", len(chunks))

    # Group by document
    doc_chunks = defaultdict(list)
    for chunk in chunks:
        doc_chunks[chunk["document_id"]].append(chunk)

    for doc_id, chunks in doc_chunks.items():
        jaar = _extract_year_from_doc_id(doc_id)
        if not jaar:
            logger.warning("Cannot extract year from document_id '%s', skipping", doc_id)
            continue

        if year_filter and jaar != year_filter:
            continue

        logger.info("Scanning %d table chunks in %s (jaar=%d)", len(chunks), doc_id, jaar)

        for chunk in chunks:
            try:
                table_data = json.loads(chunk["table_json"])
            except (json.JSONDecodeError, TypeError):
                continue

            headers = table_data.get("headers", [])
            rows = table_data.get("rows", [])

            if not headers or not rows:
                continue

            if not _is_kruistabel_candidate(headers, rows):
                continue

            logger.info(
                "  Kruistabel candidate found: chunk %d in %s (title: %s)",
                chunk["chunk_id"], doc_id, (chunk.get("title") or "")[:60],
            )

            mappings = _extract_mappings_from_table(headers, rows, iv3_lookup, jaar)

            for m in mappings:
                logger.info(
                    "    %s -> %s (%s)",
                    m["programma_label"], m["iv3_taakveld"], m["notes"],
                )

            all_mappings.extend(mappings)

    # Deduplicate: keep first occurrence per (gemeente, jaar, programma_label, iv3_taakveld)
    seen = set()
    deduped = []
    for m in all_mappings:
        key = (m["gemeente"], m["jaar"], m["programma_label"], m["iv3_taakveld"])
        if key not in seen:
            seen.add(key)
            deduped.append(m)

    logger.info("Total kruistabel mappings found: %d (deduped from %d)", len(deduped), len(all_mappings))
    return deduped


# ---------------------------------------------------------------------------
# DB commit
# ---------------------------------------------------------------------------


def commit_mappings(conn, mappings: list[dict]) -> int:
    """Write kruistabel mappings to programma_aliases.

    Uses ON CONFLICT to skip existing rows (preserving manual overrides).
    Returns the number of rows actually inserted.
    """
    inserted = 0
    with conn.cursor() as cur:
        for m in mappings:
            cur.execute(
                """
                INSERT INTO programma_aliases
                    (gemeente, jaar, programma_label, iv3_taakveld, confidence, source)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (gemeente, jaar, programma_label) DO NOTHING
                """,
                (
                    m["gemeente"],
                    m["jaar"],
                    m["programma_label"],
                    m["iv3_taakveld"],
                    Decimal(m["confidence"]),
                    m["source"],
                ),
            )
            if cur.rowcount > 0:
                inserted += 1

    conn.commit()
    return inserted


# ---------------------------------------------------------------------------
# YAML output
# ---------------------------------------------------------------------------


def write_yaml(mappings: list[dict], path: str):
    """Write mappings to a YAML file for human review."""
    try:
        import yaml
    except ImportError:
        # Fallback: write as readable text that's valid YAML
        _write_yaml_manual(mappings, path)
        return

    # Group by (gemeente, jaar) for readability
    grouped = defaultdict(list)
    for m in mappings:
        key = f"{m['gemeente']}_{m['jaar']}"
        grouped[key].append({
            "programma_label": m["programma_label"],
            "iv3_taakveld": m["iv3_taakveld"],
            "confidence": float(m["confidence"]),
            "source": m["source"],
            "notes": m.get("notes", ""),
        })

    output = {"kruistabel_mappings": dict(grouped)}

    with open(path, "w") as f:
        yaml.dump(output, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    logger.info("Wrote %d mappings to %s", len(mappings), path)


def _write_yaml_manual(mappings: list[dict], path: str):
    """Write YAML manually without PyYAML dependency."""
    grouped = defaultdict(list)
    for m in mappings:
        key = f"{m['gemeente']}_{m['jaar']}"
        grouped[key].append(m)

    with open(path, "w") as f:
        f.write("# Kruistabel mappings extracted from begroting/jaarstukken PDFs\n")
        f.write("# Review before committing with --commit\n\n")
        f.write("kruistabel_mappings:\n")

        for group_key in sorted(grouped.keys()):
            f.write(f"\n  {group_key}:\n")
            for m in grouped[group_key]:
                f.write(f"    - programma_label: \"{m['programma_label']}\"\n")
                f.write(f"      iv3_taakveld: \"{m['iv3_taakveld']}\"\n")
                f.write(f"      confidence: {m['confidence']}\n")
                f.write(f"      source: \"{m['source']}\"\n")
                if m.get("notes"):
                    f.write(f"      notes: \"{m['notes']}\"\n")

    logger.info("Wrote %d mappings to %s", len(mappings), path)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Tier A: Extract kruistabel (programma -> IV3 taakveld) from begroting PDFs"
    )
    parser.add_argument(
        "--year", type=int, default=None,
        help="Filter to a single fiscal year (e.g. 2024)",
    )
    parser.add_argument(
        "--commit", action="store_true",
        help="Actually write mappings to programma_aliases table (default: dry-run)",
    )
    parser.add_argument(
        "--output", "-o", type=str,
        default="data/financial/kruistabel_proposed.yml",
        help="Path for YAML review file (default: data/financial/kruistabel_proposed.yml)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
    )

    conn = _get_conn()
    try:
        iv3_lookup = load_iv3_taakvelden(conn)
        logger.info("Loaded %d IV3 taakvelden codes", len(iv3_lookup))

        mappings = find_kruistabel_mappings(conn, iv3_lookup, year_filter=args.year)

        if not mappings:
            print("\nNo kruistabel mappings found.")
            print("This is expected if begroting PDFs have not been ingested yet,")
            print("or if the kruistabel pages did not produce table chunks.")
            return

        # Always write YAML for review
        output_path = Path(__file__).resolve().parent.parent / args.output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        write_yaml(mappings, str(output_path))

        # Summary
        print(f"\n{'=' * 60}")
        print(f"Kruistabel extraction summary")
        print(f"{'=' * 60}")
        print(f"Mappings found: {len(mappings)}")

        # Group by year for display
        by_year = defaultdict(list)
        for m in mappings:
            by_year[m["jaar"]].append(m)

        for yr in sorted(by_year.keys()):
            yr_maps = by_year[yr]
            progs = set(m["programma_label"] for m in yr_maps)
            codes = set(m["iv3_taakveld"] for m in yr_maps)
            print(f"  {yr}: {len(yr_maps)} mappings ({len(progs)} programmas -> {len(codes)} taakvelden)")

        print(f"\nYAML review file: {output_path}")

        if args.commit:
            inserted = commit_mappings(conn, mappings)
            print(f"\nCommitted to DB: {inserted} rows inserted "
                  f"({len(mappings) - inserted} skipped due to existing entries)")
        else:
            print(f"\nDry-run mode. Use --commit to write to DB.")

        print(f"{'=' * 60}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
