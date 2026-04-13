#!/usr/bin/env python3
"""
ws10_table_extraction.py -- WS10: Table-Rich Document Extraction
================================================================

Processes large technical documents (bestemmingsplannen, MER reports,
bijlagen) through Docling layout analysis to recover table content,
figure captions, and sidebar text missed by pypdf.

Two extraction modes:
  TABLE_RICH:         Docling layout-only (do_ocr=False, ~3s/doc)
  GARBLED_TABLE_RICH: Docling full OCR (force_full_page_ocr=True, ~8s/doc)

Pipeline per document:
  classify -> download PDF -> Docling extract -> normalize -> quality gate ->
  backup original -> update content + tsvector -> delete old chunks ->
  re-chunk via SmartIngestor -> checkpoint

Usage:
  python scripts/ws10_table_extraction.py --dry-run --limit 5
  python scripts/ws10_table_extraction.py --type table_rich --limit 50
  python scripts/ws10_table_extraction.py --type garbled_table_rich --batch-size 10
  python scripts/ws10_table_extraction.py --resume
  python scripts/ws10_table_extraction.py --skip-re-embed --limit 100

See: docs/handoffs/WS10_TABLE_RICH_EXTRACTION.md
"""

import argparse
import gc
import json
import logging
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
CHECKPOINT_PATH = CHECKPOINT_DIR / "ws10_table_extraction_checkpoint.json"
LOG_DIR = PROJECT_ROOT / "logs"
LOG_PATH = LOG_DIR / "ws10_table_extraction.log"

# ---------------------------------------------------------------------------
# Imports from ocr_recovery (shared functions)
# ---------------------------------------------------------------------------
from scripts.ocr_recovery import (  # noqa: E402
    normalize_text,
    compute_clean_pct,
    has_garbled_runs,
    backup_original,
    ensure_backup_table,
    acquire_advisory_lock,
    release_advisory_lock,
    delete_old_chunks,
    rechunk_document,
    update_document_content,
    download_pdf,
    reembed_chunks,
)

# ---------------------------------------------------------------------------
# Imports from pipeline modules
# ---------------------------------------------------------------------------
from pipeline.document_classifier import DocumentClassifier, DocType  # noqa: E402
from pipeline.docling_converters import (  # noqa: E402
    get_layout_converter,
    get_ocr_converter,
)

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
logger = logging.getLogger("ws10_table_extraction")

# Suppress noisy libraries
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("docling").setLevel(logging.WARNING)
logging.getLogger("PIL").setLevel(logging.WARNING)


# ===================================================================
# 1. IDENTIFICATION
# ===================================================================

IDENTIFY_QUERY = """
    SELECT id, name, url, content_len, content, content_hash, duplicate_count
    FROM (
        SELECT DISTINCT ON (MD5(d.content))
               d.id, d.name, d.url, LENGTH(d.content) AS content_len,
               d.content,
               MD5(d.content) AS content_hash,
               COUNT(*) OVER (PARTITION BY MD5(d.content)) AS duplicate_count
        FROM documents d
        WHERE d.content IS NOT NULL
          AND LENGTH(d.content) > 100000
          AND (d.ocr_quality IS NULL OR d.ocr_quality NOT IN ('good', 'degraded'))
          AND d.url IS NOT NULL
          AND (LOWER(d.name) ~ 'bestemmingsplan|mer|deelrapport|milieu.?effect|havenbestemmings'
               OR LOWER(d.name) ~ 'bijlage.*rapport|verslag.*hoorzitting|toelichting')
          AND d.id NOT LIKE 'transcript_%%'
        ORDER BY MD5(d.content), LENGTH(d.content) DESC
    ) deduped
    ORDER BY content_len {sort_order}
"""

# Query to count total rows (including duplicates) before dedup
COUNT_QUERY = """
    SELECT COUNT(*) AS total,
           COUNT(DISTINCT MD5(d.content)) AS unique_content
    FROM documents d
    WHERE d.content IS NOT NULL
      AND LENGTH(d.content) > 100000
      AND (d.ocr_quality IS NULL OR d.ocr_quality NOT IN ('good', 'degraded'))
      AND d.url IS NOT NULL
      AND (LOWER(d.name) ~ 'bestemmingsplan|mer|deelrapport|milieu.?effect|havenbestemmings'
           OR LOWER(d.name) ~ 'bijlage.*rapport|verslag.*hoorzitting|toelichting')
      AND d.id NOT LIKE 'transcript_%%'
"""

