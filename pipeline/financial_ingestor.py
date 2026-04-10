"""
FinancialDocumentIngestor — Docling-based PDF table extraction for financial documents
=======================================================================================

Extracts structured tables and narrative text from financial PDFs using Docling,
then writes to the staging schema (staging.documents, staging.document_children,
staging.document_chunks, staging.financial_documents).

Always operates in chunk_only mode (no Qdrant writes during staging).  Embeddings
are generated later during the promotion step.

Docling is imported lazily so the module can be imported without Docling installed.

Usage:
    from pipeline.financial_ingestor import FinancialDocumentIngestor

    ingestor = FinancialDocumentIngestor()
    result = ingestor.process_pdf(
        pdf_path="data/financial_pdfs/jaarstukken/2024.pdf",
        doc_id="fin_jaarstukken_2024",
        doc_name="Jaarstukken 2024",
        doc_type="jaarstukken",
        fiscal_year=2024,
        source_url="https://watdoetdegemeente.rotterdam.nl/...",
    )
    # result -> {"tables_found": 692, "chunks_created": 1843, "pages": 916}
"""

import hashlib
import json
import logging
import os
import re
from typing import Dict, List, Optional

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_TEXT_CHUNK_CHARS = 2000
TEXT_OVERLAP_CHARS = 250


