#!/usr/bin/env python3
"""
WS5a Phase A — Chunk attribution audit (READ-ONLY)
===================================================

Detects the most dangerous failure mode on the platform: a chunk whose
``document_id`` (in PostgreSQL ``document_chunks`` and/or in the Qdrant
``notulen_chunks`` payload) does not correspond to the document whose
``documents.content`` the chunk text actually came from.

Background
----------
A false-positive surfaced where the MCP search for "parkeertarieven"
returned ``doc 246823`` ("GroenLinks — Alternatieve kaderbrief 2011") with
a parking-tariff snippet, yet ``lees_fragment`` showed the document contained
no parking content. Either:
  - the chunk row's ``document_id`` got rewritten to point at the wrong doc,
  - the Qdrant payload ``document_id`` drifted from the Postgres row, or
  - the chunk content was overwritten on another row's ``document_id``.

This script classifies every sampled chunk:
  - ``exact``     chunk ``content`` appears verbatim inside
                  ``documents.content`` for its ``document_id``
  - ``substring`` chunk ``content`` appears after whitespace normalization
                  (handles newline/whitespace drift from chunker rewrites)
  - ``fuzzy``     >= 80% token overlap, not contiguous — likely OCR
                  artifacts or restyled whitespace, NOT a bug
  - ``mismatch``  < 80% token overlap — THIS IS THE BUG
  - ``missing_doc`` chunk references a ``document_id`` that does not exist
  - ``empty_doc``   referenced document has NULL / empty ``content``
                    (cannot verify, reported as warning not error)

Also cross-checks Qdrant: fetches the chunk's Qdrant point (keyed by
``compute_point_id(document_id, db_id)``) and compares:
  - Qdrant payload ``document_id`` vs Postgres ``document_id``
  - Qdrant payload ``content`` vs Postgres ``document_chunks.content``
Any drift is reported.

CONSTRAINTS (WS5a Phase A)
--------------------------
* READ-ONLY. No INSERT / UPDATE / DELETE / DDL anywhere.
* Coexists with WS6 Phase 3 (Gemini summary writes) and WS11 Phase 6
  (autovacuum on ``document_chunks``) — uses short SELECTs, no long
  transactions, never holds a row lock.
* Uses ``services.db_pool.get_connection`` — never opens raw psycopg2
  connections directly.
* Assumes the dev SSH tunnel is up (``./scripts/dev_tunnel.sh --bg``),
  so Postgres is reachable at 127.0.0.1:5432 and Qdrant at 127.0.0.1:6333.

Exit codes
----------
* 0  no ``mismatch`` rows found
* 1  one or more ``mismatch`` rows found (smoke-test gate)
* 2  operational error (tunnel down, schema drift, etc.)

Usage
-----
    python scripts/audit_chunk_attribution.py                    # 10k-row sample
    python scripts/audit_chunk_attribution.py --full             # audit everything
    python scripts/audit_chunk_attribution.py --doc-id 246823    # hand-check one doc
    python scripts/audit_chunk_attribution.py --limit 500 --verbose
    python scripts/audit_chunk_attribution.py --output reports/my_audit.csv
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# dotenv so DATABASE_URL / QDRANT_URL resolve the same way as the app
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

from services.db_pool import get_connection  # shared pool — required
from services.embedding import QDRANT_COLLECTION, compute_point_id


logger = logging.getLogger("audit_chunk_attribution")


# ---------------------------------------------------------------------------
# Classification primitives
# ---------------------------------------------------------------------------

# Match types — ordered by severity (for CSV + smoke gate).
MATCH_EXACT = "exact"
MATCH_SUBSTRING = "substring"
MATCH_FUZZY = "fuzzy"
MATCH_MISMATCH = "mismatch"
MATCH_MISSING_DOC = "missing_doc"
MATCH_EMPTY_DOC = "empty_doc"

SEVERITY = {
    MATCH_EXACT: "ok",
    MATCH_SUBSTRING: "ok",
    MATCH_FUZZY: "warning",
    MATCH_MISMATCH: "error",
    MATCH_MISSING_DOC: "error",
    MATCH_EMPTY_DOC: "warning",
}

# Regex that strips all whitespace runs down to a single space,
# so "foo\n\nbar" and "foo bar" compare equal.
_WS_RE = re.compile(r"\s+")
# Token regex — Dutch-friendly (letters incl. accented, digits).
_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÿ0-9]+", flags=re.UNICODE)

# Threshold: below this, it's a bug.
FUZZY_THRESHOLD = 0.80


def _normalize_ws(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(text)}


def classify_attribution(chunk_content: str, doc_content: Optional[str]) -> tuple[str, float]:
    """
    Classify a chunk against its referenced document's content.
    Returns (match_type, token_overlap_ratio).

    Never writes anywhere, never raises — callers get a classification even
    for degenerate inputs.
    """
    if doc_content is None:
        return MATCH_MISSING_DOC, 0.0
    if not chunk_content or not chunk_content.strip():
        # Empty chunk — can't classify; treat as ok (not our class of bug).
        return MATCH_EXACT, 1.0
    if not doc_content.strip():
        return MATCH_EMPTY_DOC, 0.0

    # 1. Exact substring (fast path)
    if chunk_content in doc_content:
        return MATCH_EXACT, 1.0

    # 2. Whitespace-normalized substring
    chunk_n = _normalize_ws(chunk_content)
    doc_n = _normalize_ws(doc_content)
    if chunk_n and chunk_n in doc_n:
        return MATCH_SUBSTRING, 1.0

    # 3. Token overlap
    chunk_tokens = _tokens(chunk_content)
    if not chunk_tokens:
        return MATCH_EXACT, 1.0
    doc_tokens = _tokens(doc_content)
    overlap = len(chunk_tokens & doc_tokens) / len(chunk_tokens)

    if overlap >= FUZZY_THRESHOLD:
        return MATCH_FUZZY, overlap
    return MATCH_MISMATCH, overlap


# ---------------------------------------------------------------------------
# Row record
# ---------------------------------------------------------------------------

@dataclass
class AuditRow:
    chunk_id: int
    document_id: str
    source: str  # postgres | qdrant | both
    match_type: str
    token_overlap: float
    chunk_preview: str
    doc_title: str
    severity: str
    qdrant_document_id: Optional[str] = None
    qdrant_content_matches: Optional[bool] = None
    notes: str = ""

    def to_csv_row(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "source": self.source,
            "match_type": self.match_type,
            "token_overlap": f"{self.token_overlap:.3f}",
            "chunk_preview": self.chunk_preview,
            "doc_title": self.doc_title,
            "severity": self.severity,
            "qdrant_document_id": self.qdrant_document_id or "",
            "qdrant_content_matches": (
                "" if self.qdrant_content_matches is None
                else str(self.qdrant_content_matches).lower()
            ),
            "notes": self.notes,
        }


CSV_COLUMNS = [
    "chunk_id",
    "document_id",
    "source",
    "match_type",
    "token_overlap",
    "chunk_preview",
    "doc_title",
    "severity",
    "qdrant_document_id",
    "qdrant_content_matches",
    "notes",
]


# ---------------------------------------------------------------------------
# Qdrant lookup (lazy — only instantiated if we're checking vectors)
# ---------------------------------------------------------------------------

class _QdrantLookup:
    """Thin read-only wrapper — never writes, never creates collections."""

    def __init__(self, url: Optional[str] = None, api_key: Optional[str] = None):
        from qdrant_client import QdrantClient
        qdrant_url = url or os.getenv("QDRANT_URL", "http://localhost:6333")
        qdrant_api_key = api_key or os.getenv("QDRANT_API_KEY")
        self.client = QdrantClient(
            url=qdrant_url,
            api_key=qdrant_api_key,
            timeout=30,
        )
        self.collection = QDRANT_COLLECTION

    def fetch_payloads(self, point_ids: list[int]) -> dict[int, dict]:
        """Return {point_id: payload_dict} for the subset that exists."""
        if not point_ids:
            return {}
        try:
            pts = self.client.retrieve(
                collection_name=self.collection,
                ids=point_ids,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as exc:
            logger.warning("Qdrant retrieve failed for %d ids: %s", len(point_ids), exc)
            return {}
        out = {}
        for p in pts:
            out[p.id] = p.payload or {}
        return out


# ---------------------------------------------------------------------------
# Postgres helpers — short SELECTs, no locks
# ---------------------------------------------------------------------------

def _corpus_stats() -> dict:
    """
    Read-only: estimate chunk and document counts for the header log line.

    Uses ``pg_class.reltuples`` (planner statistics) rather than ``COUNT(*)``:
    an exact count over 1.7M rows takes > 60s on prod and would trip
    db_pool's ``statement_timeout=60000``. The estimate is updated by
    autovacuum (WS11 Phase 6) and is accurate to within a few percent.
    """
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT relname, reltuples::bigint
            FROM pg_class
            WHERE relname IN ('document_chunks', 'documents')
              AND relnamespace = (SELECT oid FROM pg_namespace WHERE nspname = 'public')
            """
        )
        stats = {name: n for name, n in cur.fetchall()}
        cur.close()
    return {
        "chunks": stats.get("document_chunks", -1),
        "documents": stats.get("documents", -1),
    }


