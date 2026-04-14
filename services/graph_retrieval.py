"""
Graph retrieval service — the fifth retrieval stream.

This module walks the knowledge graph stored in PostgreSQL tables
``kg_entities``, ``kg_relationships``, and ``kg_mentions`` to answer
multi-hop queries that the dense/BM25 streams cannot (indieners of a
motie, coalition membership at a given date, street-to-wijk locality,
etc.). It is Phase B of WS1 in ``docs/handoffs/WS1_GRAPHRAG.md``.

Public API (four functions — deliberate, minimal surface):

    extract_query_entities(query)        — str -> list[Entity]
    walk(seed_entity_ids, max_hops=2)    — list[int] -> list[Path]
    score_paths(paths, query_intent)     — list[Path] -> list[ScoredPath]
    hydrate_chunks(entity_ids, gemeente) — list[int] -> list[RetrievedChunk]

All four functions are safe to call during Phase 0 even before the KG
has been enriched: ``extract_query_entities`` falls back to gazetteer +
politician alias matching when no entities resolve, ``walk`` returns an
empty list on an empty seed set, ``hydrate_chunks`` returns an empty
list when ``kg_mentions`` has no rows for the requested entity ids.

Schema facts (verified against live DB 2026-04-11 — the handoff had
several wrong column names, documented in docs/handoffs/WS1_GRAPHRAG.md):

- ``kg_entities(id, type, name, metadata jsonb, created_at)`` — UNIQUE(type, name)
- ``kg_relationships(id, source_entity_id, target_entity_id, relation_type,
  document_id, chunk_id, confidence, quote, metadata jsonb, created_at)``
- ``kg_mentions(id, entity_id, chunk_id, raw_mention, created_at)``
- ``document_chunks(id, content, document_id, title, child_id, chunk_type,
  key_entities text[], ...)``

The ``walk`` function uses a recursive CTE over ``kg_relationships`` and
is hard-capped at 2 hops for v0.2 — combinatorial blowup is the biggest
risk flagged in the handoff's risk table.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence

from services.db_pool import get_connection

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Entity:
    """A resolved entity from the knowledge graph."""
    id: Optional[int]           # kg_entities.id; None if only matched by name
    type: str                   # e.g. 'Person', 'Location', 'Organization', 'Motie'
    name: str                   # canonical name as stored in kg_entities.name
    confidence: float = 1.0     # 1.0 for exact match, lower for fuzzy
    source: str = "unknown"     # 'politician_registry' | 'gazetteer' | 'ner' | 'kg_entities'


@dataclass
class Path:
    """A walk through the knowledge graph. Ordered list of nodes + edges."""
    node_ids: List[int]               # e.g. [seed_id, hop1_id, hop2_id]
    edge_types: List[str]             # one per hop (len = len(node_ids) - 1)
    edge_confidences: List[float]     # per-edge confidence from kg_relationships
    edge_sources: List[str] = field(default_factory=list)  # metadata.source per edge
    total_confidence: float = 1.0     # product of edge confidences × hop penalty


@dataclass
class ScoredPath:
    """A Path enriched with intent-scoring."""
    path: Path
    score: float                      # final score, higher = more relevant


@dataclass
class GraphChunk:
    """
    Chunk returned by hydrate_chunks — structurally compatible with
    services.rag_service.RetrievedChunk so rag_service.py can fold the
    5th stream into retrieve_parallel_context without an adapter layer.
    """
    chunk_id: Any
    document_id: str
    title: str
    content: str
    similarity_score: float = 1.0
    questions: Optional[List[str]] = None
    child_id: Optional[int] = None
    stream_type: Optional[str] = "graph"
    start_date: Optional[str] = None
    entity_ids: List[int] = field(default_factory=list)  # which seeds hit this chunk


# ---------------------------------------------------------------------------
# Config — env-gated thresholds so the 5th stream can be shipped in Phase 0
# with an empty KG and then enabled post-enrichment via a single env var.
# ---------------------------------------------------------------------------

def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    val = os.environ.get(name, "")
    if not val:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


# Minimum kg_relationships edge count before the graph_walk stream is considered
# "live". Below this, rag_service.py will skip the stream entirely so an empty
# KG can't regress retrieval. Flip once Phase 1 enrichment lands.
GRAPH_WALK_MIN_EDGES = _env_int("GRAPH_WALK_MIN_EDGES", 200_000)

# Hard master switch. When False (default until Phase 1), graph_walk always
# returns []. Set GRAPH_WALK_ENABLED=1 after Phase 1 quality audit passes.
GRAPH_WALK_ENABLED = _env_flag("GRAPH_WALK_ENABLED", default=False)

# Virtual-notulen killswitch (added 2026-04-14 for WS1 Phase A bis VN provenance).
# Mirrors the existing rag_service.py INCLUDE_VIRTUAL_NOTULEN env var. When False,
# retrieve_via_graph passes exclude_sources=['virtual_notulen'] to both walk() and
# hydrate_chunks() so no VN-derived edge or chunk leaks into graph_walk results.
# Default True — we include VN by default (max recall) and let downstream tools
# flip the switch per use case (e.g. press-moment strict mode).
INCLUDE_VIRTUAL_NOTULEN = _env_flag("INCLUDE_VIRTUAL_NOTULEN", default=True)

# v0.2 design constant — do NOT raise without a benchmark pass (handoff risk row).
MAX_HOPS_V02 = 2

# How many hydrated chunks to return from hydrate_chunks. Rerank is a caller
# concern; this just caps the SQL fanout.
HYDRATE_DEFAULT_LIMIT = 30

# Default score multiplier applied to paths containing VN-derived edges. 0.7 per
# VN edge, compounded. So a 2-hop path with 1 VN edge gets 0.7x; with both edges
# from VN gets 0.49x. Production callers can override via score_paths(vn_penalty=...).
_DEFAULT_VN_PENALTY = 0.7

# The source strings that identify VN-derived edges. Written by
# link_motie_to_notulen.py when the target chunk is from a VN meeting.
_VN_SOURCE_VALUES = frozenset({"virtual_notulen"})


# ---------------------------------------------------------------------------
# Cached readiness check — rag_service.py calls this once at stream dispatch
# time so an empty KG short-circuits before touching Flair or running a CTE.
# ---------------------------------------------------------------------------

_readiness_cache: Optional[bool] = None
_readiness_lock = threading.Lock()


def is_graph_walk_ready(force_recheck: bool = False) -> bool:
    """
    True iff (a) GRAPH_WALK_ENABLED is set, and (b) kg_relationships has at
    least GRAPH_WALK_MIN_EDGES rows. Cached after first call — set
    force_recheck=True to bust the cache after a Phase 1 run.
    """
    global _readiness_cache
    if not GRAPH_WALK_ENABLED:
        return False
    if _readiness_cache is not None and not force_recheck:
        return _readiness_cache
    with _readiness_lock:
        if _readiness_cache is not None and not force_recheck:
            return _readiness_cache
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM kg_relationships")
                    count = cur.fetchone()[0] or 0
            _readiness_cache = count >= GRAPH_WALK_MIN_EDGES
            logger.info(
                "[graph_retrieval] readiness check: kg_relationships=%d, threshold=%d, ready=%s",
                count, GRAPH_WALK_MIN_EDGES, _readiness_cache,
            )
            return _readiness_cache
        except Exception:
            logger.exception("[graph_retrieval] readiness check failed — treating as not ready")
            _readiness_cache = False
            return False


# ---------------------------------------------------------------------------
# 1. extract_query_entities
# ---------------------------------------------------------------------------

# Simple word-boundary matcher. We deliberately do NOT ship Flair inside this
# module — query-time NER latency is unacceptable on the hot path. Instead we
# rely on the gazetteer + politician registry + the already-enriched
# kg_entities table, all of which are fast exact-name lookups.

_POLITICIAN_CACHE: Optional[dict] = None
_GAZETTEER_CACHE: Optional[dict] = None
_CACHE_LOCK = threading.Lock()


def _load_politician_registry() -> dict:
    """Returns {lowercase_name_or_alias: {canonical_name, partij, surname}}."""
    global _POLITICIAN_CACHE
    if _POLITICIAN_CACHE is not None:
        return _POLITICIAN_CACHE
    with _CACHE_LOCK:
        if _POLITICIAN_CACHE is not None:
            return _POLITICIAN_CACHE
        mapping: dict = {}
        try:
            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT canonical_name, surname, partij, aliases FROM politician_registry"
                    )
                    for canonical, surname, partij, aliases in cur.fetchall():
                        entry = {
                            "canonical_name": canonical,
                            "surname": surname,
                            "partij": partij,
                        }
                        if canonical:
                            mapping[canonical.lower()] = entry
                        if surname:
                            mapping.setdefault(surname.lower(), entry)
                        if aliases:
                            for alias in aliases:
                                if alias:
                                    mapping.setdefault(alias.lower(), entry)
        except Exception:
            logger.exception("[graph_retrieval] failed to load politician_registry; using empty map")
        _POLITICIAN_CACHE = mapping
        return mapping


def _load_gazetteer() -> dict:
    """Returns {lowercase_term: canonical_term} from domain_gazetteer.json."""
    global _GAZETTEER_CACHE
    if _GAZETTEER_CACHE is not None:
        return _GAZETTEER_CACHE
    with _CACHE_LOCK:
        if _GAZETTEER_CACHE is not None:
            return _GAZETTEER_CACHE
        from pathlib import Path
        path = Path(__file__).resolve().parent.parent / "data" / "knowledge_graph" / "domain_gazetteer.json"
        mapping: dict = {}
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            for key in ("organisations", "projects", "programmes", "locations", "committees", "rotterdam_places"):
                for term in data.get(key, []):
                    if term and len(term) >= 3:
                        mapping.setdefault(term.lower(), term)
        except FileNotFoundError:
            logger.warning("[graph_retrieval] domain_gazetteer.json not found at %s", path)
        except Exception:
            logger.exception("[graph_retrieval] failed to load domain_gazetteer.json")
        _GAZETTEER_CACHE = mapping
        return mapping


def _resolve_entity_id_by_name(name: str, preferred_type: Optional[str] = None) -> Optional[tuple]:
    """
    Look up a single kg_entities.id by name (case-insensitive). If multiple
    rows match, prefer ``preferred_type`` when provided, else return the first
    row. Returns (id, type) or None.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                if preferred_type:
                    cur.execute(
                        "SELECT id, type FROM kg_entities WHERE LOWER(name) = LOWER(%s) "
                        "ORDER BY CASE WHEN type = %s THEN 0 ELSE 1 END LIMIT 1",
                        (name, preferred_type),
                    )
                else:
                    cur.execute(
                        "SELECT id, type FROM kg_entities WHERE LOWER(name) = LOWER(%s) LIMIT 1",
                        (name,),
                    )
                row = cur.fetchone()
                return (int(row[0]), row[1]) if row else None
    except Exception:
        logger.exception("[graph_retrieval] _resolve_entity_id_by_name failed for %r", name)
        return None