class FinancialDocumentIngestor:
    """Docling-based PDF ingestor that writes structured table and text chunks
    to the PostgreSQL staging schema.

    Does NOT extend SmartIngestor — financial PDFs need Docling's layout-aware
    parsing rather than SmartIngestor's text-based 4-tier chunking strategy.

    Storage layout mirrors StagingIngestor:
      - staging.documents          — one record per PDF
      - staging.document_children  — one record per chapter/section
      - staging.document_chunks    — one record per table or text fragment
      - staging.financial_documents — extraction stats & review tracking
    """

    def __init__(
        self,
        db_url: str = None,
        staging_schema: str = "staging",
    ):
        if db_url:
            self.db_url = db_url
        else:
            url = os.getenv("DATABASE_URL", "")
            if url:
                self.db_url = url
            else:
                h = os.getenv("DB_HOST", "localhost")
                p = os.getenv("DB_PORT", "5432")
                d = os.getenv("DB_NAME", "neodemos")
                u = os.getenv("DB_USER", "postgres")
                pw = os.getenv("DB_PASSWORD", "postgres")
                self.db_url = f"postgresql://{u}:{pw}@{h}:{p}/{d}"
        self.staging_schema = staging_schema
        self._converter = None  # lazy-initialised, cached for the lifetime of this ingestor

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def _get_connection(self):
        """Return a psycopg2 connection with search_path set to the staging schema."""
        conn = psycopg2.connect(self.db_url)
        cur = conn.cursor()
        cur.execute(f"SET search_path TO {self.staging_schema}, public")
        cur.close()
        return conn

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def process_pdf(
        self,
        pdf_path: str,
        doc_id: str,
        doc_name: str,
        doc_type: str,
        fiscal_year: int,
        source_url: str = None,
    ) -> dict:
        """Process a financial PDF through Docling and store in staging.

        Returns:
            {"tables_found": int, "chunks_created": int, "pages": int}
        """
        logger.info(f"[FINANCIAL] Processing: {doc_name} ({pdf_path})")

        # -- 1. Run Docling ------------------------------------------------
        docling_result = self._run_docling(pdf_path)
        doc_object = docling_result["document"]
        tables_raw = docling_result["tables"]
        page_count = docling_result["page_count"]

        logger.info(
            f"  Docling extracted {len(tables_raw)} tables, "
            f"{page_count} pages"
        )

        # -- 2. Build section map from Docling headings --------------------
        sections = self._extract_sections(doc_object)
        logger.info(f"  Detected {len(sections)} sections from Docling headings")

        # -- 3. Build chunks per section -----------------------------------
        #    Tables become chunk_type="table", text blocks become chunk_type="text"
        section_chunks = self._build_section_chunks(
            doc_object, tables_raw, sections, doc_name
        )

        # -- 4. Write to staging -------------------------------------------
        conn = self._get_connection()
        total_chunks = 0
        try:
            # 4a. Cleanup existing data for this doc_id
            self._cleanup_existing(conn, doc_id)

            # 4b. Ensure parent document record
            full_content = doc_object.export_to_markdown() or ""
            self._ensure_document_record(
                conn, doc_id, doc_name, full_content, category="financial"
            )

            # 4c. Store children + chunks
            for section_title, chunks in section_chunks:
                child_id = self._store_child(
                    conn, doc_id, section_title, chunks
                )
                self._store_chunks(conn, doc_id, doc_name, chunks, child_id)
                total_chunks += len(chunks)

            # 4d. Upsert financial_documents tracking record
            self._upsert_financial_document(
                conn,
                doc_id=doc_id,
                doc_type=doc_type,
                fiscal_year=fiscal_year,
                source_url=source_url,
                pdf_path=pdf_path,
                page_count=page_count,
                tables_found=len(tables_raw),
                chunks_created=total_chunks,
            )

            conn.commit()
            logger.info(
                f"[FINANCIAL] Done: {doc_id} — "
                f"{len(tables_raw)} tables, {total_chunks} chunks, {page_count} pages"
            )

        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        result_summary = {
            "tables_found": len(tables_raw),
            "chunks_created": total_chunks,
            "pages": page_count,
        }

        # Explicitly drop heavy Docling state so the GC can reclaim memory
        # before the worker pulls the next PDF.
        del docling_result
        del doc_object
        del tables_raw
        del sections
        del section_chunks
        try:
            del full_content
        except NameError:
            pass
        import gc
        gc.collect()

        return result_summary

    # ------------------------------------------------------------------
    # Docling extraction (lazy import)
    # ------------------------------------------------------------------

    def _get_converter(self):
        """Lazy-instantiate and cache the Docling DocumentConverter.

        Reusing one converter across PDFs avoids re-loading the ~3GB
        TableFormer + layout models on every call.
        """
        if getattr(self, "_converter", None) is not None:
            return self._converter

        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.pipeline_options import (
            PdfPipelineOptions,
            TableFormerMode,
            AcceleratorDevice,
            AcceleratorOptions,
        )
        from docling.datamodel.base_models import InputFormat

        pipeline_options = PdfPipelineOptions(do_table_structure=True)
        pipeline_options.table_structure_options.mode = TableFormerMode.ACCURATE
        pipeline_options.accelerator_options = AcceleratorOptions(
            num_threads=4,
            device=AcceleratorDevice.AUTO,
        )
        logger.info("  Docling DocumentConverter initialised (cached, 4 threads)")

        self._converter = DocumentConverter(
            format_options={
                InputFormat.PDF: PdfFormatOption(
                    pipeline_options=pipeline_options
                ),
            }
        )
        return self._converter

    def _run_docling(self, pdf_path: str) -> dict:
        """Run Docling on a PDF using the cached converter."""
        converter = self._get_converter()
        result = converter.convert(pdf_path)
        doc = result.document

        # Extract tables — pass doc= to avoid deprecation warning
        tables = []
        for table in doc.tables:
            try:
                df = table.export_to_dataframe(doc=doc)
            except TypeError:
                # Older Docling versions do not accept doc=
                df = table.export_to_dataframe()

            if df is None or df.empty:
                continue

            headers = list(df.columns)
            rows = df.values.tolist()
            # Stringify all cells
            rows = [
                [str(cell) if cell is not None else "" for cell in row]
                for row in rows
            ]
            # Deduplicate header values repeated in rows (Docling merged-cell quirk)
            rows = _deduplicate_header_rows(headers, rows)

            tables.append(
                {
                    "headers": headers,
                    "rows": rows,
                    "page": getattr(table, "page_no", None),
                }
            )

        # Determine page count
        page_count = 0
        if hasattr(doc, "pages") and doc.pages:
            page_count = len(doc.pages)
        elif hasattr(result, "pages"):
            page_count = len(result.pages) if result.pages else 0

        return {
            "document": doc,
            "tables": tables,
            "page_count": page_count,
        }

    # ------------------------------------------------------------------
    # Section extraction from Docling headings
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_sections(doc) -> List[Dict]:
        """Extract section boundaries from Docling's document structure.

        Returns a list of {"title": str, "level": int} for each heading found.
        Falls back to a single unnamed section if no headings are detected.
        """
        sections: List[Dict] = []
        try:
            # Docling DoclingDocument stores a body with items that can be
            # headings, paragraphs, tables, etc.
            for item in doc.body.children if hasattr(doc.body, "children") else []:
                label = getattr(item, "label", "") or ""
                if "heading" in label.lower() or "title" in label.lower():
                    text = ""
                    if hasattr(item, "text"):
                        text = item.text
                    elif hasattr(item, "export_to_markdown"):
                        text = item.export_to_markdown()
                    level = 1
                    # Try to parse heading level from label, e.g. "heading_2"
                    m = re.search(r"(\d+)", label)
                    if m:
                        level = int(m.group(1))
                    if text and text.strip():
                        sections.append({"title": text.strip()[:200], "level": level})
        except Exception as e:
            logger.debug(f"Could not parse Docling headings: {e}")

        if not sections:
            sections.append({"title": "Document", "level": 1})

        return sections

    # ------------------------------------------------------------------
    # Chunk building
    # ------------------------------------------------------------------

    def _build_section_chunks(
        self,
        doc,
        tables_raw: List[dict],
        sections: List[Dict],
        doc_name: str,
    ) -> List[tuple]:
        """Build (section_title, [chunk_dicts]) pairs.

        Each chunk dict has:
          - title: str
          - text: str          (content for embedding)
          - chunk_type: "table" | "text"
          - table_json: dict | None
          - questions: []
        """
        # Build table chunks
        table_chunks = []
        for idx, tbl in enumerate(tables_raw):
            table_json = {"headers": tbl["headers"], "rows": tbl["rows"]}
            md_content = _table_json_to_markdown(table_json)
            page_hint = f" (p.{tbl['page']})" if tbl.get("page") else ""
            title = f"Tabel {idx + 1}{page_hint}"

            # Try to derive a better title from the first header or context
            if tbl["headers"]:
                candidate = " | ".join(
                    str(h) for h in tbl["headers"][:3] if h and str(h).strip()
                )
                if candidate:
                    title = candidate[:120] + page_hint

            table_chunks.append(
                {
                    "title": title,
                    "text": md_content,
                    "chunk_type": "table",
                    "table_json": table_json,
                    "questions": [],
                }
            )

        # Build text chunks from Docling markdown (excluding tables)
        full_md = doc.export_to_markdown() or ""
        text_chunks = self._chunk_narrative_text(full_md, doc_name, sections)

        # If we have sections, try to distribute chunks across sections.
        # For simplicity we group all chunks under their detected section
        # or a fallback "Document" section.
        if len(sections) == 1:
            # Single section — put everything together
            all_chunks = table_chunks + text_chunks
            return [(sections[0]["title"], all_chunks)] if all_chunks else []

        # Multiple sections — assign tables by page order, text by position.
        # Simplified: group tables first, then text, both under "Tables" / "Text" sections
        result = []
        if table_chunks:
            result.append(("Financiele tabellen", table_chunks))
        if text_chunks:
            # Group text chunks by detected sections where possible
            result.append(("Narratieve tekst", text_chunks))

        return result if result else [(sections[0]["title"], [])]

    def _chunk_narrative_text(
        self, markdown: str, doc_name: str, sections: List[Dict]
    ) -> List[dict]:
        """Split Docling markdown narrative (non-table) text into chunks.

        For text blocks > MAX_TEXT_CHUNK_CHARS, split at paragraph boundaries
        with TEXT_OVERLAP_CHARS overlap.  Preserves section headings.
        """
        # Strip markdown table blocks (they are stored separately as table chunks)
        text = _strip_markdown_tables(markdown)
        text = text.strip()

        if not text:
            return []

        # If short enough, return as single chunk
        if len(text) <= MAX_TEXT_CHUNK_CHARS:
            return [
                {
                    "title": doc_name,
                    "text": text,
                    "chunk_type": "text",
                    "table_json": None,
                    "questions": [],
                }
            ]

        # Split at paragraph boundaries with overlap
        chunks = []
        paragraphs = re.split(r"\n{2,}", text)

        current_title = doc_name
        current_block = ""

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue

            # Detect section headings (markdown # headers or ALL-CAPS lines)
            if re.match(r"^#{1,4}\s+\S", para):
                current_title = re.sub(r"^#+\s*", "", para).strip()[:200]
            elif re.match(r"^[A-Z][A-Z\s]{3,}$", para.strip()):
                current_title = para.strip()[:200]

            # Would adding this paragraph exceed the limit?
            if len(current_block) + len(para) + 2 > MAX_TEXT_CHUNK_CHARS:
                # Flush current block as a chunk
                if current_block.strip():
                    chunks.append(
                        {
                            "title": current_title,
                            "text": current_block.strip(),
                            "chunk_type": "text",
                            "table_json": None,
                            "questions": [],
                        }
                    )
                # Start new block with overlap from tail of previous block
                overlap = _extract_overlap(current_block, TEXT_OVERLAP_CHARS)
                current_block = overlap + "\n\n" + para if overlap else para
            else:
                if current_block:
                    current_block += "\n\n" + para
                else:
                    current_block = para

        # Flush remaining
        if current_block.strip():
            chunks.append(
                {
                    "title": current_title,
                    "text": current_block.strip(),
                    "chunk_type": "text",
                    "table_json": None,
                    "questions": [],
                }
            )

        return chunks

    # ------------------------------------------------------------------
    # Storage helpers (staging schema)
    # ------------------------------------------------------------------

    def _cleanup_existing(self, conn, doc_id: str):
        """Delete existing children/chunks for a document before re-ingestion."""
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM document_children WHERE document_id = %s", (doc_id,)
        )
        child_ids = [r[0] for r in cur.fetchall()]
        if child_ids:
            logger.info(
                f"  Cleaning up {len(child_ids)} existing children + chunks "
                f"for {doc_id}"
            )
            cur.execute(
                "DELETE FROM document_chunks WHERE child_id = ANY(%s)",
                (child_ids,),
            )
            cur.execute(
                "DELETE FROM document_children WHERE id = ANY(%s)",
                (child_ids,),
            )
        cur.execute(
            "DELETE FROM document_chunks WHERE document_id = %s AND child_id IS NULL",
            (doc_id,),
        )
        conn.commit()
        cur.close()

    def _ensure_document_record(
        self,
        conn,
        doc_id: str,
        doc_name: str,
        content: str,
        category: str = "financial",
    ):
        """Upsert the parent staging.documents record."""
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO documents (id, name, content, category)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                content = EXCLUDED.content,
                category = EXCLUDED.category
            """,
            (doc_id, doc_name, content, category),
        )
        conn.commit()
        cur.close()

    @staticmethod
    def _store_child(conn, doc_id: str, section_title: str, chunks: list) -> int:
        """Insert a document_children record and return its id."""
        cur = conn.cursor()
        # Store a summary as the child content (first 5000 chars of combined chunk text)
        combined = "\n\n".join(c.get("text", "")[:500] for c in chunks[:10])
        meta = json.dumps({"section": section_title, "chunk_count": len(chunks)})
        cur.execute(
            """
            INSERT INTO document_children (document_id, chunk_index, content, metadata)
            VALUES (%s, 0, %s, %s) RETURNING id
            """,
            (doc_id, combined[:5000], meta),
        )
        child_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        return child_id

    @staticmethod
    def _store_chunks(
        conn,
        doc_id: str,
        doc_name: str,
        chunks: List[dict],
        child_id: int,
    ):
        """Bulk-insert chunks into staging.document_chunks."""
        if not chunks:
            return

        cur = conn.cursor()
        pg_data = []
        for idx, chunk in enumerate(chunks):
            text = chunk.get("text", "").strip()
            if len(text) < 20:
                continue
            title = chunk.get("title", "Untitled")
            chunk_type = chunk.get("chunk_type", "text")
            table_json_str = (
                json.dumps(chunk["table_json"], ensure_ascii=False)
                if chunk.get("table_json")
                else None
            )
            tokens_est = int(len(text) / 4)
            pg_data.append(
                (doc_id, idx, title, text, chunk_type, table_json_str, tokens_est, child_id)
            )

        if pg_data:
            execute_values(
                cur,
                """
                INSERT INTO document_chunks
                    (document_id, chunk_index, title, content, chunk_type,
                     table_json, tokens_estimated, child_id)
                VALUES %s
                ON CONFLICT (document_id, chunk_index) DO UPDATE SET
                    content = EXCLUDED.content,
                    title = EXCLUDED.title,
                    chunk_type = EXCLUDED.chunk_type,
                    table_json = EXCLUDED.table_json,
                    child_id = EXCLUDED.child_id
                """,
                pg_data,
            )
        conn.commit()
        cur.close()
        logger.info(
            f"    Stored {len(pg_data)} chunks (child_id={child_id}, "
            f"chunk_only=True)"
        )

    def _upsert_financial_document(
        self,
        conn,
        doc_id: str,
        doc_type: str,
        fiscal_year: int,
        source_url: Optional[str],
        pdf_path: Optional[str],
        page_count: int,
        tables_found: int,
        chunks_created: int,
    ):
        """Upsert a row in staging.financial_documents with extraction stats."""
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO financial_documents
                (id, doc_type, fiscal_year, source_url, pdf_path,
                 page_count, docling_tables_found, docling_chunks_created,
                 review_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending')
            ON CONFLICT (id) DO UPDATE SET
                doc_type = EXCLUDED.doc_type,
                fiscal_year = EXCLUDED.fiscal_year,
                source_url = COALESCE(EXCLUDED.source_url, financial_documents.source_url),
                pdf_path = COALESCE(EXCLUDED.pdf_path, financial_documents.pdf_path),
                page_count = EXCLUDED.page_count,
                docling_tables_found = EXCLUDED.docling_tables_found,
                docling_chunks_created = EXCLUDED.docling_chunks_created,
                review_status = 'pending'
            """,
            (
                doc_id,
                doc_type,
                fiscal_year,
                source_url,
                pdf_path,
                page_count,
                tables_found,
                chunks_created,
            ),
        )
        cur.close()


