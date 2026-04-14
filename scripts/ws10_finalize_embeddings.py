"""One-off: finalize embeddings for WS10 targeted batch (2026-04-14).

Uses the existing ingestion pipeline:
  - SmartIngestor(chunk_only=True) for re-chunking (same as document_processor Phase 1)
  - document_processor-style Phase 2 embed path (proper metadata payload)
  - Phase 3 tsvector backfill

Handles the 5 successfully-extracted primaries + 4 propagated duplicates:
  - Primaries: chunks already fresh (chunk_only'd by WS10 extractor), need Phase 2 embed
  - Duplicates: content was propagated but chunks are stale → delete_old_chunks + rechunk + Phase 2

Uses advisory lock 42 (WS7/WS10 shared) around DB-mutating steps.
"""

import hashlib
import logging
import os
import sys

import psycopg2
from psycopg2.extras import RealDictCursor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.ocr_recovery import (
    DB_URL,
    delete_old_chunks,
    acquire_advisory_lock,
    release_advisory_lock,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Primary docs (chunks already fresh from WS10 rechunk) — need embed only
PRIMARIES = ["6096469", "1021207", "1021208", "6119497", "3548152"]

# Duplicates (content propagated via MD5 hash, but old chunks weren't deleted)
DUPLICATES = ["4864056", "4921772", "6119609", "6121453"]

ALL_DOCS = PRIMARIES + DUPLICATES


def compute_point_id(document_id: str, child_id: str, chunk_index: int) -> int:
    """Reproduce the Qdrant point ID used by document_processor.py:496."""
    h = hashlib.md5(f"{document_id}_{child_id}_{chunk_index}".encode()).hexdigest()
    return int(h[:15], 16)


def purge_qdrant_by_postgres_chunks(conn, qdrant, collection: str, doc_id: str) -> int:
    """Delete Qdrant points for a doc by computing deterministic point IDs from
    the CURRENT Postgres chunks. Fast — no payload-filter scan needed.

    Only works if the Postgres chunks still exist (i.e. before delete_old_chunks).
    """
    from qdrant_client.models import PointIdsList

    cur = conn.cursor()
    cur.execute(
        "SELECT child_id, chunk_index FROM document_chunks WHERE document_id = %s",
        (doc_id,),
    )
    rows = cur.fetchall()
    cur.close()
    if not rows:
        return 0
    point_ids = [compute_point_id(doc_id, child_id, ci) for (child_id, ci) in rows]
    # Delete in batches of 500 to keep payloads small
    for i in range(0, len(point_ids), 500):
        qdrant.delete(
            collection_name=collection,
            points_selector=PointIdsList(points=point_ids[i : i + 500]),
        )
    return len(point_ids)


def rechunk_with_pipeline(doc_id: str, doc_name: str, content: str):
    """Same path document_processor.py uses: SmartIngestor(chunk_only=True)."""
    from pipeline.ingestion import SmartIngestor

    ingestor = SmartIngestor(db_url=DB_URL, chunk_only=True)
    ingestor.ingest_document(
        doc_id=doc_id,
        doc_name=doc_name,
        content=content,
        meeting_id=None,
        metadata={"recovery": "ws10_duplicate_propagation"},
        category="municipal_doc",
    )


def embed_docs_phase2(conn, doc_ids: list[str], qdrant, collection: str) -> int:
    """Scoped Phase 2 embed — same logic as document_processor.py:445-544 but
    restricted to our target doc_ids so we don't pick up unrelated pending chunks.
    """
    from services.embedding import create_embedder
    from qdrant_client.models import PointStruct

    embedder = create_embedder()

    embed_cur = conn.cursor(cursor_factory=RealDictCursor)
    embed_cur.execute(
        """
        SELECT dc.id, dc.document_id, dc.title, dc.content,
               dc.chunk_index, dc.child_id, dc.chunk_type,
               dc.section_topic, dc.key_entities,
               d.name AS doc_name, d.meeting_id, d.category,
               d.municipality, d.doc_classification
        FROM document_chunks dc
        JOIN documents d ON d.id = dc.document_id
        WHERE dc.document_id = ANY(%s)
          AND dc.embedded_at IS NULL
          AND dc.content IS NOT NULL
          AND LENGTH(dc.content) > 20
        ORDER BY dc.document_id, dc.chunk_index
        """,
        (doc_ids,),
    )
    unembedded = embed_cur.fetchall()
    embed_cur.close()

    if not unembedded:
        logger.info("  nothing to embed")
        return 0

    logger.info(f"  embedding {len(unembedded)} chunks via create_embedder()")

    texts = [
        f"[Document: {r['doc_name'] or ''} | Section: {r['title'] or ''}]\n{r['content']}"
        for r in unembedded
    ]

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
        points.append(PointStruct(id=point_id, vector=vec, payload=payload))
        chunk_ids_embedded.append(row["id"])

    batch_size = 100
    for i in range(0, len(points), batch_size):
        batch = points[i : i + batch_size]
        qdrant.upsert(collection_name=collection, points=batch)

    # Mark chunks as embedded (AFTER successful Qdrant upsert)
    if chunk_ids_embedded:
        up_cur = conn.cursor()
        up_cur.execute(
            "UPDATE document_chunks SET embedded_at = NOW() WHERE id = ANY(%s)",
            (chunk_ids_embedded,),
        )
        conn.commit()
        up_cur.close()

    return len(points)


def main():
    conn = psycopg2.connect(DB_URL)

    from services.embedding import QDRANT_COLLECTION
    from qdrant_client import QdrantClient

    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
    qdrant_key = os.getenv("QDRANT_API_KEY", "")
    qdrant = QdrantClient(url=qdrant_url, api_key=qdrant_key if qdrant_key else None, timeout=60)

    logger.info("=" * 70)
    logger.info("WS10 FINALIZE EMBEDDINGS (via ingestion pipeline)")
    logger.info("=" * 70)
    logger.info(f"  primaries:  {len(PRIMARIES)} docs — embed only")
    logger.info(f"  duplicates: {len(DUPLICATES)} docs — rechunk + embed")
    logger.info(f"  qdrant:     {qdrant_url} / {QDRANT_COLLECTION}")
    logger.info("")

    # NOTE on Qdrant orphans:
    # The payload index on `document_id` was created but is still building on a
    # 1.77M-point collection (takes hours). Filter-based delete times out server-side.
    # Workaround: delete by computed deterministic point IDs from the chunks we
    # still have in Postgres. Works for duplicates (chunks intact) but NOT for
    # primaries (their old chunks were already deleted by WS10 — old IDs lost).
    # Orphan debt for primaries will be cleaned by a follow-up once index builds.

    # Step 1: Purge stale Qdrant points for DUPLICATES ONLY
    # (compute old point IDs from their still-in-postgres chunks, delete by ID)
    logger.info("Step 1: purge stale Qdrant points for duplicates (by ID, not filter)")
    total_purged = 0
    for doc_id in DUPLICATES:
        n = purge_qdrant_by_postgres_chunks(conn, qdrant, QDRANT_COLLECTION, doc_id)
        total_purged += n
        logger.info(f"  [{doc_id}] purged {n} old points")
    logger.info(f"  total purged: {total_purged}")
    logger.info("  primaries: skipping (orphan debt — clean up after index completes)")
    logger.info("")

    # Step 2: Rechunk the 4 duplicates via the pipeline (under advisory lock)
    logger.info("Step 2: rechunk duplicates via SmartIngestor(chunk_only=True)")
    if not acquire_advisory_lock(conn, wait=True):
        logger.error("Could not acquire advisory lock — abort")
        sys.exit(1)
    try:
        for doc_id in DUPLICATES:
            cur = conn.cursor()
            cur.execute("SELECT name, content FROM documents WHERE id = %s", (doc_id,))
            row = cur.fetchone()
            cur.close()
            if not row or not row[1]:
                logger.warning(f"  [{doc_id}] no content — skip")
                continue
            name, content = row
            logger.info(f"  [{doc_id}] delete_old_chunks …")
            deleted = delete_old_chunks(conn, doc_id)
            conn.commit()
            logger.info(f"  [{doc_id}] deleted {deleted} chunks; rechunk ({len(content):,} chars) …")
            rechunk_with_pipeline(doc_id, name, content)
    finally:
        release_advisory_lock(conn)
    logger.info("")

    # Step 3: Phase 2 embed (scoped to our 9 docs)
    logger.info("Step 3: Phase 2 embed (scoped)")
    n_embedded = embed_docs_phase2(conn, ALL_DOCS, qdrant, QDRANT_COLLECTION)
    logger.info(f"  embedded {n_embedded} chunks")
    logger.info("")

    # Step 4: Phase 3 tsvectors
    logger.info("Step 4: backfill text_search_enriched tsvectors")
    try:
        from services.document_processor import find_chunks_missing_tsvector

        n_ts = find_chunks_missing_tsvector(conn, limit=10000)
        logger.info(f"  built tsvectors for {n_ts} chunks")
    except Exception as e:
        logger.warning(f"  tsvector backfill skipped: {e}")
    logger.info("")

    # Step 5: Verify
    logger.info("Step 5: verify")
    cur = conn.cursor()
    cur.execute(
        """
        SELECT d.id, LENGTH(d.content) AS chars, d.ocr_quality, d.doc_classification,
               (SELECT COUNT(*) FROM document_chunks WHERE document_id = d.id) AS chunks,
               (SELECT COUNT(*) FROM document_chunks
                 WHERE document_id = d.id AND embedding IS NOT NULL) AS embedded_pg
        FROM documents d WHERE d.id = ANY(%s)
        ORDER BY d.id
        """,
        (ALL_DOCS,),
    )
    # Verify new Qdrant points by fetching by deterministic IDs from new chunks
    # (avoids payload-filter scan on in-progress index)
    from qdrant_client.models import PointIdsList

    print(
        f"\n{'doc_id':<40} {'chars':>10} {'ocr':<6} {'class':<12} {'pg_chunks':>10} {'pg_embed':>9} {'qdrant':>8}"
    )
    print("-" * 105)
    verify_cur = conn.cursor()
    for r in cur.fetchall():
        doc_id, chars, ocr, cls, chunks, embed_pg = r
        # Compute expected point IDs from current Postgres chunks, check how many exist
        verify_cur.execute(
            "SELECT child_id, chunk_index FROM document_chunks WHERE document_id = %s LIMIT 1000",
            (doc_id,),
        )
        expected_ids = [compute_point_id(doc_id, cid, ci) for (cid, ci) in verify_cur.fetchall()]
        if not expected_ids:
            qpts = 0
        else:
            # retrieve returns only points that exist
            found = qdrant.retrieve(
                collection_name=QDRANT_COLLECTION,
                ids=expected_ids,
                with_payload=False,
                with_vectors=False,
            )
            qpts = len(found)
        print(
            f"{str(doc_id):<40} {chars:>10,} {str(ocr):<6} {str(cls):<12} {chunks:>10} {embed_pg:>9} {qpts:>8}"
        )
    verify_cur.close()
    cur.close()
    conn.close()
    logger.info("Done.")


if __name__ == "__main__":
    main()
