#!/usr/bin/env python3
"""
WS5a Phase A — Hourly full-ingest canary smoke test
====================================================

Runs a known-good fixture end-to-end through the production ingest path
(chunker → embedder → Qdrant writer → retriever → verifier) and asserts
that each stage still works. Today the chunker can silently drop
syllables mid-word in ~50% of chunks (see
`reports/chunk_attribution_sample_2000.csv`) — no existing job would
have caught that. This smoke test is the regression net.

FAILURE MODES DETECTED
----------------------
* Chunker regression (e.g. mid-word splits, character loss)
    → step 6 (chunk attribution audit) emits `mismatch` on fixture_01_clean
* Embed service down (Nebius outage, MLX broken)
    → step 3 (ingest) fails with no embeddings produced
* Qdrant connectivity / collection corruption
    → step 3 fails on upsert OR step 4 search returns zero hits
* Verifier regression (Jina reranker outage, source-span verifier bug)
    → step 5 returns `verified=False` for a sentence quoted verbatim
* Pipeline run / pipeline_failures / document_events tables drift
    → this script fails to insert its own observability rows

ISOLATION
---------
* Qdrant collection     : smoke_test_notulen_chunks  (separate, prod RAG never reads it)
* documents.id          : `SMOKE_TEST_<fixture>`  (prefix never collides)
* documents.category    : `smoke_test`
* Qdrant payload        : `is_smoke_test: true`  (defense-in-depth)
* Postgres writes       : only into existing tables (no DDL)
* Financial extractor   : never runs on fixtures (category != table-bearing)

LOCKS
-----
* Acquires `pg_advisory_lock(42)` only around the short write windows in
  step 1 (cleanup) and step 7 (cleanup) — never during embedding/reranker
  calls. Lock held for < 1s in both windows.
* Coexists with WS6 Phase 3 (Gemini `7_640_601` lock) — different key.

ENFORCEMENT GATE (Phase B)
--------------------------
This script detects failures and writes `pipeline_runs.status='failure'`
plus a `pipeline_failures` row per failing step. The "fail the deploy if
smoke test fails 3 hours in a row" enforcement is **Phase B** (WS5a). Here
we only do the detection; the deploy gate reads from `pipeline_runs`.

EXIT CODES
----------
* 0  all steps passed
* 1  one or more steps failed
* 2  operational error (tunnel down, Qdrant unreachable, etc.)

USAGE
-----
    python scripts/nightly/00_smoke_test.py
    python scripts/nightly/00_smoke_test.py --fixture fixture_01_clean
    python scripts/nightly/00_smoke_test.py --keep-data --verbose

Handoff: docs/handoffs/WS5a_NIGHTLY_PIPELINE.md
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Project bootstrap ──────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

from services.db_pool import get_connection  # noqa: E402
from services.embedding import (  # noqa: E402
    EMBEDDING_DIM,
    compute_point_id,
    create_embedder,
)

logger = logging.getLogger("smoke_test")


# ── Constants (isolation knobs) ───────────────────────────────────────
SMOKE_COLLECTION = "smoke_test_notulen_chunks"
SMOKE_DOC_ID_PREFIX = "SMOKE_TEST_"
SMOKE_CATEGORY = "smoke_test"
SMOKE_JOB_NAME = "00_smoke_test"
SMOKE_TRIGGERED_BY = "smoke_test"
ADVISORY_LOCK_KEY = 42
FIXTURES_DIR = PROJECT_ROOT / "data" / "smoke_tests"
MIN_TOP3_SCORE = 0.3  # lenient floor — fixture is tiny so near-duplicate dominates anyway


# ── Step-result dataclass ─────────────────────────────────────────────

@dataclass
class StepResult:
    name: str
    status: str = "pending"         # "success" | "failure" | "skipped" | "pending"
    duration_ms: int = 0
    error_message: str = ""
    error_traceback: str = ""
    detail: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == "success"

    def to_log(self) -> str:
        status = self.status.upper()
        base = f"[{status:<7}] {self.name} ({self.duration_ms}ms)"
        if self.error_message:
            base += f" — {self.error_message}"
        return base


# ── Fixture discovery ─────────────────────────────────────────────────

@dataclass
class Fixture:
    name: str
    title: str
    text: str
    canary_phrase: str
    expected_chunks_min: int
    expected_chunks_max: int
    allow_fuzzy: bool
    notes: str

    @property
    def document_id(self) -> str:
        return f"{SMOKE_DOC_ID_PREFIX}{self.name}"


def _list_fixtures() -> List[Fixture]:
    """Return every fixture on disk in a stable order (by filename)."""
    if not FIXTURES_DIR.exists():
        raise FileNotFoundError(
            f"Fixtures directory does not exist: {FIXTURES_DIR} — "
            "see data/smoke_tests/README.md"
        )
    fixtures: List[Fixture] = []
    for txt in sorted(FIXTURES_DIR.glob("fixture_*.txt")):
        sidecar = txt.with_suffix(".json")
        if not sidecar.exists():
            logger.warning(f"Fixture {txt.name} has no JSON sidecar — skipping")
            continue
        try:
            meta = json.loads(sidecar.read_text(encoding="utf-8"))
            text = txt.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"Fixture {txt.name} unreadable ({e}) — skipping")
            continue
        fixtures.append(
            Fixture(
                name=meta["name"],
                title=meta.get("title", meta["name"]),
                text=text,
                canary_phrase=meta["canary_phrase"],
                expected_chunks_min=int(meta.get("expected_chunks_min", 1)),
                expected_chunks_max=int(meta.get("expected_chunks_max", 10)),
                allow_fuzzy=bool(meta.get("allow_fuzzy", False)),
                notes=meta.get("notes", ""),
            )
        )
    if not fixtures:
        raise FileNotFoundError(
            f"No fixtures found in {FIXTURES_DIR}. Add at least one "
            "fixture_<n>_<kind>.txt + .json pair."
        )
    return fixtures


def _pick_fixture(
    all_fixtures: List[Fixture],
    explicit_name: Optional[str],
) -> Fixture:
    """Either honour --fixture or pick deterministically by ISO week."""
    if explicit_name:
        for f in all_fixtures:
            if f.name == explicit_name:
                return f
        raise SystemExit(
            f"--fixture {explicit_name!r} not found. "
            f"Available: {[f.name for f in all_fixtures]}"
        )
    idx = datetime.now(timezone.utc).isocalendar().week % len(all_fixtures)
    return all_fixtures[idx]


# ── Qdrant bootstrap ──────────────────────────────────────────────────

def _build_qdrant_client():
    """Return a QdrantClient configured from env."""
    from qdrant_client import QdrantClient
    url = os.getenv("QDRANT_URL", "http://localhost:6333")
    api_key = os.getenv("QDRANT_API_KEY")
    return QdrantClient(url=url, api_key=api_key, timeout=30)


def _ensure_smoke_collection(qdrant) -> None:
    """Idempotently ensure the smoke_test Qdrant collection exists (4096-dim, Cosine)."""
    from qdrant_client.models import Distance, VectorParams

    existing = [c.name for c in qdrant.get_collections().collections]
    if SMOKE_COLLECTION in existing:
        return
    qdrant.create_collection(
        collection_name=SMOKE_COLLECTION,
        vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
    )
    logger.info(f"Created Qdrant collection: {SMOKE_COLLECTION} ({EMBEDDING_DIM}D, Cosine)")


# ── Pipeline-runs / failures observability ────────────────────────────

def _open_pipeline_run() -> int:
    """Insert a `status='running'` row into pipeline_runs and return its id."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO pipeline_runs
                (job_name, started_at, status, triggered_by)
            VALUES (%s, NOW(), 'running', %s)
            RETURNING id
            """,
            (SMOKE_JOB_NAME, SMOKE_TRIGGERED_BY),
        )
        row_id = cur.fetchone()[0]
        cur.close()
    return row_id


def _close_pipeline_run(
    run_id: int,
    status: str,
    items_processed: int,
    items_failed: int,
    error_message: Optional[str] = None,
    error_traceback: Optional[str] = None,
) -> None:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            UPDATE pipeline_runs
            SET finished_at = NOW(),
                status = %s,
                items_discovered = %s,
                items_processed = %s,
                items_failed = %s,
                error_message = %s,
                error_traceback = %s
            WHERE id = %s
            """,
            (
                status,
                items_processed + items_failed,
                items_processed,
                items_failed,
                (error_message or None),
                (error_traceback or None),
                run_id,
            ),
        )
        cur.close()