def extract_query_entities(query: str) -> List[Entity]:
    """
    Resolve a free-text query into Entity rows. Three passes in order:

    1. politician_registry — matches full names, surnames, and aliases.
    2. domain_gazetteer.json — organisations, projects, locations, committees, etc.
    3. No NER — deliberate: keeping query-time latency under 100ms is worth
       more than the marginal recall gain. Flair NER runs offline in the
       enrichment pass; at query-time we trust that pre-enriched surface.

    For each pass, entities that also exist as rows in kg_entities get their
    id populated so walk() can use them as seeds without a second SQL round-trip.
    Returns an empty list on empty query. Never raises.
    """
    if not query or not query.strip():
        return []

    query_lower = query.lower()
    found: List[Entity] = []
    seen_names: set = set()

    # Pass 1: politicians
    pol_map = _load_politician_registry()
    for name_key, entry in pol_map.items():
        # Only match multi-char surnames to avoid "van", "de", etc.
        if len(name_key) < 3:
            continue
        if re.search(rf"\b{re.escape(name_key)}\b", query_lower):
            canonical = entry["canonical_name"]
            if canonical.lower() in seen_names:
                continue
            seen_names.add(canonical.lower())
            resolved = _resolve_entity_id_by_name(canonical, preferred_type="Person")
            found.append(Entity(
                id=resolved[0] if resolved else None,
                type="Person",
                name=canonical,
                confidence=1.0,
                source="politician_registry",
            ))

    # Pass 2: gazetteer
    gaz = _load_gazetteer()
    for term_lower, canonical in gaz.items():
        if canonical.lower() in seen_names:
            continue
        if re.search(rf"\b{re.escape(term_lower)}\b", query_lower):
            seen_names.add(canonical.lower())
            resolved = _resolve_entity_id_by_name(canonical)
            found.append(Entity(
                id=resolved[0] if resolved else None,
                type=resolved[1] if resolved else "Unknown",
                name=canonical,
                confidence=0.9,
                source="gazetteer",
            ))

    logger.debug(
        "[graph_retrieval] extract_query_entities(%r) -> %d entities (%d with ids)",
        query[:60], len(found), sum(1 for e in found if e.id is not None),
    )
    return found