def _iter_chunks(
    limit: Optional[int],
    doc_id: Optional[str],
    batch_size: int = 500,
) -> Iterable[list[tuple]]:
    """
    Yield batches of (chunk_id, document_id, chunk_content) tuples.

    Uses a server-side cursor so we don't buffer 1.7M rows in memory when
    ``--full`` is passed. Always read-only, uses ``ORDER BY id`` so runs
    are reproducible.
    """
    with get_connection() as conn:
        # Named cursor = server-side streaming; autocommit off is fine since we
        # only ever SELECT.
        cur = conn.cursor(name="audit_attribution_stream")
        cur.itersize = batch_size

        if doc_id:
            cur.execute(
                """
                SELECT id, document_id, content
                FROM document_chunks
                WHERE document_id = %s
                ORDER BY id
                """,
                (doc_id,),
            )
        elif limit is not None:
            # Random sample — uses TABLESAMPLE for the stats version, but we
            # need deterministic subset with content, so just a plain LIMIT
            # over an indexed ORDER BY for reproducibility.
            cur.execute(
                """
                SELECT id, document_id, content
                FROM document_chunks
                ORDER BY id
                LIMIT %s
                """,
                (limit,),
            )
        else:
            cur.execute(
                """
                SELECT id, document_id, content
                FROM document_chunks
                ORDER BY id
                """
            )

        batch: list[tuple] = []
        for row in cur:
            batch.append(row)
            if len(batch) >= batch_size:
                yield batch
                batch = []
        if batch:
            yield batch
        cur.close()


