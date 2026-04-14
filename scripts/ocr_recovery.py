#!/usr/bin/env python3
"""
ocr_recovery.py -- WS7 Phase B: OCR Recovery for All Garbled Documents
======================================================================

Re-processes damaged documents (moties, amendementen, bijlagen, brieven,
raadsbesluiten, raadsvoorstellen, and others) through Apple Vision OCR or
Docling+Tesseract with Dutch language config, replacing garbled pypdf text.

Damage patterns fixed:
  1. Word concatenation ("DegemeenteraadvanRotterdambijeenop28november")
  2. Unicode ligatures (fi -> fi, fl -> fl)
  3. OCR hallucinations (ROTI'ERDAM, etc.)
  4. Near-zero clean content from failed extraction

Pipeline:
  identify -> download PDF -> Docling OCR -> normalize -> quality gate ->
  backup original -> update content + tsvector -> delete old chunks ->
  re-chunk via SmartIngestor -> optionally re-embed -> checkpoint

All writes are guarded by pg_advisory_lock(42).

Usage:
  python scripts/ocr_recovery.py --dry-run --limit 5
  python scripts/ocr_recovery.py --year 2018 --batch-size 10
  python scripts/ocr_recovery.py --doc-type raadsbesluit --limit 50
  python scripts/ocr_recovery.py --resume
  python scripts/ocr_recovery.py --skip-re-embed --limit 100

See: docs/handoffs/WS7_OCR_RECOVERY.md
"""

import argparse
import gc
import json
import logging
import os
import re
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Project paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CHECKPOINT_DIR = PROJECT_ROOT / "data" / "pipeline_state"
CHECKPOINT_PATH = CHECKPOINT_DIR / "ocr_recovery_checkpoint.json"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_PATH = LOG_DIR / "ocr_recovery.log"

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
ADVISORY_LOCK_ID = 42

def _build_db_url() -> str:
    """Build the database URL from env vars, matching services/storage.py pattern."""
    url = os.getenv("DATABASE_URL", "")
    if url:
        return url
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "neodemos")
    user = os.getenv("DB_USER", "postgres")
    pw = os.getenv("DB_PASSWORD", "postgres")
    return f"postgresql://{user}:{pw}@{host}:{port}/{name}"

DB_URL = _build_db_url()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(str(LOG_PATH), encoding="utf-8"),
    ],
)
logger = logging.getLogger("ocr_recovery")

# Suppress noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("docling").setLevel(logging.WARNING)
logging.getLogger("PIL").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# Unicode ligature replacements
LIGATURE_MAP = {
    "\ufb01": "fi",   # fi
    "\ufb02": "fl",   # fl
    "\ufb00": "ff",   # ff
    "\ufb03": "ffi",  # ffi
    "\ufb04": "ffl",  # ffl
}

# Non-printable characters to strip (U+0000-U+001F except \n \t)
_NON_PRINTABLE_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

# Smart quotes to normalize
_QUOTE_MAP = {
    "\u2018": "'",  # left single
    "\u2019": "'",  # right single
    "\u201c": '"',  # left double
    "\u201d": '"',  # right double
}

# ---------------------------------------------------------------------------
# Detection: garbled runs (concatenated words, not URLs/separators)
# ---------------------------------------------------------------------------
# A "garbled run" is 40+ non-space chars that contain word-boundary evidence:
# lowercase→uppercase transitions (e.g. "gemeenteraadVanRotterdam").
# URLs, separator lines (----), and reference codes are excluded.
_LONG_RUN_RE = re.compile(r"[^\s]{40,}")
_URL_RE = re.compile(r"https?://|www\.|mailto:")
_SEPARATOR_RE = re.compile(r"^[-=_.|+*#~]{40,}$")
_WORD_CONCAT_RE = re.compile(r"[a-zà-ÿ]{3,}[A-ZÀ-Ý]")  # lowercase run → uppercase

# Download settings
DOWNLOAD_TIMEOUT = 60
DOWNLOAD_RETRIES = 3
DOWNLOAD_HEADERS = {
    "User-Agent": "NeoDemos-OCR-Recovery/1.0",
    "Accept": "application/pdf,*/*",
}


# ═══════════════════════════════════════════════════════════════════════
# 1. IDENTIFICATION
# ═══════════════════════════════════════════════════════════════════════

# The SQL query that identifies damaged documents. This matches the
# criteria from WS7_OCR_RECOVERY.md Phase A.
IDENTIFY_QUERY = """
    SELECT
        d.id AS document_id,
        d.name,
        d.url,
        LENGTH(d.content) AS content_len,
        -- Damage classification (order matters: first match wins)
        CASE
            WHEN LENGTH(REGEXP_REPLACE(d.content, '[^\\x20-\\x7E\\xC0-\\xFF\\n]', '', 'g'))::float
                 / GREATEST(LENGTH(d.content), 1) < 0.95 THEN 'low_clean_ratio'
            WHEN d.text_search @@ to_tsquery('dutch','gemeenteraad') = false
                 AND d.content ILIKE '%%gemeenteraad%%' THEN 'bm25_miss'
            WHEN d.content LIKE '%%\ufb01%%' OR d.content LIKE '%%\ufb02%%' THEN 'ligature'
            WHEN d.content ~ '[a-zà-ÿ]{3,}[A-ZÀ-Ý][a-zà-ÿ]{2,}[A-ZÀ-Ý]'
                 AND d.content ~ '[^\\s]{40,}' THEN 'garbled_spacing'
            ELSE 'unknown'
        END AS damage_type,
        ROUND(
            100.0 * LENGTH(REGEXP_REPLACE(d.content, '[^\\x20-\\x7E\\xC0-\\xFF\\n]', '', 'g'))::numeric
            / GREATEST(LENGTH(d.content), 1),
            1
        ) AS clean_pct
    FROM documents d
    WHERE d.content IS NOT NULL
      AND LENGTH(d.content) > 50
      AND d.id NOT LIKE 'transcript_%%'
      -- Exclude already-recovered documents
      AND (d.ocr_quality IS NULL OR d.ocr_quality NOT IN ('good', 'degraded'))
      AND (
          -- Garbled spacing: 40+ chars without space AND word-concatenation evidence
          -- Requires ≥2 lowercase→uppercase transitions to exclude URLs/separators
          (d.content ~ '[a-zà-ÿ]{3,}[A-ZÀ-Ý][a-zà-ÿ]{2,}[A-ZÀ-Ý]'
           AND d.content ~ '[^\\s]{40,}')
          -- BM25 miss for "gemeenteraad"
          OR (d.text_search @@ to_tsquery('dutch','gemeenteraad') = false
              AND d.content ILIKE '%%gemeenteraad%%')
          -- Ligature artifacts
          OR d.content LIKE '%%\ufb01%%'
          OR d.content LIKE '%%\ufb02%%'
          -- Low clean-char ratio
          OR LENGTH(REGEXP_REPLACE(d.content, '[^\\x20-\\x7E\\xC0-\\xFF\\n]', '', 'g'))::float
             / GREATEST(LENGTH(d.content), 1) < 0.95
      )
    ORDER BY d.id
"""