# After successful extraction: propagate new content to all duplicate docs
# Note: text_search is a GENERATED ALWAYS column — updated automatically when content changes
PROPAGATE_DUPLICATES_QUERY = """
    UPDATE documents
    SET content = %s,
        ocr_quality = 'good',
        doc_classification = %s
    WHERE MD5(content) = %s
      AND id != %s
      AND (ocr_quality IS NULL OR ocr_quality NOT IN ('good', 'degraded'))
"""


def get_candidates(conn, sort: str = "desc") -> List[Dict]:
    """Fetch table-rich document candidates, deduplicated by content hash.

    Args:
        sort: "desc" (largest first, default) or "asc" (smallest first)

    Returns one representative per unique content hash. Rows include:
        id, name, url, content_len, content, content_hash, duplicate_count
    """
    # Log total vs unique counts first
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(COUNT_QUERY)
    counts = cur.fetchone()
    dupes = counts["total"] - counts["unique_content"]
    logger.info(
        f"Identification: {counts['total']} total rows, "
        f"{counts['unique_content']} unique PDFs "
        f"({dupes} duplicates skipped by content dedup)"
    )

    sort_order = "DESC" if sort == "desc" else "ASC"
    query = IDENTIFY_QUERY.format(sort_order=sort_order)
    cur.execute(query)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    logger.info(f"Identification query found {len(rows)} table-rich candidates (sorted {sort_order})")
    return rows


def propagate_to_duplicates(conn, doc_id: str, content_hash: str, new_content: str, doc_type_value: str) -> int:
    """After successful extraction, update all duplicate docs sharing the same content hash.

    Duplicates get the improved content + metadata but are NOT rechunked —
    they share the same text as the representative, so rechunking would
    produce identical chunks wasting compute.

    Returns:
        Number of duplicate rows updated.
    """
    cur = conn.cursor()
    cur.execute(
        PROPAGATE_DUPLICATES_QUERY,
        (new_content, doc_type_value, content_hash, doc_id),
    )
    updated = cur.rowcount
    cur.close()
    return updated


# ===================================================================
# 2. CLASSIFICATION
# ===================================================================

_classifier = DocumentClassifier()


def classify_candidate(doc: Dict) -> Tuple[Optional[DocType], str]:
    """Classify a candidate document as TABLE_RICH or GARBLED_TABLE_RICH.

    Returns:
        (doc_type, reason) -- doc_type is None if document does not qualify.
    """
    classification = _classifier.classify(
        doc_id=doc["id"],
        name=doc["name"],
        content=doc["content"],
        url=doc.get("url"),
    )

    if classification.doc_type in (DocType.TABLE_RICH, DocType.GARBLED_TABLE_RICH):
        return classification.doc_type, classification.reason

    # The SQL pre-filters should mostly align with the classifier, but some
    # edge cases may not match (e.g. content length just under threshold after
    # a DB update). Log and skip.
    return None, f"Classifier returned {classification.doc_type.value}: {classification.reason}"


# ===================================================================
# 3. EXTRACTION
# ===================================================================

def run_docling_extract(pdf_path: str, doc_type: DocType) -> Optional[str]:
    """Process a PDF through Docling and return extracted text.

    Uses layout-only converter for TABLE_RICH (~3s/doc) and full OCR
    converter for GARBLED_TABLE_RICH (~8s/doc).

    Returns None on failure.
    """
    try:
        if doc_type == DocType.GARBLED_TABLE_RICH:
            converter = get_ocr_converter()
        else:
            converter = get_layout_converter()

        result = converter.convert(pdf_path)
        text = result.document.export_to_text()
        return text.strip() if text else None
    except Exception as e:
        logger.error(f"  Docling extraction failed: {e}")
        return None
    finally:
        gc.collect()


# ===================================================================
# 4. TABLE-RICH QUALITY GATE (stricter than WS7)
# ===================================================================