# ======================================================================
# Module-level helper functions
# ======================================================================


def _deduplicate_header_rows(headers: List[str], rows: List[list]) -> List[list]:
    """Remove rows where every cell duplicates the corresponding header value.

    Docling sometimes repeats the header row as the first data row when the
    PDF has merged/multi-level headers.  Also cleans individual cells within
    a row that exactly duplicate their column header (set to empty string).
    """
    if not rows:
        return rows

    cleaned = []
    header_set = set(str(h).strip().lower() for h in headers if h)

    for row in rows:
        cells = [str(c).strip() for c in row]
        # Skip entire row if ALL non-empty cells match a header
        non_empty = [c for c in cells if c]
        if non_empty and all(c.lower() in header_set for c in non_empty):
            continue

        # Deduplicate individual cells that exactly repeat their column header
        deduped_row = []
        for i, cell in enumerate(cells):
            if i < len(headers) and cell.lower() == str(headers[i]).strip().lower():
                deduped_row.append("")
            else:
                deduped_row.append(cell)
        cleaned.append(deduped_row)

    return cleaned


def _table_json_to_markdown(table_json: dict) -> str:
    """Render a table_json dict as a Markdown table string for embedding."""
    headers = table_json.get("headers", [])
    rows = table_json.get("rows", [])

    if not headers:
        return ""

    lines = []
    # Header row
    lines.append("| " + " | ".join(str(h) for h in headers) + " |")
    # Separator
    lines.append("| " + " | ".join("---" for _ in headers) + " |")
    # Data rows
    for row in rows:
        # Pad or truncate row to match header count
        padded = list(row) + [""] * max(0, len(headers) - len(row))
        lines.append(
            "| " + " | ".join(str(c) for c in padded[: len(headers)]) + " |"
        )

    return "\n".join(lines)


def _strip_markdown_tables(text: str) -> str:
    """Remove Markdown table blocks from text so we don't double-count them.

    A Markdown table is a block of lines where each line starts with '|'.
    """
    lines = text.splitlines()
    out = []
    in_table = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and "|" in stripped[1:]:
            in_table = True
            continue
        if in_table and not stripped:
            # Blank line after table — end of table block
            in_table = False
            continue
        if in_table:
            # Non-table line immediately after table lines
            in_table = False
        out.append(line)

    return "\n".join(out)


def _extract_overlap(text: str, max_chars: int) -> str:
    """Extract the last ~max_chars of text, breaking at a paragraph boundary."""
    if len(text) <= max_chars:
        return text

    # Try to break at a paragraph boundary
    search_from = len(text) - max_chars - 100
    if search_from < 0:
        search_from = 0

    pos = text.rfind("\n\n", search_from, len(text) - 50)
    if pos > search_from:
        return text[pos:].strip()

    # Fall back to sentence boundary
    pos = text.rfind(". ", search_from)
    if pos > search_from:
        return text[pos + 2 :].strip()

    # Hard cut
    return text[-max_chars:].strip()
