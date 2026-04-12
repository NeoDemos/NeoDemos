#!/usr/bin/env python3
"""
Review & Promotion Tool for Financial Documents
================================================

CLI tool to review staged financial documents (jaarstukken, voorjaarsnota,
begroting, 10-maandsrapportage) and promote approved docs to the production
PostgreSQL schema and Qdrant collection.

Audit-first architecture: staging only has chunks in PostgreSQL (no vectors).
Embedding happens at promotion time using AIService (Nebius API).

Usage:
    python scripts/promote_financial_docs.py --list
    python scripts/promote_financial_docs.py --preview fin_jaarstukken_2024
    python scripts/promote_financial_docs.py --approve fin_jaarstukken_2024
    python scripts/promote_financial_docs.py --approve-batch --min-tables 5
    python scripts/promote_financial_docs.py --stats
"""

import os
import json
import argparse
import hashlib
import logging
from datetime import datetime

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from dotenv import load_dotenv

load_dotenv()

def _build_db_url():
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
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", None)
PRODUCTION_COLLECTION = "notulen_chunks"

logger = logging.getLogger(__name__)

# Module-level caches — instantiated once per process, reused across all docs
_qdrant_client = None
_embedder = None


def _get_qdrant():
    global _qdrant_client
    if _qdrant_client is None:
        from qdrant_client import QdrantClient
        _qdrant_client = QdrantClient(
            url=QDRANT_URL, api_key=QDRANT_API_KEY, timeout=120
        )
    return _qdrant_client


def _get_embedder():
    global _embedder
    if _embedder is None:
        from services.embedding import create_embedder
        _embedder = create_embedder()
    return _embedder


def _pg_row(row: dict) -> dict:
    """Serialize any dict/list values to JSON strings for psycopg2."""
    return {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in row.items()}