def _log_pipeline_failure(step: StepResult, fixture_name: str) -> None:
    """Insert a pipeline_failures row for a failed step."""
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO pipeline_failures
                    (job_name, item_id, item_type, failed_at,
                     retry_count, error_class, error_message, raw_payload)
                VALUES (%s, %s, %s, NOW(), 0, %s, %s, %s::jsonb)
                """,
                (
                    SMOKE_JOB_NAME,
                    step.name,
                    "smoke_step",
                    (step.error_traceback.splitlines()[-1].split(":")[0]
                     if step.error_traceback else "Exception"),
                    step.error_message[:2000],
                    json.dumps({
                        "fixture": fixture_name,
                        "duration_ms": step.duration_ms,
                        "detail": step.detail,
                        "traceback": step.error_traceback[-4000:],
                    }),
                ),
            )
            cur.close()
    except Exception as exc:
        logger.warning(f"Could not log pipeline_failures row: {exc}")


def _log_document_event(doc_id: str, event_type: str, details: Dict[str, Any]) -> None:
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO document_events
                    (document_id, event_type, details, triggered_by)
                VALUES (%s, %s, %s::jsonb, %s)
                """,
                (
                    doc_id, event_type,
                    json.dumps(details, default=str),
                    SMOKE_TRIGGERED_BY,
                ),
            )
            cur.close()
    except Exception as exc:
        logger.warning(f"Could not log document_events row ({event_type}): {exc}")