# ---------------------------------------------------------------------------
# 2. walk
# ---------------------------------------------------------------------------


def walk(
    seed_entity_ids: Sequence[int],
    max_hops: int = 2,
    edge_types: Optional[Sequence[str]] = None,
    path_limit: int = 200,
    exclude_sources: Optional[Sequence[str]] = None,
    min_source_quality: float = 0.0,
) -> List[Path]:
    """
    Breadth-first walk through ``kg_relationships`` starting from
    ``seed_entity_ids``, capped at ``max_hops`` (hard limit = MAX_HOPS_V02).

    Traversal is bidirectional — an edge ``A --LID_VAN--> B`` can be walked
    from A to B OR from B to A. This matters because ``LID_VAN`` is stored
    person -> party; walking only forward would miss "which persons belong
    to party X?".

    Cycles are broken by a visited-set check on node ids. Path explosion is
    bounded by ``path_limit`` (default 200) — we keep the path_limit best by
    edge confidence and drop the rest. Set to a higher value for diagnostic
    runs.

    The recursive CTE keeps the entire walk inside a single SQL round-trip,
    which is ~10× faster than iterating in Python for 2-hop walks with a
    kg_relationships table of ~500K rows.

    **VN provenance filters (added 2026-04-14 for WS1 Phase A bis):**
    - ``exclude_sources``: list of ``metadata->>'source'`` values to exclude from
      the walk. Pass ``['virtual_notulen']`` for strict production mode. Edges
      with ``NULL`` source are kept (backwards-compat with pre-backfill data).
    - ``min_source_quality``: minimum ``metadata->>'source_quality'`` for an
      edge to be walked. Default 0.0 (no floor). Edges with ``NULL`` quality
      are treated as 1.0 so legacy high-trust edges are not excluded.
    """
    if not seed_entity_ids:
        return []
    max_hops = min(int(max_hops), MAX_HOPS_V02)
    if max_hops <= 0:
        return []

    seed_list = [int(x) for x in seed_entity_ids if x is not None]
    if not seed_list:
        return []

    # Build optional filter clauses. Each is applied inside the LATERAL subquery
    # so the recursive step only walks to edges matching all predicates.
    extra_filters: List[str] = []
    extra_params: List[Any] = []
    if edge_types:
        extra_filters.append("AND r.relation_type = ANY(%s)")
        extra_params.append(list(edge_types))
    if exclude_sources:
        extra_filters.append(
            "AND (r.metadata->>'source' IS NULL "
            "OR NOT (r.metadata->>'source' = ANY(%s::text[])))"
        )
        extra_params.append(list(exclude_sources))
    if min_source_quality and min_source_quality > 0.0:
        extra_filters.append(
            "AND COALESCE((r.metadata->>'source_quality')::float, 1.0) >= %s"
        )
        extra_params.append(float(min_source_quality))
    edge_filter = "\n                  ".join(extra_filters)

    params: List[Any] = [seed_list, max_hops, *extra_params, path_limit]

    # Note: column is relation_type, not relationship_type (handoff drift fix).
    # The CTE emits both directions of every edge by unioning a forward and
    # reverse projection in the recursive step. edge_sources tracks metadata.source
    # per edge so score_paths can apply a VN penalty downstream.
    sql = f"""
        WITH RECURSIVE walk(node_ids, edge_types, edge_confs, edge_sources, depth, last_node, visited) AS (
            SELECT
                ARRAY[seed_id]::bigint[],
                ARRAY[]::text[],
                ARRAY[]::double precision[],
                ARRAY[]::text[],
                0,
                seed_id,
                ARRAY[seed_id]::bigint[]
            FROM unnest(%s::bigint[]) AS seed_id
            UNION ALL
            SELECT
                w.node_ids || next_node,
                w.edge_types || r.relation_type,
                w.edge_confs || COALESCE(r.confidence, 0.8),
                w.edge_sources || COALESCE(r.metadata->>'source', 'unknown'),
                w.depth + 1,
                next_node,
                w.visited || next_node
            FROM walk w
            JOIN LATERAL (
                SELECT r.relation_type, r.confidence, r.metadata,
                       CASE WHEN r.source_entity_id = w.last_node
                            THEN r.target_entity_id
                            ELSE r.source_entity_id
                       END AS next_node
                FROM kg_relationships r
                WHERE (r.source_entity_id = w.last_node OR r.target_entity_id = w.last_node)
                  {edge_filter}
            ) r ON TRUE
            WHERE w.depth < %s
              AND NOT (r.next_node = ANY(w.visited))
              AND r.next_node IS NOT NULL
        )
        SELECT node_ids, edge_types, edge_confs, edge_sources, depth
        FROM walk
        WHERE depth > 0
        ORDER BY depth ASC, (
            SELECT COALESCE(EXP(SUM(LN(GREATEST(c, 0.01)))), 0)
            FROM unnest(edge_confs) AS c
        ) DESC
        LIMIT %s
    """

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
    except Exception:
        logger.exception(
            "[graph_retrieval] walk failed (seeds=%s, max_hops=%d, exclude_sources=%s, min_sq=%s)",
            seed_list[:5], max_hops, exclude_sources, min_source_quality,
        )
        return []

    paths: List[Path] = []
    for node_ids, edge_types_arr, edge_confs, edge_sources_arr, _depth in rows:
        # Convert Postgres arrays (may come back as Python lists already
        # depending on psycopg2 adapter) to plain lists of python ints/floats.
        node_ids_list = [int(x) for x in (node_ids or [])]
        edge_types_list = [str(x) for x in (edge_types_arr or [])]
        edge_confs_list = [float(x) for x in (edge_confs or [])]
        edge_sources_list = [str(x) for x in (edge_sources_arr or [])]
        if not edge_types_list:
            continue
        product = 1.0
        for c in edge_confs_list:
            product *= max(c, 0.01)
        paths.append(Path(
            node_ids=node_ids_list,
            edge_types=edge_types_list,
            edge_confidences=edge_confs_list,
            edge_sources=edge_sources_list,
            total_confidence=product,
        ))
    return paths