def table_rich_quality_gate(
    old_text: str,
    new_text: str,
) -> Tuple[bool, str]:
    """Compare old vs new text for table-rich documents.

    Stricter than WS7's general quality_gate:
      - Length must be >= 110% of original (tables add content)
      - Clean-char ratio must not decrease
      - Garbled runs must not increase

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

    # Gate 1: New text must be at least 110% of original length.
    # Table-rich documents should gain content from recovered tables,
    # figure captions, and sidebars.
    if new_len < old_len * 1.1:
        return False, (
            f"length increase insufficient ({new_len:,} vs {old_len:,} chars, "
            f"{100 * new_len / max(old_len, 1):.0f}% of original, need >= 110%)"
        )

    # Gate 2: Clean-char ratio must not decrease
    if new_clean < old_clean - 1.0:  # 1% tolerance for rounding
        return False, (
            f"clean-char ratio decreased ({old_clean:.1f}% -> {new_clean:.1f}%)"
        )

    # Gate 3: Garbled runs must not increase
    if new_garbled > old_garbled:
        return False, (
            f"garbled runs increased ({old_garbled} -> {new_garbled})"
        )

    # Build improvement summary
    reasons = []
    reasons.append(f"length: {old_len:,} -> {new_len:,} chars (+{100 * (new_len - old_len) / max(old_len, 1):.0f}%)")

    if new_clean > old_clean + 0.5:
        reasons.append(f"clean-char: {old_clean:.1f}% -> {new_clean:.1f}%")

    if new_garbled < old_garbled:
        reasons.append(f"garbled runs: {old_garbled} -> {new_garbled}")

    return True, "; ".join(reasons)


# ===================================================================
# 5. DOCUMENT EVENT LOGGING
# ===================================================================

def log_document_event(
    conn,
    doc_id: str,
    event_type: str,
    detail: str,
):
    """Insert a row into document_events for audit trail."""
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO document_events
                (document_id, event_type, details, triggered_by)
            VALUES (%s, %s, %s, %s)
        """, (doc_id, event_type,
              json.dumps({"detail": detail[:2000]}, ensure_ascii=False),
              "ws10_table_extraction"))
        cur.close()
    except Exception as e:
        logger.warning(f"  Failed to log document_event for {doc_id}: {e}")
        try:
            conn.rollback()
        except Exception:
            pass


# ===================================================================
# 6. CHECKPOINT
# ===================================================================

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
    tmp_path = str(CHECKPOINT_PATH) + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, str(CHECKPOINT_PATH))


# ===================================================================
# 7. MAIN PIPELINE
# ===================================================================