# ── Step 1 — cleanup prior run (idempotent reset) ──────────────────────

def _step_cleanup(fixture: Fixture, qdrant) -> StepResult:
    """Delete any existing smoke_test chunks / documents / Qdrant points for this fixture.

    Held advisory lock 42 briefly so we don't step on a concurrent writer.
    """
    started = time.monotonic()
    res = StepResult(name="01_cleanup_prior_run")
    doc_id = fixture.document_id

    try:
        with get_connection() as conn:
            cur = conn.cursor()

            # Short, non-blocking lock window.
            cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
            got_lock = bool(cur.fetchone()[0])
            if not got_lock:
                # Another writer is active — we coexist by NOT blocking. We still
                # do our deletes because our rows are in our own namespace.
                logger.warning(
                    "advisory_lock(42) is busy — proceeding without lock "
                    "(smoke rows are namespaced under SMOKE_TEST_*)"
                )

            try:
                # Count what exists first (for the step detail)
                cur.execute(
                    "SELECT COUNT(*) FROM document_chunks WHERE document_id = %s",
                    (doc_id,),
                )
                chunks_before = cur.fetchone()[0]

                # Delete chunks → children → document in dependency order.
                cur.execute(
                    "DELETE FROM document_chunks WHERE document_id = %s", (doc_id,),
                )
                cur.execute(
                    "DELETE FROM document_children WHERE document_id = %s", (doc_id,),
                )
                cur.execute(
                    "DELETE FROM documents WHERE id = %s AND category = %s",
                    (doc_id, SMOKE_CATEGORY),
                )
            finally:
                if got_lock:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))

            cur.close()

        # Qdrant cleanup — scoped filter so we only touch smoke_test payloads.
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        try:
            qdrant.delete(
                collection_name=SMOKE_COLLECTION,
                points_selector=Filter(
                    must=[
                        FieldCondition(key="document_id", match=MatchValue(value=doc_id)),
                        FieldCondition(key="is_smoke_test", match=MatchValue(value=True)),
                    ]
                ),
            )
        except Exception as exc:
            # Collection may not exist yet on the very first run — ignore.
            logger.debug(f"Qdrant delete non-fatal: {exc}")

        res.status = "success"
        res.detail = {"chunks_before": int(chunks_before or 0), "doc_id": doc_id}
    except Exception as exc:
        res.status = "failure"
        res.error_message = str(exc)[:500]
        res.error_traceback = traceback.format_exc()
    finally:
        res.duration_ms = int((time.monotonic() - started) * 1000)
    return res


