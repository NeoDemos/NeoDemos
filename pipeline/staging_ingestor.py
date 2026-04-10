"""
StagingIngestor — Isolated ingestion into the staging schema
=============================================================

Thin subclass of SmartIngestor that redirects all writes to:
  - PostgreSQL: staging.* tables (via SET search_path)
  - Qdrant: committee_transcripts_staging collection

Enriches every Qdrant payload with:
  - doc_type: "virtual_notulen"   — clearly distinguishes from official notulen
  - is_virtual_notulen: True      — boolean flag for filtering
  - start_date                    — from staging.meetings (enables date filtering in RAG)
  - committee                     — from staging.meetings (enables committee filtering)

All chunking, embedding, and storage logic is inherited unchanged.
"""

import logging
import hashlib
import os
import psycopg2
from psycopg2.extras import execute_values
from qdrant_client.models import PointStruct
from pipeline.ingestion import SmartIngestor

logger = logging.getLogger(__name__)


def _default_db_url():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    url = os.getenv("DATABASE_URL")
    if url:
        return url
    user = os.getenv("DB_USER", "postgres")
    pw = os.getenv("DB_PASSWORD", "postgres")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "neodemos")
    return f"postgresql://{user}:{pw}@{host}:{port}/{name}"


class StagingIngestor(SmartIngestor):
    """SmartIngestor that writes to the staging schema and staging Qdrant collection."""

    def __init__(
        self,
        db_url: str = None,
        chunk_only: bool = False,
        staging_schema: str = "staging",
        qdrant_collection: str = "committee_transcripts_staging",
    ):
        if db_url is None:
            db_url = _default_db_url()
        super().__init__(db_url=db_url, chunk_only=chunk_only)
        self.staging_schema = staging_schema
        self.collection_name = qdrant_collection  # overrides "notulen_chunks"
        logger.info(
            f"StagingIngestor: schema={staging_schema}, "
            f"qdrant_collection={qdrant_collection}"
        )

    def _get_connection(self):
        """Get a PostgreSQL connection with search_path set to the staging schema."""
        conn = psycopg2.connect(self.db_url)
        cur = conn.cursor()
        cur.execute(f"SET search_path TO {self.staging_schema}, public")
        cur.close()
        return conn

    def ingest_document(self, doc_id, doc_name, content, meeting_id=None, metadata=None, category="committee_transcript"):
        """Override to use staging connection instead of production."""
        logger.info(f"[STAGING] Ingesting document: {doc_name} (ID: {doc_id}) | Category: {category}")
        conn = self._get_connection()
        try:
            import hashlib
            from typing import Dict

            # --- Cleanup Phase ---
            cur = conn.cursor()
            cur.execute("SELECT id FROM document_children WHERE document_id = %s", (doc_id,))
            child_ids = [r[0] for r in cur.fetchall()]
            if child_ids:
                logger.info(f"  Cleaning up {len(child_ids)} existing child sections and chunks...")
                cur.execute("DELETE FROM document_chunks WHERE child_id = ANY(%s)", (child_ids,))
                cur.execute("DELETE FROM document_children WHERE id = ANY(%s)", (child_ids,))

            cur.execute("DELETE FROM document_chunks WHERE document_id = %s AND child_id IS NULL", (doc_id,))
            conn.commit()
            cur.close()

            # 0. Ensure Parent Document Record
            agenda_item_id = metadata.get('agenda_item_id') if metadata else None
            self._ensure_document_record(conn, doc_id, doc_name, meeting_id, content, agenda_item_id=agenda_item_id, category=category)

            doc_size = len(content)

            # --- 4-TIER STRATEGY (inherited) ---
            if doc_size < self.atomic_threshold:
                logger.info(f"  Atomic: {doc_size} chars -> 1 chunk.")
                self._store_single_chunk(conn, doc_id, doc_name, meeting_id, content, metadata, chunk_type="full_text")

            elif doc_size <= self.compact_threshold:
                logger.info(f"  Compact: {doc_size} chars -> 1 chunk.")
                self._store_single_chunk(conn, doc_id, doc_name, meeting_id, content, metadata, chunk_type="quote")

            elif doc_size <= self.structural_threshold:
                chunks = self._recursive_chunk(content, doc_name)
                logger.info(f"  Recursive: {doc_size} chars -> {len(chunks)} chunks.")
                self._store_child_and_chunks(conn, doc_id, doc_name, meeting_id, content, metadata, chunks)

            else:
                logger.info(f"  Structural: {doc_size} chars. Using Gemini for section detection...")
                sections = self._detect_sections_via_gemini(content, doc_name)
                if not sections:
                    sections = [{"title": doc_name, "text": content}]
                all_chunks = []
                for sec in sections:
                    sec_chunks = self._recursive_chunk(sec["text"], sec.get("title", doc_name))
                    all_chunks.extend(sec_chunks)
                logger.info(f"  Structural: {len(sections)} sections -> {len(all_chunks)} chunks.")
                self._store_child_and_chunks(conn, doc_id, doc_name, meeting_id, content, metadata, all_chunks)

            logger.info(f"[STAGING] Successfully ingested document: {doc_id}")
        finally:
            conn.close()

    def _ensure_document_record(self, conn, doc_id, doc_name, meeting_id, content, agenda_item_id=None, category="committee_transcript"):
        """Creates or updates the parent document record in the staging schema.

        Unlike the parent class, we skip the meeting existence check against public.meetings
        since the meeting record should already exist in staging before ingestion.
        """
        cur = conn.cursor()

        # Check meeting exists in staging
        if meeting_id:
            cur.execute("SELECT 1 FROM meetings WHERE id = %s", (meeting_id,))
            if not cur.fetchone():
                logger.warning(f"Meeting {meeting_id} not found in staging. Ingesting as orphan.")
                meeting_id = None
            else:
                if category != "municipal_doc":
                    cur.execute("UPDATE meetings SET category = %s WHERE id = %s", (category, meeting_id))

        cur.execute("""
            INSERT INTO documents (id, name, meeting_id, content, category)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                content = EXCLUDED.content,
                category = EXCLUDED.category
        """, (doc_id, doc_name, meeting_id, content, category))

        if meeting_id or agenda_item_id:
            cur.execute("""
                INSERT INTO document_assignments (document_id, meeting_id, agenda_item_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (document_id, meeting_id, agenda_item_id) DO NOTHING
            """, (doc_id, meeting_id, agenda_item_id))

        conn.commit()
        cur.close()

    def ensure_staging_meeting(self, meeting_id: str, name: str, start_date=None,
                                committee: str = None, transcript_source: str = None):
        """Create or update a meeting record in the staging schema.

        Must be called before ingesting transcripts for a meeting so that
        FK constraints are satisfied.
        """
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO meetings (id, name, start_date, committee, transcript_source, review_status)
                VALUES (%s, %s, %s, %s, %s, 'pending')
                ON CONFLICT (id) DO UPDATE SET
                    name = EXCLUDED.name,
                    start_date = COALESCE(EXCLUDED.start_date, meetings.start_date),
                    committee = COALESCE(EXCLUDED.committee, meetings.committee),
                    transcript_source = COALESCE(EXCLUDED.transcript_source, meetings.transcript_source)
            """, (meeting_id, name, start_date, committee, transcript_source))
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def update_quality_score(self, meeting_id: str, score: float, review_status: str = None):
        """Update the quality score and optionally the review status for a staging meeting."""
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            if review_status:
                cur.execute(
                    "UPDATE meetings SET quality_score = %s, review_status = %s WHERE id = %s",
                    (score, review_status, meeting_id)
                )
            else:
                cur.execute(
                    "UPDATE meetings SET quality_score = %s WHERE id = %s",
                    (score, meeting_id)
                )
            conn.commit()
            cur.close()
        finally:
            conn.close()

    def _get_meeting_meta(self, conn, meeting_id: str) -> dict:
        """Fetch start_date and committee from staging.meetings for payload enrichment."""
        if not meeting_id:
            return {}
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT start_date, committee FROM meetings WHERE id = %s",
                (meeting_id,)
            )
            row = cur.fetchone()
            cur.close()
            if row:
                return {
                    "start_date": row[0].isoformat() if row[0] else None,
                    "committee": row[1] or None,
                }
        except Exception as e:
            logger.debug(f"Could not fetch meeting meta for {meeting_id}: {e}")
        return {}

    def _store_grandchildren(self, conn, doc_id: str, doc_name: str, meeting_id: str,
                              chunks, child_id: int):
        """Override: enriches each Qdrant point payload with virtual-notulen metadata.

        Adds compared to the base implementation:
          - doc_type: "virtual_notulen"
          - is_virtual_notulen: True
          - start_date: ISO date from staging.meetings (enables RAG date filtering)
          - committee: committee name (enables MCP committee display/filtering)
        """
        cur = conn.cursor()
        points = []
        pg_data = []

        # Fetch meeting metadata once for all chunks in this call
        meeting_meta = self._get_meeting_meta(conn, meeting_id)

        for idx, chunk in enumerate(chunks):
            text = chunk.get("text", "").strip()
            if len(text) < 20:
                continue
            title = chunk.get("title", "Untitled")
            chunk_type = chunk.get("chunk_type", "quote")
            pg_data.append((doc_id, idx, title, text, chunk_type, None, int(len(text) / 4), child_id))

            if not self.chunk_only and self.local_ai and self.local_ai.is_available():
                context_str = f"[Document: {doc_name} | Section: {title}]\n"
                embedding = self.local_ai.generate_embedding(context_str + text)
                if embedding is not None:
                    hash_str = hashlib.md5(f"{doc_id}_{child_id}_{idx}".encode()).hexdigest()
                    point_id = int(hash_str[:15], 16)
                    payload = {
                        "document_id": doc_id,
                        "doc_name": doc_name,
                        # ── Virtual notulen identity fields ──────────
                        "doc_type": "virtual_notulen",
                        "is_virtual_notulen": True,
                        # ── Meeting context (enables RAG filtering) ──
                        "meeting_id": meeting_id,
                        "start_date": meeting_meta.get("start_date"),
                        "committee": meeting_meta.get("committee"),
                        # ── Chunk context ────────────────────────────
                        "child_id": child_id,
                        "chunk_index": idx,
                        "chunk_type": chunk_type,
                        "title": title,
                        "content": text,
                        "questions": chunk.get("questions", []),
                    }
                    points.append(PointStruct(id=point_id, vector=embedding, payload=payload))

        if points and self.qdrant:
            self.qdrant.upsert(collection_name=self.collection_name, points=points)
        if pg_data:
            execute_values(cur, """
                INSERT INTO document_chunks
                    (document_id, chunk_index, title, content, chunk_type, table_json, tokens_estimated, child_id)
                VALUES %s
                ON CONFLICT (document_id, chunk_index) DO UPDATE SET
                    content = EXCLUDED.content, title = EXCLUDED.title, child_id = EXCLUDED.child_id
            """, pg_data)
        conn.commit()
        cur.close()
        logger.info(f"    ✓ [STAGING] Stored {len(pg_data)} chunks (doc_type=virtual_notulen)"
                    f"{' (chunk_only, no embedding)' if self.chunk_only else ''}")