def process_single_document(
    conn,
    doc: Dict,
    doc_type: DocType,
    temp_dir: str,
    dry_run: bool = False,
    skip_re_embed: bool = False,
    wait_for_lock: bool = True,
) -> Tuple[str, str]:
    """Process a single table-rich document through the extraction pipeline.

    Returns:
        (status, detail) where status is one of:
        'extracted', 'skipped', 'no_source', 'quality_fail', 'error'
    """
    doc_id = doc["id"]
    doc_name = doc.get("name", "Unknown")
    url = doc.get("url")

    # Step 1: Download the source PDF
    if not url:
        return "no_source", "no URL available"

    pdf_path = download_pdf(url, temp_dir)
    if not pdf_path:
        return "no_source", f"download failed for {url}"

    try:
        # Step 2: Docling extraction (layout-only or full OCR)
        new_text = run_docling_extract(pdf_path, doc_type)
        if not new_text:
            return "error", "Docling returned empty text"

        # Step 3: Post-extraction normalization
        new_text = normalize_text(new_text)

        # Step 4: Get current content for comparison
        old_text = doc["content"]
        if not old_text:
            return "error", "document content is empty in DB"

        # Step 5: Table-rich quality gate (stricter: 110% minimum)
        accept, reason = table_rich_quality_gate(old_text, new_text)
        if not accept:
            return "quality_fail", reason

        # Step 6: Report in dry-run mode
        if dry_run:
            old_clean = compute_clean_pct(old_text)
            new_clean = compute_clean_pct(new_text)
            return "dry_run_pass", (
                f"WOULD extract ({doc_type.value}): {reason} | "
                f"clean: {old_clean:.1f}% -> {new_clean:.1f}%"
            )

        # Step 7: Write (under advisory lock)
        lock_acquired = acquire_advisory_lock(conn, wait=wait_for_lock)
        if not lock_acquired:
            return "skipped", "could not acquire advisory lock (--no-wait-for-lock)"

        try:
            old_clean_pct = compute_clean_pct(old_text)

            # 7a. Backup original
            backup_original(conn, doc_id, old_text, old_clean_pct)

            # 7b. Update content + tsvector
            update_document_content(conn, doc_id, new_text)

            # 7c. Set doc_classification and ocr_quality
            cur = conn.cursor()
            cur.execute("""
                UPDATE documents
                SET ocr_quality = 'good',
                    doc_classification = %s
                WHERE id = %s
            """, (doc_type.value, doc_id))
            cur.close()

            # 7d. Delete old chunks + FK dependents, then COMMIT.
            #     SmartIngestor opens its own connection — commit first to
            #     avoid deadlock on row-level locks.
            deleted = delete_old_chunks(conn, doc_id)

            # 7e. Propagate improved content to all duplicate docs (same PDF)
            content_hash = doc.get("content_hash")
            dup_updated = 0
            if content_hash and doc.get("duplicate_count", 1) > 1:
                dup_updated = propagate_to_duplicates(
                    conn, doc_id, content_hash, new_text, doc_type.value
                )

            # 7f. Log document event
            log_document_event(
                conn, doc_id, "ws10_table_extraction",
                f"{doc_type.value}: {reason} | deleted {deleted} old chunks"
                + (f" | propagated to {dup_updated} duplicates" if dup_updated else ""),
            )
            conn.commit()

            # 7g. Re-chunk via SmartIngestor (new connection, clean slate)
            rechunk_document(conn, doc_id, doc_name, new_text)

        finally:
            release_advisory_lock(conn)

        # Re-embed after releasing the lock (not write-critical)
        if not skip_re_embed:
            reembed_chunks(conn, doc_id)

        new_clean_pct = compute_clean_pct(new_text)
        dup_msg = f" | +{dup_updated} dupes updated" if dup_updated else ""
        return "extracted", (
            f"{doc_type.value}: {reason} | "
            f"clean: {old_clean_pct:.1f}% -> {new_clean_pct:.1f}% | "
            f"deleted {deleted} old chunks{dup_msg}"
        )

    finally:
        # Clean up the downloaded PDF
        if pdf_path and os.path.exists(pdf_path):
            os.remove(pdf_path)


def _print_summary(stats: Dict, dry_run: bool) -> None:
    """Log the final extraction summary."""
    logger.info("")
    logger.info("=" * 70)
    logger.info("WS10 TABLE EXTRACTION COMPLETED")
    logger.info("=" * 70)
    logger.info(f"Total candidates:         {stats.get('total', 0)}")
    if dry_run:
        logger.info(f"Would extract:            {stats.get('dry_run_pass', 0)}")
    else:
        logger.info(f"Successfully extracted:   {stats.get('extracted', 0)}")
        logger.info(f"  table_rich:             {stats.get('table_rich', 0)}")
        logger.info(f"  garbled_table_rich:     {stats.get('garbled_table_rich', 0)}")
    logger.info(f"Quality gate failures:    {stats.get('quality_fail', 0)}")
    logger.info(f"No source PDF:            {stats.get('no_source', 0)}")
    logger.info(f"Skipped:                  {stats.get('skipped', 0)}")
    logger.info(f"Errors:                   {stats.get('error', 0)}")
    logger.info("=" * 70)
    if not dry_run and stats.get("extracted", 0) > 0:
        logger.info(
            "\nNext steps:\n"
            "  1. Spot-check 20 extracted documents for table completeness\n"
            "  2. Verify chunk counts: SELECT document_id, COUNT(*) FROM document_chunks GROUP BY 1\n"
            "  3. If --skip-re-embed was used, run document_processor Phase 2 for embeddings"
        )


