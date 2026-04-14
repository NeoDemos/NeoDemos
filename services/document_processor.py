"""
Document Processor Service
==========================

Periodic job that makes newly downloaded documents searchable by:
1. Chunking unchunked documents via SmartIngestor
2. Building text_search_enriched tsvector for BM25 full-text search
3. Logging every action to document_events

Runs as an APScheduler job (every 15 min, after refresh) or manually:
    python -m services.document_processor
    python -m services.document_processor --limit 50
    python -m services.document_processor --dry-run
"""

import gc
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


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
               triggered_by: str = "document_processor"):
    """Insert into document_events (non-fatal)."""
    try:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO document_events (document_id, event_type, details, triggered_by)
               VALUES (%s, %s, %s, %s)""",
            (document_id, event_type,
             json.dumps(details, ensure_ascii=False, default=str) if details else None,
             triggered_by),
        )
        conn.commit()
        cur.close()
    except Exception:
        conn.rollback()


# ---------------------------------------------------------------------------
# Find unchunked documents
# ---------------------------------------------------------------------------

def find_unchunked_documents(conn, limit: int = 200) -> list[dict]:
    """Documents with content but no chunks — not yet searchable."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT d.id, d.name, d.content, d.meeting_id, d.category,
               LENGTH(d.content) AS content_length
        FROM documents d
        WHERE NOT EXISTS (SELECT 1 FROM document_chunks dc WHERE dc.document_id = d.id)
          AND d.content IS NOT NULL
          AND LENGTH(d.content) > 50
        ORDER BY LENGTH(d.content) ASC
        LIMIT %s
    """, (limit,))
    rows = cur.fetchall()
    cur.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Find chunks missing BM25 tsvector
# ---------------------------------------------------------------------------

def find_chunks_missing_tsvector(conn, limit: int = 5000) -> int:
    """Build text_search_enriched for chunks that don't have it yet."""
    cur = conn.cursor()
    cur.execute("""
        UPDATE document_chunks SET text_search_enriched =
            to_tsvector('dutch', COALESCE(content, '')) ||
            to_tsvector('simple',
                COALESCE(section_topic, '') || ' ' ||
                COALESCE(array_to_string(key_entities, ' '), '') || ' ' ||
                COALESCE(title, '')
            )
        WHERE id IN (
            SELECT id FROM document_chunks
            WHERE text_search_enriched IS NULL
              AND content IS NOT NULL
              AND LENGTH(content) > 20
            LIMIT %s
        )
    """, (limit,))
    count = cur.rowcount
    conn.commit()
    cur.close()
    return count


# ---------------------------------------------------------------------------
# Document recovery via Docling (classifier-routed)
# ---------------------------------------------------------------------------