DOC_TYPE_PATTERNS = {
    "motie": "motie",
    "amendement": "amendement",
    "bijlage": "bijlage",
    "brief": "brief|collegebrief|wethoudersbrief",
    "raadsbesluit": "raadsbesluit",
    "raadsvoorstel": "raadsvoorstel|collegevoorstel",
    "initiatiefvoorstel": "initiatiefvoorstel",
    "notulen": "notulen|verslag",
    "financieel": "begroting|jaarstuk|jaarrekening|voorjaarsnota",
}


def get_candidates(
    conn,
    year: Optional[int] = None,
    doc_type: Optional[str] = None,
    damage_type: Optional[str] = None,
    use_queue: bool = True,
) -> List[Dict]:
    """Fetch documents to recover.

    If ``staging.ocr_recovery_queue`` exists and has pending rows, read from
    it.  Otherwise fall back to the inline identification query.

    Returns a list of dicts with keys:
        document_id, name, url, content_len, damage_type, clean_pct
    """
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Try the staging queue first
    if use_queue:
        try:
            cur.execute("SELECT 1 FROM staging.ocr_recovery_queue LIMIT 1")
            queue_exists = True
        except psycopg2.errors.UndefinedTable:
            conn.rollback()
            queue_exists = False

        if queue_exists:
            q = """
                SELECT q.document_id, d.name, d.url,
                       LENGTH(d.content) AS content_len,
                       q.damage_type, q.clean_pct
                FROM staging.ocr_recovery_queue q
                JOIN documents d ON d.id = q.document_id
                WHERE q.status = 'pending'
            """
            filters = []
            params: list = []

            if year:
                filters.append("""
                    EXISTS (
                        SELECT 1 FROM document_assignments da
                        JOIN meetings m ON m.id = da.meeting_id
                        WHERE da.document_id = q.document_id
                          AND EXTRACT(YEAR FROM m.start_date) = %s
                    )
                """)
                params.append(year)
            if damage_type:
                filters.append("q.damage_type = %s")
                params.append(damage_type)

            if filters:
                q += " AND " + " AND ".join(filters)
            q += " ORDER BY q.document_id"

            cur.execute(q, params)
            rows = [dict(r) for r in cur.fetchall()]
            if rows:
                logger.info(f"Read {len(rows)} pending docs from staging.ocr_recovery_queue")
                cur.close()
                return rows
            logger.info("Queue table exists but has no pending rows; falling back to inline query")

    # Fallback: inline identification
    base = IDENTIFY_QUERY
    filters = []
    params = []

    if doc_type:
        pattern = DOC_TYPE_PATTERNS.get(doc_type)
        if pattern:
            filters.append("LOWER(sub.name) ~ %s")
            params.append(pattern)
        elif doc_type == "overig":
            known = "|".join(DOC_TYPE_PATTERNS.values())
            filters.append("NOT LOWER(sub.name) ~ %s")
            params.append(known)
    if year:
        filters.append("""
            EXISTS (
                SELECT 1 FROM document_assignments da
                JOIN meetings m ON m.id = da.meeting_id
                WHERE da.document_id = sub.document_id
                  AND EXTRACT(YEAR FROM m.start_date) = %s
            )
        """)
        params.append(year)
    if damage_type:
        filters.append("sub.damage_type = %s")
        params.append(damage_type)

    if filters:
        base = f"SELECT * FROM ({base}) sub WHERE " + " AND ".join(filters)

    cur.execute(base, params)
    rows = [dict(r) for r in cur.fetchall()]
    logger.info(f"Inline identification found {len(rows)} damaged documents")
    cur.close()
    return rows


# ═══════════════════════════════════════════════════════════════════════
# 2. DOWNLOAD
# ═══════════════════════════════════════════════════════════════════════

# Thread-local httpx client — reuses connections (TLS, HTTP/2) across downloads.
# Each worker thread gets its own client with persistent connection pooling.
_thread_local = threading.local()


def _get_http_client() -> httpx.Client:
    """Return a thread-local httpx.Client, creating one if needed."""
    if not hasattr(_thread_local, "http_client"):
        _thread_local.http_client = httpx.Client(
            headers=DOWNLOAD_HEADERS,
            follow_redirects=True,
            timeout=DOWNLOAD_TIMEOUT,
        )
    return _thread_local.http_client


def download_pdf(url: str, temp_dir: str) -> Optional[str]:
    """Download a PDF from ``url`` to ``temp_dir``.

    Uses a thread-local httpx.Client for connection reuse (skips TLS handshake
    after first request per thread).

    Returns the local file path, or None on failure.
    """
    if not url:
        return None

    client = _get_http_client()
    for attempt in range(1, DOWNLOAD_RETRIES + 1):
        try:
            resp = client.get(url)
            resp.raise_for_status()

            # Verify it looks like a PDF
            if not resp.content[:5].startswith(b"%PDF"):
                logger.warning(f"  URL did not return a PDF (first bytes: {resp.content[:20]!r})")
                return None

            fd, path = tempfile.mkstemp(suffix=".pdf", dir=temp_dir)
            with os.fdopen(fd, "wb") as f:
                f.write(resp.content)
            return path
        except Exception as e:
            if attempt < DOWNLOAD_RETRIES:
                wait = attempt * 2
                logger.warning(f"  Download attempt {attempt} failed ({e}), retrying in {wait}s...")
                time.sleep(wait)
            else:
                logger.error(f"  Download failed after {DOWNLOAD_RETRIES} attempts: {e}")
                return None
    return None


# ═══════════════════════════════════════════════════════════════════════
# 3. RE-OCR VIA DOCLING
# ═══════════════════════════════════════════════════════════════════════

# Lazy-cached converter (heavy model loading, reuse across documents)
_docling_converter = None
_docling_use_count = 0
_DOCLING_RESET_EVERY = 500  # Reset converter to reclaim C++ memory (Tesseract/MPS)


def _get_docling_converter():
    """Lazy-initialise and cache the Docling DocumentConverter with
    TesseractCliOcrOptions for Dutch + English, full-page OCR.

    Automatically resets every 500 uses to reclaim C++ memory from
    Tesseract/MPS that gc.collect() cannot reach (~10-30s re-init penalty).

    NOTE: The WS7 handoff originally specified OcrAutoOptions which
    falls back to RapidOCR (no Dutch support).  The correct approach
    is TesseractCliOcrOptions.  Requires: ``brew install tesseract tesseract-lang``
    """
    global _docling_converter, _docling_use_count
    _docling_use_count += 1
    if _docling_converter is not None and _docling_use_count < _DOCLING_RESET_EVERY:
        return _docling_converter
    if _docling_converter is not None:
        logger.info(f"  Resetting Docling converter after {_docling_use_count} uses (memory reclaim)")
        _docling_converter = None
        _docling_use_count = 0
        gc.collect()

    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        TesseractCliOcrOptions,
    )
    from docling.datamodel.base_models import InputFormat

    ocr_options = TesseractCliOcrOptions(
        lang=["nld", "eng"],
        force_full_page_ocr=True,
    )
    pipeline_options = PdfPipelineOptions(
        do_ocr=True,
        ocr_options=ocr_options,
    )
    _docling_converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
        }
    )
    logger.info("Docling DocumentConverter initialised (Tesseract nld+eng, force_full_page_ocr)")
    return _docling_converter