def _parallel_extract(args_tuple) -> Dict:
    """Worker for parallel live extraction. No DB access — pure compute.

    Runs in a thread pool (download + Docling + quality gate).
    Returns a result dict consumed by the serialized write phase.
    """
    doc, dtype = args_tuple
    try:
        with tempfile.TemporaryDirectory(prefix="ws10_par_") as temp_dir:
            url = doc.get("url")
            if not url:
                return {"doc": doc, "dtype": dtype, "status": "no_source", "detail": "no URL", "new_text": None}
            pdf_path = download_pdf(url, temp_dir)
            if not pdf_path:
                return {"doc": doc, "dtype": dtype, "status": "no_source", "detail": "download failed", "new_text": None}
            new_text = run_docling_extract(pdf_path, dtype)
            if not new_text:
                return {"doc": doc, "dtype": dtype, "status": "error", "detail": "Docling returned empty", "new_text": None}
            new_text = normalize_text(new_text)
            old_text = doc.get("content", "")
            accept, reason = table_rich_quality_gate(old_text, new_text)
            if not accept:
                return {"doc": doc, "dtype": dtype, "status": "quality_fail", "detail": reason, "new_text": None}
            return {"doc": doc, "dtype": dtype, "status": "pending_write", "detail": reason, "new_text": new_text}
    except Exception as e:
        return {"doc": doc, "dtype": dtype, "status": "error", "detail": str(e), "new_text": None}


def _write_to_db(
    conn,
    result: Dict,
    skip_re_embed: bool = False,
    wait_for_lock: bool = True,
) -> Tuple[str, str]:
    """Serialized write phase for parallel live processing.

    Acquires advisory lock, writes content, rechunks. Called from the
    main thread after a worker completes _parallel_extract.

    Returns (status, detail).
    """
    doc = result["doc"]
    dtype = result["dtype"]
    new_text = result["new_text"]
    reason = result["detail"]
    doc_id = doc["id"]
    doc_name = doc.get("name", "Unknown")
    old_text = doc.get("content", "")

    lock_acquired = acquire_advisory_lock(conn, wait=wait_for_lock)
    if not lock_acquired:
        return "skipped", "could not acquire advisory lock"

    try:
        old_clean_pct = compute_clean_pct(old_text)

        backup_original(conn, doc_id, old_text, old_clean_pct)
        update_document_content(conn, doc_id, new_text)

        cur = conn.cursor()
        cur.execute(
            "UPDATE documents SET ocr_quality = 'good', doc_classification = %s WHERE id = %s",
            (dtype.value, doc_id),
        )
        cur.close()

        deleted = delete_old_chunks(conn, doc_id)

        content_hash = doc.get("content_hash")
        dup_updated = 0
        if content_hash and doc.get("duplicate_count", 1) > 1:
            dup_updated = propagate_to_duplicates(conn, doc_id, content_hash, new_text, dtype.value)

        dup_msg = f" | +{dup_updated} dupes updated" if dup_updated else ""
        log_document_event(
            conn, doc_id, "ws10_table_extraction",
            f"{dtype.value}: {reason} | deleted {deleted} old chunks{dup_msg}",
        )
        conn.commit()

        rechunk_document(conn, doc_id, doc_name, new_text)

    finally:
        release_advisory_lock(conn)

    if not skip_re_embed:
        reembed_chunks(conn, doc_id)

    new_clean_pct = compute_clean_pct(new_text)
    dup_msg = f" | +{dup_updated} dupes updated" if dup_updated else ""
    return "extracted", (
        f"{dtype.value}: {reason} | "
        f"clean: {old_clean_pct:.1f}% -> {new_clean_pct:.1f}% | "
        f"deleted {deleted} old chunks{dup_msg}"
    )