# ---------------------------------------------------------------------------
# 3. score_paths
# ---------------------------------------------------------------------------

# Intent-to-edge affinity table. Each intent hints at which edge types are most
# semantically relevant. Unknown intents fall back to a uniform 1.0 multiplier.
_INTENT_EDGE_BOOSTS: dict = {
    "motie_trace": {
        "DIENT_IN": 1.5, "LID_VAN": 1.3, "STEMT_VOOR": 1.4, "STEMT_TEGEN": 1.4,
        "AANGENOMEN": 1.3, "VERWORPEN": 1.3, "DISCUSSED_IN": 1.2, "VOTED_IN": 1.4,
    },
    "party_comparison": {
        "LID_VAN": 1.4, "SPREEKT_OVER": 1.5, "STEMT_VOOR": 1.2, "STEMT_TEGEN": 1.2,
    },
    "location": {
        "LOCATED_IN": 1.5, "BETREFT_WIJK": 1.4,
    },
    "financial": {
        "HEEFT_BUDGET": 1.5,
    },
}

_HOP_PENALTY = 0.7  # each extra hop multiplies score by this


def score_paths(
    paths: Sequence[Path],
    query_intent: str = "",
    vn_penalty: float = _DEFAULT_VN_PENALTY,
) -> List[ScoredPath]:
    """
    Attach a final score to each Path. Heuristic:

        score = total_confidence × (HOP_PENALTY ** (hops - 1)) × intent_boost × vn_factor

    where:
    - ``intent_boost`` is the geometric mean of the edge-type boosts from
      ``_INTENT_EDGE_BOOSTS[query_intent]`` (defaults to 1.0 outside table)
    - ``vn_factor`` is ``vn_penalty ** (count of VN-derived edges in path)``.
      So a 2-hop path with 1 VN edge gets a 0.7x penalty; with both VN gets
      0.49x. Pass ``vn_penalty=1.0`` to disable the VN penalty.

    Results are sorted descending by score.

    No ML classifier — this is deliberate for v0.2. If/when an intent
    classifier is trained, swap the string lookup for a model inference call.
    """
    intent_boosts = _INTENT_EDGE_BOOSTS.get(query_intent, {})
    scored: List[ScoredPath] = []
    for p in paths:
        hops = len(p.edge_types)
        if hops == 0:
            continue
        # geometric mean of per-edge intent boosts (defaults to 1.0 outside table)
        boost_product = 1.0
        for et in p.edge_types:
            boost_product *= intent_boosts.get(et, 1.0)
        boost = boost_product ** (1.0 / hops)
        penalty = _HOP_PENALTY ** max(hops - 1, 0)
        # VN penalty: count edges whose source is in _VN_SOURCE_VALUES. Paths
        # walked with no source metadata (pre-backfill) are treated as non-VN
        # and get a vn_factor of 1.0.
        vn_edge_count = sum(1 for s in p.edge_sources if s in _VN_SOURCE_VALUES)
        vn_factor = vn_penalty ** vn_edge_count if vn_edge_count else 1.0
        score = p.total_confidence * penalty * boost * vn_factor
        scored.append(ScoredPath(path=p, score=score))
    scored.sort(key=lambda sp: sp.score, reverse=True)
    return scored