def get_staging_connection():
    """Get a connection with search_path set to staging."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    cur.execute("SET search_path TO staging, public")
    cur.close()
    return conn


def get_production_connection():
    """Get a connection with default search_path (public)."""
    return psycopg2.connect(DB_URL)


# -- List ------------------------------------------------------------------

def cmd_list(args):
    """List all staged financial documents with quality info."""
    conn = get_staging_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    status_filter = ""
    params = []
    if args.status:
        status_filter = "WHERE fd.review_status = %s"
        params.append(args.status)

    cur.execute(f"""
        SELECT fd.id, fd.doc_type, fd.fiscal_year, fd.source,
               fd.page_count, fd.docling_tables_found, fd.docling_chunks_created,
               fd.review_status, fd.quality_score, fd.promoted_at, fd.created_at,
               (SELECT COUNT(*) FROM document_chunks dc
                WHERE dc.document_id = fd.id) AS chunk_count,
               (SELECT COUNT(*) FROM document_chunks dc
                WHERE dc.document_id = fd.id AND dc.chunk_type = 'table') AS table_count,
               (SELECT COUNT(*) FROM document_chunks dc
                WHERE dc.document_id = fd.id AND dc.chunk_type = 'text') AS text_count
        FROM financial_documents fd
        {status_filter}
        ORDER BY fd.fiscal_year DESC, fd.doc_type
    """, params)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    if not rows:
        print("No financial documents in staging.")
        return

    print(f"\n{'Document ID':<35} {'Type':<18} {'Year':<6} {'Tables':<8} {'Text':<8} {'Score':<7} {'Status':<15}")
    print("-" * 105)
    for r in rows:
        doc_id = (r["id"] or "")[:33]
        doc_type = (r["doc_type"] or "?")[:16]
        year = str(r["fiscal_year"]) if r["fiscal_year"] else "?"
        tables = r["table_count"] or 0
        text = r["text_count"] or 0
        score = f"{r['quality_score']:.3f}" if r["quality_score"] is not None else "N/A"
        status = r["review_status"] or "?"

        if status == "approved":
            status_display = f"\033[92m{status}\033[0m"
        elif status == "pending":
            status_display = f"\033[93m{status}\033[0m"
        elif status == "rejected":
            status_display = f"\033[91m{status}\033[0m"
        else:
            status_display = status

        print(f"{doc_id:<35} {doc_type:<18} {year:<6} {tables:<8} {text:<8} {score:<7} {status_display:<24}")

    print(f"\nTotal: {len(rows)} financial documents in staging")

    by_status = {}
    for r in rows:
        s = r["review_status"] or "unknown"
        by_status[s] = by_status.get(s, 0) + 1
    print(f"By status: {', '.join(f'{k}: {v}' for k, v in sorted(by_status.items()))}")


# -- Preview ---------------------------------------------------------------

def cmd_preview(args):
    """Preview a staged financial document's chunks."""
    conn = get_staging_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("SELECT * FROM financial_documents WHERE id = %s", (args.preview,))
    doc = cur.fetchone()
    if not doc:
        print(f"Financial document {args.preview} not found in staging.")
        cur.close()
        conn.close()
        return

    print(f"\n{'='*60}")
    print(f"Document:    {doc['id']}")
    print(f"Type:        {doc['doc_type']}")
    print(f"Fiscal Year: {doc['fiscal_year']}")
    print(f"Source:      {doc['source']}")
    print(f"Pages:       {doc['page_count']}")
    print(f"Tables:      {doc['docling_tables_found']}")
    print(f"Chunks:      {doc['docling_chunks_created']}")
    print(f"Score:       {doc['quality_score']}")
    print(f"Status:      {doc['review_status']}")
    print(f"{'='*60}")

    # Show chunk breakdown
    cur.execute("""
        SELECT chunk_type, COUNT(*) AS cnt,
               AVG(tokens_estimated) AS avg_tokens
        FROM document_chunks
        WHERE document_id = %s
        GROUP BY chunk_type
        ORDER BY chunk_type
    """, (args.preview,))
    breakdown = cur.fetchall()
    if breakdown:
        print(f"\nChunk breakdown:")
        for b in breakdown:
            avg_tok = f"{b['avg_tokens']:.0f}" if b["avg_tokens"] else "?"
            print(f"  {b['chunk_type']}: {b['cnt']} chunks (avg {avg_tok} tokens)")

    # Show first few chunks
    cur.execute("""
        SELECT title, content, chunk_type, chunk_index
        FROM document_chunks
        WHERE document_id = %s
        ORDER BY chunk_index
        LIMIT 5
    """, (args.preview,))
    chunks = cur.fetchall()

    if chunks:
        print(f"\nFirst {len(chunks)} chunks:")
        for i, c in enumerate(chunks, 1):
            ctype = c["chunk_type"] or "?"
            print(f"\n  [{c['chunk_index']}] {c['title'] or 'Untitled'} ({ctype})")
            text = (c["content"] or "")[:300]
            print(f"      {text}{'...' if len(c['content'] or '') > 300 else ''}")

    cur.close()
    conn.close()


# -- Approve / Promote -----------------------------------------------------