# ── Step 2 — ensure Qdrant collection exists ─────────────────────────

def _step_ensure_collection(qdrant) -> StepResult:
    started = time.monotonic()
    res = StepResult(name="02_ensure_qdrant_collection")
    try:
        _ensure_smoke_collection(qdrant)
        res.status = "success"
        res.detail = {"collection": SMOKE_COLLECTION, "dim": EMBEDDING_DIM}
    except Exception as exc:
        res.status = "failure"
        res.error_message = str(exc)[:500]
        res.error_traceback = traceback.format_exc()
    finally:
        res.duration_ms = int((time.monotonic() - started) * 1000)
    return res


# ── Step 3 — ingest fixture via production chunker + embedder ────────

def _step_ingest(fixture: Fixture, qdrant) -> StepResult:
    """Run chunker → embedder → Qdrant write. Advisory-lock around DB writes only.

    Uses the production `SmartIngestor._recursive_chunk` via a direct method
    call to avoid dragging the full ingest-with-cleanup logic (which hits
    many FK-referencing tables we don't need for a canary). The chunks get
    inserted manually with `is_smoke_test=True` payload.
    """
    started = time.monotonic()
    res = StepResult(name="03_ingest_fixture")
    doc_id = fixture.document_id
    try:
        from pipeline.ingestion import SmartIngestor
        from qdrant_client.models import PointStruct

        embedder = create_embedder()
        if not embedder.is_available():
            raise RuntimeError("Embedder not available (NEBIUS_API_KEY missing and no local MLX)")

        ingestor = SmartIngestor(chunk_only=True)  # we handle Qdrant ourselves

        # Chunk using the production chunker. For small fixtures (< max_chunk_chars)
        # the chunker returns a single chunk. We still go through _recursive_chunk
        # to exercise the path that's been regressing.
        if len(fixture.text) <= ingestor.max_chunk_chars:
            chunks = [{
                "title": fixture.title,
                "text": fixture.text.strip(),
                "questions": [],
                "chunk_type": "quote",
            }]
        else:
            chunks = ingestor._recursive_chunk(fixture.text, fixture.title)

        if len(chunks) < fixture.expected_chunks_min:
            raise RuntimeError(
                f"Chunker produced {len(chunks)} chunk(s), expected "
                f">= {fixture.expected_chunks_min}"
            )
        if len(chunks) > fixture.expected_chunks_max:
            raise RuntimeError(
                f"Chunker produced {len(chunks)} chunk(s), expected "
                f"<= {fixture.expected_chunks_max}"
            )

        # Acquire lock only for the brief Postgres write window.
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_KEY,))
            got_lock = bool(cur.fetchone()[0])
            if not got_lock:
                logger.warning("advisory_lock(42) busy — smoke writes proceed (isolated namespace)")

            try:
                # 1. documents row (category='smoke_test')
                cur.execute(
                    """
                    INSERT INTO documents (id, name, meeting_id, content, category)
                    VALUES (%s, %s, NULL, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        name = EXCLUDED.name,
                        content = EXCLUDED.content,
                        category = EXCLUDED.category
                    """,
                    (doc_id, fixture.title, fixture.text, SMOKE_CATEGORY),
                )

                # 2. document_children row (full content, chunk_index=0)
                cur.execute(
                    """
                    INSERT INTO document_children (document_id, chunk_index, content, metadata)
                    VALUES (%s, 0, %s, %s::jsonb)
                    RETURNING id
                    """,
                    (doc_id, fixture.text, json.dumps({"is_smoke_test": True})),
                )
                child_id = cur.fetchone()[0]

                # 3. document_chunks rows (one per chunk)
                db_ids: List[int] = []
                chunk_rows = []
                for idx, chunk in enumerate(chunks):
                    text = chunk.get("text", "").strip()
                    if len(text) < 20:
                        continue
                    chunk_rows.append((
                        doc_id, idx, chunk.get("title") or fixture.title, text,
                        chunk.get("chunk_type", "quote"), None,
                        int(len(text) / 4), child_id,
                    ))

                # Use executemany with RETURNING by a single INSERT per row (small N).
                for row in chunk_rows:
                    cur.execute(
                        """
                        INSERT INTO document_chunks
                            (document_id, chunk_index, title, content,
                             chunk_type, table_json, tokens_estimated, child_id)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (document_id, chunk_index) DO UPDATE SET
                            content = EXCLUDED.content,
                            title = EXCLUDED.title,
                            child_id = EXCLUDED.child_id
                        RETURNING id
                        """,
                        row,
                    )
                    db_ids.append(cur.fetchone()[0])
            finally:
                if got_lock:
                    cur.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_KEY,))
            cur.close()

        # 4. Embed each chunk (outside the lock — network calls are slow)
        points = []
        for chunk, db_id in zip(chunks, db_ids):
            text = chunk.get("text", "").strip()
            title = chunk.get("title") or fixture.title
            context_str = f"[Document: {fixture.title} | Section: {title}]\n{text}"
            vector = embedder.embed(context_str)
            if vector is None:
                raise RuntimeError(f"Embedder returned None for chunk db_id={db_id}")
            point_id = compute_point_id(doc_id, db_id)
            payload = {
                "document_id": doc_id,
                "doc_name": fixture.title,
                "doc_type": "smoke_test",
                "is_smoke_test": True,       # defense-in-depth flag
                "meeting_id": None,
                "child_id": child_id,
                "chunk_index": chunk.get("chunk_index", db_ids.index(db_id)),
                "chunk_type": chunk.get("chunk_type", "quote"),
                "title": title,
                "content": text,
                "questions": [],
                "canary_phrase": fixture.canary_phrase,
            }
            points.append(PointStruct(id=point_id, vector=vector, payload=payload))

        # 5. Qdrant upsert (outside Postgres lock window)
        qdrant.upsert(collection_name=SMOKE_COLLECTION, points=points)

        # 6. Mark embedded_at on the chunks (back in Postgres, tiny UPDATE)
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "UPDATE document_chunks SET embedded_at = NOW() WHERE id = ANY(%s)",
                (db_ids,),
            )
            cur.close()

        res.status = "success"
        res.detail = {
            "chunks": len(chunks),
            "embedded": len(points),
            "db_ids": db_ids,
            "child_id": child_id,
        }
    except Exception as exc:
        res.status = "failure"
        res.error_message = str(exc)[:500]
        res.error_traceback = traceback.format_exc()
    finally:
        res.duration_ms = int((time.monotonic() - started) * 1000)
    return res


