"""
FinancialLinesExtractor — Structured extraction from Docling table_json blobs
==============================================================================

Reads ``document_chunks`` rows with ``chunk_type='table'`` and a non-null
``table_json`` column, parses the Docling table structure, identifies header
rows (Programma / Sub-programma / Jaar / Lasten / Baten / Saldo), and emits
one ``financial_lines`` row per (programma x sub_programma x jaar x label)
cell.

All euro amounts use ``decimal.Decimal`` — never float.  Every emitted row
carries a SHA-256 hash of the raw cell text for downstream verification.

Usage:
    from pipeline.financial_lines_extractor import FinancialLinesExtractor
    from services.db_pool import get_connection

    with get_connection() as conn:
        extractor = FinancialLinesExtractor(conn)
        result = extractor.extract_from_document("fin_jaarstukken_2024")
        # result -> ExtractResult(lines_extracted=1842, ...)
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import traceback
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Optional

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Advisory lock ID shared with other WS2 pipeline writers.
_ADVISORY_LOCK_ID = 42

# Header-detection keywords (case-insensitive).
_PROGRAMMA_KEYWORDS = re.compile(
    r"^(programma|prog\.?)$", re.IGNORECASE
)
_SUB_PROGRAMMA_KEYWORDS = re.compile(
    r"^(deelprogramma|sub[_\-\s]?programma|subprogramma|product|taakveld)$",
    re.IGNORECASE,
)
_BEDRAG_KEYWORDS = re.compile(
    r"(lasten|baten|saldo|begroting|realisatie|werkelijk|bijgesteld|primitief|actueel)",
    re.IGNORECASE,
)
_YEAR_PATTERN = re.compile(r"\b(20[12]\d)\b")

# Row-level totals that we extract separately with sub_programma='totaal'.
_TOTAL_PATTERNS = re.compile(
    r"^(totaal|subtotaal|totalen|total|sub\s*totaal)\b", re.IGNORECASE
)

# Euro parsing: "x 1.000" or "x 1.000.000" multiplier in header text.
_MULTIPLIER_PATTERN = re.compile(
    r"x\s*[\d.]+", re.IGNORECASE
)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class FinancialLine:
    """One extracted financial cell — maps 1-to-1 to a ``financial_lines`` row."""
    gemeente: str
    entity_id: str
    scope: str
    document_id: str
    page: int
    table_id: str
    row_idx: int
    col_idx: int
    programma: Optional[str]
    sub_programma: Optional[str]
    jaar: int
    bedrag_eur: Decimal
    bedrag_label: Optional[str]
    bron_chunk_id: int
    source_pdf_url: Optional[str]
    sha256: str


@dataclass
class ExtractResult:
    """Summary returned by ``extract_from_document``."""
    document_id: str
    lines_extracted: int
    lines_skipped: int
    failures: list = field(default_factory=list)
    tables_processed: int = 0


# ---------------------------------------------------------------------------
# Header layout — the result of analysing one table's header rows
# ---------------------------------------------------------------------------

@dataclass
class _HeaderLayout:
    """Parsed structure of a table's header row(s)."""
    # Column index of the programma column (-1 if not found).
    programma_col: int = -1
    # Column index of the sub-programma column (-1 if not found).
    sub_programma_col: int = -1
    # Mapping: column index -> (year: int, label: str|None).
    # label is e.g. "Lasten", "Baten" — None when the column is year-only.
    value_columns: dict = field(default_factory=dict)
    # Global multiplier extracted from header text (e.g. 1000 for "x 1.000").
    multiplier: Decimal = field(default_factory=lambda: Decimal("1"))
    # Number of header rows consumed (1 or 2).
    header_rows_consumed: int = 1


# ---------------------------------------------------------------------------
# FinancialLinesExtractor
# ---------------------------------------------------------------------------