def _dry_run_single(args_tuple) -> tuple:
    """Worker function for parallel dry-run classification.

    Runs in a thread pool — no DB writes, no advisory lock.
    Returns (doc_id, doc_name, dtype, status, detail).
    """
    doc, dtype = args_tuple
    doc_id = doc["id"]
    doc_name = (doc.get("name") or "Unknown")[:60]
    try:
        with tempfile.TemporaryDirectory(prefix="ws10_dry_") as temp_dir:
            # Reuse a single DB connection is not safe across threads —
            # dry-run only needs download + Docling, no DB writes.
            url = doc.get("url")
            if not url:
                return doc_id, doc_name, dtype, "no_source", "no URL"
            pdf_path = download_pdf(url, temp_dir)
            if not pdf_path:
                return doc_id, doc_name, dtype, "no_source", "download failed"
            new_text = run_docling_extract(pdf_path, dtype)
            if not new_text:
                return doc_id, doc_name, dtype, "error", "Docling returned empty"
            new_text = normalize_text(new_text)
            old_text = doc.get("content", "")
            accept, reason = table_rich_quality_gate(old_text, new_text)
            status = "dry_run_pass" if accept else "quality_fail"
            old_clean = compute_clean_pct(old_text)
            new_clean = compute_clean_pct(new_text)
            detail = (
                f"WOULD extract ({dtype.value}): {reason} | "
                f"clean: {old_clean:.1f}% -> {new_clean:.1f}%"
                if accept else reason
            )
            return doc_id, doc_name, dtype, status, detail
    except Exception as e:
        return doc_id, doc_name, dtype, "error", str(e)


