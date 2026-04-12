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

        # --- Phase 1: Chunk unchunked documents ---
        docs = find_unchunked_documents(conn, limit=limit)
        if docs:
            logger.info("[doc_processor] Found %d unchunked documents", len(docs))

            if dry_run:
                for d in docs:
                    print(f"  [DRY] {d['id'][:45]:<45} | {d['content_length']:>7} chars | {d['name'][:50]}")
                summary["details"] = [{"id": d["id"], "chars": d["content_length"]} for d in docs]
                return summary

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

        # --- Phase 2: Build BM25 tsvectors for any chunks missing them ---
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
                 "ok" if summary["chunk_errors"] == 0 else "partial",
                 total, summary["chunked"], summary["chunk_errors"],
                 triggered_by),
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
          f"{result['tsvectors_built']} tsvectors built")
    if result["details"]:
        for d in result["details"][:20]:
            print(f"  {d.get('document_id', d.get('id', '?'))[:45]} — "
                  f"{d.get('content_length', d.get('chars', '?'))} chars")