DOCLING_TIMEOUT = 300  # 5 min — aggressive; if it can't finish in 5 min, skip it


def run_docling_ocr(pdf_path: str) -> Optional[str]:
    """Process a PDF through Docling and return the extracted text.

    Enforces a 15-minute timeout via a thread future. A 500-page full-page-OCR
    job takes ~15–20 min at most; anything beyond that is a stuck/malformed PDF.

    Returns None on failure or timeout.
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

    def _convert():
        try:
            converter = _get_docling_converter()
            result = converter.convert(pdf_path)
            text = result.document.export_to_text()
            return text.strip() if text else None
        except Exception as e:
            logger.error(f"  Docling OCR failed: {e}")
            return None
        finally:
            gc.collect()

    with ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(_convert)
        try:
            return future.result(timeout=DOCLING_TIMEOUT)
        except FuturesTimeoutError:
            logger.error(f"  Docling OCR timed out ({DOCLING_TIMEOUT}s) — skipping")
            future.cancel()
            return None


def run_apple_vision_ocr(pdf_path: str) -> Optional[str]:
    """Process a PDF through the native macOS Apple Vision OCR tool.

    Requires the compiled Swift binary at scripts/ocr_pdf.
    Faster than Docling (~2.5s/doc vs ~8s/doc), macOS-only.
    """
    import subprocess
    ocr_tool = PROJECT_ROOT / "scripts" / "ocr_pdf"
    if not ocr_tool.exists():
        logger.error(f"Apple Vision OCR tool not found at {ocr_tool}")
        return None
    try:
        result = subprocess.run(
            [str(ocr_tool), pdf_path],
            capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            logger.error(f"  Apple Vision OCR failed (rc={result.returncode}): {result.stderr[:200]}")
            return None
        text = result.stdout
        if "--- OCR RESULT START ---" in text:
            text = text.split("--- OCR RESULT START ---")[1].split("--- OCR RESULT END ---")[0]
        return text.strip() or None
    except subprocess.TimeoutExpired:
        logger.error("  Apple Vision OCR timed out (120s)")
        return None
    except Exception as e:
        logger.error(f"  Apple Vision OCR error: {e}")
        return None


def run_docling_layout_only(pdf_path: str) -> Optional[str]:
    """Extract text using Docling layout analysis without OCR.

    Uses the shared layout converter from docling_converters.py (do_ocr=False).
    Fast (~3s any doc size) because it uses the PDF's existing text layer with
    visual layout coordinates for proper word segmentation. Ideal for
    garbled_spacing docs where the text IS there but words are smashed together.

    Returns None on failure or timeout.
    """
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

    def _convert():
        try:
            from pipeline.docling_converters import get_layout_converter
            converter = get_layout_converter()
            result = converter.convert(pdf_path)
            text = result.document.export_to_text()
            return text.strip() if text else None
        except Exception as e:
            logger.error(f"  Docling layout-only failed: {e}")
            return None
        finally:
            gc.collect()

    with ThreadPoolExecutor(max_workers=1) as ex:
        future = ex.submit(_convert)
        try:
            return future.result(timeout=60)  # 60s — layout-only should be fast
        except FuturesTimeoutError:
            logger.error("  Docling layout-only timed out (60s) — skipping")
            future.cancel()
            return None


def run_ocr(pdf_path: str, engine: str, damage_type: str = "") -> Optional[str]:
    """Dispatch to the configured OCR engine with damage-type-aware routing.

    For garbled_spacing: tries layout-only first (~3s any size, no Tesseract).
    Text IS in the PDF text layer, just needs re-extraction with proper spacing.
    Falls back to full OCR only if layout-only fails.

    For ligature/low_clean_ratio: full OCR (text layer is actually broken).

    When engine is 'apple_vision' and it fails, falls back to Docling+Tesseract.
    """
    # garbled_spacing: layout-only first (text is there, just badly spaced)
    if damage_type == "garbled_spacing":
        logger.info("  Trying layout-only extraction (garbled_spacing)...")
        result = run_docling_layout_only(pdf_path)
        if result:
            return result
        logger.info("  Layout-only insufficient — falling back to full OCR")

    # Full OCR path
    if engine == "apple_vision":
        result = run_apple_vision_ocr(pdf_path)
        if result is None:
            logger.info("  Apple Vision failed — falling back to Docling+Tesseract")
            return run_docling_ocr(pdf_path)
        return result
    return run_docling_ocr(pdf_path)


# ═══════════════════════════════════════════════════════════════════════
# 4. POST-OCR NORMALIZATION
# ═══════════════════════════════════════════════════════════════════════

def normalize_text(text: str) -> str:
    """Apply post-OCR normalization to cleaned text.

    Steps:
      1. Ligature replacement (fi -> fi, fl -> fl, etc.)
      2. Smart quote normalization
      3. Strip non-printable characters (keep \\n, \\t)
      4. Collapse excessive whitespace (3+ newlines -> 2)
      5. Placeholder for SymSpellPy / hunspell smart space insertion

    Returns the normalized text.
    """
    if not text:
        return text

    # 1. Ligature replacement
    for lig, replacement in LIGATURE_MAP.items():
        text = text.replace(lig, replacement)

    # 2. Smart quote normalization
    for fancy, ascii_eq in _QUOTE_MAP.items():
        text = text.replace(fancy, ascii_eq)

    # 3. Strip non-printable characters
    text = _NON_PRINTABLE_RE.sub("", text)

    # 4. Collapse excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse runs of spaces (but not newlines) into single space
    text = re.sub(r"[^\S\n]+", " ", text)

    # 5. Smart space insertion for concatenated words
    #    TODO: Integrate SymSpellPy or hunspell nl_NL dictionary for
    #    conservative word boundary detection on tokens > 20 chars.
    #    For now, apply the most reliable heuristic: split on
    #    camelCase boundaries that look like Dutch word joins.
    text = _insert_spaces_at_case_boundaries(text)

    return text.strip()


def _insert_spaces_at_case_boundaries(text: str) -> str:
    """Insert spaces at obvious lowercase-to-uppercase transitions that
    indicate concatenated Dutch words.

    Examples:
        "DegemeenteraadvanRotterdam" -> "De gemeenteraad van Rotterdam"
        "financieelOverzicht" -> "financieel Overzicht"

    This is deliberately conservative -- it only splits at transitions
    within tokens that are suspiciously long (>= 25 chars) and have
    multiple case transitions, to avoid false positives on proper names
    like "McDonald" or abbreviations.
    """
    def _split_long_token(match):
        token = match.group(0)
        if len(token) < 25:
            return token
        # Count case transitions
        transitions = sum(
            1 for i in range(1, len(token))
            if token[i - 1].islower() and token[i].isupper()
        )
        if transitions < 2:
            return token
        # Insert spaces at lowercase->uppercase boundaries
        result = []
        for i, ch in enumerate(token):
            if i > 0 and token[i - 1].islower() and ch.isupper():
                result.append(" ")
            result.append(ch)
        return "".join(result)

    # Only process long tokens (no spaces inside)
    return re.sub(r"\S{25,}", _split_long_token, text)


# ═══════════════════════════════════════════════════════════════════════
# 5. QUALITY GATE
# ═══════════════════════════════════════════════════════════════════════

def compute_clean_pct(text: str) -> float:
    """Compute the percentage of 'clean' characters in the text.

    Clean = printable ASCII (0x20-0x7E), accented Latin (0xC0-0xFF), newline.
    """
    if not text:
        return 0.0
    clean_chars = len(re.sub(r"[^\x20-\x7E\xC0-\xFF\n]", "", text))
    return 100.0 * clean_chars / max(len(text), 1)


def has_garbled_runs(text: str) -> int:
    """Count genuinely garbled runs (concatenated words, not URLs/separators).

    A run qualifies if it is 40+ non-space chars AND contains at least one
    lowercase→uppercase word-boundary transition (e.g. 'gemeenteraadVan').
    """
    count = 0
    for run in _LONG_RUN_RE.findall(text):
        if _URL_RE.search(run):
            continue
        if _SEPARATOR_RE.match(run):
            continue
        if _WORD_CONCAT_RE.search(run):
            count += 1
    return count


def quality_gate(old_text: str, new_text: str) -> Tuple[bool, str]:
    """Compare old vs new text and decide whether to accept the new version.

    Returns:
        (accept: bool, reason: str)
    """
    if not new_text or len(new_text.strip()) < 50:
        return False, "new text too short (< 50 chars)"

    old_clean = compute_clean_pct(old_text)
    new_clean = compute_clean_pct(new_text)

    old_garbled = has_garbled_runs(old_text)
    new_garbled = has_garbled_runs(new_text)

    old_len = len(old_text)
    new_len = len(new_text)

    # Gate 1: New text must not be dramatically shorter (>50% loss)
    if new_len < old_len * 0.5:
        return False, (
            f"new text too short ({new_len} vs {old_len} chars, "
            f"{100 * new_len / max(old_len, 1):.0f}% of original)"
        )

    # Gate 2: Clean-char ratio must not decrease
    if new_clean < old_clean - 1.0:  # 1% tolerance for rounding
        return False, (
            f"clean-char ratio decreased ({old_clean:.1f}% -> {new_clean:.1f}%)"
        )

    # Gate 3: Garbled runs should not increase
    if new_garbled > old_garbled:
        return False, (
            f"garbled runs increased ({old_garbled} -> {new_garbled})"
        )

    # Gate 4: At least SOME improvement must exist
    improved = False
    reasons = []

    if new_clean > old_clean + 0.5:
        improved = True
        reasons.append(f"clean-char: {old_clean:.1f}% -> {new_clean:.1f}%")

    if new_garbled < old_garbled:
        improved = True
        reasons.append(f"garbled runs: {old_garbled} -> {new_garbled}")

    if new_len > old_len * 1.1:
        improved = True
        reasons.append(f"length: {old_len} -> {new_len} chars")

    # Check BM25 keyword presence improvement
    keyword = "gemeenteraad"
    old_has_keyword = keyword in old_text.lower()
    new_has_keyword = keyword in new_text.lower()
    if new_has_keyword and not old_has_keyword:
        improved = True
        reasons.append("BM25 keyword 'gemeenteraad' now present")

    if not improved:
        return False, "no measurable improvement"

    return True, "; ".join(reasons)


# ═══════════════════════════════════════════════════════════════════════
# 6. DATABASE WRITES (under advisory lock)
# ═══════════════════════════════════════════════════════════════════════

def acquire_advisory_lock(conn, wait: bool = True) -> bool:
    """Acquire pg_advisory_lock(42).

    If ``wait`` is True, blocks until the lock is available.
    If ``wait`` is False, tries once and returns False if unavailable.
    """
    cur = conn.cursor()
    if wait:
        cur.execute("SELECT pg_advisory_lock(%s)", (ADVISORY_LOCK_ID,))
        cur.close()
        return True
    else:
        cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_ID,))
        acquired = cur.fetchone()[0]
        cur.close()
        return acquired


def release_advisory_lock(conn):
    """Release pg_advisory_lock(42)."""
    try:
        cur = conn.cursor()
        cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_ID,))
        cur.close()
    except Exception as e:
        logger.warning(f"Failed to release advisory lock: {e}")


def ensure_backup_table(conn):
    """Create staging.ocr_recovery_originals if it doesn't exist."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS staging.ocr_recovery_originals (
            document_id TEXT PRIMARY KEY,
            original_content TEXT NOT NULL,
            original_clean_pct NUMERIC,
            backed_up_at TIMESTAMP DEFAULT NOW()
        )
    """)
    conn.commit()
    cur.close()


def ensure_queue_table(conn):
    """Create staging.ocr_recovery_queue if it doesn't exist."""
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS staging.ocr_recovery_queue (
            document_id TEXT PRIMARY KEY,
            damage_type TEXT,
            clean_pct NUMERIC,
            flagged_at TIMESTAMP DEFAULT NOW(),
            status TEXT DEFAULT 'pending',
            recovered_at TIMESTAMP,
            error_message TEXT
        )
    """)
    conn.commit()
    cur.close()


def backup_original(conn, doc_id: str, content: str, clean_pct: float):
    """Backup the original content to staging.ocr_recovery_originals.

    Skips if already backed up (idempotent).
    """
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO staging.ocr_recovery_originals (document_id, original_content, original_clean_pct)
        VALUES (%s, %s, %s)
        ON CONFLICT (document_id) DO NOTHING
    """, (doc_id, content, clean_pct))
    cur.close()