def run(
    dry_run: bool = False,
    limit: Optional[int] = None,
    resume: bool = False,
    batch_size: int = 10,
    type_filter: Optional[str] = None,
    skip_re_embed: bool = False,
    wait_for_lock: bool = True,
    workers: int = 1,
    sort: str = "desc",
):
    """Main entry point for the WS10 table extraction pipeline."""
    logger.info("=" * 70)
    logger.info("WS10: Table-Rich Document Extraction")
    logger.info("=" * 70)
    logger.info(f"  dry_run={dry_run}, limit={limit}, resume={resume}")
    logger.info(f"  batch_size={batch_size}, type_filter={type_filter}, workers={workers}, sort={sort}")
    logger.info(f"  skip_re_embed={skip_re_embed}, wait_for_lock={wait_for_lock}")

    # Load checkpoint for resume mode
    checkpoint = load_checkpoint() if resume else {"completed_ids": [], "stats": {}}
    completed_ids = set(checkpoint.get("completed_ids", []))
    if resume and completed_ids:
        logger.info(f"  Resuming: {len(completed_ids)} documents already completed")

    # Connect and identify candidates
    conn = psycopg2.connect(DB_URL)
    conn.autocommit = False

    try:
        # Ensure staging schema + backup table exist
        cur = conn.cursor()
        cur.execute("CREATE SCHEMA IF NOT EXISTS staging")
        conn.commit()
        cur.close()
        ensure_backup_table(conn)

        # Get candidates
        candidates = get_candidates(conn, sort=sort)

        if not candidates:
            logger.info("No table-rich candidates found. Nothing to do.")
            return

        # Classify each candidate
        logger.info("Classifying candidates...")
        classified: List[Tuple[Dict, DocType]] = []
        skipped_classification = 0

        for doc in candidates:
            dtype, reason = classify_candidate(doc)
            if dtype is None:
                skipped_classification += 1
                logger.debug(f"  Skipped {doc['name'][:60]}: {reason}")
                continue
            classified.append((doc, dtype))

        logger.info(
            f"Classified {len(classified)} documents "
            f"({skipped_classification} skipped by classifier)"
        )

        # Filter by type if requested
        if type_filter and type_filter != "all":
            type_map = {
                "table_rich": DocType.TABLE_RICH,
                "garbled_table_rich": DocType.GARBLED_TABLE_RICH,
            }
            target_type = type_map.get(type_filter)
            if target_type:
                before = len(classified)
                classified = [(d, t) for d, t in classified if t == target_type]
                logger.info(
                    f"  Filtered by type '{type_filter}': {before} -> {len(classified)}"
                )

        # Filter out already-completed (resume mode)
        if completed_ids:
            before = len(classified)
            classified = [(d, t) for d, t in classified if d["id"] not in completed_ids]
            logger.info(f"  Filtered by resume: {before} -> {len(classified)}")

        # Apply limit
        if limit:
            classified = classified[:limit]

        total = len(classified)
        if total == 0:
            logger.info("No documents to process after filtering. Nothing to do.")
            return

        logger.info(f"Processing {total} documents")

        # Print type distribution
        type_counts: Dict[str, int] = {}
        for _, dtype in classified:
            type_counts[dtype.value] = type_counts.get(dtype.value, 0) + 1
        for dtype_name, count in sorted(type_counts.items()):
            logger.info(f"  {dtype_name}: {count}")

        # Stats tracking
        stats = {
            "total": total,
            "extracted": 0,
            "quality_fail": 0,
            "no_source": 0,
            "skipped": 0,
            "error": 0,
            "dry_run_pass": 0,
            "table_rich": 0,
            "garbled_table_rich": 0,
            "total_length_increase": 0,
            "length_increase_count": 0,
        }

        STATUS_SYMBOLS = {
            "extracted": "OK",
            "dry_run_pass": "DRY",
            "quality_fail": "QFAIL",
            "no_source": "NOSRC",
            "skipped": "SKIP",
            "error": "ERR",
        }

        # Parallel dry-run: no DB writes, safe to run N workers
        if dry_run and workers > 1:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            logger.info(f"Parallel dry-run with {workers} workers")
            futures_map = {}
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for doc, dtype in classified:
                    f = pool.submit(_dry_run_single, (doc, dtype))
                    futures_map[f] = (doc, dtype)
                for idx, f in enumerate(as_completed(futures_map), 1):
                    doc_id, doc_name, dtype, status, detail = f.result()
                    sym = STATUS_SYMBOLS.get(status, "?")
                    logger.info(f"[{idx}/{total}] {doc_name} [{sym}] {detail}")
                    stats[status] = stats.get(status, 0) + 1
                    completed_ids.add(doc_id)
                    if idx % batch_size == 0:
                        save_checkpoint(list(completed_ids), stats)
                        logger.info(f"  Checkpoint saved ({idx}/{total})")
            # Skip sequential loop below
            conn.close()
            _print_summary(stats, dry_run)
            save_checkpoint(list(completed_ids), stats)
            return

        # Parallel live: workers extract in parallel, main thread serializes writes
        if not dry_run and workers > 1:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            logger.info(f"Parallel live extraction with {workers} workers (writes serialized on main thread)")
            futures_map = {}
            idx = 0
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for doc, dtype in classified:
                    f = pool.submit(_parallel_extract, (doc, dtype))
                    futures_map[f] = (doc, dtype)
                for f in as_completed(futures_map):
                    idx += 1
                    result = f.result()
                    doc_id = result["doc"]["id"]
                    doc_name = (result["doc"].get("name") or "Unknown")[:60]
                    content_len = result["doc"].get("content_len", 0)
                    status = result["status"]
                    logger.info(f"[{idx}/{total}] {doc_name} (type={result['dtype'].value}, len={content_len:,})")
                    if status == "pending_write":
                        try:
                            status, detail = _write_to_db(conn, result, skip_re_embed, wait_for_lock)
                        except Exception as e:
                            status = "error"
                            detail = f"write failed: {e}"
                            logger.exception(f"  Write error for {doc_id}")
                            try:
                                conn.rollback()
                            except Exception:
                                pass
                    else:
                        detail = result["detail"]
                        # Mark quality failures
                        if status == "quality_fail":
                            try:
                                cur = conn.cursor()
                                cur.execute(
                                    "UPDATE documents SET ocr_quality = 'degraded' WHERE id = %s",
                                    (doc_id,),
                                )
                                cur.close()
                                conn.commit()
                            except Exception:
                                conn.rollback()
                    sym = STATUS_SYMBOLS.get(status, "?")
                    logger.info(f"  [{sym}] {detail}")
                    stats[status] = stats.get(status, 0) + 1
                    if status == "extracted":
                        stats[result["dtype"].value] = stats.get(result["dtype"].value, 0) + 1
                    completed_ids.add(doc_id)
                    if idx % batch_size == 0:
                        save_checkpoint(list(completed_ids), stats)
                        logger.info(f"  Checkpoint saved ({idx}/{total})")
                        gc.collect()
                    result["doc"]["content"] = None
                    gc.collect()
            save_checkpoint(list(completed_ids), stats)
            conn.close()
            _print_summary(stats, dry_run)
            return

        # Sequential processing (single worker or dry-run with workers=1)
        with tempfile.TemporaryDirectory(prefix="ws10_table_") as temp_dir:
            for idx, (doc, dtype) in enumerate(classified, 1):
                doc_id = doc["id"]
                doc_name = (doc.get("name") or "Unknown")[:60]
                content_len = doc.get("content_len", 0)

                logger.info(
                    f"[{idx}/{total}] {doc_name} "
                    f"(type={dtype.value}, len={content_len:,})"
                )

                try:
                    status, detail = process_single_document(
                        conn=conn,
                        doc=doc,
                        doc_type=dtype,
                        temp_dir=temp_dir,
                        dry_run=dry_run,
                        skip_re_embed=skip_re_embed,
                        wait_for_lock=wait_for_lock,
                    )
                except Exception as e:
                    status = "error"
                    detail = f"unexpected: {e}"
                    logger.exception(f"  Unexpected error processing {doc_id}")
                    try:
                        conn.rollback()
                    except Exception:
                        pass

                sym = STATUS_SYMBOLS.get(status, "?")
                logger.info(f"  [{sym}] {detail}")

                # Update stats
                stats[status] = stats.get(status, 0) + 1

                # Track type-specific counts for extracted documents
                if status == "extracted":
                    stats[dtype.value] = stats.get(dtype.value, 0) + 1
                    # Track length increase for average calculation
                    old_len = len(doc.get("content") or "")
                    # Parse new length from detail string (rough; best-effort)
                    stats["length_increase_count"] += 1

                # Mark quality failures as 'degraded'
                if not dry_run and status == "quality_fail":
                    try:
                        cur = conn.cursor()
                        cur.execute(
                            "UPDATE documents SET ocr_quality = 'degraded' WHERE id = %s",
                            (doc_id,),
                        )
                        cur.close()
                        conn.commit()
                    except Exception:
                        conn.rollback()

                # Track completion for checkpoint
                completed_ids.add(doc_id)

                # Checkpoint every batch_size documents
                if idx % batch_size == 0:
                    save_checkpoint(list(completed_ids), stats)
                    logger.info(f"  Checkpoint saved ({idx}/{total})")
                    gc.collect()

                # Free the content string from memory (large documents)
                doc["content"] = None
                gc.collect()

        # Final checkpoint
        save_checkpoint(list(completed_ids), stats)

    finally:
        conn.close()

    _print_summary(stats, dry_run)