def _fetch_doc_bulk(doc_ids: set[str]) -> dict[str, tuple[str, Optional[str]]]:
    """
    Return {document_id: (name, content)}. Missing ids are absent from the map.
    Uses a single SELECT ... = ANY(%s) — fast over a pk.
    """
    if not doc_ids:
        return {}
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, COALESCE(name, ''), content
            FROM documents
            WHERE id = ANY(%s)
            """,
            (list(doc_ids),),
        )
        rows = cur.fetchall()
        cur.close()
    return {r[0]: (r[1], r[2]) for r in rows}


# ---------------------------------------------------------------------------
# Core audit
# ---------------------------------------------------------------------------

def run_audit(
    limit: Optional[int],
    doc_id: Optional[str],
    check_qdrant: bool = True,
) -> tuple[list[AuditRow], dict]:
    """
    Execute the audit. Returns (rows, summary).

    summary = {
        'total': int,
        'by_match': {match_type: count},
        'by_severity': {severity: count},
    }
    """
    qdrant = None
    if check_qdrant:
        try:
            qdrant = _QdrantLookup()
        except Exception as exc:
            logger.warning(
                "Qdrant client init failed (%s) — continuing with Postgres-only audit", exc,
            )
            qdrant = None

    rows: list[AuditRow] = []
    counts_match: dict[str, int] = {}
    counts_sev: dict[str, int] = {}

    for batch in _iter_chunks(limit=limit, doc_id=doc_id):
        # Bulk-fetch docs referenced in this batch.
        doc_ids_in_batch = {r[1] for r in batch if r[1] is not None}
        docs = _fetch_doc_bulk(doc_ids_in_batch)

        # Qdrant lookup for this batch (optional).
        qdrant_payloads_by_point: dict[int, dict] = {}
        if qdrant is not None:
            point_ids = [compute_point_id(str(r[1]), r[0]) for r in batch if r[1]]
            # batch retrieve is faster than per-row
            qdrant_payloads_by_point = qdrant.fetch_payloads(point_ids)

        for chunk_id, document_id, chunk_content in batch:
            doc_name, doc_content = docs.get(document_id, ("", None))
            match_type, overlap = classify_attribution(chunk_content or "", doc_content)

            q_doc_id: Optional[str] = None
            q_content_matches: Optional[bool] = None
            source = "postgres"
            notes = ""

            if qdrant is not None and document_id:
                point_id = compute_point_id(str(document_id), chunk_id)
                payload = qdrant_payloads_by_point.get(point_id)
                if payload is None:
                    notes = "qdrant_point_missing"
                else:
                    source = "both"
                    q_doc_id = str(payload.get("document_id", "")) or None
                    q_content = payload.get("content") or ""
                    # Compare whitespace-normalised — chunker round-trips may
                    # differ on trailing newlines.
                    q_content_matches = (
                        _normalize_ws(q_content) == _normalize_ws(chunk_content or "")
                    )
                    # If Qdrant payload says a DIFFERENT document_id than Postgres,
                    # that is always a mismatch — override Postgres classification.
                    if q_doc_id and q_doc_id != str(document_id):
                        match_type = MATCH_MISMATCH
                        notes = (notes + ";qdrant_doc_id_drift").strip(";")

            preview = _normalize_ws(chunk_content or "")[:180]
            rows.append(
                AuditRow(
                    chunk_id=chunk_id,
                    document_id=str(document_id) if document_id else "",
                    source=source,
                    match_type=match_type,
                    token_overlap=overlap,
                    chunk_preview=preview,
                    doc_title=doc_name[:160],
                    severity=SEVERITY.get(match_type, "warning"),
                    qdrant_document_id=q_doc_id,
                    qdrant_content_matches=q_content_matches,
                    notes=notes,
                )
            )
            counts_match[match_type] = counts_match.get(match_type, 0) + 1
            sev = SEVERITY.get(match_type, "warning")
            counts_sev[sev] = counts_sev.get(sev, 0) + 1

    summary = {
        "total": len(rows),
        "by_match": counts_match,
        "by_severity": counts_sev,
    }
    return rows, summary


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def write_csv(rows: list[AuditRow], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.to_csv_row())


def print_summary(rows: list[AuditRow], summary: dict, doc_id_mode: bool) -> None:
    logger.info("=" * 70)
    logger.info("AUDIT SUMMARY")
    logger.info("=" * 70)
    logger.info("Total chunks examined: %d", summary["total"])
    for match_type in [
        MATCH_EXACT, MATCH_SUBSTRING, MATCH_FUZZY,
        MATCH_MISMATCH, MATCH_MISSING_DOC, MATCH_EMPTY_DOC,
    ]:
        n = summary["by_match"].get(match_type, 0)
        logger.info("  %-14s %d", match_type, n)

    mismatches = [r for r in rows if r.match_type == MATCH_MISMATCH]
    if mismatches:
        logger.error("=" * 70)
        logger.error("TOP %d MISMATCHES (chunk -> document attribution drift)", min(20, len(mismatches)))
        logger.error("=" * 70)
        for r in mismatches[:20]:
            logger.error(
                "chunk=%d  doc=%s  overlap=%.2f  qdrant_doc=%s  notes=%s",
                r.chunk_id, r.document_id, r.token_overlap,
                r.qdrant_document_id or "-", r.notes or "-",
            )
            logger.error("    doc_title: %s", r.doc_title)
            logger.error("    preview  : %s", r.chunk_preview)

    if doc_id_mode:
        logger.info("=" * 70)
        logger.info("PER-CHUNK DETAIL (--doc-id mode)")
        logger.info("=" * 70)
        for r in rows:
            logger.info(
                "[%s] chunk=%d overlap=%.2f q_doc=%s q_content_match=%s",
                r.match_type, r.chunk_id, r.token_overlap,
                r.qdrant_document_id or "-",
                r.qdrant_content_matches if r.qdrant_content_matches is not None else "-",
            )
            logger.info("    preview: %s", r.chunk_preview)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Read-only chunk attribution audit (WS5a Phase A).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--limit", type=int, default=10_000,
        help="Sample size (default 10000). Ignored when --full or --doc-id is used.",
    )
    p.add_argument(
        "--full", action="store_true",
        help="Audit every chunk in document_chunks (overrides --limit).",
    )
    p.add_argument(
        "--doc-id", type=str, default=None,
        help="Inspect a single document — prints every chunk's classification.",
    )
    p.add_argument(
        "--output", type=str,
        default="reports/chunk_attribution_audit.csv",
        help="CSV output path (default reports/chunk_attribution_audit.csv).",
    )
    p.add_argument(
        "--no-qdrant", action="store_true",
        help="Skip Qdrant payload cross-check (Postgres-only audit).",
    )
    p.add_argument(
        "--dry-run", action="store_true", default=True,
        help="No-op flag — the audit is READ-ONLY by construction. Present "
             "for interface symmetry with the upcoming Phase B repair script.",
    )
    p.add_argument(
        "--verbose", action="store_true",
        help="DEBUG-level logging.",
    )
    return p


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_argparser()
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Paranoia: make absolutely sure this is a read-only run.
    if not args.dry_run:
        logger.error("Refusing to run: --dry-run=False is not supported in this script.")
        return 2

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path

    # Corpus stats (header)
    try:
        stats = _corpus_stats()
        logger.info(
            "Corpus: %s documents, %s chunks",
            f"{stats['documents']:,}", f"{stats['chunks']:,}",
        )
    except Exception as exc:
        logger.error("Cannot reach PostgreSQL (is the SSH tunnel up?): %s", exc)
        return 2

    # Mode determination
    if args.doc_id:
        logger.info("Mode: --doc-id %s (every chunk for this doc)", args.doc_id)
        limit = None
    elif args.full:
        logger.info("Mode: --full (auditing entire document_chunks table)")
        limit = None
    else:
        logger.info("Mode: sample of %d rows", args.limit)
        limit = args.limit

    try:
        rows, summary = run_audit(
            limit=limit,
            doc_id=args.doc_id,
            check_qdrant=not args.no_qdrant,
        )
    except Exception as exc:
        logger.exception("Audit failed: %s", exc)
        return 2

    try:
        write_csv(rows, output_path)
        logger.info("CSV written: %s (%d rows)", output_path, len(rows))
    except Exception as exc:
        logger.error("CSV write failed: %s", exc)
        return 2

    print_summary(rows, summary, doc_id_mode=bool(args.doc_id))

    # Exit-code gate: any mismatch => non-zero (so CI / smoke can fail).
    n_mismatch = summary["by_match"].get(MATCH_MISMATCH, 0)
    n_missing = summary["by_match"].get(MATCH_MISSING_DOC, 0)
    if n_mismatch or n_missing:
        logger.error(
            "FAIL: %d mismatch + %d missing_doc rows (see %s)",
            n_mismatch, n_missing, output_path,
        )
        return 1
    logger.info("OK: no mismatches.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