def _attempt_unified_recovery(conn, doc: dict, classification) -> str | None:
    """Re-OCR a garbled document using Docling. Returns clean text or None.

    Used for GARBLED_OCR and GARBLED_TABLE_RICH doc types.
    """
    doc_id = doc["id"]
    tmp_path = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT url FROM documents WHERE id = %s", (doc_id,))
        row = cur.fetchone()
        cur.close()
        if not row or not row[0]:
            return None

        url = row[0]
        logger.info("[doc_processor] Unified OCR recovery (mode=%s) for %s",
                    classification.docling_mode, doc_id[:40])

        import tempfile
        import httpx

        # Lazy imports from ocr_recovery (heavy module)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from scripts.ocr_recovery import (
            normalize_text, quality_gate, backup_original, ensure_backup_table,
        )
        from pipeline.docling_converters import get_ocr_converter

        resp = httpx.get(url, follow_redirects=True, timeout=60,
                         headers={"User-Agent": "NeoDemos/1.0"}, verify=False)
        if resp.status_code != 200:
            return None

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path = tmp.name

        # Pick the right converter based on classification
        if classification.docling_mode == "ocr":
            converter = get_ocr_converter()
        else:
            from pipeline.docling_converters import get_layout_converter
            converter = get_layout_converter()

        result = converter.convert(tmp_path)
        text = result.document.export_to_text()
        if not text:
            return None

        text = normalize_text(text)
        accepted, reason = quality_gate(doc.get("content", ""), text)
        if not accepted:
            logger.info("[doc_processor] Quality gate rejected recovery for %s: %s",
                        doc_id[:40], reason)
            return None

        # Backup original, then update
        ensure_backup_table(conn)
        from scripts.ocr_recovery import compute_clean_pct
        old_clean_pct = compute_clean_pct(doc.get("content", ""))
        backup_original(conn, doc_id, doc.get("content", ""), old_clean_pct)

        cur = conn.cursor()
        cur.execute(
            "UPDATE documents SET content = %s, ocr_quality = 'good' WHERE id = %s",
            (text, doc_id),
        )
        conn.commit()
        cur.close()
        logger.info("[doc_processor] OCR recovered %s: %d → %d chars",
                    doc_id[:40], len(doc.get("content", "")), len(text))
        return text

    except Exception as e:
        logger.warning("[doc_processor] Unified recovery failed for %s: %s", doc_id[:40], e)
        conn.rollback()
        return None
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        gc.collect()


def _attempt_layout_extraction(conn, doc: dict) -> str | None:
    """Re-extract a table-rich document using Docling layout mode.

    Used for TABLE_RICH doc types (not garbled, but may benefit from
    better structure extraction).
    """
    doc_id = doc["id"]
    tmp_path = None
    try:
        cur = conn.cursor()
        cur.execute("SELECT url FROM documents WHERE id = %s", (doc_id,))
        row = cur.fetchone()
        cur.close()
        if not row or not row[0]:
            return None

        url = row[0]
        logger.info("[doc_processor] Layout extraction for %s", doc_id[:40])

        import tempfile
        import httpx

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from scripts.ocr_recovery import (
            compute_clean_pct, backup_original, ensure_backup_table,
        )
        from pipeline.docling_converters import get_layout_converter

        resp = httpx.get(url, follow_redirects=True, timeout=60,
                         headers={"User-Agent": "NeoDemos/1.0"}, verify=False)
        if resp.status_code != 200:
            return None

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path = tmp.name

        converter = get_layout_converter()
        result = converter.convert(tmp_path)
        text = result.document.export_to_text()
        if not text:
            return None

        old_content = doc.get("content", "")
        old_clean_pct = compute_clean_pct(old_content)
        new_clean_pct = compute_clean_pct(text)

        # Quality gate: new text >= 110% length AND clean_pct doesn't decrease
        if len(text) < len(old_content) * 1.1:
            logger.info("[doc_processor] Layout extraction rejected for %s: "
                        "new text too short (%d vs %d)", doc_id[:40], len(text), len(old_content))
            return None
        if new_clean_pct < old_clean_pct:
            logger.info("[doc_processor] Layout extraction rejected for %s: "
                        "clean_pct decreased (%.1f%% → %.1f%%)",
                        doc_id[:40], old_clean_pct * 100, new_clean_pct * 100)
            return None

        # Backup original, then update
        ensure_backup_table(conn)
        backup_original(conn, doc_id, old_content, old_clean_pct)

        cur = conn.cursor()
        cur.execute(
            "UPDATE documents SET content = %s WHERE id = %s",
            (text, doc_id),
        )
        conn.commit()
        cur.close()
        logger.info("[doc_processor] Layout extracted %s: %d → %d chars",
                    doc_id[:40], len(old_content), len(text))
        return text

    except Exception as e:
        logger.warning("[doc_processor] Layout extraction failed for %s: %s", doc_id[:40], e)
        conn.rollback()
        return None
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        gc.collect()


# ---------------------------------------------------------------------------
# Core processor
# ---------------------------------------------------------------------------