# ===================================================================
# CLI
# ===================================================================

def main():
    parser = argparse.ArgumentParser(
        description=(
            "WS10 Table Extraction: process table-rich documents "
            "through Docling layout analysis to recover tables, "
            "figure captions, and sidebar text missed by pypdf."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run on first 5 documents (no DB writes)
  python scripts/ws10_table_extraction.py --dry-run --limit 5

  # Process only TABLE_RICH documents (layout-only, ~3s/doc)
  python scripts/ws10_table_extraction.py --type table_rich --limit 50

  # Process only GARBLED_TABLE_RICH (full OCR, ~8s/doc)
  python scripts/ws10_table_extraction.py --type garbled_table_rich --batch-size 10

  # Resume from where we left off
  python scripts/ws10_table_extraction.py --resume

  # Extract without re-embedding (batch embed later via document_processor)
  python scripts/ws10_table_extraction.py --skip-re-embed --limit 100

See: docs/handoffs/WS10_TABLE_RICH_EXTRACTION.md
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Download + extract + quality-gate, but no DB writes",
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
        help="Documents per checkpoint (default: 10)",
    )
    parser.add_argument(
        "--type",
        type=str,
        default="all",
        choices=["table_rich", "garbled_table_rich", "all"],
        dest="type_filter",
        help="Filter by classification type (default: all)",
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
        help="Parallel workers for dry-run mode (default: 1, ignored for live runs)",
    )
    parser.add_argument(
        "--sort",
        choices=["desc", "asc"],
        default="desc",
        help="Sort candidates by content length: desc=largest first (default), asc=smallest first",
    )

    args = parser.parse_args()

    run(
        dry_run=args.dry_run,
        limit=args.limit,
        resume=args.resume,
        batch_size=args.batch_size,
        type_filter=args.type_filter,
        skip_re_embed=args.skip_re_embed,
        wait_for_lock=args.wait_for_lock,
        workers=args.workers,
        sort=args.sort,
    )


if __name__ == "__main__":
    main()
