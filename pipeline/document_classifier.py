"""Document classification module — routes documents to the correct processing pipeline.

Decision tree (order matters — first match wins):

1. TRANSCRIPT           doc_id starts with "transcript_"
2. FINANCIAL             name matches financial pattern (begroting, jaarstuk, etc.)
   2a. FINANCIAL_TABLE_RICH  if also table-rich name AND content > 100K chars
   2b. FINANCIAL             otherwise
3. GARBLED_OCR           content fails OCR quality heuristic
   3a. GARBLED_TABLE_RICH    if also table-rich name AND content > 100K chars
   3b. GARBLED_OCR           otherwise
4. TABLE_RICH            name matches table-rich pattern AND content > 100K chars
5. REGULAR               everything else

Order rationale:
- Transcripts are identified by convention (doc_id prefix), cheapest check.
- Financial docs need specialised extraction regardless of OCR quality.
- Garbled OCR must be caught before table-rich, because garbled table-rich
  docs need OCR recovery *and* Tableformer, not just Tableformer.
- Table-rich is the last structural check before the default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from services.scraper import _is_garbled_ocr

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TABLE_RICH_MIN_LENGTH = 100_000

# Civic document type labels — set by WS11a backfill or WS11b ORI ingestion.
# The document_processor must NOT overwrite these with its pipeline-routing values
# (garbled_ocr, table_rich, regular, …).  The column's primary role is civic type.
CIVIC_DOC_TYPES: frozenset[str] = frozenset({
    # --- Civic decision types (P0/P1) ---
    "schriftelijke_vraag",
    "initiatiefnotitie",
    "initiatiefvoorstel",
    "motie",
    "amendement",
    "raadsvoorstel",
    "toezegging",
    "brief_college",
    "afdoeningsvoorstel",
    # --- Meeting & procedural types ---
    "agenda",
    "notulen",
    "verslag",
    "annotatie",
    "adviezenlijst",
    "besluitenlijst",
    "ingekomen_stukken",
    "spreektijdentabel",
    "planning",
    # --- Document types ---
    "transcript",
    "rapport",
    "notitie",
    "memo",
    "bijlage",
    "presentatie",
    "monitor_rapport",
    # --- Financial & legal ---
    "begroting",
    "jaarstukken",
    "grondexploitatie",
    "voorbereidingsbesluit",
    "rekenkamer",
})

# ---------------------------------------------------------------------------
# Enum & dataclass
# ---------------------------------------------------------------------------


class DocType(Enum):
    TRANSCRIPT = "transcript"
    FINANCIAL = "financial"
    FINANCIAL_TABLE_RICH = "financial_table_rich"
    GARBLED_OCR = "garbled_ocr"
    GARBLED_TABLE_RICH = "garbled_table_rich"
    TABLE_RICH = "table_rich"
    REGULAR = "regular"


@dataclass(frozen=True)
class DocumentClassification:
    doc_type: DocType
    needs_pdf_download: bool
    needs_docling: bool
    docling_mode: str  # "ocr" | "layout" | "tableformer" | "none"
    quality_gate_min_length_ratio: float
    reason: str


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


class DocumentClassifier:
    """Pure-logic classifier — no DB access, no Docling imports."""

    _FINANCIAL_RE = re.compile(
        r"begroting|jaarstuk|jaarrekening|voorjaarsnota|10.?maands",
        re.IGNORECASE,
    )
    _TABLE_RICH_RE = re.compile(
        r"bestemmingsplan|MER\b|deelrapport|milieu.?effect|havenbestemmings"
        r"|bijlage.*rapport|verslag.*hoorzitting|toelichting|rapportage",
        re.IGNORECASE,
    )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def classify(
        self,
        doc_id: str,
        name: str,
        content: str,
        url: str | None = None,
    ) -> DocumentClassification:
        """Classify a document and return routing metadata."""

        # 1. Transcript — cheapest check
        if doc_id.startswith("transcript_"):
            return DocumentClassification(
                doc_type=DocType.TRANSCRIPT,
                needs_pdf_download=False,
                needs_docling=False,
                docling_mode="none",
                quality_gate_min_length_ratio=0.0,
                reason=f"doc_id prefix 'transcript_' ({doc_id})",
            )

        is_financial = bool(self._FINANCIAL_RE.search(name))
        is_table_rich = bool(self._TABLE_RICH_RE.search(name))
        is_long = len(content) > TABLE_RICH_MIN_LENGTH

        # 2. Financial
        if is_financial:
            if is_table_rich and is_long:
                return DocumentClassification(
                    doc_type=DocType.FINANCIAL_TABLE_RICH,
                    needs_pdf_download=True,
                    needs_docling=True,
                    docling_mode="tableformer",
                    quality_gate_min_length_ratio=1.1,
                    reason=f"Financial + table-rich name, {len(content):,} chars",
                )
            return DocumentClassification(
                doc_type=DocType.FINANCIAL,
                needs_pdf_download=True,
                needs_docling=True,
                docling_mode="tableformer",
                quality_gate_min_length_ratio=0.0,
                reason=f"Financial name match: {name!r}",
            )

        # 3. Garbled OCR
        if _is_garbled_ocr(content[:5000]):
            if is_table_rich and is_long:
                return DocumentClassification(
                    doc_type=DocType.GARBLED_TABLE_RICH,
                    needs_pdf_download=True,
                    needs_docling=True,
                    docling_mode="ocr",
                    quality_gate_min_length_ratio=1.1,
                    reason="Garbled OCR + table-rich name pattern",
                )
            return DocumentClassification(
                doc_type=DocType.GARBLED_OCR,
                needs_pdf_download=True,
                needs_docling=True,
                docling_mode="ocr",
                quality_gate_min_length_ratio=0.5,
                reason="Content failed OCR quality heuristic",
            )

        # 4. Table-rich
        if is_table_rich and is_long:
            return DocumentClassification(
                doc_type=DocType.TABLE_RICH,
                needs_pdf_download=True,
                needs_docling=True,
                docling_mode="layout",
                quality_gate_min_length_ratio=1.1,
                reason=f"Table-rich name pattern, {len(content):,} chars",
            )

        # 5. Regular — default
        return DocumentClassification(
            doc_type=DocType.REGULAR,
            needs_pdf_download=False,
            needs_docling=False,
            docling_mode="none",
            quality_gate_min_length_ratio=0.0,
            reason="No special patterns matched",
        )