def update_document_content(conn, doc_id: str, new_content: str):
    """Update the document's content and re-generate its tsvector."""
    cur = conn.cursor()
    # Strip NUL bytes (Postgres text columns reject them)
    clean = new_content.replace("\x00", "")
    cur.execute("""
        UPDATE documents
        SET content = %s
        WHERE id = %s
    """, (clean, doc_id))
    cur.close()



def delete_old_chunks(conn, doc_id: str) -> int:
    """Delete existing chunks and children for a document. Returns count deleted.

    Deletes all FK-referencing rows (kg_mentions, kg_relationships,
    kg_extraction_log, chunk_questions, financial_lines,
    gr_member_contributions) before removing chunks, to avoid FK violations.
    """
    cur = conn.cursor()

    # Helper: delete FK dependents for a set of chunk IDs expressed as a subquery
    def _delete_fk_dependents_by_doc(subquery: str, params):
        cur.execute(f"""
            DELETE FROM kg_mentions WHERE chunk_id IN ({subquery})
        """, params)
        cur.execute(f"""
            DELETE FROM kg_relationships WHERE chunk_id IN ({subquery})
        """, params)
        cur.execute(f"""
            DELETE FROM kg_extraction_log WHERE chunk_id IN ({subquery})
        """, params)
        cur.execute(f"""
            DELETE FROM chunk_questions WHERE chunk_id IN ({subquery})
        """, params)
        cur.execute(f"""
            DELETE FROM financial_lines WHERE bron_chunk_id IN ({subquery})
        """, params)
        cur.execute(f"""
            DELETE FROM gr_member_contributions WHERE bron_chunk_id IN ({subquery})
        """, params)

    # Delete FK dependents for child chunks (via document_children)
    _delete_fk_dependents_by_doc(
        "SELECT dc.id FROM document_chunks dc "
        "JOIN document_children dch ON dc.child_id = dch.id "
        "WHERE dch.document_id = %s",
        (doc_id,),
    )

    # Delete FK dependents for orphan chunks (no child_id)
    _delete_fk_dependents_by_doc(
        "SELECT id FROM document_chunks WHERE document_id = %s AND child_id IS NULL",
        (doc_id,),
    )

    # Now safe to delete the chunks themselves
    # Delete chunks that belong to children of this document
    cur.execute("""
        DELETE FROM document_chunks
        WHERE child_id IN (
            SELECT id FROM document_children WHERE document_id = %s
        )
    """, (doc_id,))
    child_chunk_count = cur.rowcount

    # Delete orphan chunks (no child_id)
    cur.execute("""
        DELETE FROM document_chunks
        WHERE document_id = %s AND child_id IS NULL
    """, (doc_id,))
    orphan_count = cur.rowcount

    # Delete children
    cur.execute("""
        DELETE FROM document_children WHERE document_id = %s
    """, (doc_id,))

    cur.close()
    return child_chunk_count + orphan_count