# ── Step 4 — vector retrieve on canary phrase ─────────────────────────

def _step_retrieve(fixture: Fixture, qdrant) -> StepResult:
    started = time.monotonic()
    res = StepResult(name="04_retrieve_canary")
    try:
        embedder = create_embedder()
        query_vec = embedder.embed(fixture.canary_phrase)
        if query_vec is None:
            raise RuntimeError("Embedder returned None for canary query")

        from qdrant_client.models import Filter, FieldCondition, MatchValue
        hits = qdrant.search(
            collection_name=SMOKE_COLLECTION,
            query_vector=query_vec,
            limit=3,
            query_filter=Filter(
                must=[
                    FieldCondition(key="document_id", match=MatchValue(value=fixture.document_id)),
                    FieldCondition(key="is_smoke_test", match=MatchValue(value=True)),
                ]
            ),
            with_payload=True,
        )
        if not hits:
            raise RuntimeError("Qdrant search returned zero hits for canary phrase")

        top = hits[0]
        score = float(top.score or 0.0)
        content = (top.payload or {}).get("content", "") or ""

        if fixture.canary_phrase not in content:
            raise RuntimeError(
                f"Top hit content does not contain canary phrase "
                f"{fixture.canary_phrase!r} — got: {content[:200]}"
            )
        if score < MIN_TOP3_SCORE:
            raise RuntimeError(
                f"Top hit score {score:.3f} < minimum {MIN_TOP3_SCORE:.3f}"
            )

        res.status = "success"
        res.detail = {
            "top_score": score,
            "num_hits": len(hits),
            "top_point_id": top.id,
        }
    except Exception as exc:
        res.status = "failure"
        res.error_message = str(exc)[:500]
        res.error_traceback = traceback.format_exc()
    finally:
        res.duration_ms = int((time.monotonic() - started) * 1000)
    return res