# ---------------------------------------------------------------------------
# 4. hydrate_chunks
# ---------------------------------------------------------------------------


def hydrate_chunks(
    entity_ids: Sequence[int],
    gemeente: Optional[str] = None,
    limit: int = HYDRATE_DEFAULT_LIMIT,
    exclude_sources: Optional[Sequence[str]] = None,
    min_source_quality: float = 0.0,
) -> List[GraphChunk]:
    """
    For a set of entity ids, return the document_chunks where those entities
    were mentioned. Join goes via ``kg_mentions`` (NOT a nonexistent
    ``chunk_entities`` table — handoff drift fix).

    Optional ``gemeente`` filter scopes results to chunks whose **seed entity**
    has ``metadata->>'gemeente' = :gemeente``. The multi-portal schema design
    puts gemeente on every Location entity — when this field is populated
    (Phase 1 onwards), WS5b gets per-tenant isolation for free.

    **VN provenance filters (added 2026-04-14 for WS1 Phase A bis):**
    - ``exclude_sources``: pass ``['virtual_notulen']`` to drop chunks whose
      document is a virtual notulen (``documents.is_virtual_notulen = TRUE``).
      Semantics match the edge-level filter in ``walk()`` — 'virtual_notulen'
      maps to ``is_virtual_notulen = TRUE`` on the chunk's source document.
    - ``min_source_quality``: drop chunks where the source meeting's
      ``staging.meetings.quality_score`` is below the threshold. Default 0.0.
      Only applies to VN chunks; official chunks always pass.

    Results are ordered by (a) number of distinct seed entities mentioned in
    the chunk (DESC), then (b) chunk id (ASC) as a deterministic tiebreaker.
    Rerank is caller responsibility — that is how rag_service.py layers on
    Jina v3 for the 5th stream.
    """
    if not entity_ids:
        return []
    eid_list = [int(x) for x in entity_ids if x is not None]
    if not eid_list:
        return []

    filters = ["km.entity_id = ANY(%s)"]
    params: List[Any] = [eid_list]
    if gemeente:
        filters.append("e.metadata->>'gemeente' = %s")
        params.append(gemeente)

    where_clause = " AND ".join(filters)

    # Post-hits filtering on the chunk's source document.
    doc_filters: List[str] = []
    doc_params: List[Any] = []
    exclude_vn = bool(exclude_sources) and "virtual_notulen" in set(exclude_sources)
    if exclude_vn:
        doc_filters.append("COALESCE(d.is_virtual_notulen, FALSE) = FALSE")
    min_sq = float(min_source_quality or 0.0)
    needs_staging_join = min_sq > 0.0
    if needs_staging_join:
        # Only filter VN chunks by quality_score; official chunks always pass.
        doc_filters.append(
            "(COALESCE(d.is_virtual_notulen, FALSE) = FALSE "
            "OR COALESCE(sm.quality_score, 0.5) >= %s)"
        )
        doc_params.append(min_sq)

    doc_where = ""
    if doc_filters:
        doc_where = "WHERE " + " AND ".join(doc_filters)

    staging_join = (
        "LEFT JOIN staging.meetings sm ON sm.id = d.meeting_id"
        if needs_staging_join else ""
    )

    sql = f"""
        WITH hits AS (
            SELECT km.chunk_id, COUNT(DISTINCT km.entity_id) AS seed_hits,
                   ARRAY_AGG(DISTINCT km.entity_id) AS matched_entity_ids
            FROM kg_mentions km
            JOIN kg_entities e ON e.id = km.entity_id
            WHERE {where_clause}
            GROUP BY km.chunk_id
        )
        SELECT dc.id, dc.document_id, dc.title, dc.content, dc.child_id,
               m.start_date, h.seed_hits, h.matched_entity_ids
        FROM hits h
        JOIN document_chunks dc ON dc.id = h.chunk_id
        LEFT JOIN documents d ON dc.document_id = d.id
        LEFT JOIN meetings m ON d.meeting_id = m.id
        {staging_join}
        {doc_where}
        ORDER BY h.seed_hits DESC, dc.id ASC
        LIMIT %s
    """
    params = [*params, *doc_params, int(limit)]

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
    except Exception:
        logger.exception(
            "[graph_retrieval] hydrate_chunks failed (n_entities=%d, gemeente=%s, exclude_sources=%s, min_sq=%s)",
            len(eid_list), gemeente, exclude_sources, min_source_quality,
        )
        return []

    out: List[GraphChunk] = []
    for chunk_id, document_id, title, content, child_id, start_date, seed_hits, matched_ids in rows:
        out.append(GraphChunk(
            chunk_id=chunk_id,
            document_id=str(document_id) if document_id is not None else "",
            title=str(title or "Untitled"),
            content=str(content or ""),
            similarity_score=float(seed_hits) / max(len(eid_list), 1),
            questions=[],
            child_id=child_id,
            stream_type="graph",
            start_date=str(start_date)[:10] if start_date else None,
            entity_ids=[int(x) for x in (matched_ids or [])],
        ))
    return out