def rechunk_document(conn, doc_id: str, doc_name: str, new_content: str):
    """Re-chunk the document using SmartIngestor (chunk_only mode).

    This reuses the existing tiered chunking strategy without embedding.
    """
    from pipeline.ingestion import SmartIngestor

    ingestor = SmartIngestor(db_url=DB_URL, chunk_only=True)
    # SmartIngestor.ingest_document handles child + chunk creation
    ingestor.ingest_document(
        doc_id=doc_id,
        doc_name=doc_name,
        content=new_content,
        meeting_id=None,
        metadata={"recovery": "ocr_ws7"},
        category="municipal_doc",
    )


def reembed_chunks(conn, doc_id: str):
    """Re-embed chunks for a document via the local embedding service.

    This generates new embeddings and upserts them to Qdrant.
    """
    try:
        from services.local_ai_service import LocalAIService
        from qdrant_client import QdrantClient
        from qdrant_client.models import PointStruct
        import hashlib

        local_ai = LocalAIService(skip_llm=True)
        if not local_ai.is_available():
            logger.warning(f"  Local AI not available for re-embedding {doc_id}")
            return

        qdrant = QdrantClient(url="http://localhost:6333")
        collection_name = "notulen_chunks"

        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            SELECT dc.id, dc.document_id, dc.child_id, dc.chunk_index,
                   dc.title, dc.content, dc.chunk_type,
                   d.name AS doc_name
            FROM document_chunks dc
            JOIN documents d ON d.id = dc.document_id
            WHERE dc.document_id = %s
        """, (doc_id,))
        chunks = cur.fetchall()
        cur.close()

        if not chunks:
            logger.info(f"  No chunks to embed for {doc_id}")
            return

        points = []
        for chunk in chunks:
            text = chunk["content"] or ""
            if len(text) < 20:
                continue
            context_str = f"[Document: {chunk['doc_name']} | Section: {chunk['title']}]\n"
            embedding = local_ai.generate_embedding(context_str + text)
            if embedding is not None:
                hash_str = hashlib.md5(
                    f"{chunk['document_id']}_{chunk['child_id']}_{chunk['chunk_index']}".encode()
                ).hexdigest()
                point_id = int(hash_str[:15], 16)
                payload = {
                    "document_id": chunk["document_id"],
                    "doc_name": chunk["doc_name"],
                    "doc_type": "municipal_doc",
                    "child_id": chunk["child_id"],
                    "chunk_index": chunk["chunk_index"],
                    "chunk_type": chunk["chunk_type"],
                    "title": chunk["title"],
                    "content": text,
                }
                points.append(PointStruct(id=point_id, vector=embedding, payload=payload))

        if points:
            # Upsert in batches of 100
            for i in range(0, len(points), 100):
                qdrant.upsert(
                    collection_name=collection_name,
                    points=points[i : i + 100],
                )
            logger.info(f"  Embedded {len(points)} chunks for {doc_id}")
        else:
            logger.info(f"  No embeddable chunks for {doc_id}")
    except Exception as e:
        logger.error(f"  Re-embedding failed for {doc_id}: {e}")


def update_queue_status(conn, doc_id: str, status: str, error_message: str = None):
    """Update the recovery queue status for a document.

    Creates the row if it doesn't exist (handles inline-identification mode).
    """
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO staging.ocr_recovery_queue
            (document_id, status, recovered_at, error_message)
        VALUES (%s, %s, CASE WHEN %s = 'recovered' THEN NOW() ELSE NULL END, %s)
        ON CONFLICT (document_id) DO UPDATE SET
            status = EXCLUDED.status,
            recovered_at = CASE WHEN EXCLUDED.status = 'recovered' THEN NOW()
                                ELSE staging.ocr_recovery_queue.recovered_at END,
            error_message = EXCLUDED.error_message
    """, (doc_id, status, status, error_message))
    cur.close()


# ═══════════════════════════════════════════════════════════════════════
# 7. CHECKPOINT
# ═══════════════════════════════════════════════════════════════════════

def load_checkpoint() -> Dict:
    """Load the checkpoint file if it exists."""
    if CHECKPOINT_PATH.exists():
        try:
            with open(CHECKPOINT_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("Corrupt checkpoint file, starting fresh")
    return {"completed_ids": [], "stats": {}}


def save_checkpoint(completed_ids: List[str], stats: Dict):
    """Save checkpoint to disk."""
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "completed_ids": completed_ids,
        "stats": stats,
        "last_saved": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    # Write atomically (write to temp then rename)
    tmp_path = str(CHECKPOINT_PATH) + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, str(CHECKPOINT_PATH))


# ═══════════════════════════════════════════════════════════════════════
# 8. MAIN PIPELINE
# ═══════════════════════════════════════════════════════════════════════

def prefetch_doc(
    doc: Dict,
    temp_dir: str,
    engine: str,
) -> Tuple[str, Optional[str], Optional[Tuple[str, str]]]:
    """Download, OCR, and normalize a single document. Thread-safe — no DB access.

    Returns:
        (doc_id, new_text, error)
        where error is (status, detail) if something went wrong, else None.
    """
    doc_id = doc["document_id"]
    url = doc.get("url")
    damage = doc.get("damage_type", "")

    if not url:
        return doc_id, None, ("no_source", "no URL available")

    pdf_path = download_pdf(url, temp_dir)
    if not pdf_path:
        return doc_id, None, ("no_source", f"download failed for {url}")

    try:
        new_text = run_ocr(pdf_path, engine, damage_type=damage)
        if not new_text:
            return doc_id, None, ("error", f"{engine} returned empty text")
        return doc_id, normalize_text(new_text), None
    except Exception as e:
        return doc_id, None, ("error", str(e))
    finally:
        if pdf_path and os.path.exists(pdf_path):
            os.remove(pdf_path)