def promote_financial_doc(doc_id: str) -> bool:
    """Promote a single financial document from staging to production."""
    staging_conn = get_staging_connection()
    prod_conn = get_production_connection()

    try:
        s_cur = staging_conn.cursor(cursor_factory=RealDictCursor)
        p_cur = prod_conn.cursor(cursor_factory=RealDictCursor)

        # 1. Check document exists in staging
        s_cur.execute("SELECT * FROM financial_documents WHERE id = %s", (doc_id,))
        fin_doc = s_cur.fetchone()
        if not fin_doc:
            print(f"Financial document {doc_id} not found in staging.")
            return False

        fiscal_year = fin_doc["fiscal_year"]
        doc_type = fin_doc["doc_type"]
        doc_name = f"{doc_type} {fiscal_year}"

        # Count chunks by type for progress reporting
        s_cur.execute("""
            SELECT chunk_type, COUNT(*) AS cnt
            FROM document_chunks
            WHERE document_id = %s
            GROUP BY chunk_type
        """, (doc_id,))
        type_counts = {r["chunk_type"]: r["cnt"] for r in s_cur.fetchall()}
        table_count = type_counts.get("table", 0)
        text_count = type_counts.get("text", 0)
        print(f"  Promoting {doc_id}: {table_count} tables, {text_count} text chunks...")

        # 2a. Capture OLD point_ids BEFORE deleting prod chunks so we can clean up
        #     stale Qdrant vectors that the new version won't overwrite.
        p_cur.execute(
            """SELECT chunk_index, child_id FROM document_chunks WHERE document_id = %s""",
            (doc_id,),
        )
        old_chunk_keys = [(r["chunk_index"], r["child_id"]) for r in p_cur.fetchall()]
        old_point_ids = set()
        for chunk_index, child_id in old_chunk_keys:
            hash_str = hashlib.md5(
                f"{doc_id}_{child_id}_{chunk_index}".encode()
            ).hexdigest()
            old_point_ids.add(int(hash_str[:15], 16))

        # 2b. Delete OLD production data for the same doc_id (full replacement).
        #    First clean up FK references (kg_mentions has ON DELETE NO ACTION)
        p_cur.execute(
            """DELETE FROM kg_mentions WHERE chunk_id IN
               (SELECT id FROM document_chunks WHERE document_id = %s)""",
            (doc_id,),
        )
        old_mentions = p_cur.rowcount
        #    Delete document_children (CASCADE cleans most chunks)
        p_cur.execute("DELETE FROM document_children WHERE document_id = %s", (doc_id,))
        old_children = p_cur.rowcount
        #    Delete any remaining orphan chunks
        p_cur.execute("DELETE FROM document_chunks WHERE document_id = %s", (doc_id,))
        old_chunks = p_cur.rowcount
        if old_children > 0 or old_chunks > 0 or old_mentions > 0:
            print(f"  - Removed old production data: {old_children} children, {old_chunks} chunks, {old_mentions} kg_mentions")

        # Note: Qdrant cleanup happens AFTER step 6 (upsert), so we know which
        # point_ids the new version uses and can delete only the stale ones.

        # 3. Copy document record to production
        s_cur.execute("SELECT * FROM documents WHERE id = %s", (doc_id,))
        doc_row = s_cur.fetchone()
        if doc_row:
            p_cur.execute("""
                INSERT INTO documents (id, name, meeting_id, content, category)
                VALUES (%(id)s, %(name)s, %(meeting_id)s, %(content)s, %(category)s)
                ON CONFLICT (id) DO UPDATE SET
                    content = EXCLUDED.content,
                    name = EXCLUDED.name,
                    category = EXCLUDED.category
            """, _pg_row(dict(doc_row)))
            print(f"  + Document record upserted")

        # 4. Copy document_children with ID remapping (batch insert via execute_values)
        s_cur.execute("""
            SELECT * FROM document_children WHERE document_id = %s
        """, (doc_id,))
        children = s_cur.fetchall()

        child_id_map = {}
        if children:
            # Use execute_values for one round-trip; capture returned ids
            child_rows = [
                (
                    c["document_id"],
                    c["content"],
                    c["chunk_index"],
                    json.dumps(c["metadata"]) if isinstance(c["metadata"], (dict, list)) else c["metadata"],
                )
                for c in children
            ]
            new_ids = execute_values(
                p_cur,
                """INSERT INTO document_children (document_id, content, chunk_index, metadata)
                   VALUES %s RETURNING id, chunk_index""",
                child_rows,
                template="(%s, %s, %s, %s)",
                fetch=True,
            )
            # execute_values returns rows in INSERT order. p_cur is a RealDictCursor so
            # rows are dicts with "id" and "chunk_index" keys.
            child_idx_to_new = {row["chunk_index"]: row["id"] for row in new_ids}
            for c in children:
                child_id_map[c["id"]] = child_idx_to_new.get(c["chunk_index"])
            print(f"  + {len(children)} document_children copied (batch)")

        # 5. Copy document_chunks with remapped child_ids (batch insert via execute_values)
        #    Preserve table_json and chunk_type fields
        s_cur.execute("""
            SELECT * FROM document_chunks WHERE document_id = %s
        """, (doc_id,))
        chunks = s_cur.fetchall()
        if chunks:
            chunk_rows = []
            for chunk in chunks:
                old_child_id = chunk.get("child_id")
                new_child_id = child_id_map.get(old_child_id, old_child_id)
                tj = chunk.get("table_json")
                if isinstance(tj, (dict, list)):
                    tj = json.dumps(tj)
                chunk_rows.append((
                    chunk["document_id"],
                    chunk["chunk_index"],
                    chunk["title"],
                    chunk["content"],
                    chunk["chunk_type"],
                    tj,
                    chunk["tokens_estimated"],
                    new_child_id,
                ))
            execute_values(
                p_cur,
                """INSERT INTO document_chunks
                       (document_id, chunk_index, title, content, chunk_type,
                        table_json, tokens_estimated, child_id)
                   VALUES %s
                   ON CONFLICT (document_id, chunk_index) DO UPDATE SET
                       content = EXCLUDED.content,
                       title = EXCLUDED.title,
                       chunk_type = EXCLUDED.chunk_type,
                       table_json = EXCLUDED.table_json,
                       child_id = EXCLUDED.child_id""",
                chunk_rows,
                template="(%s, %s, %s, %s, %s, %s, %s, %s)",
            )
            print(f"  + {len(chunks)} document_chunks copied (batch)")

        # 6. Generate embeddings and upsert to production Qdrant
        try:
            from qdrant_client.models import PointStruct

            qdrant = _get_qdrant()  # cached at module level
            embedder = _get_embedder()  # cached at module level

            # Reuse the chunk rows we just inserted to avoid an extra SELECT round-trip
            embed_inputs = []  # (text_to_embed, point_id, payload)
            for chunk in chunks:
                text = (chunk["content"] or "").strip()
                if len(text) < 20:
                    continue
                title = chunk["title"] or "Untitled"
                chunk_type = chunk["chunk_type"] or "text"
                old_child_id = chunk.get("child_id")
                new_child_id = child_id_map.get(old_child_id, old_child_id)

                if chunk_type == "table":
                    context_str = f"[Financieel document: {doc_name} | Tabel: {title}]\n"
                else:
                    context_str = f"[Financieel document: {doc_name} | Sectie: {title}]\n"

                hash_str = hashlib.md5(
                    f"{chunk['document_id']}_{new_child_id}_{chunk['chunk_index']}".encode()
                ).hexdigest()
                point_id = int(hash_str[:15], 16)

                payload = {
                    "document_id": chunk["document_id"],
                    "doc_name": doc_name,
                    "doc_type": "financial",
                    "chunk_index": chunk["chunk_index"],
                    "chunk_type": chunk_type,
                    "title": title,
                    "content": text,
                    "start_date": f"{fiscal_year}-01-01T00:00:00",
                }
                embed_inputs.append((context_str + text, point_id, payload))

            # Batch embed via Nebius API
            texts_to_embed = [t[0] for t in embed_inputs]
            if texts_to_embed:
                all_embeddings = embedder.embed_batch(texts_to_embed, batch_size=64)
                points = []
                for i, (_, point_id, payload) in enumerate(embed_inputs):
                    emb = all_embeddings[i]
                    if emb is not None:
                        points.append(PointStruct(id=point_id, vector=emb, payload=payload))

                # Single Qdrant upsert (one round-trip for the whole doc).
                # Same point_ids overwrite existing vectors automatically because
                # md5(doc_id|child_id|chunk_index) is deterministic.
                if points:
                    qdrant.upsert(collection_name=PRODUCTION_COLLECTION, points=points)

                # SAFETY: delete any STALE old point_ids that the new version
                # didn't overwrite (e.g. when new version has fewer chunks).
                new_point_ids = {p.id for p in points}
                stale_point_ids = list(old_point_ids - new_point_ids)
                if stale_point_ids:
                    from qdrant_client.models import PointIdsList
                    qdrant.delete(
                        collection_name=PRODUCTION_COLLECTION,
                        points_selector=PointIdsList(points=stale_point_ids),
                    )
                    print(f"  - Removed {len(stale_point_ids)} stale Qdrant point(s)")

            print(f"  + {len(points)} chunks embedded and upserted to {PRODUCTION_COLLECTION}")
        except ImportError as ie:
            print(f"  ! Missing dependency: {ie}")
        except Exception as e:
            import traceback
            print(f"  ! Embedding/Qdrant promotion failed: {type(e).__name__}: {e}")
            traceback.print_exc()

        # 7. Build text_search_enriched tsvector for BM25 keyword search
        p_cur.execute("""
            UPDATE document_chunks SET text_search_enriched =
                to_tsvector('dutch', COALESCE(content, '')) ||
                to_tsvector('simple',
                    COALESCE(section_topic, '') || ' ' ||
                    COALESCE(array_to_string(key_entities, ' '), '') || ' ' ||
                    COALESCE(title, '')
                )
            WHERE document_id = %s AND text_search_enriched IS NULL
        """, (doc_id,))
        print(f"  + text_search_enriched tsvector built for BM25")

        # 8. Mark as promoted in staging
        s_cur.execute("""
            UPDATE financial_documents SET review_status = 'approved', promoted_at = NOW()
            WHERE id = %s
        """, (doc_id,))

        prod_conn.commit()
        staging_conn.commit()
        s_cur.close()
        p_cur.close()

        print(f"  Promoted: {doc_id}")

        # 9. Extract financial_lines from promoted table_json chunks (WS2)
        #    This populates the structured financial_lines table and assigns
        #    iv3_taakveld codes from programma_aliases.
        try:
            from pipeline.financial_lines_extractor import FinancialLinesExtractor
            fle_conn = psycopg2.connect(_build_db_url())
            extractor = FinancialLinesExtractor(fle_conn)
            result = extractor.extract_from_document(doc_id)
            print(f"  + financial_lines: {result.lines_extracted} rows"
                  f" ({len(result.failures)} failures)")
            fle_conn.close()
        except Exception as fle_err:
            # Non-fatal: promotion succeeded, extraction can be retried manually
            print(f"  WARN financial_lines extraction failed: {fle_err}")

        return True

    except Exception as e:
        prod_conn.rollback()
        staging_conn.rollback()
        import traceback
        print(f"  ERROR promoting {doc_id}: {type(e).__name__}: {e}")
        traceback.print_exc()
        return False
    finally:
        staging_conn.close()
        prod_conn.close()