# ---------------------------------------------------------------------------
# Convenience orchestration — used by rag_service.py's 5th stream.
# ---------------------------------------------------------------------------


def retrieve_via_graph(
    query: str,
    k: int = 10,
    query_intent: str = "",
    gemeente: Optional[str] = None,
    include_virtual_notulen: Optional[bool] = None,
) -> List[GraphChunk]:
    """
    One-call helper that glues the four primitives together:
        extract_query_entities -> walk -> score_paths -> hydrate_chunks

    Used by the 5th 'graph_walk' stream in services/rag_service.py. Returns
    an empty list when (a) the KG isn't ready per is_graph_walk_ready(),
    (b) no entities resolve from the query, or (c) the walk yields no paths.
    Never raises.

    ``include_virtual_notulen`` controls whether VN-derived edges and chunks
    leak into the result. When None (default), falls back to the module-level
    ``INCLUDE_VIRTUAL_NOTULEN`` env var (default True). Pass ``False`` for
    strict production / press-moment mode.
    """
    if not is_graph_walk_ready():
        return []

    entities = extract_query_entities(query)
    seed_ids = [e.id for e in entities if e.id is not None]
    if not seed_ids:
        logger.debug("[graph_retrieval] retrieve_via_graph: no seeds resolved for %r", query[:60])
        return []

    # Resolve VN gating: explicit arg overrides env var; env var overrides default.
    vn_ok = INCLUDE_VIRTUAL_NOTULEN if include_virtual_notulen is None else bool(include_virtual_notulen)
    exclude_sources = None if vn_ok else ["virtual_notulen"]

    paths = walk(
        seed_ids,
        max_hops=MAX_HOPS_V02,
        exclude_sources=exclude_sources,
    )
    if not paths:
        return []

    # vn_penalty=1.0 when VN is allowed with full trust; default 0.7 otherwise.
    # This is additive to the edge-level filter: even in VN-allowed mode, VN
    # edges get a confidence down-rank so official sources bubble up first.
    scored = score_paths(paths, query_intent=query_intent)

    # Collect distinct terminal nodes from the top-scored paths — these are the
    # entities we hydrate chunks for.
    target_ids: List[int] = []
    seen: set = set()
    for sp in scored:
        if len(target_ids) >= k * 3:
            break
        tail = sp.path.node_ids[-1] if sp.path.node_ids else None
        if tail is not None and tail not in seen:
            target_ids.append(tail)
            seen.add(tail)

    # Also include the seeds themselves so a seed-only chunk can be returned.
    for sid in seed_ids:
        if sid not in seen:
            target_ids.append(sid)
            seen.add(sid)

    return hydrate_chunks(
        target_ids,
        gemeente=gemeente,
        limit=k,
        exclude_sources=exclude_sources,
    )