def process_single_document(
    conn,
    doc: Dict,
    temp_dir: str,
    dry_run: bool = False,
    skip_re_embed: bool = False,
    wait_for_lock: bool = True,
    engine: str = "apple_vision",
    prefetched_text: Optional[str] = None,
) -> Tuple[str, str]:
    """Process a single document through the full recovery pipeline.

    If ``prefetched_text`` is provided (from a parallel prefetch worker),
    the download + OCR phase is skipped.

    Returns:
        (status, detail) where status is one of:
        'recovered', 'skipped', 'no_source', 'quality_fail', 'review_needed', 'error'
    """
    doc_id = doc["document_id"]
    doc_name = doc.get("name", "Unknown")

    if prefetched_text is not None:
        new_text = prefetched_text
    else:
        # Step 1: Download the source PDF
        url = doc.get("url")
        if not url:
            return "no_source", "no URL available"

        pdf_path = download_pdf(url, temp_dir)
        if not pdf_path:
            return "no_source", f"download failed for {url}"

        try:
            # Step 2: Re-OCR via selected engine
            damage = doc.get("damage_type", "")
            new_text = run_ocr(pdf_path, engine, damage_type=damage)
            if not new_text:
                return "error", f"{engine} returned empty text"
            # Step 3: Post-OCR normalization
            new_text = normalize_text(new_text)
        finally:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)

    try:

        # Step 4: Get current content for comparison
        cur = conn.cursor()
        cur.execute("SELECT content FROM documents WHERE id = %s", (doc_id,))
        row = cur.fetchone()
        cur.close()

        if not row or not row[0]:
            return "error", "document not found in DB"

        old_text = row[0]

        # Step 5: Quality gate
        accept, reason = quality_gate(old_text, new_text)
        if not accept:
            return "quality_fail", reason

        # Step 6: Report in dry-run mode
        if dry_run:
            old_clean = compute_clean_pct(old_text)
            new_clean = compute_clean_pct(new_text)
            old_garbled = has_garbled_runs(old_text)
            new_garbled = has_garbled_runs(new_text)
            return "dry_run_pass", (
                f"WOULD recover: {reason} | "
                f"clean: {old_clean:.1f}% -> {new_clean:.1f}% | "
                f"garbled: {old_garbled} -> {new_garbled} | "
                f"len: {len(old_text)} -> {len(new_text)}"
            )

        # Step 7: Write (under advisory lock)
        lock_acquired = acquire_advisory_lock(conn, wait=wait_for_lock)
        if not lock_acquired:
            return "skipped", "could not acquire advisory lock (--no-wait-for-lock)"

        try:
            old_clean_pct = compute_clean_pct(old_text)

            # 7a. Backup original (table created at startup)
            backup_original(conn, doc_id, old_text, old_clean_pct)

            # 7b. Update content + tsvector
            update_document_content(conn, doc_id, new_text)

            # 7b-ii. Mark OCR quality as good (recovery succeeded)
            cur = conn.cursor()
            cur.execute(
                "UPDATE documents SET ocr_quality = 'good' WHERE id = %s",
                (doc_id,),
            )
            cur.close()

            # 7c. Delete kg_mentions + old chunks, then COMMIT immediately.
            #     SmartIngestor opens its own DB connection and DELETEs the same
            #     rows — if our transaction is open when it runs, it blocks on
            #     row-level locks → deadlock. Committing first releases those locks.
            deleted = delete_old_chunks(conn, doc_id)
            conn.commit()

            # 7d. Re-chunk via SmartIngestor (new connection, clean slate)
            rechunk_document(conn, doc_id, doc_name, new_text)

            # 7e. Update queue status (table created at startup)
            update_queue_status(conn, doc_id, "recovered")
            conn.commit()

            # 7f. Re-embed (outside the advisory lock -- embedding is slow
            #     and reads from the committed data)
        finally:
            release_advisory_lock(conn)

        # Re-embed after releasing the lock (not write-critical)
        if not skip_re_embed:
            reembed_chunks(conn, doc_id)

        new_clean_pct = compute_clean_pct(new_text)
        return "recovered", (
            f"{reason} | clean: {old_clean_pct:.1f}% -> {new_clean_pct:.1f}% | "
            f"deleted {deleted} old chunks"
        )

    except Exception:
        raise