class FinancialLinesExtractor:
    """Core extraction engine for WS2 (Trustworthy Financial Analysis).

    Reads all ``table_json`` chunks for a document, parses headers, and emits
    ``financial_lines`` rows via an advisory-locked batch insert.
    """

    def __init__(self, conn):
        """
        Parameters
        ----------
        conn : psycopg2 connection
            An open database connection (from ``services.db_pool.get_connection``
            or a plain ``psycopg2.connect`` call).  The caller manages the
            connection lifecycle.
        """
        self.conn = conn

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def extract_from_document(self, document_id: str) -> ExtractResult:
        """Main entry point.

        Reads all ``table_json`` chunks for *document_id*, parses headers,
        emits ``financial_lines`` rows.  Returns an :class:`ExtractResult`
        summary.
        """
        logger.info("[FLE] Starting extraction for document %s", document_id)
        result = ExtractResult(document_id=document_id, lines_extracted=0,
                               lines_skipped=0, tables_processed=0)

        chunks = self._load_table_chunks(document_id)
        if not chunks:
            logger.warning("[FLE] No table chunks found for document %s", document_id)
            return result

        logger.info("[FLE] Found %d table chunks for %s", len(chunks), document_id)

        # Look up source_pdf_url from staging.financial_documents (best-effort).
        source_pdf_url = self._lookup_source_pdf_url(document_id)

        all_lines: list[FinancialLine] = []

        for chunk in chunks:
            chunk_id = chunk["id"]
            table_json_str = chunk["table_json"]
            page = chunk.get("page", 0) or 0

            try:
                table_data = json.loads(table_json_str)
            except (json.JSONDecodeError, TypeError) as exc:
                failure = self._make_failure(
                    document_id, chunk_id, "json_parse",
                    str(exc), {"raw": table_json_str[:500] if table_json_str else None}
                )
                result.failures.append(failure)
                continue

            lines = self.extract_from_chunk(
                chunk_id=chunk_id,
                chunk_data=table_data,
                document_id=document_id,
                page=page,
                source_pdf_url=source_pdf_url,
            )

            result.tables_processed += 1
            all_lines.extend(lines)

        # Write to DB inside advisory lock.
        if all_lines:
            self._write_lines(document_id, all_lines)

        # Log failures to pipeline_failures.
        if result.failures:
            self._log_failures(result.failures)

        result.lines_extracted = len(all_lines)
        logger.info(
            "[FLE] Document %s: %d lines extracted, %d skipped, "
            "%d failures, %d tables processed",
            document_id, result.lines_extracted, result.lines_skipped,
            len(result.failures), result.tables_processed,
        )
        return result

    def extract_from_chunk(
        self,
        chunk_id: int,
        chunk_data: dict,
        document_id: str = "",
        page: int = 0,
        source_pdf_url: Optional[str] = None,
    ) -> list[FinancialLine]:
        """Parse a single ``table_json`` blob into :class:`FinancialLine` rows.

        Parameters
        ----------
        chunk_id : int
            The ``document_chunks.id`` of this chunk.
        chunk_data : dict
            Parsed ``table_json`` dict with ``headers`` (list[str]) and
            ``rows`` (list[list[str]]).
        document_id : str
            The owning ``documents.id``.
        page : int
            Page number in the source PDF.
        source_pdf_url : str | None
            URL of the source PDF for provenance.

        Returns
        -------
        list[FinancialLine]
        """
        headers = chunk_data.get("headers", [])
        rows = chunk_data.get("rows", [])

        if not headers or not rows:
            return []

        table_id = f"chunk_{chunk_id}"
        layout = self._detect_header_layout(headers, rows)

        if not layout.value_columns:
            # No numeric (year x label) columns detected — skip this table.
            logger.debug(
                "[FLE] Chunk %d: no value columns detected, skipping", chunk_id
            )
            return []

        # Determine which rows are data rows (skip consumed header rows).
        data_rows = rows[layout.header_rows_consumed - 1:] if layout.header_rows_consumed > 1 else rows

        lines: list[FinancialLine] = []
        current_programma: Optional[str] = None

        for row_idx, row in enumerate(data_rows):
            # Pad row to header length.
            padded = list(row) + [""] * max(0, len(headers) - len(row))

            # Extract programma from designated column or first text column.
            raw_programma = self._extract_cell_text(padded, layout.programma_col)
            raw_sub_programma = self._extract_cell_text(padded, layout.sub_programma_col)

            # Detect total rows.
            is_total = False
            if raw_programma and _TOTAL_PATTERNS.search(raw_programma):
                is_total = True
            elif raw_sub_programma and _TOTAL_PATTERNS.search(raw_sub_programma):
                is_total = True

            # Group header logic: if the programma column has text but all
            # value columns are empty, this is a group header row.
            if raw_programma and not is_total:
                all_values_empty = all(
                    not _cell_has_number(self._extract_cell_text(padded, ci))
                    for ci in layout.value_columns
                )
                if all_values_empty:
                    current_programma = _clean_label(raw_programma)
                    continue

            # Determine effective programma for this row.
            if raw_programma and not is_total:
                effective_programma = _clean_label(raw_programma)
                # Also update current_programma so subsequent rows inherit.
                current_programma = effective_programma
            elif is_total:
                effective_programma = current_programma
            else:
                effective_programma = current_programma

            effective_sub_programma = _clean_label(raw_sub_programma) if raw_sub_programma else None
            if is_total:
                effective_sub_programma = "totaal"

            # Skip rows without a programma (unless it's a total).
            if not effective_programma and not is_total:
                continue

            # Emit one FinancialLine per value column.
            for col_idx, (jaar, bedrag_label) in layout.value_columns.items():
                raw_cell = self._extract_cell_text(padded, col_idx)
                if not raw_cell:
                    continue

                parsed = _parse_euro_amount(raw_cell, layout.multiplier)
                if parsed is None:
                    # Unparseable — not a number cell, skip silently.
                    continue

                bedrag_eur = parsed

                # Skip zeros that are not saldos.
                if bedrag_eur == Decimal("0.00") and bedrag_label != "Saldo":
                    continue

                sha = hashlib.sha256(raw_cell.encode("utf-8")).hexdigest()

                lines.append(FinancialLine(
                    gemeente="rotterdam",
                    entity_id="rotterdam",
                    scope="gemeente",
                    document_id=document_id,
                    page=page,
                    table_id=table_id,
                    row_idx=row_idx,
                    col_idx=col_idx,
                    programma=effective_programma,
                    sub_programma=effective_sub_programma,
                    jaar=jaar,
                    bedrag_eur=bedrag_eur,
                    bedrag_label=bedrag_label,
                    bron_chunk_id=chunk_id,
                    source_pdf_url=source_pdf_url,
                    sha256=sha,
                ))

        return lines

    # ------------------------------------------------------------------
    # Header detection
    # ------------------------------------------------------------------

    def _detect_header_layout(
        self, headers: list[str], rows: list[list[str]]
    ) -> _HeaderLayout:
        """Analyse header row(s) to determine column roles.

        Handles:
        - Single-row headers with ``Programma | 2023 Lasten | 2023 Baten | ...``
        - Multi-row headers where row 1 has years and row 2 has labels.
        - Multiplier hints such as ``x 1.000`` in any header cell.
        """
        layout = _HeaderLayout()

        # --- Detect multiplier in any header cell ---
        for h in headers:
            layout.multiplier = _detect_multiplier(h)
            if layout.multiplier != Decimal("1"):
                break
        # Also check first data row for multiplier hints (sometimes it's there).
        if layout.multiplier == Decimal("1") and rows:
            for cell in rows[0]:
                layout.multiplier = _detect_multiplier(str(cell))
                if layout.multiplier != Decimal("1"):
                    break

        # --- Pass 1: classify each header column ---
        col_roles: dict[int, str] = {}  # idx -> "programma"|"sub"|"year"|"value"|"skip"
        col_years: dict[int, int] = {}
        col_labels: dict[int, str] = {}

        for idx, h in enumerate(headers):
            h_clean = str(h).strip()

            if _PROGRAMMA_KEYWORDS.match(h_clean):
                col_roles[idx] = "programma"
                layout.programma_col = idx
                continue

            if _SUB_PROGRAMMA_KEYWORDS.match(h_clean):
                col_roles[idx] = "sub"
                layout.sub_programma_col = idx
                continue

            # Try to extract year from header text.
            year_match = _YEAR_PATTERN.search(h_clean)
            year = int(year_match.group(1)) if year_match else None

            # Try to extract bedrag label.
            label_match = _BEDRAG_KEYWORDS.search(h_clean)
            label = label_match.group(1).capitalize() if label_match else None

            if year and label:
                # Combined: "2023 Lasten"
                col_roles[idx] = "value"
                col_years[idx] = year
                col_labels[idx] = label
            elif year and not label:
                # Year-only header — might be multi-row.
                col_roles[idx] = "year"
                col_years[idx] = year
            elif label and not year:
                # Label-only — might inherit year from a multi-row header.
                col_roles[idx] = "value_no_year"
                col_labels[idx] = label
            else:
                col_roles[idx] = "skip"

        # --- Pass 2: detect multi-row headers ---
        # If we have "year" columns but no fully resolved "value" columns,
        # check if the first data row contains labels (Lasten / Baten / ...).
        year_only_cols = {i for i, r in col_roles.items() if r == "year"}
        label_only_cols = {i for i, r in col_roles.items() if r == "value_no_year"}
        fully_resolved = {i for i, r in col_roles.items() if r == "value"}

        need_multirow = False

        if year_only_cols and not fully_resolved and rows:
            # Check first data row for labels.
            first_row = [str(c).strip() for c in rows[0]]
            multirow_resolved = {}
            for ci in year_only_cols:
                if ci < len(first_row):
                    label_m = _BEDRAG_KEYWORDS.search(first_row[ci])
                    if label_m:
                        multirow_resolved[ci] = (
                            col_years[ci],
                            label_m.group(1).capitalize(),
                        )
            if multirow_resolved:
                need_multirow = True
                layout.header_rows_consumed = 2
                for ci, (yr, lbl) in multirow_resolved.items():
                    layout.value_columns[ci] = (yr, lbl)

        # Also handle the case where years span multiple columns and the
        # first data row has sub-column labels.  E.g.:
        #   Header:    | Programma | 2023        | 2024        |
        #   Sub-hdr:   |           | Lasten|Baten| Lasten|Baten|
        # Docling often merges these so that "2023" appears in two columns.
        if not need_multirow and year_only_cols and rows:
            # Attempt: maybe each year header covers multiple adjacent columns
            # whose first-data-row values are labels.
            first_row = [str(c).strip() for c in rows[0]]
            for ci in sorted(year_only_cols):
                yr = col_years[ci]
                if ci < len(first_row):
                    label_m = _BEDRAG_KEYWORDS.search(first_row[ci])
                    if label_m:
                        layout.value_columns[ci] = (yr, label_m.group(1).capitalize())
                        need_multirow = True
            if need_multirow:
                layout.header_rows_consumed = 2

        # --- Pass 3: collect fully resolved value columns ---
        for ci, (yr, lbl) in [(i, (col_years[i], col_labels[i]))
                               for i in fully_resolved]:
            layout.value_columns[ci] = (yr, lbl)

        # --- Pass 4: handle label-only columns that need a year ---
        # If there are label-only columns and exactly one year appears
        # across all headers, assign that year to all label-only columns.
        if label_only_cols:
            all_years = set(col_years.values())
            if len(all_years) == 1:
                the_year = next(iter(all_years))
                for ci in label_only_cols:
                    layout.value_columns[ci] = (the_year, col_labels[ci])
            elif not all_years:
                # Try extracting a year from any header cell globally.
                global_years = set()
                for h in headers:
                    for m in _YEAR_PATTERN.finditer(str(h)):
                        global_years.add(int(m.group(1)))
                if len(global_years) == 1:
                    the_year = next(iter(global_years))
                    for ci in label_only_cols:
                        layout.value_columns[ci] = (the_year, col_labels[ci])

        # --- Fallback: if no programma column found, use column 0 ---
        if layout.programma_col == -1:
            # Use the first column that is not a value/year column.
            for idx in range(len(headers)):
                if idx not in layout.value_columns:
                    layout.programma_col = idx
                    break

        return layout

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def _load_table_chunks(self, document_id: str) -> list[dict]:
        """Load all table-type chunks for a document from ``document_chunks``."""
        cur = self.conn.cursor()
        cur.execute(
            """
            SELECT id, chunk_index, title, table_json, content
            FROM document_chunks
            WHERE document_id = %s
              AND chunk_type = 'table'
              AND table_json IS NOT NULL
            ORDER BY chunk_index
            """,
            (document_id,),
        )
        rows = cur.fetchall()
        cur.close()

        chunks = []
        for r in rows:
            # Try to extract page number from title (e.g. "Tabel 1 (p.42)").
            page = 0
            title = r[2] or ""
            pm = re.search(r"\(p\.(\d+)\)", title)
            if pm:
                page = int(pm.group(1))

            chunks.append({
                "id": r[0],
                "chunk_index": r[1],
                "title": title,
                "table_json": r[3],
                "page": page,
            })
        return chunks

    def _lookup_source_pdf_url(self, document_id: str) -> Optional[str]:
        """Best-effort lookup of source_url from staging.financial_documents."""
        try:
            cur = self.conn.cursor()
            cur.execute(
                """
                SELECT source_url FROM staging.financial_documents
                WHERE id = %s LIMIT 1
                """,
                (document_id,),
            )
            row = cur.fetchone()
            cur.close()
            return row[0] if row else None
        except Exception:
            # staging schema may not exist; not critical.
            return None

    def _write_lines(self, document_id: str, lines: list[FinancialLine]):
        """Batch-insert ``financial_lines`` rows inside an advisory lock.

        Deletes existing rows for the document first (idempotent re-runs).
        """
        def _do_write(conn):
            cur = conn.cursor()

            # Idempotent: remove previous extraction for this document.
            cur.execute(
                "DELETE FROM financial_lines WHERE document_id = %s",
                (document_id,),
            )
            deleted = cur.rowcount
            if deleted:
                logger.info(
                    "[FLE] Deleted %d previous financial_lines for %s",
                    deleted, document_id,
                )

            pg_data = [
                (
                    ln.gemeente,
                    ln.entity_id,
                    ln.scope,
                    ln.document_id,
                    ln.page,
                    ln.table_id,
                    ln.row_idx,
                    ln.col_idx,
                    ln.programma,
                    ln.sub_programma,
                    ln.jaar,
                    ln.bedrag_eur,
                    ln.bedrag_label,
                    ln.bron_chunk_id,
                    ln.source_pdf_url,
                    ln.sha256,
                )
                for ln in lines
            ]

            execute_values(
                cur,
                """
                INSERT INTO financial_lines
                    (gemeente, entity_id, scope, document_id, page, table_id,
                     row_idx, col_idx, programma, sub_programma, jaar,
                     bedrag_eur, bedrag_label, bron_chunk_id, source_pdf_url,
                     sha256)
                VALUES %s
                """,
                pg_data,
                page_size=500,
            )
            logger.info(
                "[FLE] Inserted %d financial_lines for %s",
                len(pg_data), document_id,
            )
            cur.close()

        self._with_advisory_lock(self.conn, _do_write)
        self.conn.commit()

    def _log_failures(self, failures: list[dict]):
        """Write extraction failures to the ``pipeline_failures`` table."""
        try:
            cur = self.conn.cursor()
            for f in failures:
                cur.execute(
                    """
                    INSERT INTO pipeline_failures
                        (job_name, item_id, item_type, error_class,
                         error_message, raw_payload)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        "financial_lines_extractor",
                        str(f.get("item_id", "")),
                        f.get("item_type", "chunk"),
                        f.get("error_class", "extraction_error"),
                        f.get("error_message", ""),
                        json.dumps(f.get("raw_payload"), ensure_ascii=False,
                                   default=str)
                        if f.get("raw_payload") else None,
                    ),
                )
            cur.close()
        except Exception as exc:
            logger.warning(
                "[FLE] Could not log %d failures to pipeline_failures: %s",
                len(failures), exc,
            )

    # ------------------------------------------------------------------
    # Advisory lock
    # ------------------------------------------------------------------

    @staticmethod
    def _with_advisory_lock(conn, callback):
        """Execute *callback(conn)* while holding pg_advisory_lock(42).

        Advisory locks are session-level (not transaction-level), so we must
        always unlock even after a failed transaction.  We rollback any
        aborted transaction state before issuing the unlock.
        """
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_lock(%s)", (_ADVISORY_LOCK_ID,))
        cur.close()
        try:
            callback(conn)
        except Exception:
            conn.rollback()  # clear aborted transaction before unlock
            raise
        finally:
            cur = conn.cursor()
            cur.execute("SELECT pg_advisory_unlock(%s)", (_ADVISORY_LOCK_ID,))
            cur.close()

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_cell_text(row: list, col_idx: int) -> str:
        """Safely extract and strip text from a row at *col_idx*."""
        if col_idx < 0 or col_idx >= len(row):
            return ""
        val = str(row[col_idx]).strip()
        return val

    @staticmethod
    def _make_failure(
        document_id: str,
        chunk_id: int,
        error_class: str,
        error_message: str,
        raw_payload: Optional[dict] = None,
    ) -> dict:
        return {
            "item_id": f"{document_id}:chunk_{chunk_id}",
            "item_type": "chunk",
            "error_class": error_class,
            "error_message": error_message,
            "raw_payload": raw_payload,
        }


# ======================================================================
# Module-level helper functions
# ======================================================================

def _clean_label(text: str) -> Optional[str]:
    """Normalise a programma / sub-programma label.

    Strips leading numbering (e.g. "1. Bestuur en dienstverlening" -> same),
    collapses whitespace.  Returns None for empty/whitespace-only input.
    """
    if not text:
        return None
    text = text.strip()
    if not text:
        return None
    # Collapse whitespace.
    text = re.sub(r"\s+", " ", text)
    return text


def _cell_has_number(text: str) -> bool:
    """Quick check whether *text* contains something that looks like a number."""
    if not text:
        return False
    # Strip known non-numeric decorators.
    cleaned = text.replace("€", "").replace(" ", "").replace("\u00a0", "")
    # Check for digits.
    return bool(re.search(r"\d", cleaned))


def _parse_euro_amount(raw: str, multiplier: Decimal = Decimal("1")) -> Optional[Decimal]:
    """Parse a Dutch-formatted euro amount string into a :class:`Decimal`.

    Handles:
    - Thousands separator ``.`` and decimal separator ``,``
    - Euro sign ``€``
    - Negative amounts in parentheses: ``(1.234)``
    - Negative with minus sign: ``-1.234`` or ``- 1.234``
    - Trailing minus: ``1.234-``
    - Multiplier headers: if the table says ``x 1.000`` the caller passes
      ``multiplier=Decimal('1000')``.
    - Non-breaking spaces, thin spaces

    Returns ``None`` if the cell is not parseable as a number.
    """
    if not raw:
        return None

    text = raw.strip()

    # Remove euro sign and various space characters.
    text = text.replace("€", "").replace("\u00a0", " ").replace("\u2009", " ")
    text = text.replace(" ", "").strip()

    if not text:
        return None

    # Detect parenthesised negatives: (1.234,56)
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()
    elif text.startswith("-") or text.startswith("\u2212"):
        negative = True
        text = text.lstrip("-\u2212").strip()
    elif text.endswith("-"):
        negative = True
        text = text[:-1].strip()

    if not text:
        return None

    # At this point text should be like "1.234.567,89" or "1234567.89" or
    # "1234" or "1.234" (ambiguous: could be 1234 with thousands sep or
    # 1.234 decimal — in Dutch financial tables, ``.`` is always thousands).

    # Determine which separator convention is in use.
    has_comma = "," in text
    has_dot = "." in text

    if has_comma and has_dot:
        # Both present: Dutch convention — dot is thousands, comma is decimal.
        text = text.replace(".", "")   # strip thousands
        text = text.replace(",", ".")  # decimal
    elif has_comma and not has_dot:
        # Only comma — treat as decimal separator.
        text = text.replace(",", ".")
    elif has_dot and not has_comma:
        # Only dot — could be thousands separator or decimal.
        # In Dutch financial tables, "1.234" means 1234 (thousands sep).
        # "1.23" is ambiguous but rare; we treat dots as thousands separator
        # unless there is exactly one dot and the part after it is not 3 digits
        # (i.e., looks like a real decimal).
        parts = text.split(".")
        if len(parts) == 2 and len(parts[1]) != 3:
            # Likely a decimal point (e.g. "3.14").
            pass  # keep as-is
        else:
            # Thousands separator(s).
            text = text.replace(".", "")

    # Remove any remaining non-numeric characters except dot and minus.
    text = re.sub(r"[^\d.]", "", text)

    if not text:
        return None

    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        return None

    # Apply multiplier.
    value = value * multiplier

    if negative:
        value = -value

    # Round to 2 decimal places (cent precision).
    try:
        value = value.quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None

    # Sanity cap: no Rotterdam budget line exceeds €100 billion.
    # NUMERIC(18,2) max is ~10^16; reject anything > 10^11 as a parse error.
    if abs(value) > Decimal("100000000000"):
        return None

    return value


def _detect_multiplier(header_text) -> Decimal:
    """Detect a ``x 1.000`` style multiplier in header text.

    Returns the multiplier as a Decimal (e.g. ``Decimal('1000')``), or
    ``Decimal('1')`` if none found.
    """
    header_text = str(header_text) if header_text is not None else ""
    match = _MULTIPLIER_PATTERN.search(header_text)
    if not match:
        return Decimal("1")

    raw = match.group(0)  # e.g. "x 1.000" or "x 1.000.000"
    # Strip the leading "x" and whitespace.
    num_str = re.sub(r"^x\s*", "", raw, flags=re.IGNORECASE)
    # Parse as a Dutch-formatted number (dots are thousands separators).
    num_str = num_str.replace(".", "")

    try:
        return Decimal(num_str)
    except InvalidOperation:
        return Decimal("1")


# ======================================================================
# CLI entry point
# ======================================================================

def _build_db_url() -> str:
    """Build a PostgreSQL connection URL from environment variables."""
    url = os.getenv("DATABASE_URL", "")
    if url:
        return url
    h = os.getenv("DB_HOST", "localhost")
    p = os.getenv("DB_PORT", "5432")
    d = os.getenv("DB_NAME", "neodemos")
    u = os.getenv("DB_USER", "postgres")
    pw = os.getenv("DB_PASSWORD", "postgres")
    return f"postgresql://{u}:{pw}@{h}:{p}/{d}"


def main():
    """CLI entry point — extract financial_lines from a single document."""
    parser = argparse.ArgumentParser(
        description="Extract financial_lines from Docling table_json blobs"
    )
    parser.add_argument(
        "document_id",
        help="The document_id to extract (e.g. 'fin_jaarstukken_2024')",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    db_url = _build_db_url()
    logger.info("Connecting to database...")

    conn = psycopg2.connect(db_url)
    try:
        extractor = FinancialLinesExtractor(conn)
        result = extractor.extract_from_document(args.document_id)
        conn.commit()

        print(f"\n{'=' * 60}")
        print(f"Document:         {result.document_id}")
        print(f"Tables processed: {result.tables_processed}")
        print(f"Lines extracted:  {result.lines_extracted}")
        print(f"Lines skipped:    {result.lines_skipped}")
        print(f"Failures:         {len(result.failures)}")
        if result.failures:
            print(f"\nFailure details:")
            for f in result.failures[:20]:
                print(f"  [{f.get('error_class')}] {f.get('item_id')}: "
                      f"{f.get('error_message', '')[:120]}")
            if len(result.failures) > 20:
                print(f"  ... and {len(result.failures) - 20} more")
        print(f"{'=' * 60}")

    except Exception:
        conn.rollback()
        logger.error("Extraction failed:\n%s", traceback.format_exc())
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
