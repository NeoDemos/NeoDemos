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

import hashlib
import json
import logging
import os
import sys
import time

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
# OCR recovery via Docling
# ---------------------------------------------------------------------------

def _attempt_ocr_recovery(conn, doc: dict) -> str | None:
    """Re-OCR a garbled document using Docling. Returns clean text or None."""
    doc_id = doc["id"]
    try:
        # Check if the source PDF URL is available
        cur = conn.cursor()
        cur.execute("SELECT url FROM documents WHERE id = %s", (doc_id,))
        row = cur.fetchone()
        cur.close()
        if not row or not row[0]:
            return None

        url = row[0]
        logger.info("[doc_processor] OCR recovery via Docling for %s", doc_id[:40])

        import tempfile
        import httpx
        from docling.document_converter import DocumentConverter

        # Download PDF
        resp = httpx.get(url, follow_redirects=True, timeout=60,
                         headers={"User-Agent": "NeoDemos/1.0"}, verify=False)
        if resp.status_code != 200:
            return None

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(resp.content)
            tmp_path = tmp.name

        try:
            converter = DocumentConverter()
            result = converter.convert(tmp_path)
            text = result.document.export_to_markdown()
            if text and len(text) > len(doc.get("content", "")) * 0.5:
                # Update the documents table with recovered text
                cur = conn.cursor()
                cur.execute(
                    "UPDATE documents SET content = %s WHERE id = %s",
                    (text, doc_id),
                )
                conn.commit()
                cur.close()
                logger.info("[doc_processor] OCR recovered %s: %d → %d chars",
                            doc_id[:40], len(doc.get("content", "")), len(text))
                return text
        finally:
            os.remove(tmp_path)

    except Exception as e:
        logger.warning("[doc_processor] OCR recovery failed for %s: %s", doc_id[:40], e)
        conn.rollback()
    return None


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

        # --- Phase 0: Detect garbled OCR in unchunked docs ---
        #     If garbled, attempt Docling re-OCR to get clean text before chunking.
        from services.scraper import _is_garbled_ocr
        summary["ocr_recovered"] = 0

        # --- Phase 1: Chunk unchunked documents ---
        docs = find_unchunked_documents(conn, limit=limit)
        if docs:
            logger.info("[doc_processor] Found %d unchunked documents", len(docs))

            if dry_run:
                for d in docs:
                    garbled = _is_garbled_ocr(d["content"][:5000])
                    flag = " [GARBLED]" if garbled else ""
                    print(f"  [DRY] {d['id'][:45]:<45} | {d['content_length']:>7} chars | {d['name'][:50]}{flag}")
                summary["details"] = [{"id": d["id"], "chars": d["content_length"]} for d in docs]
                return summary

            # Check each doc for garbled OCR and attempt recovery
            for doc in docs:
                if _is_garbled_ocr(doc["content"][:5000]):
                    recovered = _attempt_ocr_recovery(conn, doc)
                    if recovered:
                        doc["content"] = recovered
                        summary["ocr_recovered"] += 1
                        _log_event(conn, doc["id"], "ocr_recovered",
                                   {"method": "docling", "old_len": doc["content_length"],
                                    "new_len": len(recovered)},
                                   triggered_by)

            # Import SmartIngestor lazily (heavy deps)
            from pipeline.ingestion import SmartIngestor
            ingestor = SmartIngestor(db_url=_build_db_url(), chunk_only=True)

            for doc in docs:
                doc_id = doc["id"]
                doc_name = doc.get("name") or "Unnamed"
                content = doc["content"]
                meeting_id = doc.get("meeting_id")
                category = doc.get("category") or "municipal_doc"
                start = time.time()

                try:
                    ingestor.ingest_document(
                        doc_id=doc_id,
                        doc_name=doc_name,
                        content=content,
                        meeting_id=meeting_id,
                        category=category,
                    )
                    elapsed = time.time() - start
                    detail = {
                        "document_id": doc_id,
                        "name": doc_name[:100],
                        "content_length": len(content),
                        "elapsed_s": round(elapsed, 1),
                    }
                    summary["chunked"] += 1
                    summary["details"].append(detail)
                    _log_event(conn, doc_id, "document_chunked", detail, triggered_by)
                    logger.info(
                        "[doc_processor] Chunked %s (%d chars, %.1fs)",
                        doc_id[:40], len(content), elapsed,
                    )
                except Exception as e:
                    summary["chunk_errors"] += 1
                    _log_event(conn, doc_id, "chunk_failed",
                               {"error": str(e), "name": doc_name[:100]}, triggered_by)
                    logger.warning("[doc_processor] Failed to chunk %s: %s", doc_id[:40], e)
                    conn.rollback()
        else:
            logger.debug("[doc_processor] No unchunked documents found")

        # --- Phase 2: Embed chunks missing vectors → Qdrant ---
        summary["embedded"] = 0
        try:
            from services.embedding import create_embedder, EMBEDDING_DIM, QDRANT_COLLECTION
            from qdrant_client import QdrantClient
            from qdrant_client.models import PointStruct

            embedder = create_embedder()  # auto: NEBIUS_API_KEY → API, else local
            qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
            qdrant = QdrantClient(url=qdrant_url)

            # Find chunks that have no embedding yet (not in Qdrant)
            # We check for chunks created recently that likely need embedding
            embed_cur = conn.cursor(cursor_factory=RealDictCursor)
            embed_cur.execute("""
                SELECT dc.id, dc.document_id, dc.title, dc.content,
                       dc.chunk_index, dc.child_id, dc.chunk_type,
                       d.name AS doc_name, d.meeting_id
                FROM document_chunks dc
                JOIN documents d ON d.id = dc.document_id
                WHERE dc.embedding IS NULL
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
                pg_updates = []
                for row, vec in zip(unembedded, vectors):
                    if vec is None:
                        continue
                    hash_str = hashlib.md5(
                        f"{row['document_id']}_{row['child_id']}_{row['chunk_index']}".encode()
                    ).hexdigest()
                    point_id = int(hash_str[:15], 16)
                    points.append(PointStruct(
                        id=point_id,
                        vector=vec,
                        payload={
                            "document_id": row["document_id"],
                            "doc_name": row["doc_name"] or "",
                            "doc_type": "municipal_doc",
                            "meeting_id": row.get("meeting_id") or "",
                            "child_id": row["child_id"],
                            "chunk_index": row["chunk_index"],
                            "chunk_type": row["chunk_type"] or "quote",
                            "title": row["title"] or "",
                            "content": row["content"],
                        },
                    ))
                    pg_updates.append((vec, row["id"]))

                # Upsert to Qdrant in batches
                batch_size = 100
                for i in range(0, len(points), batch_size):
                    batch = points[i : i + batch_size]
                    qdrant.upsert(collection_name=QDRANT_COLLECTION, points=batch)

                # Store embedding in Postgres too
                if pg_updates:
                    up_cur = conn.cursor()
                    for vec, chunk_id in pg_updates:
                        up_cur.execute(
                            "UPDATE document_chunks SET embedding = %s WHERE id = %s",
                            (vec, chunk_id),
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
          f"{result['tsvectors_built']} tsvectors built")
    if result["details"]:
        for d in result["details"][:20]:
            print(f"  {d.get('document_id', d.get('id', '?'))[:45]} — "
                  f"{d.get('content_length', d.get('chars', '?'))} chars")