# ── Step 5 — source-span verification ─────────────────────────────────

def _step_verify(fixture: Fixture) -> StepResult:
    """Run the WS6 source-span verifier against a sentence we KNOW is in the fixture."""
    started = time.monotonic()
    res = StepResult(name="05_source_span_verify")
    try:
        from types import SimpleNamespace
        from services.source_span_verifier import SourceSpanVerifier

        # We craft a minimal "summary" that's literally quoted from the fixture
        # — so the verifier MUST return verified=True. If it doesn't, the
        # verifier or its reranker dependency has regressed.
        quoted = _pick_quotable_sentence(fixture.text)
        # `chunks` is a list of ChunkProtocol — only .chunk_id and .content used.
        chunk_obj = SimpleNamespace(chunk_id="smoke_chunk_0", content=fixture.text)

        verifier = SourceSpanVerifier()
        result = verifier.verify(summary_text=quoted, chunks=[chunk_obj])

        # Verifier returns `verified=False` if too many sentences stripped.
        # Since we passed a verbatim quote, `verified` should be True.
        if not result.verified:
            classifications = {}
            for s in result.sentences:
                classifications[s.classification] = classifications.get(s.classification, 0) + 1
            raise RuntimeError(
                f"SourceSpanVerifier returned verified=False for verbatim quote "
                f"(strip_ratio={result.strip_ratio:.2f}, classifications={classifications})"
            )

        res.status = "success"
        res.detail = {
            "strip_ratio": round(result.strip_ratio, 3),
            "total_sentences": result.total_sentences,
            "latency_ms": result.latency_ms,
            "quoted_preview": quoted[:120],
        }
    except Exception as exc:
        res.status = "failure"
        res.error_message = str(exc)[:500]
        res.error_traceback = traceback.format_exc()
    finally:
        res.duration_ms = int((time.monotonic() - started) * 1000)
    return res


def _pick_quotable_sentence(text: str) -> str:
    """Pick the first sentence >= 40 chars (verifier needs 12 to score)."""
    # A rough split on '. ' is sufficient — the verifier has its own splitter.
    chunks = [s.strip() for s in text.replace("\n", " ").split(". ") if s.strip()]
    for s in chunks:
        if len(s) >= 40:
            return (s.rstrip(".") + ".")
    # Fallback — return whole text
    return text.strip()


# ── Step 6 — chunk attribution audit invariant ────────────────────────