def process_documents(limit: int = 200, triggered_by: str = "apscheduler",
                      dry_run: bool = False) -> dict:
    """Main entry: chunk unchunked docs + build BM25 tsvectors.

    Returns: {chunked, chunk_errors, tsvectors_built, details}
    """
    conn = psycopg2.connect(_build_db_url())
    summary = {"chunked": 0, "chunk_errors": 0, "tsvectors_built": 0, "details": []}

    try:
        # Ensure document_events table exists
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

        # --- Phase 0+1: Classify, recover/extract, and chunk ---
        from pipeline.document_classifier import DocumentClassifier, DocType, CIVIC_DOC_TYPES

        classifier = DocumentClassifier()
        summary["ocr_recovered"] = 0
        summary["layout_extracted"] = 0
        summary["financial_skipped"] = 0

        docs = find_unchunked_documents(conn, limit=limit)
        if docs:
            logger.info("[doc_processor] Found %d unchunked documents", len(docs))

            if dry_run:
                for d in docs:
                    cur = conn.cursor()
                    cur.execute("SELECT url FROM documents WHERE id = %s", (d["id"],))
                    url_row = cur.fetchone()
                    cur.close()
                    doc_url = url_row[0] if url_row else None
                    c = classifier.classify(d["id"], d["name"], d["content"], doc_url)
                    print(f"  [DRY] {d['id'][:45]:<45} | {d['content_length']:>7} chars "
                          f"| {c.doc_type.value:<20s} | {d['name'][:40]}")
                summary["details"] = [{"id": d["id"], "chars": d["content_length"]} for d in docs]
                return summary

            from pipeline.ingestion import SmartIngestor
            ingestor = SmartIngestor(db_url=_build_db_url(), chunk_only=True)

            for doc in docs:
                doc_id = doc["id"]
                doc_name = doc.get("name") or "Unnamed"
                content = doc["content"]
                meeting_id = doc.get("meeting_id")
                category = doc.get("category") or "municipal_doc"

                # Fetch URL for classification
                cur = conn.cursor()
                cur.execute("SELECT url FROM documents WHERE id = %s", (doc_id,))
                url_row = cur.fetchone()
                cur.close()
                doc_url = url_row[0] if url_row else None

                classification = classifier.classify(doc_id, doc_name, content, doc_url)
                _log_event(conn, doc_id, "classified",
                           {"type": classification.doc_type.value,
                            "reason": classification.reason},
                           triggered_by)

                # Store pipeline classification — but never overwrite a civic type
                # (e.g. schriftelijke_vraag) that was pre-set by WS11a/b.
                cur = conn.cursor()
                cur.execute("SELECT doc_classification FROM documents WHERE id = %s", (doc_id,))
                existing_row = cur.fetchone()
                existing_cls = existing_row[0] if existing_row else None
                if existing_cls not in CIVIC_DOC_TYPES:
                    cur.execute("UPDATE documents SET doc_classification = %s WHERE id = %s",
                                (classification.doc_type.value, doc_id))
                    conn.commit()
                cur.close()

                start = time.time()

                if classification.doc_type in (DocType.FINANCIAL, DocType.FINANCIAL_TABLE_RICH):
                    # Financial docs handled by WS2 dedicated pipeline — skip chunking
                    summary["financial_skipped"] += 1
                    _log_event(conn, doc_id, "financial_skipped",
                               {"reason": "routed to WS2 financial pipeline"}, triggered_by)
                    logger.info("[doc_processor] Skipped financial doc %s (WS2 pipeline)",
                                doc_id[:40])
                    continue

                if classification.doc_type in (DocType.GARBLED_OCR, DocType.GARBLED_TABLE_RICH):
                    recovered = _attempt_unified_recovery(conn, doc, classification)
                    if recovered:
                        content = recovered
                        doc["content"] = recovered
                        summary["ocr_recovered"] += 1
                        _log_event(conn, doc_id, "ocr_recovered",
                                   {"method": "docling",
                                    "mode": classification.docling_mode,
                                    "old_len": doc["content_length"],
                                    "new_len": len(recovered)},
                                   triggered_by)

                elif classification.doc_type == DocType.TABLE_RICH:
                    recovered = _attempt_layout_extraction(conn, doc)
                    if recovered:
                        content = recovered
                        doc["content"] = recovered
                        summary["layout_extracted"] += 1
                        _log_event(conn, doc_id, "layout_extracted",
                                   {"old_len": doc["content_length"],
                                    "new_len": len(recovered)},
                                   triggered_by)

                # Chunk the document (all types except financial)
                try:
                    ingestor.ingest_document(
                        doc_id=doc_id, doc_name=doc_name, content=content,
                        meeting_id=meeting_id, category=category,
                    )
                    elapsed = time.time() - start
                    detail = {
                        "document_id": doc_id,
                        "name": doc_name[:100],
                        "content_length": len(content),
                        "elapsed_s": round(elapsed, 1),
                        "classification": classification.doc_type.value,
                    }
                    summary["chunked"] += 1
                    summary["details"].append(detail)
                    _log_event(conn, doc_id, "document_chunked", detail, triggered_by)
                    logger.info("[doc_processor] Chunked %s (%d chars, %.1fs, %s)",
                                doc_id[:40], len(content), elapsed,
                                classification.doc_type.value)
                except Exception as e:
                    summary["chunk_errors"] += 1
                    _log_event(conn, doc_id, "chunk_failed",
                               {"error": str(e), "name": doc_name[:100]}, triggered_by)
                    logger.warning("[doc_processor] Failed to chunk %s: %s", doc_id[:40], e)
                    conn.rollback()
        else:
            logger.debug("[doc_processor] No unchunked documents found")

        # --- Phase 2: Embed chunks missing vectors → Qdrant ---
        # Qdrant is the source of truth for embeddings. The `embedded_at`
        # timestamp on document_chunks is a lightweight "this chunk is in
        # Qdrant" marker — it prevents Phase 2 from wastefully re-embedding
        # chunks that are already in Qdrant. See Alembic migration 0010.
        summary["embedded"] = 0
        try:
            from services.embedding import create_embedder, QDRANT_COLLECTION
            from qdrant_client import QdrantClient
            from qdrant_client.models import PointStruct

            embedder = create_embedder()  # auto: NEBIUS_API_KEY → API, else local
            qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
            qdrant_key = os.getenv("QDRANT_API_KEY", "")
            qdrant = QdrantClient(url=qdrant_url,
                                  api_key=qdrant_key if qdrant_key else None)

            embed_cur = conn.cursor(cursor_factory=RealDictCursor)
            embed_cur.execute("""
                SELECT dc.id, dc.document_id, dc.title, dc.content,
                       dc.chunk_index, dc.child_id, dc.chunk_type,
                       dc.section_topic, dc.key_entities,
                       d.name AS doc_name, d.meeting_id, d.category,
                       d.municipality, d.doc_classification
                FROM document_chunks dc
                JOIN documents d ON d.id = dc.document_id
                WHERE dc.embedded_at IS NULL
                  AND dc.content IS NOT NULL
                  AND LENGTH(dc.content) > 20
                ORDER BY dc.id DESC
                LIMIT 500
            """)
            unembedded = embed_cur.fetchall()
            embed_cur.close()

            if unembedded:
                logger.info("[doc_processor] Embedding %d chunks via API", len(unembedded))
                texts = [
                    f"[Document: {r['doc_name'] or ''} | Section: {r['title'] or ''}]\n{r['content']}"
                    for r in unembedded
                ]

                # Batch embed
                if hasattr(embedder, "embed_batch"):
                    vectors = embedder.embed_batch(texts, batch_size=64)
                else:
                    vectors = [embedder.embed(t) for t in texts]

                points = []
                chunk_ids_embedded = []
                for row, vec in zip(unembedded, vectors):
                    if vec is None:
                        continue
                    hash_str = hashlib.md5(
                        f"{row['document_id']}_{row['child_id']}_{row['chunk_index']}".encode()
                    ).hexdigest()
                    point_id = int(hash_str[:15], 16)
                    payload = {
                        "document_id": row["document_id"],
                        "doc_name": row["doc_name"] or "",
                        "doc_type": row.get("category") or "municipal_doc",
                        "meeting_id": row.get("meeting_id") or "",
                        "child_id": row["child_id"],
                        "chunk_index": row["chunk_index"],
                        "chunk_type": row["chunk_type"] or "quote",
                        "title": row["title"] or "",
                        "content": row["content"],
                        "municipality": row.get("municipality") or "rotterdam",
                    }
                    if row.get("doc_classification"):
                        payload["doc_classification"] = row["doc_classification"]
                    if row.get("section_topic"):
                        payload["section_topic"] = row["section_topic"]
                    if row.get("key_entities"):
                        payload["key_entities"] = row["key_entities"]
                    points.append(PointStruct(
                        id=point_id, vector=vec, payload=payload,
                    ))
                    chunk_ids_embedded.append(row["id"])

                # Upsert to Qdrant in batches
                batch_size = 100
                for i in range(0, len(points), batch_size):
                    batch = points[i : i + batch_size]
                    qdrant.upsert(collection_name=QDRANT_COLLECTION, points=batch)

                # Mark chunks as embedded (only AFTER successful Qdrant upsert)
                if chunk_ids_embedded:
                    up_cur = conn.cursor()
                    up_cur.execute(
                        "UPDATE document_chunks SET embedded_at = NOW() WHERE id = ANY(%s)",
                        (chunk_ids_embedded,),
                    )
                    conn.commit()
                    up_cur.close()

                summary["embedded"] = len(points)
                logger.info("[doc_processor] Embedded %d chunks → Qdrant", len(points))

        except Exception as e:
            logger.warning("[doc_processor] Embedding phase failed (non-fatal): %s", e)

        # --- Phase 3: Build BM25 tsvectors for any chunks missing them ---
        tsvector_count = find_chunks_missing_tsvector(conn, limit=10000)
        summary["tsvectors_built"] = tsvector_count
        if tsvector_count:
            logger.info("[doc_processor] Built text_search_enriched for %d chunks", tsvector_count)

        # --- Log pipeline run ---
        total = summary["chunked"] + summary["chunk_errors"]
        if total > 0 or tsvector_count > 0:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO pipeline_runs
                       (job_name, started_at, finished_at, status,
                        items_discovered, items_processed, items_failed, triggered_by)
                   VALUES (%s, NOW(), NOW(), %s, %s, %s, %s, %s)""",
                ("document_processor",
                 "success" if summary["chunk_errors"] == 0 else "failure",
                 total, summary["chunked"], summary["chunk_errors"],
                 "cron" if triggered_by == "apscheduler" else "manual"),
            )
            conn.commit()
            cur.close()

    finally:
        conn.close()

    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    limit = 200
    dry_run = False
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--dry-run":
            dry_run = True
        elif arg == "--limit" and i < len(sys.argv) - 1:
            limit = int(sys.argv[i + 1])

    result = process_documents(limit=limit, triggered_by="cli", dry_run=dry_run)
    print(f"\nProcessor complete: {result['chunked']} chunked, "
          f"{result['chunk_errors']} errors, "
          f"{result.get('embedded', 0)} embedded, "
          f"{result.get('ocr_recovered', 0)} OCR recovered, "
          f"{result.get('layout_extracted', 0)} layout extracted, "
          f"{result.get('financial_skipped', 0)} financial skipped, "
          f"{result['tsvectors_built']} tsvectors built")
    if result["details"]:
        for d in result["details"][:20]:
            print(f"  {d.get('document_id', d.get('id', '?'))[:45]} — "
                  f"{d.get('content_length', d.get('chars', '?'))} chars")