def cmd_approve(args):
    """Approve and promote a single financial document."""
    print(f"\nPromoting financial document: {args.approve}")
    promote_financial_doc(args.approve)


def cmd_approve_batch(args):
    """Approve and promote all financial documents above the minimum table count."""
    min_tables = args.min_tables or 5
    conn = get_staging_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT fd.id, fd.doc_type, fd.fiscal_year, fd.quality_score,
               fd.docling_tables_found
        FROM financial_documents fd
        WHERE fd.review_status IN ('pending', 'auto_approved')
          AND fd.promoted_at IS NULL
          AND COALESCE(fd.docling_tables_found, 0) >= %s
        ORDER BY fd.fiscal_year DESC, fd.doc_type
    """, (min_tables,))

    docs = cur.fetchall()
    cur.close()
    conn.close()

    if not docs:
        print(f"No financial documents to promote (min_tables={min_tables})")
        return

    print(f"\nPromoting {len(docs)} financial documents (min_tables >= {min_tables}):")
    for d in docs:
        score = f"{d['quality_score']:.3f}" if d["quality_score"] is not None else "N/A"
        print(f"  {d['id']:<35} | {d['doc_type']} {d['fiscal_year']} | "
              f"tables={d['docling_tables_found']} | score={score}")

    promoted = 0
    for d in docs:
        if promote_financial_doc(d["id"]):
            promoted += 1

    print(f"\nPromoted: {promoted}/{len(docs)}")


def cmd_reject(args):
    """Reject a financial document."""
    conn = get_staging_connection()
    cur = conn.cursor()

    reason = args.reason or "Rejected by reviewer"
    cur.execute("""
        UPDATE financial_documents SET review_status = 'rejected'
        WHERE id = %s
    """, (args.reject,))

    if cur.rowcount == 0:
        print(f"Financial document {args.reject} not found in staging.")
    else:
        print(f"Rejected: {args.reject} (reason: {reason})")

    conn.commit()
    cur.close()
    conn.close()


# -- Stats -----------------------------------------------------------------

def cmd_stats(args):
    """Show financial document pipeline statistics."""
    conn = get_staging_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE review_status = 'approved') AS promoted,
            COUNT(*) FILTER (WHERE review_status = 'pending') AS pending,
            COUNT(*) FILTER (WHERE review_status = 'auto_approved') AS auto_approved,
            COUNT(*) FILTER (WHERE review_status = 'rejected') AS rejected,
            AVG(quality_score) FILTER (WHERE quality_score IS NOT NULL) AS avg_score,
            MIN(quality_score) FILTER (WHERE quality_score IS NOT NULL) AS min_score,
            MAX(quality_score) FILTER (WHERE quality_score IS NOT NULL) AS max_score,
            SUM(docling_tables_found) AS total_tables,
            SUM(docling_chunks_created) AS total_chunks,
            SUM(page_count) AS total_pages
        FROM financial_documents
    """)
    stats = cur.fetchone()

    print(f"\nFinancial Document Pipeline Statistics")
    print(f"{'='*45}")
    print(f"Total documents:  {stats['total']}")
    print(f"  Promoted:       {stats['promoted']}")
    print(f"  Pending review: {stats['pending']}")
    print(f"  Auto-approved:  {stats['auto_approved']}")
    print(f"  Rejected:       {stats['rejected']}")
    print(f"\nContent:")
    print(f"  Total pages:    {stats['total_pages'] or 0}")
    print(f"  Total tables:   {stats['total_tables'] or 0}")
    print(f"  Total chunks:   {stats['total_chunks'] or 0}")
    print(f"\nQuality Scores:")
    print(f"  Average: {stats['avg_score']:.3f}" if stats["avg_score"] else "  Average: N/A")
    print(f"  Min:     {stats['min_score']:.3f}" if stats["min_score"] else "  Min:     N/A")
    print(f"  Max:     {stats['max_score']:.3f}" if stats["max_score"] else "  Max:     N/A")

    # Per doc_type breakdown
    cur.execute("""
        SELECT doc_type, fiscal_year, review_status,
               docling_tables_found, docling_chunks_created, page_count
        FROM financial_documents
        ORDER BY fiscal_year DESC, doc_type
    """)
    docs = cur.fetchall()
    if docs:
        print(f"\nDocuments:")
        print(f"  {'Type':<20} {'Year':<6} {'Pages':<7} {'Tables':<8} {'Chunks':<8} {'Status':<12}")
        print(f"  {'-'*65}")
        for d in docs:
            print(f"  {d['doc_type'] or '?':<20} {d['fiscal_year'] or '?':<6} "
                  f"{d['page_count'] or 0:<7} {d['docling_tables_found'] or 0:<8} "
                  f"{d['docling_chunks_created'] or 0:<8} {d['review_status'] or '?':<12}")

    cur.close()
    conn.close()


# -- Main ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Review & promote staged financial documents to production"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="List staged financial documents")
    group.add_argument("--preview", type=str, help="Preview a document's chunks")
    group.add_argument("--approve", type=str, help="Promote a document to production")
    group.add_argument("--approve-batch", action="store_true", help="Promote all qualifying documents")
    group.add_argument("--reject", type=str, help="Reject a document")
    group.add_argument("--stats", action="store_true", help="Show pipeline statistics")

    parser.add_argument("--status", type=str, help="Filter --list by status (pending/approved/rejected)")
    parser.add_argument("--min-tables", type=int, default=5, help="Min tables for --approve-batch (default: 5)")
    parser.add_argument("--reason", type=str, help="Rejection reason for --reject")

    args = parser.parse_args()

    if args.list:
        cmd_list(args)
    elif args.preview:
        cmd_preview(args)
    elif args.approve:
        cmd_approve(args)
    elif args.approve_batch:
        cmd_approve_batch(args)
    elif args.reject:
        cmd_reject(args)
    elif args.stats:
        cmd_stats(args)


if __name__ == "__main__":
    main()