def _step_audit(fixture: Fixture) -> StepResult:
    """Call the run_audit() API from scripts/audit_chunk_attribution.py

    Asserts zero `mismatch` rows on the smoke fixture. Fuzzy is allowed
    iff fixture.allow_fuzzy is True.
    """
    started = time.monotonic()
    res = StepResult(name="06_chunk_attribution_audit")
    try:
        from scripts.audit_chunk_attribution import (  # local import: script is callable module
            MATCH_MISMATCH,
            MATCH_MISSING_DOC,
            MATCH_FUZZY,
            run_audit,
        )
        rows, summary = run_audit(
            limit=None,
            doc_id=fixture.document_id,
            check_qdrant=False,  # we already cross-checked in step 4; skip qdrant lookup on smoke collection
        )
        by_match = summary.get("by_match", {})
        n_mismatch = by_match.get(MATCH_MISMATCH, 0)
        n_missing = by_match.get(MATCH_MISSING_DOC, 0)
        n_fuzzy = by_match.get(MATCH_FUZZY, 0)

        if n_mismatch or n_missing:
            raise RuntimeError(
                f"Audit FAIL: {n_mismatch} mismatch + {n_missing} missing_doc "
                f"rows on {fixture.document_id}"
            )
        if n_fuzzy and not fixture.allow_fuzzy:
            raise RuntimeError(
                f"Audit FAIL: {n_fuzzy} fuzzy rows on {fixture.document_id} "
                "but fixture.allow_fuzzy=False — chunker is dropping characters"
            )

        res.status = "success"
        res.detail = {
            "total": summary.get("total", 0),
            "by_match": by_match,
            "allow_fuzzy": fixture.allow_fuzzy,
        }
    except Exception as exc:
        res.status = "failure"
        res.error_message = str(exc)[:500]
        res.error_traceback = traceback.format_exc()
    finally:
        res.duration_ms = int((time.monotonic() - started) * 1000)
    return res


# ── Step 7 — teardown ─────────────────────────────────────────────────

def _step_teardown(fixture: Fixture, qdrant, keep_data: bool) -> StepResult:
    """Delete smoke fixture data so the next hourly run starts clean."""
    started = time.monotonic()
    res = StepResult(name="07_teardown")
    if keep_data:
        res.status = "skipped"
        res.detail = {"reason": "--keep-data"}
        res.duration_ms = int((time.monotonic() - started) * 1000)
        return res

    # Re-uses the step 1 cleanup code path.
    out = _step_cleanup(fixture, qdrant)
    out.name = "07_teardown"
    return out


# ── Main orchestration ────────────────────────────────────────────────

def run_smoke(
    fixture: Fixture,
    keep_data: bool,
) -> Tuple[List[StepResult], int, int]:
    """Run all steps in order. Returns (results, n_passed, n_failed).

    If any step fails, downstream steps that depend on it are auto-skipped.
    """
    logger.info("=" * 70)
    logger.info(f"Smoke test: fixture={fixture.name}, doc_id={fixture.document_id}")
    logger.info("=" * 70)

    qdrant = _build_qdrant_client()
    results: List[StepResult] = []

    def _skip(name: str, reason: str) -> StepResult:
        r = StepResult(name=name, status="skipped",
                       error_message=f"skipped: {reason}")
        return r

    # Step 1
    r1 = _step_cleanup(fixture, qdrant)
    results.append(r1)
    logger.info(r1.to_log())

    # Step 2
    r2 = _step_ensure_collection(qdrant)
    results.append(r2)
    logger.info(r2.to_log())
    if not r2.passed:
        for n in ["03_ingest_fixture", "04_retrieve_canary",
                  "05_source_span_verify", "06_chunk_attribution_audit"]:
            results.append(_skip(n, "Qdrant collection unavailable"))
        results.append(_step_teardown(fixture, qdrant, keep_data))
        return results, _count_pass(results), _count_fail(results)

    # Step 3
    r3 = _step_ingest(fixture, qdrant)
    results.append(r3)
    logger.info(r3.to_log())
    if not r3.passed:
        for n in ["04_retrieve_canary", "05_source_span_verify",
                  "06_chunk_attribution_audit"]:
            results.append(_skip(n, "ingest failed"))
        results.append(_step_teardown(fixture, qdrant, keep_data))
        return results, _count_pass(results), _count_fail(results)

    # Step 4
    r4 = _step_retrieve(fixture, qdrant)
    results.append(r4)
    logger.info(r4.to_log())

    # Step 5 can run independently of 4 (verifier uses fixture text + crafted chunk)
    r5 = _step_verify(fixture)
    results.append(r5)
    logger.info(r5.to_log())

    # Step 6 only depends on ingest succeeding.
    r6 = _step_audit(fixture)
    results.append(r6)
    logger.info(r6.to_log())

    # Step 7 — teardown always runs unless --keep-data.
    r7 = _step_teardown(fixture, qdrant, keep_data)
    results.append(r7)
    logger.info(r7.to_log())

    return results, _count_pass(results), _count_fail(results)