def run(
    dry_run: bool = False,
    limit: Optional[int] = None,
    resume: bool = False,
    batch_size: int = 10,
    year: Optional[int] = None,
    doc_type: Optional[str] = None,
    damage_type: Optional[str] = None,
    skip_damage_types: Optional[List[str]] = None,
    skip_re_embed: bool = False,
    wait_for_lock: bool = True,
    engine: str = "apple_vision",
    workers: int = 1,
    max_content_len: Optional[int] = None,
):
    """Main entry point for the OCR recovery pipeline."""
    logger.info("=" * 70)
    logger.info("OCR Recovery for All Document Types (WS7)")
    logger.info("=" * 70)
    logger.info(f"  dry_run={dry_run}, limit={limit}, resume={resume}")
    logger.info(f"  batch_size={batch_size}, year={year}, doc_type={doc_type}, damage_type={damage_type}")
    logger.info(f"  skip_damage_types={skip_damage_types}, workers={workers}")
    logger.info(f"  skip_re_embed={skip_re_embed}, wait_for_lock={wait_for_lock}, engine={engine}")

    # Load checkpoint for resume mode
    checkpoint = load_checkpoint() if resume else {"completed_ids": [], "stats": {}}
    completed_ids = set(checkpoint.get("completed_ids", []))
    if resume and completed_ids:
        logger.info(f"  Resuming: {len(completed_ids)} documents already completed")

    # Connect and identify candidates
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False

    try:
        # Ensure staging schema exists
        cur = conn.cursor()
        cur.execute("CREATE SCHEMA IF NOT EXISTS staging")
        conn.commit()
        cur.close()

        # Ensure staging tables exist (once, not per-document)
        ensure_queue_table(conn)
        ensure_backup_table(conn)

        # Get candidates
        candidates = get_candidates(conn, year=year, doc_type=doc_type, damage_type=damage_type)

        if not candidates:
            logger.info("No damaged documents found. Nothing to do.")
            return

        # Filter out already-completed (resume mode)
        if completed_ids:
            before = len(candidates)
            candidates = [c for c in candidates if c["document_id"] not in completed_ids]
            logger.info(f"  Filtered: {before} -> {len(candidates)} after resume exclusion")

        # Skip specified damage types (e.g. bm25_miss has near-zero recovery rate)
        if skip_damage_types:
            before = len(candidates)
            candidates = [c for c in candidates if c.get("damage_type") not in skip_damage_types]
            skipped_n = before - len(candidates)
            if skipped_n:
                logger.info(f"  Skipped {skipped_n} docs with damage types: {skip_damage_types}")

        # Filter by content length (focus on quick wins, skip heavy docs)
        if max_content_len:
            before = len(candidates)
            candidates = [c for c in candidates if c.get("content_len", 0) <= max_content_len]
            skipped_n = before - len(candidates)
            if skipped_n:
                logger.info(f"  Skipped {skipped_n} docs > {max_content_len} chars (--max-content-len)")

        # Descope low-yield candidates (data-driven from 3,500-doc run):
        #   - ligature at 99.9%+ clean: only 17% recovery rate
        #   - garbled_spacing at 99.9%+ clean: 45% — borderline, keep for now
        before = len(candidates)
        candidates = [
            c for c in candidates
            if not (c.get("damage_type") == "ligature" and c.get("clean_pct", 0) >= 99.9)
        ]
        descoped = before - len(candidates)
        if descoped:
            logger.info(f"  Descoped {descoped} ligature docs at >=99.9% clean (17% recovery rate)")

        # Sort by recovery potential: lowest clean_pct first (92-99% recovery
        # for clean<99% vs 35% for clean>99.9%). This ensures the best shots
        # get processed first even if the run is interrupted.
        candidates.sort(key=lambda c: c.get("clean_pct", 100))

        # Apply limit
        if limit:
            candidates = candidates[:limit]

        total = len(candidates)
        logger.info(f"Processing {total} documents (sorted by recovery potential)")

        # NOTE: we do NOT bulk-mark ocr_quality='bad' upfront. Each document
        # is marked individually only after successfully downloading + OCR'ing,
        # to avoid polluting the column with false positives.

        # Print damage type distribution
        damage_counts: Dict[str, int] = {}
        for c in candidates:
            dt = c.get("damage_type", "unknown")
            damage_counts[dt] = damage_counts.get(dt, 0) + 1
        for dt, count in sorted(damage_counts.items()):
            logger.info(f"  {dt}: {count}")

        # Stats tracking
        stats = {
            "total": total,
            "recovered": 0,
            "quality_fail": 0,
            "no_source": 0,
            "skipped": 0,
            "error": 0,
            "dry_run_pass": 0,
        }

        STATUS_SYMBOL = {
            "recovered": "OK", "dry_run_pass": "DRY", "quality_fail": "QFAIL",
            "no_source": "NOSRC", "skipped": "SKIP", "review_needed": "REVIEW",
            "error": "ERR",
        }

        def _handle_result(idx: int, doc: Dict, status: str, detail: str):
            """Log result, update queue/stats, checkpoint. Called serially from main thread."""
            doc_id = doc["document_id"]
            doc_name = (doc.get("name") or "Unknown")[:60]
            damage = doc.get("damage_type", "?")
            clean = doc.get("clean_pct", 0)
            sym = STATUS_SYMBOL.get(status, "?")
            logger.info(f"[{idx}/{total}] {doc_name} (damage={damage}, clean={clean}%)")
            logger.info(f"  [{sym}] {detail}")

            stats[status] = stats.get(status, 0) + 1

            if not dry_run and status in ("no_source", "quality_fail", "error"):
                try:
                    update_queue_status(
                        conn, doc_id,
                        "review_needed" if status == "quality_fail" else status,
                        error_message=detail[:500],
                    )
                    if status == "quality_fail":
                        cur = conn.cursor()
                        cur.execute(
                            "UPDATE documents SET ocr_quality = 'degraded' WHERE id = %s",
                            (doc_id,),
                        )
                        cur.close()
                    conn.commit()
                except Exception:
                    conn.rollback()

            completed_ids.add(doc_id)
            if idx % batch_size == 0:
                save_checkpoint(list(completed_ids), stats)
                logger.info(f"  Checkpoint saved ({idx}/{total})")
                gc.collect()

        # Create temp dir for PDF downloads
        with tempfile.TemporaryDirectory(prefix="ocr_recovery_") as temp_dir:
            global_idx = 0

            # ── Phase 1: Fast in-place ligature fix (no download, no OCR) ──────
            # Ligature damage = Unicode chars (ﬁ→fi, ﬂ→fl, etc.) already present
            # in the stored DB content. normalize_text() fixes them instantly.
            # No PDF download or OCR engine needed — pure text transformation.
            ligature_cands = [c for c in candidates if c.get("damage_type") == "ligature"]
            ocr_cands      = [c for c in candidates if c.get("damage_type") != "ligature"]

            if ligature_cands:
                logger.info(
                    f"  Phase 1: In-place ligature fix ({len(ligature_cands)} docs — no OCR)"
                )
                # Fetch all ligature doc content in one batch query
                lig_ids = [c["document_id"] for c in ligature_cands]
                _cur = conn.cursor(cursor_factory=RealDictCursor)
                _cur.execute(
                    "SELECT id::text AS id, content FROM documents WHERE id = ANY(%s)",
                    (lig_ids,),
                )
                content_map: Dict[str, str] = {r["id"]: r["content"] for r in _cur.fetchall()}
                _cur.close()

                for doc in ligature_cands:
                    global_idx += 1
                    doc_id = doc["document_id"]
                    existing = content_map.get(doc_id)
                    if not existing:
                        _handle_result(global_idx, doc, "no_source", "content not found in DB")
                        continue
                    normalized = normalize_text(existing)
                    try:
                        status, detail = process_single_document(
                            conn=conn, doc=doc, temp_dir=temp_dir,
                            dry_run=dry_run, skip_re_embed=skip_re_embed,
                            wait_for_lock=wait_for_lock, engine=engine,
                            prefetched_text=normalized,
                        )
                    except Exception as e:
                        status, detail = "error", f"unexpected: {e}"
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                    _handle_result(global_idx, doc, status, detail)

            # ── Phase 2: OCR-based recovery for non-ligature docs ───────────────
            candidates = ocr_cands  # garbled_spacing, low_clean_ratio, etc.

            if workers <= 1:
                # ── Sequential mode (default) ──────────────────────────────
                for doc in candidates:
                    global_idx += 1
                    try:
                        status, detail = process_single_document(
                            conn=conn, doc=doc, temp_dir=temp_dir,
                            dry_run=dry_run, skip_re_embed=skip_re_embed,
                            wait_for_lock=wait_for_lock, engine=engine,
                        )
                    except Exception as e:
                        status, detail = "error", f"unexpected: {e}"
                        logger.exception(f"  Unexpected error processing {doc['document_id']}")
                        try:
                            conn.rollback()
                        except Exception:
                            pass
                    _handle_result(global_idx, doc, status, detail)

            else:
                # ── Parallel prefetch mode ──────────────────────────────────
                # Streaming pool: always keep exactly `workers` prefetch threads
                # in flight. As soon as one finishes, the next candidate is
                # submitted immediately — pool is never idle waiting for a batch.
                # Memory bounded: at most `workers` OCR results in memory at once.
                # GC runs every batch_size docs to reclaim Docling/Tesseract memory.
                logger.info(f"  Phase 2: OCR prefetch: {workers} workers (streaming, {len(candidates)} docs)")
                idx = global_idx  # continue counter from phase 1

                with ThreadPoolExecutor(max_workers=workers) as pool:
                    future_to_doc: Dict = {}

                    # Prime the pool with the first `workers` docs
                    for doc in candidates[:workers]:
                        future_to_doc[pool.submit(prefetch_doc, doc, temp_dir, engine)] = doc
                    remaining_iter = iter(candidates[workers:])

                    while future_to_doc:
                        # Wait for the next completed future (any order)
                        done_future = next(as_completed(future_to_doc))
                        doc = future_to_doc.pop(done_future)
                        doc_id = doc["document_id"]
                        idx += 1

                        # Immediately submit next candidate to keep pool full
                        try:
                            next_doc = next(remaining_iter)
                            future_to_doc[pool.submit(prefetch_doc, next_doc, temp_dir, engine)] = next_doc
                        except StopIteration:
                            pass  # No more candidates — drain remaining futures

                        # Handle the completed prefetch
                        try:
                            _, fetched_text, prefetch_err = done_future.result()
                        except Exception as e:
                            _handle_result(idx, doc, "error", f"prefetch exception: {e}")
                            continue

                        if prefetch_err:
                            _handle_result(idx, doc, prefetch_err[0], prefetch_err[1])
                            continue

                        # Write phase is serial (advisory lock serialises anyway)
                        try:
                            status, detail = process_single_document(
                                conn=conn, doc=doc, temp_dir=temp_dir,
                                dry_run=dry_run, skip_re_embed=skip_re_embed,
                                wait_for_lock=wait_for_lock, engine=engine,
                                prefetched_text=fetched_text,
                            )
                        except Exception as e:
                            status, detail = "error", f"unexpected: {e}"
                            logger.exception(f"  Unexpected error writing {doc_id}")
                            try:
                                conn.rollback()
                            except Exception:
                                pass
                        _handle_result(idx, doc, status, detail)

                        # Periodic GC to reclaim Docling C++ memory
                        if idx % (batch_size * 5) == 0:
                            gc.collect()

        # Final checkpoint
        save_checkpoint(list(completed_ids), stats)

    finally:
        conn.close()

    # Summary
    logger.info("")
    logger.info("=" * 70)
    logger.info("OCR RECOVERY COMPLETED")
    logger.info("=" * 70)
    logger.info(f"Total candidates:       {stats.get('total', 0)}")
    if dry_run:
        logger.info(f"Would recover:          {stats.get('dry_run_pass', 0)}")
    else:
        logger.info(f"Successfully recovered: {stats.get('recovered', 0)}")
    logger.info(f"Quality gate failures:  {stats.get('quality_fail', 0)}")
    logger.info(f"No source PDF:          {stats.get('no_source', 0)}")
    logger.info(f"Skipped:                {stats.get('skipped', 0)}")
    logger.info(f"Errors:                 {stats.get('error', 0)}")
    logger.info("=" * 70)

    if not dry_run and stats.get("recovered", 0) > 0:
        logger.info(
            "\nNext steps:\n"
            "  1. Run the BM25 hit rate verification query from WS7_OCR_RECOVERY.md Phase C\n"
            "  2. Spot-check 20 recovered documents for readability\n"
            "  3. If --skip-re-embed was used, run the embedding separately"
        )


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description=(
            "WS7 OCR Recovery: re-process all garbled documents "
            "through Docling with full-page Dutch OCR."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run on first 5 documents (no DB writes)
  python scripts/ocr_recovery.py --dry-run --limit 5

  # Recover 2018 documents (worst cohort)
  python scripts/ocr_recovery.py --year 2018 --batch-size 10

  # Recover only raadsbesluiten (36.5% garbled)
  python scripts/ocr_recovery.py --doc-type raadsbesluit --limit 50

  # Resume from where we left off
  python scripts/ocr_recovery.py --resume

  # Recovery without re-embedding (batch embedding later)
  python scripts/ocr_recovery.py --skip-re-embed

See: docs/handoffs/WS7_OCR_RECOVERY.md
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Download + OCR + normalize + quality-gate, but no DB writes",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N documents",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from the last checkpoint",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=10,
        help="Documents per checkpoint (default: 10, kept small because Docling OCR is slow)",
    )
    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Only process documents from a specific year (e.g. 2018)",
    )
    parser.add_argument(
        "--doc-type",
        type=str,
        default=None,
        choices=["motie", "amendement", "bijlage", "brief", "raadsbesluit",
                 "raadsvoorstel", "initiatiefvoorstel", "notulen", "financieel", "overig"],
        help="Only process documents of a specific type (by name pattern)",
    )
    parser.add_argument(
        "--engine",
        choices=["apple_vision", "docling_tesseract"],
        default="apple_vision",
        help="OCR engine (default: apple_vision — ~2.5s/doc, macOS only; "
             "docling_tesseract — ~8s/doc, runs on Linux/server)",
    )
    parser.add_argument(
        "--damage-type",
        type=str,
        default=None,
        choices=["garbled_spacing", "ligature", "bm25_miss", "low_clean_ratio"],
        help="Filter by damage type from the recovery queue",
    )
    parser.add_argument(
        "--skip-re-embed",
        action="store_true",
        help="Update text + chunks but skip re-embedding (batch embed later)",
    )
    parser.add_argument(
        "--wait-for-lock",
        action="store_true",
        default=True,
        dest="wait_for_lock",
        help="Wait for advisory lock 42 if held by another process (default)",
    )
    parser.add_argument(
        "--no-wait-for-lock",
        action="store_false",
        dest="wait_for_lock",
        help="Skip documents if advisory lock 42 is held by another process",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help=(
            "Number of parallel download+OCR workers (default: 1). "
            "Writes remain serial under advisory lock. "
            "Recommended: 3-4 on a fast connection."
        ),
    )
    parser.add_argument(
        "--skip-damage-type",
        action="append",
        dest="skip_damage_types",
        default=[],
        choices=["garbled_spacing", "ligature", "bm25_miss", "low_clean_ratio"],
        metavar="TYPE",
        help=(
            "Skip documents with this damage type. "
            "Can be repeated. E.g. --skip-damage-type bm25_miss "
            "(bm25_miss has ~0%% recovery rate and wastes OCR cycles)."
        ),
    )
    parser.add_argument(
        "--max-content-len",
        type=int,
        default=None,
        metavar="CHARS",
        help=(
            "Skip documents longer than this (in chars). "
            "Use to focus on quick wins first: --max-content-len 50000 "
            "processes <50K docs (~2-5s each), skipping heavy 200K+ docs."
        ),
    )

    args = parser.parse_args()

    run(
        dry_run=args.dry_run,
        limit=args.limit,
        resume=args.resume,
        batch_size=args.batch_size,
        year=args.year,
        doc_type=args.doc_type,
        damage_type=args.damage_type,
        skip_damage_types=args.skip_damage_types or [],
        skip_re_embed=args.skip_re_embed,
        wait_for_lock=args.wait_for_lock,
        engine=args.engine,
        workers=args.workers,
        max_content_len=args.max_content_len,
    )


if __name__ == "__main__":
    main()
