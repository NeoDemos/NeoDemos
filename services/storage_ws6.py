"""
WS6 storage extensions — per-document chunk retrieval + summary cache I/O.

Separated from storage.py because multiple Claude Code agents edit that
file concurrently, and uncommitted additions get overwritten when another
agent writes back its stale version. This module is WS6-only — no other
workstream should need to touch it.

Usage:
    from services.storage_ws6 import WS6StorageMixin

    # Adds methods to an existing StorageService instance:
    storage = StorageService()
    ws6 = WS6StorageMixin(storage)
    chunks = ws6.get_all_chunks_for_document(doc_id)

Or use the standalone functions directly:
    from services.storage_ws6 import (
        get_all_chunks_for_document,
        get_document_summary_cache,
        update_document_summary_columns,
        list_documents_needing_summary,
    )
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from psycopg2.extras import RealDictCursor

from services.db_pool import get_connection


def get_chunks_bulk(doc_ids: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Fetch chunks for multiple documents in a single query.

    Returns a dict keyed by document_id. Missing docs get an empty list.
    Use this instead of calling get_all_chunks_for_document in a loop.
    """
    if not doc_ids:
        return {}
    result: Dict[str, List[Dict[str, Any]]] = {doc_id: [] for doc_id in doc_ids}
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, document_id, COALESCE(title, ''), content, chunk_index "
                    "FROM document_chunks WHERE document_id = ANY(%s) "
                    "ORDER BY document_id, chunk_index ASC",
                    (doc_ids,),
                )
                for r in cur.fetchall():
                    doc_id = r[1]
                    if doc_id in result:
                        result[doc_id].append(
                            {"chunk_id": r[0], "document_id": r[1],
                             "title": r[2], "content": r[3], "chunk_index": r[4]}
                        )
    except Exception as e:
        print(f"Error bulk-fetching chunks for {len(doc_ids)} docs: {e}")
    return result


def get_all_chunks_for_document(doc_id: str) -> List[Dict[str, Any]]:
    """Return every document_chunks row for doc_id in chunk_index order."""
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, document_id, COALESCE(title, ''), content, chunk_index "
                    "FROM document_chunks WHERE document_id = %s ORDER BY chunk_index ASC",
                    (doc_id,),
                )
                return [
                    {"chunk_id": r[0], "document_id": r[1], "title": r[2],
                     "content": r[3], "chunk_index": r[4]}
                    for r in cur.fetchall()
                ]
    except Exception as e:
        print(f"Error fetching chunks for document {doc_id}: {e}")
        return []


def get_document_summary_cache(doc_id: str) -> Optional[Dict[str, Any]]:
    """Read the cached WS6 summary columns for a single document."""
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, summary_short, summary_long, summary_verified, "
                    "summary_computed_at FROM documents WHERE id = %s",
                    (doc_id,),
                )
                row = cur.fetchone()
                if not row:
                    return None
                row = dict(row)
                computed = row.get("summary_computed_at")
                if computed and hasattr(computed, "isoformat"):
                    row["summary_computed_at"] = computed.isoformat()
                return row
    except Exception as e:
        print(f"Error reading summary cache for {doc_id}: {e}")
        return None


def update_document_summary_columns(
    doc_id: str,
    *,
    summary_short: Optional[str] = None,
    summary_long: Optional[str] = None,
    summary_verified: Optional[bool] = None,
) -> bool:
    """Upsert WS6 cached summary columns for a single document."""
    sets: List[str] = []
    params: list = []
    if summary_short is not None:
        sets.append("summary_short = %s")
        params.append(summary_short)
    if summary_long is not None:
        sets.append("summary_long = %s")
        params.append(summary_long)
    if summary_verified is not None:
        sets.append("summary_verified = %s")
        params.append(summary_verified)
    if not sets:
        return False
    sets.append("summary_computed_at = NOW()")
    params.append(doc_id)
    sql = f"UPDATE documents SET {', '.join(sets)} WHERE id = %s"
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, tuple(params))
                return cur.rowcount > 0
    except Exception as e:
        print(f"Error updating summary columns for {doc_id}: {e}")
        return False


def list_documents_needing_summary(
    *,
    limit: int = 100,
    min_content_chars: int = 500,
) -> List[Dict[str, Any]]:
    """Return documents that still need a short summary computed."""
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, name FROM documents "
                    "WHERE summary_short IS NULL "
                    "AND content IS NOT NULL "
                    "AND LENGTH(content) >= %s "
                    "AND (ocr_quality IS NULL OR ocr_quality != 'bad') "
                    "ORDER BY id LIMIT %s",
                    (min_content_chars, limit),
                )
                return [dict(row) for row in cur.fetchall()]
    except Exception as e:
        print(f"Error listing documents needing summary: {e}")
        return []


class WS6StorageMixin:
    """Convenience wrapper that delegates to standalone functions.

    Allows callers to use the familiar `storage.get_all_chunks_for_document()`
    pattern without depending on these methods being in StorageService.
    """

    def __init__(self, storage: Any = None):
        self._storage = storage

    get_chunks_bulk = staticmethod(get_chunks_bulk)
    get_all_chunks_for_document = staticmethod(get_all_chunks_for_document)
    get_document_summary_cache = staticmethod(get_document_summary_cache)
    update_document_summary_columns = staticmethod(update_document_summary_columns)
    list_documents_needing_summary = staticmethod(list_documents_needing_summary)