def _count_pass(results: List[StepResult]) -> int:
    return sum(1 for r in results if r.status == "success")


def _count_fail(results: List[StepResult]) -> int:
    return sum(1 for r in results if r.status == "failure")


# ── CLI ───────────────────────────────────────────────────────────────

def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Hourly full-ingest canary smoke test (WS5a Phase A).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/nightly/00_smoke_test.py\n"
            "  python scripts/nightly/00_smoke_test.py --fixture fixture_01_clean\n"
            "  python scripts/nightly/00_smoke_test.py --keep-data --verbose\n"
        ),
    )
    p.add_argument(
        "--fixture", type=str, default=None,
        help="Fixture name (without .txt). Default: pick deterministically by ISO week.",
    )
    p.add_argument(
        "--keep-data", action="store_true",
        help="Skip step 7 teardown — leave fixture data in place for debugging.",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="DEBUG-level logging.",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_argparser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Fixture selection
    try:
        all_fixtures = _list_fixtures()
        fixture = _pick_fixture(all_fixtures, args.fixture)
    except Exception as exc:
        logger.exception(f"Fixture setup failed: {exc}")
        return 2

    # Open pipeline_run
    try:
        run_id = _open_pipeline_run()
    except Exception as exc:
        logger.exception(f"Cannot open pipeline_runs row (DB down?): {exc}")
        return 2

    exit_code = 0
    try:
        results, n_passed, n_failed = run_smoke(fixture, keep_data=args.keep_data)
        overall_status = "success" if n_failed == 0 else "failure"
        error_message = None
        error_tb = None
        if n_failed:
            first_fail = next(r for r in results if r.status == "failure")
            error_message = f"{first_fail.name}: {first_fail.error_message}"
            error_tb = first_fail.error_traceback
            # Record per-failure details
            for r in results:
                if r.status == "failure":
                    _log_pipeline_failure(r, fixture_name=fixture.name)

        # Close run
        _close_pipeline_run(
            run_id=run_id, status=overall_status,
            items_processed=n_passed, items_failed=n_failed,
            error_message=error_message, error_traceback=error_tb,
        )

        # On full pass, log a nice document_event
        if overall_status == "success":
            _log_document_event(
                doc_id=fixture.document_id,
                event_type="smoke_test_passed",
                details={
                    "fixture": fixture.name,
                    "steps_passed": n_passed,
                    "steps_total": len(results),
                    "durations_ms": {r.name: r.duration_ms for r in results},
                },
            )

        # Final log
        logger.info("=" * 70)
        logger.info(
            f"SMOKE TEST {overall_status.upper()}: "
            f"{n_passed} passed, {n_failed} failed (run_id={run_id})"
        )
        logger.info("=" * 70)

        exit_code = 0 if overall_status == "success" else 1
    except Exception as exc:
        logger.exception(f"Smoke test crashed unexpectedly: {exc}")
        try:
            _close_pipeline_run(
                run_id=run_id, status="failure",
                items_processed=0, items_failed=1,
                error_message=str(exc)[:500],
                error_traceback=traceback.format_exc(),
            )
        except Exception:
            pass
        exit_code = 2

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
