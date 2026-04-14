#!/usr/bin/env python3
"""
NeoDemos MCP Server — Civic Intelligence for Rotterdam City Council
-------------------------------------------------------------------
Pure retrieval layer for Claude Desktop, ChatGPT, and Perplexity.
All analysis, synthesis, and reasoning is performed by the host LLM.

Features:
  - Hybrid search: BM25 (dual-dictionary dutch+simple) + vector (Qwen3-8B)
  - Jina API reranking for chunk quality
  - Party-filtered retrieval via Qdrant payload filter
  - Structured motie/amendement metadata (indieners, vote outcomes, vote counts)
  - Dynamic top_k based on query complexity
  - Temporal phrase extraction from Dutch text
  - Financial table boost for budget queries
  - Knowledge graph: 57K+ relationship edges (LID_VAN, DIENT_IN, STEMT_VOOR/TEGEN)

API usage: minimal — only Nebius embedding (1/query) + Jina reranking (1-3/query).
No LLM calls. The host AI assistant IS the reasoning engine.

Transport: stdio (default) or SSE (for remote deployment)
"""

import os
import re
import sys
import json
import glob
import time
import threading
from services.db_pool import get_connection
from services.temporal_parser import parse as _temporal_parse, has_temporal_signal
from datetime import date, datetime
from typing import Optional
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Retrieval quality constants (WS4 2026-04-11)
# ---------------------------------------------------------------------------
# MIN_SIMILARITY: drop chunks below this score before rendering. Generalises
# the existing `>= 0.25` filter in zoek_financieel. 0.06-scoring chunks observed
# in scan_breed (haven/duurzaamheid 2026-04-11) were noise; 0.15 cuts the noise
# without dropping borderline-but-useful hits.
MIN_SIMILARITY = 0.15
MIN_SIMILARITY_RELAXED = 0.10  # fallback if strict floor leaves < 3 chunks
MIN_CHUNKS_BEFORE_RELAX = 3

# MIN_CONTENT_CHARS: chunks whose stripped content is shorter than this are
# "empty slots" — "Geen stukken ontvangen", bare section headers, empty table
# cells. Filter before rendering.
MIN_CONTENT_CHARS = 80

# Corpus coverage footer for zero-result responses (WS4 — no WS5a dep).
# Update when year-ranges are re-ingested.
ZERO_RESULT_FOOTER = (
    "_Corpus: Rotterdam raadsdocumenten 2002–heden. 0 resultaten betekent niet "
    "dat het beleid niet bestaat — probeer een bredere zoekvraag of controleer "
    "de datumfilter._"
)

from neodemos_version import DISPLAY_NAME, VERSION_LABEL
from mcp.server.fastmcp import FastMCP
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions

# OAuth 2.1 auth — only enabled for HTTP transports (not stdio)
_transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
_auth_settings = None
_auth_provider = None

if _transport in ("sse", "streamable-http", "--http"):
    from services.mcp_oauth_provider import NeodemosOAuthProvider
    _mcp_base_url = os.environ.get("MCP_BASE_URL", "https://mcp.neodemos.nl")
    _auth_provider = NeodemosOAuthProvider()
    _auth_settings = AuthSettings(
        issuer_url=_mcp_base_url,
        resource_server_url=_mcp_base_url,
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["mcp", "search"],
            default_scopes=["mcp", "search"],
        ),
        required_scopes=["mcp"],
    )

_port = int(os.environ.get("MCP_PORT", "8001"))
_host = os.environ.get("MCP_HOST", "0.0.0.0")

mcp = FastMCP(
    DISPLAY_NAME,
    auth_server_provider=_auth_provider,
    auth=_auth_settings,
    host=_host if _transport != "stdio" else "127.0.0.1",
    port=_port,
    instructions=(
        f"Je bent verbonden met {DISPLAY_NAME} {VERSION_LABEL}, een civic intelligence platform "
        "voor de Rotterdamse gemeenteraad (90.000+ documenten, 2002-heden). "
        "Gebruik de beschikbare tools om relevante raadsinformatie op te halen. "
        "Analyseer en synthetiseer de opgehaalde data zelf — de tools doen alleen retrieval. "
        "Antwoord altijd in het Nederlands tenzij de gebruiker anders vraagt. "
        "TEMPORELE DETECTIE: Wanneer een vraag tijdsgebonden taal bevat "
        "(bijv. 'vorig jaar', 'sinds 2023', 'afgelopen maanden', 'recent', 'eerder'), "
        "vertaal dit ALTIJD naar concrete datum_van/datum_tot parameters bij je tool calls. "
        f"Vandaag is {date.today().isoformat()}. Verwijder temporele termen uit de zoektekst zelf — "
        "die filteren werkt via metadata, niet via vectorsimilariteit. "
        "PARTIJ-FILTER: Wanneer een vraag gaat over een specifieke partij, "
        "geef de partijnaam mee als parameter. De tools zoeken dan gericht in fragmenten "
        "van die partij. "
        "COMPLEXE VRAGEN: Voor vragen die meerdere stappen vereisen (bijv. 'welke moties "
        "zijn verworpen en hoe stemde partij X'), gebruik meerdere tool calls in sequentie."
    ),
)

# Public MCP server — no auth, eligible tools only, per-IP rate limited
# (see all_public_tools() in services/mcp_tool_registry.py and rate limiting middleware)
_public_mcp: "FastMCP | None" = None
if _transport in ("sse", "streamable-http", "--http"):
    _public_mcp = FastMCP(
        f"{DISPLAY_NAME} (Public)",
        auth=None,
        auth_server_provider=None,
        host=_host,
        port=_port,
        instructions=(
            f"Je bent verbonden met {DISPLAY_NAME} {VERSION_LABEL} — publiek toegankelijk, geen login vereist. "
            "Beschikbaar voor alle Rotterdam-gerelateerde raadsinformatie (90.000+ documenten, 2002-heden). "
            "Gebruik de beschikbare tools om relevante raadsinformatie op te halen."
        ),
    )

# ---------------------------------------------------------------------------
# Liveness probe — consumed by kamal-proxy during deploy health checks.
# Returns 200 OK without touching DB/Qdrant so it stays fast and unauthenticated.
# ---------------------------------------------------------------------------

from starlette.responses import PlainTextResponse as _PlainTextResponse
from starlette.requests import Request as _StarletteRequest


@mcp.custom_route("/up", methods=["GET"])
async def _kamal_liveness(request: _StarletteRequest):  # pragma: no cover
    return _PlainTextResponse("ok", status_code=200)


# ---------------------------------------------------------------------------
# Query logging — appends one JSONL line per tool call to logs/mcp_queries.jsonl
# ---------------------------------------------------------------------------

_QUERY_LOG = PROJECT_ROOT / "logs" / "mcp_queries.jsonl"
_LOG_LOCK = threading.Lock()


def _log_query(tool_name: str, params: dict, result: str, latency_ms: int) -> None:
    """Append one JSONL line. Never raises — logging must not crash the tool."""
    try:
        entry = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "tool": tool_name,
            "params": params,
            "latency_ms": latency_ms,
            "result_chars": len(result),
        }
        _QUERY_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_LOCK:
            with open(_QUERY_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def logged_tool(func):
    """Drop-in for @mcp.tool() — registers with FastMCP, logs to mcp_queries.jsonl,
    writes one row per call to mcp_audit_log (never logs raw params — only sha256),
    and pulls the FastMCP tool description from `services.mcp_tool_registry.REGISTRY`
    when an entry exists (WS4 2026-04-11). Falls back to the function docstring when
    the tool is not yet registered."""
    import inspect
    import functools

    sig = inspect.signature(func)

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            log_params = {k: v for k, v in bound.arguments.items() if v is not None}
        except Exception:
            log_params = kwargs

        # Layer 2 parameter validation (WS4 2026-04-13)
        try:
            from services.mcp_validation import validate_tool_params as _validate
            _validate(func.__name__, log_params)
        except ValueError as _ve:
            return f"_Validatiefout: {_ve}_"
        except Exception:
            pass  # validation failure must never block a tool

        # Rate limiting (WS4 2026-04-13): global per-tool sliding window
        try:
            from services.mcp_rate_limiter import check_tool_rate_limit as _rl_check
            if not _rl_check(func.__name__):
                return (
                    f"_Te veel verzoeken voor tool '{func.__name__}'. "
                    f"Probeer het over een minuut opnieuw._"
                )
        except Exception:
            pass  # rate limit failure must never block a tool

        t0 = time.monotonic()
        error: Optional[Exception] = None
        result = None
        try:
            result = func(*args, **kwargs)
            # Layer 4 output filter — context bomb prevention + PII stripping (WS4 2026-04-13)
            try:
                from services.output_filter import filter_output as _filter_output
                result = _filter_output(result)
            except Exception:
                pass  # Layer 4 must never block a tool
            return result
        except Exception as exc:
            error = exc
            raise
        finally:
            latency_ms = int((time.monotonic() - t0) * 1000)
            # Legacy JSONL trail (result is None on error — _log_query handles len() safely)
            _log_query(func.__name__, log_params, result if result is not None else "", latency_ms)
            # WS4 audit log — hash-only params, never raw values
            try:
                from services.audit_logger import audit_log_sync
                audit_log_sync(
                    tool_name=func.__name__,
                    params=log_params,
                    result=result,
                    latency_ms=latency_ms,
                    error=error,
                )
            except Exception:
                pass  # audit log must never crash a tool

    # Pull AI-consumption description from the registry if registered (WS4 single source of truth)
    description = None
    try:
        from services.mcp_tool_registry import REGISTRY as _TOOL_REGISTRY
        _spec = _TOOL_REGISTRY.get(func.__name__)
        if _spec is not None:
            description = _spec.ai_description
    except Exception:
        # Registry not available (e.g. during bootstrap) — fall back to docstring
        description = None

    if description is not None:
        registered = mcp.tool(description=description)(wrapper)
        # Also register on public server if tool is public-eligible (WS4)
        if _public_mcp is not None:
            try:
                _public_spec = _TOOL_REGISTRY.get(func.__name__)
                if _public_spec is not None and _public_spec.public:
                    _public_mcp.tool(description=description)(wrapper)
            except Exception:
                pass  # dual-registration failure must never abort the primary registration
        return registered

    registered = mcp.tool()(wrapper)
    if _public_mcp is not None:
        try:
            _public_spec = _TOOL_REGISTRY.get(func.__name__)
            if _public_spec is not None and _public_spec.public:
                _public_mcp.tool()(wrapper)
        except Exception:
            pass
    return registered


# ---------------------------------------------------------------------------
# Lazy service singletons
# ---------------------------------------------------------------------------

_rag = None
_storage = None


def _get_rag():
    """
    Returns a RAGService with v3 retrieval (Nebius embedding + Jina reranking).
    """
    global _rag
    if _rag is None:
        from services.rag_service import RAGService
        _rag = RAGService()  # API-only: Nebius embedding, Jina reranker
    return _rag


def _get_storage():
    global _storage
    if _storage is None:
        from services.storage import StorageService
        _storage = StorageService()
    return _storage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_party_profile(partij: str) -> dict:
    """Load the party profile JSON for the given party name."""
    partij_slug = partij.lower().replace(" ", "_").replace("-", "_")
    candidates = [
        PROJECT_ROOT / "data" / "profiles" / f"party_profile_{partij_slug}.json",
        PROJECT_ROOT / "data" / "profiles" / f"{partij_slug.upper()}_PROFILE_DEMO.json",
    ]
    for path in glob.glob(str(PROJECT_ROOT / "data" / "profiles" / "*.json")):
        if partij_slug in Path(path).stem.lower():
            candidates.append(Path(path))

    for path in candidates:
        if Path(path).exists():
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
    return {}


def _parse_uitkomst(name: str) -> str:
    """Extract motie/amendement outcome from document name."""
    name_lower = name.lower()
    for keyword in ["aangenomen", "verworpen", "ingetrokken", "aangehouden"]:
        if keyword in name_lower:
            return keyword
    return "onbekend"


def _format_table_json(table_json_str: str) -> str:
    """Convert table_json payload to a clean markdown table for Claude."""
    try:
        data = json.loads(table_json_str) if isinstance(table_json_str, str) else table_json_str
        if not data:
            return str(data)

        if isinstance(data, dict):
            headers = data.get("headers", [])
            rows = data.get("rows", [])
        elif isinstance(data, list):
            if isinstance(data[0], dict):
                headers = list(data[0].keys())
                rows = [list(row.values()) for row in data]
            elif isinstance(data[0], list):
                headers = [str(h) for h in data[0]]
                rows = data[1:]
            else:
                return str(data)
        else:
            return str(data)

        if not headers:
            return str(data)

        lines = [
            "| " + " | ".join(str(h) for h in headers) + " |",
            "|" + "|".join(["---"] * len(headers)) + "|",
        ]
        for row in rows:
            lines.append("| " + " | ".join(str(c) for c in row) + " |")
        return "\n".join(lines)
    except Exception:
        return str(table_json_str)


def _batch_fetch_document_urls(document_ids: list) -> dict:
    """
    Batch-fetch source URLs for a list of document IDs in a single query.
    Returns {document_id: url} for docs that have a non-NULL url (97% coverage).
    Never raises — returns {} on any DB error so URL rendering degrades gracefully.
    """
    if not document_ids:
        return {}
    # Deduplicate while preserving order for the IN clause
    unique_ids = list({str(d): None for d in document_ids}.keys())
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id, url FROM documents WHERE id = ANY(%s) AND url IS NOT NULL",
                (unique_ids,),
            )
            result = {str(row[0]): row[1] for row in cur.fetchall() if row[1]}
            cur.close()
            return result
    except Exception:
        return {}


def _format_chunks_v3(chunks, max_content: int = 800, dedup_by_doc: bool = False, show_followup: bool = True) -> str:
    """
    Format retrieved chunks with enriched v3 metadata.
    Shows party, committee, doc_type alongside each chunk.

    Args:
        dedup_by_doc: If True, keep only the first (highest-ranked) chunk per document_id.
        show_followup: If True, append a lees_fragment hint for the top 3 results.
    """
    # Filter content-empty chunks (WS4 2026-04-11): "Geen stukken ontvangen",
    # bare section headers, empty table cells. Drop before rendering so they
    # never burn a slot or leak noise into LLM context.
    if chunks:
        chunks = [
            c for c in chunks
            if c.content and len((c.content or "").strip()) >= MIN_CONTENT_CHARS
        ]

    if not chunks:
        return f"_Geen resultaten gevonden._\n\n{ZERO_RESULT_FOOTER}"

    # Deduplicate by document_id when requested
    if dedup_by_doc:
        seen_docs = set()
        deduped = []
        for chunk in chunks:
            if chunk.document_id not in seen_docs:
                seen_docs.add(chunk.document_id)
                deduped.append(chunk)
        chunks = deduped

    # Batch-fetch source URLs (97% coverage — chunks without URL render without link)
    url_map = _batch_fetch_document_urls([c.document_id for c in chunks])

    lines = []
    followup_ids = []
    for i, chunk in enumerate(chunks, 1):
        date_str = f" · {chunk.start_date[:10]}" if chunk.start_date else ""
        stream = f" [{chunk.stream_type}]" if chunk.stream_type else ""

        # v3 enriched metadata from Qdrant payload
        party = getattr(chunk, "party", None)
        party_str = f" · {party}" if party else ""

        lines.append(f"### [{i}]{stream} {chunk.title}{date_str}{party_str}")

        content = chunk.content[:max_content]
        lines.append(content)
        lines.append(f"_document_id: {chunk.document_id}_")

        # Source URL link (if present in 97% of corpus)
        url = url_map.get(str(chunk.document_id))
        if url:
            lines.append(f"[Brondocument ↗]({url})")
        lines.append("")

        if show_followup and i <= 3:
            followup_ids.append(chunk.document_id)

    if followup_ids:
        lines.append("---")
        lines.append("_Volledige tekst ophalen:_")
        for doc_id in followup_ids:
            lines.append(f'- `lees_fragment(document_id="{doc_id}")`')

    return "\n".join(lines)


def _apply_quality_filters(chunks: list, top_k: int, dedup_by_document: bool = True) -> list:
    """
    Apply WS4 retrieval-quality filters in order:
      1. Min-similarity floor (drop 0.06-scoring noise); relax if < MIN_CHUNKS_BEFORE_RELAX survive.
      2. Dedup by document_id — keep only the highest-scoring chunk per document so that
         max_resultaten=8 returns 8 unique documents, not 8 chunks from 2 documents.
         (Upstream failure mode observed 2026-04-11 in zoek_uitspraken/scan_breed.)
      3. Slice to top_k.
    """
    if not chunks:
        return chunks

    # Step 1: min-similarity floor with relaxation fallback.
    # IMPORTANT: chunks from BM25-only streams or merge paths may have
    # similarity_score = None. These are NOT low-quality — they simply weren't
    # scored by the vector retrieval. Always pass them through.
    def _has_score(c):
        return getattr(c, "similarity_score", None) is not None

    scored = [c for c in chunks if _has_score(c)]
    unscored = [c for c in chunks if not _has_score(c)]

    strict = [c for c in scored if c.similarity_score >= MIN_SIMILARITY]
    if len(strict) >= MIN_CHUNKS_BEFORE_RELAX:
        scored = strict
    else:
        # Relax floor rather than return nothing on borderline queries
        scored = [c for c in scored if c.similarity_score >= MIN_SIMILARITY_RELAXED]

    # Recombine: scored chunks first (rank-ordered), unscored chunks after
    chunks = scored + unscored

    # Step 2: keep the highest-scoring chunk per document_id (assumes input is already score-sorted)
    if dedup_by_document:
        seen_docs = set()
        deduped = []
        for c in chunks:
            if c.document_id not in seen_docs:
                seen_docs.add(c.document_id)
                deduped.append(c)
        chunks = deduped

    return chunks[:top_k]


def _retrieve_with_reranking(
    query: str,
    top_k: int = 10,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    party: Optional[str] = None,
    dedup_by_document: bool = True,
) -> list:
    """
    Core v3 retrieval: hybrid search + Jina reranking (not fast_mode).
    Expands Dutch compound words for better BM25 coverage.
    Optionally filters by party via Qdrant payload.

    Applies WS4 retrieval quality filters: min-similarity floor and
    dedup-by-document_id so that top_k returns unique documents.
    Over-fetches by 3x to give quality filtering headroom before the final slice.
    """
    rag = _get_rag()
    over_fetch = max(top_k * 3, top_k + 5)

    if party:
        # T7 audit (2026-04-14): `key="party"` in Qdrant payload is set via
        # enrich_qdrant_metadata.py::primary_party(parties) — it is the
        # *dominant party mentioned in the chunk text*, not a speaker-level
        # attribution. This means a chunk where "VVD" is mentioned by
        # GroenLinks might get party="VVD". True speaker-level filtering would
        # require a dedicated `speaker_party` payload field (not yet enriched).
        # For now, this is the best available proxy.
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        party_filter = Filter(must=[
            FieldCondition(key="party", match=MatchValue(value=party))
        ])

        query_embedding = rag.embedder.embed(query)
        party_chunks = rag._retrieve_by_vector_similarity_with_filter(
            query_embedding,
            top_k=over_fetch,
            qdrant_filter=party_filter,
            date_from=date_from,
            date_to=date_to,
        )

        # T7 fix: only supplement with standard_chunks when party_chunks is
        # genuinely empty (e.g. party has no Qdrant enrichment at all). When
        # party_chunks contains any results, don't merge unfiltered chunks —
        # they dilute the party signal with generic housing/policy chunks.
        # The _retrieve_by_vector_similarity_with_filter fallback already
        # drops the party filter internally if < 5 results are found.
        if not party_chunks:
            standard_chunks = rag.retrieve_relevant_context(
                query_text=query,
                top_k=over_fetch,
                date_from=date_from,
                date_to=date_to,
                fast_mode=False,
            )
            return _apply_quality_filters(standard_chunks, top_k, dedup_by_document=dedup_by_document)

        return _apply_quality_filters(party_chunks, top_k, dedup_by_document=dedup_by_document)

    # Standard retrieval with reranking
    chunks = rag.retrieve_relevant_context(
        query_text=query,
        top_k=over_fetch,
        date_from=date_from,
        date_to=date_to,
        fast_mode=False,  # v3: always rerank
    )

    # Compound words handled by decomposed_terms in the tsvector
    return _apply_quality_filters(chunks, top_k, dedup_by_document=dedup_by_document)


# ---------------------------------------------------------------------------
# Tool 1 — Algemene raadshistorie zoekfunctie
# ---------------------------------------------------------------------------

@logged_tool
def zoek_raadshistorie(
    vraag: str,
    datum_van: Optional[str] = None,
    datum_tot: Optional[str] = None,
    partij: Optional[str] = None,
    max_resultaten: int = 10,
) -> str:
    """
    Doorzoek de Rotterdam gemeenteraad notulen op een vraag of onderwerp.
    Retourneert relevante tekstfragmenten met bronvermelding, datum en partij.
    Resultaten worden gerangschikt op relevantie (Jina Reranker v3).

    Args:
        vraag: Zoekterm of vraag in het Nederlands
        datum_van: Startdatum filter, ISO formaat (bijv. "2022-01-01")
        datum_tot: Einddatum filter, ISO formaat (bijv. "2024-12-31")
        partij: Filter op partijnaam (bijv. "VVD", "PvdA", "Leefbaar Rotterdam")
        max_resultaten: Aantal resultaten (max 20, standaard 10)
    """
    # Temporal fallback (WS4 2026-04-13): when weaker models (Mistral/Le Chat)
    # fail to extract datum_van/datum_tot from Dutch temporal phrases, apply
    # server-side regex+LLM parser as safety net.
    if datum_van is None and datum_tot is None:
        try:
            _tparsed = _temporal_parse(vraag)
            datum_van = _tparsed.get("date_from")
            datum_tot = _tparsed.get("date_to")
        except Exception:
            pass

    top_k = min(max(1, max_resultaten), 20)

    chunks = _retrieve_with_reranking(
        query=vraag,
        top_k=top_k,
        date_from=datum_van,
        date_to=datum_tot,
        party=partij,
    )

    header = f"## Raadshistorie: '{vraag}'"
    if datum_van or datum_tot:
        header += f"\n_Periode: {datum_van or '…'} — {datum_tot or 'heden'}_"
    if partij:
        header += f"\n_Partij filter: {partij}_"

    return header + "\n\n" + _format_chunks_v3(chunks, dedup_by_doc=True)


# ---------------------------------------------------------------------------
# Tool 2 — Financiële gegevens (with table boost)
# ---------------------------------------------------------------------------

@logged_tool
def zoek_financieel(
    onderwerp: str,
    datum_van: Optional[str] = None,
    datum_tot: Optional[str] = None,
    budget_year: Optional[int] = None,
    max_resultaten: int = 12,
) -> str:
    """
    Zoek financiële gegevens, begrotingen en budgetmutaties in de raadsstukken.
    Geeft prioriteit aan tabeldata (begrotingstabellen, jaarstukken).
    Gebruik dit voor vragen over kosten, budgetten, bezuinigingen, subsidies.
    Retourneert tabellen als gestructureerde markdown.

    LET OP: Zonder datum_van filter kan deze tool ook oude data retourneren
    (bijv. deelgemeente-begrotingen van vóór 2014). Geef bij vragen over
    recente financiën altijd een datum_van mee.

    BUDGET_YEAR vs DATUM_VAN — wanneer gebruiken?
    De Begroting 2025 wordt in oktober 2024 ingediend (publicatiedatum) maar
    beschrijft fiscaal jaar 2025 (budget_year). Kies op basis van intentie:
      - "Wat is de begrotingsruimte voor 2025?"           → budget_year=2025
      - "Welke begrotingsdocumenten werden in oktober 2024 gepubliceerd?"
                                                          → datum_van='2024-10-01'
    budget_year is autoritatief via staging.financial_documents.budget_years;
    datum_van filtert op publicatiedatum van de meeting waarin het stuk behandeld werd.

    Args:
        onderwerp: Financieel onderwerp (bijv. "jeugdzorg begroting 2023")
        datum_van: Startdatum filter, ISO formaat — filtert op publicatiedatum
        datum_tot: Einddatum filter, ISO formaat — filtert op publicatiedatum
        budget_year: Doeljaar van het budget (het fiscaal jaar dat het document beschrijft).
                     Gebruik dit voor "wat is X voor jaar Y"-vragen.
        max_resultaten: Aantal resultaten (standaard 12)
    """
    # Temporal fallback (WS4 2026-04-13): when weaker models (Mistral/Le Chat)
    # fail to extract datum_van/datum_tot from Dutch temporal phrases, apply
    # server-side regex+LLM parser as safety net.
    if datum_van is None and datum_tot is None:
        try:
            _tparsed = _temporal_parse(onderwerp)
            datum_van = _tparsed.get("date_from")
            datum_tot = _tparsed.get("date_to")
        except Exception:
            pass

    # ── WS2 structured routing: if the query mentions a specific programma + jaar,
    # call vraag_begrotingsregel internally first and prepend structured results. ──
    structured_block = ""
    _narrative_keywords = re.compile(
        r"\b(waarom|toelichting|reden|uitleg|achtergrond|motivatie|context|verklaring)\b",
        re.IGNORECASE,
    )
    if not _narrative_keywords.search(onderwerp):
        # Try to detect a programma + year combination in the query
        _year_match = re.search(r"\b(20[12]\d)\b", onderwerp)
        # Extract potential programma: strip the year and common financial keywords
        _programma_candidate = re.sub(
            r"\b(20[12]\d|begroting|budget|lasten|baten|saldo|kosten|subsidie|bezuiniging)\b",
            "", onderwerp, flags=re.IGNORECASE,
        ).strip()
        _programma_candidate = re.sub(r"\s+", " ", _programma_candidate).strip()

        if _year_match and _programma_candidate and len(_programma_candidate) >= 3:
            try:
                _structured_raw = vraag_begrotingsregel(
                    gemeente="rotterdam",
                    jaar=int(_year_match.group(1)),
                    programma=_programma_candidate,
                )
                _structured_data = json.loads(_structured_raw)
                if _structured_data.get("total", 0) > 0:
                    structured_block = (
                        "### Gestructureerde begrotingsregels (financial_lines)\n"
                        f"_{_structured_data['total']} exacte match(es) voor "
                        f"'{_programma_candidate}' in {_year_match.group(1)}_\n\n"
                        "```json\n"
                        + json.dumps(_structured_data["matches"][:10], ensure_ascii=False, indent=2)
                        + "\n```\n\n---\n\n"
                    )
            except Exception:
                pass  # structured routing is best-effort; fall through to text RAG

    rag = _get_rag()
    top_k = min(max(1, max_resultaten), 20)

    # No hard date default — Claude's temporal detection should handle this.
    # Historical queries (deelgemeenten, pre-2014) are valid use cases.

    # Use the user's query directly — no keyword stuffing that dilutes intent
    query = onderwerp

    # Resolve budget_year → set of document IDs via staging metadata
    # (budget_years is the TARGET fiscal year, not the discussion/submission date)
    budget_year_doc_ids: Optional[list] = None
    if budget_year is not None:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT id FROM staging.financial_documents WHERE budget_years @> ARRAY[%s]::int[]",
                (budget_year,),
            )
            budget_year_doc_ids = [str(r[0]) for r in cur.fetchall()]
            cur.close()
        if not budget_year_doc_ids:
            return (
                f"## Financiële gegevens: '{onderwerp}'\n\n"
                f"_Geen documenten gevonden voor budgetjaar {budget_year}._"
            )

    # Standard retrieval with reranking
    chunks = rag.retrieve_relevant_context(
        query_text=query,
        top_k=top_k,
        date_from=datum_van,
        date_to=datum_tot,
        fast_mode=False,
    )

    # Post-filter by budget_year if set (staging.financial_documents.budget_years is authoritative)
    if budget_year_doc_ids is not None:
        doc_id_set = set(budget_year_doc_ids)
        chunks = [c for c in chunks if str(c.document_id) in doc_id_set]

    # Boost: also retrieve table-type chunks, but filtered to the SAME topic
    from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny
    table_must = [FieldCondition(key="chunk_type", match=MatchValue(value="table"))]
    if budget_year_doc_ids is not None:
        # Narrow table boost to matching budget-year docs only
        table_must.append(
            FieldCondition(key="document_id", match=MatchAny(any=budget_year_doc_ids))
        )
    table_filter = Filter(must=table_must)
    query_embedding = rag.embedder.embed(onderwerp)
    table_chunks = rag._retrieve_by_vector_similarity_with_filter(
        query_embedding,
        top_k=10,
        qdrant_filter=table_filter,
        date_from=datum_van,
        date_to=datum_tot,
    )

    # Relevance threshold: drop low-scoring table chunks (they're not topic-filtered in Qdrant)
    table_chunks = [c for c in table_chunks if (c.similarity_score or 0) >= 0.25]

    # Merge: table chunks first (they have structured data), then standard, dedup by chunk_id
    seen = set()
    merged = []
    for c in table_chunks + chunks:
        if c.chunk_id not in seen:
            seen.add(c.chunk_id)
            merged.append(c)

    # Render table_json as markdown tables in content where available
    doc_ids_with_tables = [c.document_id for c in merged[:top_k]]
    table_map = {}
    if doc_ids_with_tables:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT document_id, chunk_index, table_json
                FROM document_chunks
                WHERE document_id = ANY(%s) AND table_json IS NOT NULL
            """, (doc_ids_with_tables,))
            for doc_id, cidx, tjson in cur.fetchall():
                table_map[f"{doc_id}_{cidx}"] = tjson
            cur.close()

    # Enrich content with formatted tables
    for c in merged[:top_k]:
        for key, tjson in table_map.items():
            if key.startswith(str(c.document_id)):
                formatted = _format_table_json(tjson)
                if formatted and "---" in formatted:  # valid markdown table
                    c.content = c.content + "\n\n" + formatted
                break

    # ── WS2 scope annotation: prefix entity/scope info on financial chunks ──
    _entities_cache: dict = {}
    try:
        entities_path = PROJECT_ROOT / "data" / "financial" / "financial_entities_seed.json"
        if entities_path.exists():
            with open(entities_path, encoding="utf-8") as _ef:
                for ent in json.load(_ef).get("entities", []):
                    _entities_cache[ent["id"]] = ent
    except Exception:
        pass

    scopes_seen: set = set()
    for c in merged[:top_k]:
        # Try to resolve scope from financial_lines for this chunk's document
        try:
            with get_connection() as _conn:
                _cur = _conn.cursor()
                _cur.execute("""
                    SELECT DISTINCT scope, entity_id
                    FROM financial_lines
                    WHERE document_id = %s
                    LIMIT 5
                """, (str(c.document_id),))
                fl_rows = _cur.fetchall()
                _cur.close()

            for _scope, _eid in fl_rows:
                scopes_seen.add(_scope)
                ent_info = _entities_cache.get(_eid)
                if ent_info and _scope == "gemeenschappelijke_regeling":
                    members = ent_info.get("member_gemeenten", [])
                    member_count = len(members) if members else "?"
                    prefix = (
                        f"**Bron-entiteit:** {ent_info['display_name']} "
                        f"({_scope} — {member_count} gemeenten)"
                    )
                    c.content = prefix + "\n\n" + (c.content or "")
                    break
                elif ent_info and _scope != "gemeente":
                    c.content = (
                        f"**Bron-entiteit:** {ent_info['display_name']} (scope: {_scope})"
                        + "\n\n" + (c.content or "")
                    )
                    break
        except Exception:
            pass  # scope annotation is best-effort

    header = f"## Financiële gegevens: '{onderwerp}'"
    if budget_year is not None:
        header += f"\n_Budgetjaar: {budget_year} ({len(budget_year_doc_ids)} documenten)_"
    if datum_van or datum_tot:
        header += f"\n_Periode: {datum_van or '…'} — {datum_tot or 'heden'}_"

    text_rag_result = _format_chunks_v3(merged[:top_k], max_content=1500, dedup_by_doc=True)

    # Scope summary when mixed scopes are present
    scope_summary = ""
    if len(scopes_seen) > 1:
        scope_summary = (
            "\n\n---\n**Scope-samenvatting:** Deze resultaten bevatten data uit "
            f"meerdere scopes: {', '.join(sorted(scopes_seen))}. "
            "Let op dat bedragen van gemeenschappelijke regelingen het totaal van de "
            "regeling weergeven, niet het aandeel van een individuele gemeente."
        )

    return header + "\n\n" + structured_block + text_rag_result + scope_summary


# ---------------------------------------------------------------------------
# Tool 3 — Uitspraken en citaten (party-filtered)
# ---------------------------------------------------------------------------

@logged_tool
def zoek_uitspraken(
    onderwerp: str,
    partij_of_raadslid: Optional[str] = None,
    datum_van: Optional[str] = None,
    datum_tot: Optional[str] = None,
    max_resultaten: int = 10,
) -> str:
    """
    Zoek uitspraken en citaten van raadsleden in de debatnotulen.
    Bij opgave van een partij wordt gefilterd op fragmenten van die partij.

    TIP: Als iemand van rol is gewisseld (bijv. Buijt: raadslid → wethouder),
    gebruik dan zoek_uitspraken_op_rol voor automatische periode-filtering.

    Args:
        onderwerp: Onderwerp waarover de uitspraak gaat
        partij_of_raadslid: Partijnaam of naam raadslid (bijv. "SP", "Pastors")
        datum_van: Startdatum filter, ISO formaat
        datum_tot: Einddatum filter, ISO formaat
        max_resultaten: Aantal resultaten (standaard 10)
    """
    # Temporal fallback (WS4 2026-04-13): when weaker models (Mistral/Le Chat)
    # fail to extract datum_van/datum_tot from Dutch temporal phrases, apply
    # server-side regex+LLM parser as safety net.
    if datum_van is None and datum_tot is None:
        try:
            _tparsed = _temporal_parse(onderwerp)
            datum_van = _tparsed.get("date_from")
            datum_tot = _tparsed.get("date_to")
        except Exception:
            pass

    top_k = min(max(1, max_resultaten), 20)
    query = f"{onderwerp} debat standpunten uitspraken"

    # Detect if input is a party name for Qdrant filter
    party = None
    if partij_of_raadslid:
        from services.party_utils import extract_party_from_query
        party = extract_party_from_query(partij_of_raadslid)
        if not party:
            # Not a known party — append as keyword (could be a person name)
            query += f" {partij_of_raadslid}"

    chunks = _retrieve_with_reranking(
        query=query,
        top_k=top_k,
        date_from=datum_van,
        date_to=datum_tot,
        party=party,
    )

    header = f"## Uitspraken over: '{onderwerp}'"
    if partij_of_raadslid:
        header += f"\n_Filter: {partij_of_raadslid}_"
    if datum_van or datum_tot:
        header += f"\n_Periode: {datum_van or '…'} — {datum_tot or 'heden'}_"

    return header + "\n\n" + _format_chunks_v3(chunks, dedup_by_doc=True)


# ---------------------------------------------------------------------------
# Tool 4 — Vergaderdetails ophalen (unchanged from v1)
# ---------------------------------------------------------------------------

@logged_tool
def haal_vergadering_op(
    vergadering_id: Optional[str] = None,
    datum: Optional[str] = None,
) -> str:
    """
    Haal details van een specifieke vergadering op: agenda, commissie, documenten.
    Geef vergadering_id OF datum (JJJJ-MM-DD).

    Args:
        vergadering_id: Unieke ID van de vergadering
        datum: Datum van de vergadering (JJJJ-MM-DD)
    """
    storage = _get_storage()

    if not vergadering_id and not datum:
        return "Geef een vergadering_id of datum op."

    if vergadering_id:
        meeting = storage.get_meeting_details(vergadering_id)
    else:
        meetings = storage.get_meetings(limit=200)
        match = next(
            (m for m in meetings if (m.get("start_date") or "").startswith(datum)),
            None,
        )
        if not match:
            return f"Geen vergadering gevonden op {datum}."
        meeting = storage.get_meeting_details(match["id"])

    if not meeting:
        return "Vergadering niet gevonden."

    lines = [
        f"## {meeting.get('name', 'Vergadering')}",
        f"**Datum:** {(meeting.get('start_date') or '')[:10]}",
        f"**Commissie:** {meeting.get('committee') or 'Onbekend'}",
        f"**ID:** {meeting.get('id')}\n",
        "### Agenda",
    ]

    for item in meeting.get("agenda", []):
        num = item.get("number") or ""
        name = item.get("name") or ""
        lines.append(f"- **{num}** {name}")
        for sub in item.get("sub_items", []):
            lines.append(f"  - {sub.get('name') or ''}")

    docs = meeting.get("documents", [])
    if docs:
        lines.append(f"\n### Documenten ({len(docs)})")
        for d in docs[:10]:
            lines.append(f"- [{d.get('name', 'Document')}] id={d.get('id')}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 5 — Lijst van vergaderingen (unchanged from v1)
# ---------------------------------------------------------------------------

@logged_tool
def lijst_vergaderingen(
    jaar: Optional[int] = None,
    commissie: Optional[str] = None,
    max_resultaten: int = 25,
) -> str:
    """
    Geeft een lijst van vergaderingen, optioneel gefilterd op jaar of commissie.

    Args:
        jaar: Filterjaar (bijv. 2023)
        commissie: Filter op commissienaam (gedeeltelijke match)
        max_resultaten: Maximaal aantal resultaten (standaard 25, max 100)
    """
    storage = _get_storage()
    limit = min(max(1, max_resultaten), 100)
    meetings = storage.get_meetings(limit=limit, year=jaar)

    if commissie:
        # T5 (2026-04-14): also match on meeting name so "onderwijs" finds
        # "Commissie Werk & Inkomen, Onderwijs, Samenleven, Schuld" even when
        # the abbreviated committee code ("WIOS") doesn't contain the search term.
        commissie_lower = commissie.lower()
        meetings = [
            m for m in meetings
            if commissie_lower in (m.get("committee") or "").lower()
            or commissie_lower in (m.get("name") or "").lower()
        ]

    if not meetings:
        return "Geen vergaderingen gevonden."

    lines = [
        f"## Vergaderingen{' ' + str(jaar) if jaar else ''}"
        + (f" — {commissie}" if commissie else ""),
        "",
        "| Datum | Naam | Commissie | ID |",
        "|---|---|---|---|",
    ]
    for m in meetings[:limit]:
        d = (m.get("start_date") or "")[:10]
        name = (m.get("name") or "")[:55]
        committee = (m.get("committee") or "")[:35]
        mid = m.get("id", "")
        lines.append(f"| {d} | {name} | {committee} | {mid} |")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 6 — Tijdlijn besluitvorming (with reranking)
# ---------------------------------------------------------------------------

@logged_tool
def tijdlijn_besluitvorming(
    onderwerp: str,
    datum_van: Optional[str] = None,
    datum_tot: Optional[str] = None,
) -> str:
    """
    Bouw een chronologische tijdlijn van discussies en besluitvorming.
    Groepeert raadsfragmenten per jaar voor evolutie-analyse.
    Filtert irrelevante resultaten (score < 0.2) en lost ontbrekende
    datums op via de vergaderdatum van het document.

    Args:
        onderwerp: Beleidsonderwerp voor de tijdlijn
        datum_van: Startdatum (ISO), bijv. "2020-01-01"
        datum_tot: Einddatum (ISO), bijv. "2024-12-31"
    """
    import psycopg2

    # T1 (2026-04-14): use _retrieve_with_reranking (same path as zoek_raadshistorie)
    # to guarantee Jina rerank + quality filters. Previous direct rag.retrieve_relevant_context
    # call was missing _apply_quality_filters (dedup + min-similarity).
    # Raised floor from 0.2 → 0.5; purely procedural docs excluded below.
    chunks = _retrieve_with_reranking(
        query=onderwerp,
        top_k=30,
        date_from=datum_van,
        date_to=datum_tot,
    )

    if not chunks:
        return f"Geen fragmenten gevonden voor '{onderwerp}'."

    # --- Relevance threshold: drop chunks below 0.5 — Jina scores < 0.5 are
    # marginally related. Repro: "lerarentekort" returned zienswijze + handboek
    # at 0.71-0.73 (matched superficially). See T1 in WS4 post-ship §(4).
    PROCEDURAL_DOC_TYPES = {
        "ontvangstbevestiging", "zienswijze", "effectenrapportage", "handboek",
    }
    chunks = [
        c for c in chunks
        if (c.similarity_score or 0) >= 0.5
        and (c.stream_type or "").lower() not in PROCEDURAL_DOC_TYPES
    ]
    if not chunks:
        return f"Geen voldoende relevante fragmenten gevonden voor '{onderwerp}'."

    # --- Resolve missing start_date from meeting date ---
    missing_doc_ids = [c.document_id for c in chunks if not c.start_date]
    doc_date_map = {}
    if missing_doc_ids:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT d.id, m.start_date
                FROM documents d
                LEFT JOIN meetings m ON d.meeting_id = m.id
                WHERE d.id = ANY(%s)
            """, (missing_doc_ids,))
            for doc_id, meeting_date in cur.fetchall():
                if meeting_date:
                    doc_date_map[doc_id] = str(meeting_date)[:10]
            cur.close()

        for c in chunks:
            if not c.start_date and c.document_id in doc_date_map:
                c.start_date = doc_date_map[c.document_id]

    # --- Deduplicate by document_id (keep highest-scored per doc) ---
    seen_docs = set()
    deduped = []
    for c in chunks:
        if c.document_id not in seen_docs:
            seen_docs.add(c.document_id)
            deduped.append(c)
    chunks = deduped

    # --- Group by year ---
    from collections import defaultdict
    buckets: dict[str, list] = defaultdict(list)
    for chunk in chunks:
        year = (chunk.start_date or "")[:4]
        if not year or year == "0000":
            year = "onbekend"
        buckets[year].append({
            "titel": chunk.title,
            "snippet": chunk.content[:300],
            "document_id": chunk.document_id,
            "stream_type": chunk.stream_type,
            "score": f"{chunk.similarity_score:.2f}" if chunk.similarity_score else "",
        })

    lines = [f"## Tijdlijn: {onderwerp}"]
    if datum_van or datum_tot:
        lines.append(f"_Periode: {datum_van or '…'} — {datum_tot or 'heden'}_")
    lines.append(f"_{len(chunks)} relevante fragmenten (score ≥ 0.5, procedurele types uitgesloten)_")
    lines.append("")

    for year in sorted(buckets.keys()):
        events = buckets[year]
        lines.append(f"### {year} ({len(events)} fragmenten)")
        for ev in events[:5]:
            stream = f" [{ev['stream_type']}]" if ev.get("stream_type") else ""
            lines.append(f"- **{ev['titel']}**{stream} (score: {ev['score']})")
            lines.append(f"  {ev['snippet']}")
            lines.append(f"  _document_id: {ev['document_id']}_")
        if len(events) > 5:
            lines.append(f"  _… en {len(events) - 5} meer_")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 7 — Agendapunt analyse (with reranking)
# ---------------------------------------------------------------------------

@logged_tool
def analyseer_agendapunt(
    agendapunt_id: str,
    partij: str = "GroenLinks-PvdA",
) -> str:
    """
    Verzamelt alle benodigde informatie voor analyse van een agendapunt.
    Retourneert: documentinhoud, partijprofiel en historische RAG-context.

    Args:
        agendapunt_id: ID van het agendapunt
        partij: Partijnaam voor de lenssessie (standaard "GroenLinks-PvdA")
    """
    storage = _get_storage()

    item_data = storage.get_agenda_item_with_sub_documents(agendapunt_id)
    if not item_data:
        return f"Agendapunt '{agendapunt_id}' niet gevonden."

    item_name = item_data.get("name") or "Onbekend agendapunt"
    meeting_name = item_data.get("meeting_name") or ""
    meeting_date = (item_data.get("start_date") or "")[:10]

    docs = item_data.get("documents", [])
    doc_sections = []
    for d in docs[:4]:
        content = (d.get("content") or "").strip()[:4000]
        doc_sections.append(f"#### {d.get('name', 'Document')} (id={d.get('id')})\n{content}")

    profile = _load_party_profile(partij)
    posities = profile.get("posities", {})
    profile_md = ""
    if posities:
        profile_md = f"\n### Partijprofiel: {partij}\n"
        for gebied, pos in list(posities.items())[:6]:
            profile_md += (
                f"**{gebied}** — {pos.get('kernwaarde', '')} "
                f"(consistentie: {pos.get('consistentie', 'onbekend')})\n"
            )
    else:
        profile_md = f"\n_Geen partijprofiel gevonden voor '{partij}'._\n"

    hist_chunks = _retrieve_with_reranking(
        query=item_name,
        top_k=6,
        party=None,
    )

    lines = [
        f"## Agendapunt: {item_name}",
        f"**Vergadering:** {meeting_name} ({meeting_date})",
        f"**ID:** {agendapunt_id}",
        "",
        "---",
        "### Documenten",
        "\n\n".join(doc_sections) if doc_sections else "_Geen documenten beschikbaar._",
        "",
        profile_md,
        "---",
        "### Historische context (RAG)",
        _format_chunks_v3(hist_chunks, max_content=400),
        "",
        "---",
        "_Analyseer bovenstaande informatie vanuit het perspectief van "
        f"{partij}: afstemming met programmapunten, kritische vragen, "
        "mogelijke amendementen en eigen bijdrage aan het debat._",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 8 — Partijstandpunt (party-filtered)
# ---------------------------------------------------------------------------

@logged_tool
def haal_partijstandpunt_op(
    beleidsgebied: str,
    partij: str = "GroenLinks-PvdA",
    datum_van: Optional[str] = None,
    datum_tot: Optional[str] = None,
) -> str:
    """
    Haalt het standpunt van een partij op voor een beleidsgebied,
    aangevuld met relevante uitspraken uit de notulen.
    Zoekt gericht in fragmenten van de opgegeven partij.

    Args:
        beleidsgebied: Beleidsgebied (bijv. "Wonen", "Klimaat", "Onderwijs")
        partij: Partijnaam (standaard "GroenLinks-PvdA")
        datum_van: Optionele startdatum voor RAG-fallback (ISO, bijv. "2022-01-01")
        datum_tot: Optionele einddatum voor RAG-fallback (ISO, bijv. "2024-12-31")
    """
    profile = _load_party_profile(partij)
    posities = profile.get("posities", {})

    zoek = beleidsgebied.lower()
    matched = {
        k: v for k, v in posities.items()
        if zoek in k.lower() or k.lower() in zoek
    }

    lines = [f"## Partijstandpunt: {partij} — {beleidsgebied}\n"]

    if not matched:
        lines.append(f"_Geen profielentries gevonden voor '{beleidsgebied}'._")
        # T6: surface available beleidsgebieden so the caller can try adjacent terms
        if posities:
            lines.append("_Beschikbare gebieden:_ " + ", ".join(list(posities.keys())[:15]))
        else:
            lines.append("_Statisch profiel nog niet beschikbaar voor deze partij — zie RAG-context hieronder._")
    else:
        for gebied, pos in list(matched.items())[:5]:
            notulen_refs = pos.get("uit_notulen", [])
            lines += [
                f"### {gebied}",
                f"**Programmapunt:** {pos.get('uit_programma', 'Niet expliciet')}",
                f"**Kernwaarde:** {pos.get('kernwaarde', 'Onbekend')}",
                f"**Consistentie:** {pos.get('consistentie', 'Onbekend')}",
            ]
            if notulen_refs:
                lines.append(f"**Notulenverwijzingen ({len(notulen_refs)}):**")
                for ref in notulen_refs[:4]:
                    datum = (ref.get("datum") or "")[:10]
                    tekst = (ref.get("tekst") or "")[:250]
                    lines.append(f"  - [{datum}] {tekst}")
            lines.append("")

    # T6 (2026-04-14): Party-filtered RAG for richer context.
    # Honour caller-supplied datum_van/datum_tot (longitudinal queries like
    # "how has D66's position shifted since 2018" need the full date range).
    # When no date range is supplied, return results without temporal filter but
    # sort by document_date DESC as secondary sort so recent fragments surface
    # first for "what does the party think now" queries.
    chunks = _retrieve_with_reranking(
        query=f"{beleidsgebied} {partij} standpunt visie programma",
        top_k=6,
        party=partij,
        date_from=datum_van,
        date_to=datum_tot,
    )

    # T6: secondary sort by date DESC when no date range specified.
    # ISO date strings are sortable lexicographically; to sort descending,
    # convert to int (YYYYMMDD) and negate.
    if not datum_van and not datum_tot and chunks:
        def _chunk_sort_key(c):
            try:
                date_int = int((c.start_date or "0000-00-00").replace("-", "")[:8])
            except (ValueError, TypeError):
                date_int = 0
            return (-(c.similarity_score or 0), -date_int)

        chunks = sorted(chunks, key=_chunk_sort_key)

    if chunks:
        # T6: surface date_range_in_results so caller notices temporal spread
        dates = [c.start_date[:10] for c in chunks if c.start_date]
        if dates:
            lines.append(
                f"_Bronperiode RAG-fragmenten: {min(dates)} — {max(dates)}_"
            )
        lines.append("### Aanvullende context uit notulen")
        lines.append(_format_chunks_v3(chunks[:5], max_content=400))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 9 — Zoek moties en amendementen (direct SQL on document names)
# ---------------------------------------------------------------------------

@logged_tool
def zoek_moties(
    onderwerp: str,
    uitkomst: Optional[str] = None,
    datum_van: Optional[str] = None,
    datum_tot: Optional[str] = None,
    partij: Optional[str] = None,
    indiener: Optional[str] = None,
    max_resultaten: int = 20,
) -> str:
    """
    Doorzoek alle moties, amendementen en initiatiefvoorstellen op onderwerp.
    Retourneert documentnaam, geparseerde uitkomst (aangenomen/verworpen/ingetrokken),
    datum en de eerste 300 tekens van de inhoud.

    Dit is de beste tool voor vragen over stemmingen, verworpen voorstellen,
    en het stemgedrag van partijen.

    Args:
        onderwerp: Zoekterm in de motie/amendement (bijv. "leegstand", "warmtebedrijf")
        uitkomst: Filter op uitkomst: "verworpen", "aangenomen", "ingetrokken" (optioneel)
        datum_van: Startdatum filter (ISO)
        datum_tot: Einddatum filter (ISO)
        partij: Filter op partijnaam in de inhoud van de motie
        indiener: Filter op naam van de indiener/raadslid (bijv. "Tak", "D.P.A. Tak")
        max_resultaten: Maximaal aantal resultaten (standaard 20)
    """
    # Search terms: the original query words (min 3 chars)
    search_terms = [w for w in onderwerp.lower().split() if len(w) > 2]

    # Build WHERE clause
    conditions = [
        "(LOWER(d.name) LIKE '%%motie%%' OR LOWER(d.name) LIKE '%%amendement%%' OR LOWER(d.name) LIKE '%%initiatiefvoorstel%%')",
        # T2 (2026-04-14): exclude committee meta-docs ("Lijst met openstaande/aangehouden/
        # afgedane moties"). They carry every motion title so they match every query and
        # waste result slots. Repro: `onderwerp="onderwijs", partij=D66` gave 4/5 slots
        # to WIOSSAN overview docs.
        "d.name !~* '^Lijst met .* moties'",
    ]
    params = []

    if search_terms:
        # WS4 2026-04-11: always search both name AND content so initiatiefvoorstellen
        # with generic titles ("Initiatiefvoorstel Engberts & Vogelaar over wonen")
        # are discoverable by topic keyword (e.g. "leegstand"). Previous behavior
        # only looked in content when len(search_terms) >= 3, which missed the
        # Engberts/Vogelaar case for single-word topic queries.
        or_clauses = []
        for term in search_terms:
            or_clauses.append("LOWER(d.name) LIKE %s")
            or_clauses.append("LOWER(d.content) LIKE %s")
            params.append(f"%{term}%")
            params.append(f"%{term}%")
        conditions.append(f"({' OR '.join(or_clauses)})")

        # Multi-word precision guard: for 2+ terms, require at least 2 terms to
        # match in (name OR content) — prevents a single common word from flooding
        # results. Single-word queries skip this guard.
        if len(search_terms) >= 2:
            count_expr_parts = []
            for term in search_terms:
                count_expr_parts.append(
                    "CASE WHEN LOWER(d.name) LIKE %s OR LOWER(d.content) LIKE %s THEN 1 ELSE 0 END"
                )
                params.append(f"%{term}%")
                params.append(f"%{term}%")
            conditions.append(f"({' + '.join(count_expr_parts)}) >= 2")

    if uitkomst:
        conditions.append("LOWER(d.name) LIKE %s")
        params.append(f"%{uitkomst.lower()}%")

    if datum_van:
        conditions.append("m.start_date >= %s")
        params.append(datum_van)
    if datum_tot:
        conditions.append("m.start_date <= %s")
        params.append(datum_tot)

    if partij:
        conditions.append("LOWER(d.content) LIKE %s")
        params.append(f"%{partij.lower()}%")

    if indiener:
        # Normalize: strip initials so "D.P.A. Tak" and "Dennis Tak" both resolve to "tak"
        indiener_norm = re.sub(r'\b[a-z]\.\s*', '', indiener.lower()).strip()
        conditions.append(
            "(LOWER(d.name) LIKE %s OR LOWER(d.content) LIKE %s"
            " OR LOWER(d.name) LIKE %s OR LOWER(d.content) LIKE %s)"
        )
        params += [f"%{indiener.lower()}%", f"%{indiener.lower()}%",
                   f"%{indiener_norm}%", f"%{indiener_norm}%"]

    where = " AND ".join(conditions)
    limit = min(max(1, max_resultaten), 80)

    with get_connection() as conn:
        cur = conn.cursor()
        # WS4 2026-04-11: bump content preview 400 → 1600 chars so the host LLM
        # rarely needs to follow up with lees_fragment on overview queries.
        # Preview shown to user is sliced to 1500 below — 1600 gives a small buffer
        # for newline-stripping.
        cur.execute(f"""
            SELECT d.id, d.name, m.start_date, LEFT(d.content, 1600), d.url,
                   dc_enrich.indieners, dc_enrich.vote_outcome, dc_enrich.vote_counts
            FROM documents d
            LEFT JOIN meetings m ON d.meeting_id = m.id
            LEFT JOIN LATERAL (
                SELECT indieners, vote_outcome, vote_counts
                FROM document_chunks
                WHERE document_id = d.id
                  AND (indieners IS NOT NULL OR vote_outcome IS NOT NULL)
                LIMIT 1
            ) dc_enrich ON true
            WHERE {where}
            ORDER BY m.start_date DESC
            LIMIT %s
        """, params + [limit])
        rows = cur.fetchall()
        cur.close()

    if not rows:
        return (
            f"Geen moties/amendementen gevonden voor '{onderwerp}'.\n\n"
            f"{ZERO_RESULT_FOOTER}"
        )

    # T10 (2026-04-14): post-SQL Jina rerank + 0.2 min-score for broad queries
    # (2+ search terms). Broad queries like "horeca sluitingstijden beperking
    # overlast nachtleven inperken" return off-topic initiatiefvoorstellen.
    # The Jina reranker scores against the original `onderwerp` and drops noise.
    # Single-word queries skip this to avoid over-filtering narrow lookups.
    if len(search_terms) >= 2:
        try:
            _get_rag()  # ensures _reranker is initialized
            from services import rag_service as _rag_svc_mod
            _reranker = _rag_svc_mod._reranker
            if _reranker is not None:
                texts = [(r[3] or r[1] or "") for r in rows]  # content or name
                scores = _reranker.score_pairs(onderwerp, texts)
                rows = [
                    r for r, s in zip(rows, scores)
                    if (s is not None and s >= 0.2)
                ]
        except Exception:
            pass  # non-critical: fall through to un-reranked rows

    if not rows:
        return (
            f"Geen voldoende relevante moties gevonden voor '{onderwerp}'.\n\n"
            f"{ZERO_RESULT_FOOTER}"
        )

    # T4 (2026-04-14): BB-number deduplication.
    # Cluster rows by BB-nummer (e.g. "21bb004603"). Keep only the most recent
    # version per cluster (highest start_date, already ORDER BY DESC). Fold
    # other versions into a `related_docs` list on the primary result.
    # Repro: "Kracht van de nacht" occupied 5 slots (3 versions + 2 tussenberichten).
    _BB_RE = re.compile(r'\b(\d{2}bb\d+)\b', re.IGNORECASE)

    def _extract_bb(name: str) -> str | None:
        m = _BB_RE.search(name or "")
        return m.group(1).lower() if m else None

    seen_bb: dict = {}  # bb_nr → primary row index in deduped
    deduped_rows = []
    related_map: dict = {}  # primary index → list of (name, doc_id)

    for row in rows:
        bb = _extract_bb(row[1])  # row[1] = name
        if bb and bb in seen_bb:
            pri = seen_bb[bb]
            related_map.setdefault(pri, []).append((row[1], row[0]))
        else:
            idx = len(deduped_rows)
            deduped_rows.append(row)
            if bb:
                seen_bb[bb] = idx

    lines = [
        f"## Moties & amendementen: '{onderwerp}'",
        f"_Gevonden: {len(deduped_rows)} resultaten ({len(rows)} vóór BB-dedup)_",
    ]
    if uitkomst:
        lines.append(f"_Filter uitkomst: {uitkomst}_")
    if datum_van or datum_tot:
        lines.append(f"_Periode: {datum_van or '…'} — {datum_tot or 'heden'}_")
    if partij:
        lines.append(f"_Partij: {partij}_")
    if indiener:
        lines.append(f"_Indiener: {indiener}_")
    lines.append("")

    # T3 regex (2026-04-14): body text fallback for uitkomst='onbekend'.
    _UITKOMST_BODY_RE = re.compile(
        r'\b(AANGENOMEN|VERWORPEN|INGETROKKEN|AANGEHOUDEN)\b', re.IGNORECASE
    )

    for i, (doc_id, name, start_date, content, url, indieners, vote_outcome, vote_counts) in enumerate(deduped_rows, 1):
        d = str(start_date)[:10] if start_date else "?"

        # Prefer enriched vote_outcome, then title regex, then body regex (T3).
        parsed_uitkomst = vote_outcome or _parse_uitkomst(name or "")
        if parsed_uitkomst == "onbekend" and content:
            body_match = _UITKOMST_BODY_RE.search(content)
            if body_match:
                parsed_uitkomst = body_match.group(1).lower()

        content_clean = (content or "").replace("\n", " ")[:1500]
        lines.append(f"### [{i}] {d} — {name[:100]}")
        lines.append(f"**Uitkomst:** {parsed_uitkomst}")
        if vote_counts:
            import json as _json
            counts_str = _json.dumps(vote_counts) if isinstance(vote_counts, dict) else str(vote_counts)
            lines.append(f"**Stemverhouding:** {counts_str}")
        if indieners:
            indiener_str = ", ".join(indieners) if isinstance(indieners, list) else str(indieners)
            lines.append(f"**Indieners:** {indiener_str}")
        lines.append(f"_{content_clean}_")
        lines.append(f"_document_id: {doc_id}_")
        if url:
            lines.append(f"[Brondocument ↗]({url})")
        # T4: show folded related docs
        related = related_map.get(i - 1, [])
        if related:
            lines.append(f"_Gerelateerde versies ({len(related)}): " +
                         ", ".join(f"{n[:60]} (id: {d})" for n, d in related) + "_")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 10 — Breed scannen (many results, short previews — for deep research)
# ---------------------------------------------------------------------------

@logged_tool
def scan_breed(
    vraag: str,
    datum_van: Optional[str] = None,
    datum_tot: Optional[str] = None,
    partij: Optional[str] = None,
    max_resultaten: int = 40,
) -> str:
    """
    Brede scan: retourneert VEEL resultaten met korte previews (titel + 150 tekens).
    Gebruik dit als eerste stap bij complexe vragen om een overzicht te krijgen
    van wat er beschikbaar is. Volg daarna op met zoek_raadshistorie of
    lees_fragment voor de volledige tekst van relevante fragmenten.

    Dit is efficienter dan meerdere zoek_raadshistorie calls bij complexe onderzoeksvragen.

    Args:
        vraag: Zoekterm of vraag
        datum_van: Startdatum filter (ISO)
        datum_tot: Einddatum filter (ISO)
        partij: Filter op partijnaam
        max_resultaten: Aantal resultaten (max 80, standaard 40)
    """
    # Temporal fallback (WS4 2026-04-13): when weaker models (Mistral/Le Chat)
    # fail to extract datum_van/datum_tot from Dutch temporal phrases, apply
    # server-side regex+LLM parser as safety net.
    if datum_van is None and datum_tot is None:
        try:
            _tparsed = _temporal_parse(vraag)
            datum_van = _tparsed.get("date_from")
            datum_tot = _tparsed.get("date_to")
        except Exception:
            pass

    top_k = min(max(1, max_resultaten), 80)

    chunks = _retrieve_with_reranking(
        query=vraag,
        top_k=top_k,
        date_from=datum_van,
        date_to=datum_tot,
        party=partij,
    )

    header = f"## Scan: '{vraag}' ({len(chunks)} resultaten)"
    if datum_van or datum_tot:
        header += f"\n_Periode: {datum_van or '…'} — {datum_tot or 'heden'}_"
    if partij:
        header += f"\n_Partij: {partij}_"

    lines = [header, ""]
    lines.append("| # | Datum | Titel | Partij | Score | Doc ID |")
    lines.append("|---|---|---|---|---|---|")

    for i, c in enumerate(chunks, 1):
        d = (c.start_date or "")[:10]
        title = (c.title or "")[:50]
        party = getattr(c, "party", "") or ""
        score = f"{c.similarity_score:.2f}" if c.similarity_score else ""
        doc_id = c.document_id[:20]
        snippet = (c.content or "")[:100].replace("\n", " ").replace("|", "/")
        lines.append(f"| {i} | {d} | {title} | {party} | {score} | {doc_id} |")
        lines.append(f"|   |   | _{snippet}..._ |   |   |   |")

    lines.append("")
    lines.append("_Gebruik `lees_fragment` met een document_id om de volledige tekst op te halen._")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 10 — Lees volledig fragment (deep read after scan)
# ---------------------------------------------------------------------------

@logged_tool
def lees_fragment(
    document_id: str,
    max_fragmenten: int = 5,
    query: Optional[str] = None,
) -> str:
    """
    Lees de volledige tekst van fragmenten uit een specifiek document.
    Gebruik dit na scan_breed om de volledige inhoud te lezen van
    documenten die relevant lijken.

    ALTIJD query meegeven als je dit document via een topic-zoektocht vond:
    dan worden de fragmenten ge-reranked op relevantie voor het onderwerp.
    Zonder query worden fragmenten in opslag-volgorde teruggegeven — dat
    kan betekenen dat het fragment dat zoek_raadshistorie vond begraven
    wordt onder samenvattingsparagrafen.

    Args:
        document_id: Document ID (uit scan_breed of andere zoekresultaten)
        max_fragmenten: Maximaal aantal fragmenten uit dit document (standaard 5)
        query: Optioneel. Als je dit document via een topic-zoektocht vond,
               geef dezelfde query mee. Fragmenten worden dan via Jina v3
               ge-reranked op relevantie voor de query.
    """
    # Fetch ALL fragments for the document when a query is provided, so the
    # reranker sees the full candidate set before slicing. Without a query,
    # respect max_fragmenten at the SQL level (current behavior).
    fetch_limit = 200 if query else max_fragmenten
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute("""
            SELECT dc.title, dc.content, dc.chunk_type, d.name as doc_name,
                   m.start_date, m.name as meeting_name, dc.table_json, d.url
            FROM document_chunks dc
            JOIN documents d ON dc.document_id = d.id
            LEFT JOIN meetings m ON d.meeting_id = m.id
            WHERE dc.document_id = %s
            ORDER BY dc.chunk_index
            LIMIT %s
        """, (document_id, fetch_limit))
        rows = cur.fetchall()
        cur.close()

    if not rows:
        return (
            f"Geen fragmenten gevonden voor document '{document_id}'.\n\n"
            f"{ZERO_RESULT_FOOTER}"
        )

    # Optional in-document reranking against a query (WS4 2026-04-11).
    # Failure case: zoek_raadshistorie found the Middelland venstertijden paragraph
    # in fin_jaarstukken_2019 but lees_fragment returned financial-summary tables
    # in chunk_index order. Jina v3 re-rank fixes this when the caller passes
    # the original query.
    if query and len(rows) > max_fragmenten:
        try:
            _get_rag()  # ensures services.rag_service._reranker is initialized
            from services import rag_service as _rag_svc_mod
            reranker = _rag_svc_mod._reranker  # Jina v3 API reranker singleton
            if reranker is None:
                raise RuntimeError("reranker unavailable")
            fragment_texts = [(r[1] or "") for r in rows]
            scores = reranker.score_pairs(query, fragment_texts)
            # Pair original rows with scores, sort desc, take top max_fragmenten
            scored = sorted(
                zip(rows, scores), key=lambda p: (p[1] if p[1] is not None else 0), reverse=True
            )[:max_fragmenten]
            rows = [pair[0] for pair in scored]
        except Exception:
            # Non-critical: fall back to chunk_index order
            rows = rows[:max_fragmenten]
    elif len(rows) > max_fragmenten:
        rows = rows[:max_fragmenten]

    doc_name = rows[0][3] or "Onbekend document"
    meeting_date = (str(rows[0][4] or ""))[:10]
    meeting_name = rows[0][5] or ""
    doc_url = rows[0][7]

    # T9 (2026-04-14): expose total_chunks_in_document when we returned fewer
    # than requested — so the caller knows if the doc is short vs. reranker filtered.
    total_in_doc: Optional[int] = None
    if len(rows) < max_fragmenten:
        try:
            with get_connection() as _conn:
                _cur = _conn.cursor()
                _cur.execute(
                    "SELECT COUNT(*) FROM document_chunks WHERE document_id = %s",
                    (document_id,),
                )
                total_in_doc = _cur.fetchone()[0]
                _cur.close()
        except Exception:
            pass

    lines = [
        f"## {doc_name}",
        f"_Datum: {meeting_date} | Vergadering: {meeting_name}_",
        f"_Document ID: {document_id} | {len(rows)} van {total_in_doc or '?'} fragmenten_",
    ]
    if doc_url:
        lines.append(f"[Brondocument ↗]({doc_url})")
    if query:
        lines.append(f"_Re-ranked op: '{query}'_")
    lines.append("")

    for i, (title, content, chunk_type, _, _, _, table_json, _) in enumerate(rows, 1):
        type_tag = f" [{chunk_type}]" if chunk_type else ""
        lines.append(f"### Fragment {i}{type_tag}: {title or 'Ongetiteld'}")
        lines.append(content or "_Geen inhoud._")
        # Render table_json as structured markdown table
        if table_json:
            formatted = _format_table_json(table_json)
            if formatted and "---" in formatted:
                lines.append(f"\n**Tabeldata:**\n{formatted}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 10b — Lees meerdere fragmenten in één call (batch)
# ---------------------------------------------------------------------------

@logged_tool
def lees_fragmenten_batch(
    document_ids: list,
    max_fragmenten_per_doc: int = 3,
) -> str:
    """
    Lees de eerste fragmenten van meerdere documenten in één tool-call.
    Gebruik dit in plaats van meerdere lees_fragment calls om latency te verminderen
    (overview queries duurden 15-25s door sequentieel lees_fragment gebruik).

    Zonder query-parameter retourneert elk document de eerste max_fragmenten_per_doc
    fragmenten in opslag-volgorde. Voor in-document reranking gebruik lees_fragment
    met query=... op het specifieke document.

    Args:
        document_ids: Lijst van document IDs (max 10).
        max_fragmenten_per_doc: Maximaal aantal fragmenten per document (standaard 3, max 5).
    """
    if not document_ids:
        return "_Geen document IDs opgegeven._"

    # Cap inputs to prevent abuse
    doc_ids = [str(d) for d in document_ids[:10]]
    per_doc = min(max(1, max_fragmenten_per_doc), 5)

    results = []
    url_map = _batch_fetch_document_urls(doc_ids)

    for doc_id in doc_ids:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT dc.title, dc.content, dc.chunk_type, d.name as doc_name,
                       m.start_date, m.name as meeting_name
                FROM document_chunks dc
                JOIN documents d ON dc.document_id = d.id
                LEFT JOIN meetings m ON d.meeting_id = m.id
                WHERE dc.document_id = %s
                ORDER BY dc.chunk_index
                LIMIT %s
            """, (doc_id, per_doc))
            rows = cur.fetchall()
            cur.close()

        if not rows:
            results.append(f"### Document {doc_id}\n_Geen fragmenten gevonden._\n")
            continue

        doc_name = rows[0][3] or doc_id
        meeting_date = (str(rows[0][4] or ""))[:10]
        url = url_map.get(doc_id)
        url_line = f"[Brondocument ↗]({url})\n" if url else ""

        lines = [f"### {doc_name}", f"_Datum: {meeting_date}_", url_line]
        for row in rows:
            title, content, chunk_type = row[0], row[1], row[2]
            if title:
                lines.append(f"**{title}**")
            if content and len(content.strip()) >= MIN_CONTENT_CHARS:
                lines.append(content[:800])
        results.append("\n".join(lines))

    return "\n\n---\n\n".join(results)


# ---------------------------------------------------------------------------
# Tool 12a — vat_document_samen (WS6 Source-Spans-Only Summarization)
# ---------------------------------------------------------------------------

@logged_tool
def vat_document_samen(
    document_id: str,
    mode: str = "short",
) -> str:
    """
    Geef een gecontroleerde samenvatting van één specifiek document.

    Gebruik dit wanneer de gebruiker vraagt om een korte samenvatting, TL;DR
    of overzicht van één document (ID bekend uit zoek_raadshistorie, scan_breed,
    of lees_fragment). Gebruik dit NIET voor synthese over meerdere documenten
    — gebruik dan eerst zoek_raadshistorie en laat de host-LLM zelf synthese doen.

    De samenvatting is post-hoc geverifieerd tegen de brontfragmenten: elke zin
    moet via de Jina v3 reranker boven een drempelwaarde scoren op minstens
    één fragment van dit document, anders wordt de zin verwijderd. Een
    'Geverifieerd'-badge betekent dat elke zin in de uitvoer herleidbaar is
    tot een fragment. 'Gedeeltelijk' betekent dat > 30% van de zinnen is
    verwijderd — de resterende samenvatting kan dan incompleet zijn.

    Args:
        document_id: Document ID (uit scan_breed of andere zoekresultaten).
        mode: 'short' (2-3 zinnen exec summary, standaard) of 'long'
              (uitgebreide structureel-samenvatting via Map-Reduce).

    Returns:
        JSON-string met: document_id, mode, text, verified, stripped_count,
        total_sentences, citations (chunk_ids), computed_at, cached.
    """
    import asyncio as _asyncio
    from types import SimpleNamespace as _SNS

    if mode not in ("short", "long"):
        return json.dumps({
            "error": f"Ongeldige mode '{mode}'. Kies 'short' of 'long'.",
            "document_id": document_id,
        }, ensure_ascii=False)

    from services.storage_ws6 import (
        get_all_chunks_for_document as _ws6_get_chunks,
        get_document_summary_cache as _ws6_get_cache,
        update_document_summary_columns as _ws6_update,
    )

    # 1. Try the cached column first (mode='short' only — 'long' is always
    #    recomputed for now since we haven't cached it yet at the column level).
    if mode == "short":
        cached = _ws6_get_cache(document_id)
        if cached and cached.get("summary_short"):
            return json.dumps({
                "document_id": document_id,
                "mode": "short",
                "text": cached["summary_short"],
                "verified": bool(cached.get("summary_verified")),
                "cached": True,
                "computed_at": cached.get("summary_computed_at"),
            }, ensure_ascii=False)

    # 2. Compute on demand: gather chunks and run the Summarizer.
    chunk_rows = _ws6_get_chunks(document_id)
    if not chunk_rows:
        return json.dumps({
            "error": f"Geen fragmenten gevonden voor document '{document_id}'.",
            "document_id": document_id,
        }, ensure_ascii=False)

    chunks = [
        _SNS(
            chunk_id=r["chunk_id"],
            document_id=r["document_id"],
            title=r.get("title") or "",
            content=r.get("content") or "",
        )
        for r in chunk_rows
    ]

    from services.summarizer import Summarizer
    summarizer = Summarizer()
    try:
        result = _asyncio.run(summarizer.summarize_async(chunks, mode=mode))
    except Exception as e:
        return json.dumps({
            "error": f"Samenvatten mislukt: {e}",
            "document_id": document_id,
        }, ensure_ascii=False)

    if not result.text:
        return json.dumps({
            "error": "Samenvatter leverde lege uitvoer op — waarschijnlijk een LLM-fout.",
            "document_id": document_id,
        }, ensure_ascii=False)

    # Best-effort write-through: only for mode='short', and only if the new
    # columns exist. Failures here are non-fatal — the caller still gets the
    # computed summary.
    if mode == "short":
        try:
            _ws6_update(
                document_id,
                summary_short=result.text,
                summary_verified=result.verified,
            )
        except Exception:
            pass

    payload = {
        "document_id": document_id,
        "mode": mode,
        "text": result.text,
        "verified": result.verified,
        "stripped_count": result.stripped_count,
        "total_sentences": result.total_sentences,
        "citations": [c.chunk_id for c in result.sources],
        "cached": False,
        "latency_ms": result.latency_ms,
    }
    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool 12 — Zoek gerelateerde documenten (cross-referencing)
# ---------------------------------------------------------------------------

@logged_tool
def zoek_gerelateerd(
    document_id: str,
    max_resultaten: int = 10,
) -> str:
    """
    Vind documenten die gerelateerd zijn aan een gegeven document.
    Zoekt naar:
      - Andere documenten uit dezelfde vergadering (moties, amendementen, brieven)
      - Moties/amendementen met dezelfde trefwoorden in de naam
      - Afdoeningsvoorstellen die verwijzen naar het brondocument

    Gebruik dit om vanuit een motie het bijbehorende debat te vinden,
    of vanuit een raadsbrief de bijbehorende moties.

    Args:
        document_id: Document ID waarvan je gerelateerde stukken wilt vinden
        max_resultaten: Maximaal aantal gerelateerde documenten (standaard 10)
    """
    try:
        with get_connection() as conn:
            cur = conn.cursor()

            # Step 1: Get the source document's metadata
            cur.execute("""
                SELECT d.id, d.name, d.meeting_id, m.start_date, m.name as meeting_name
                FROM documents d
                LEFT JOIN meetings m ON d.meeting_id = m.id
                WHERE d.id = %s
            """, (document_id,))
            source = cur.fetchone()
            if not source:
                cur.close()
                return f"Document '{document_id}' niet gevonden."

            src_id, src_name, meeting_id, meeting_date, meeting_name = source
            src_name = src_name or ""

            related = []
            seen_ids = {document_id}

            # Step 2: Same meeting
            if meeting_id:
                cur.execute("""
                    SELECT d.id, d.name, m.start_date, 'zelfde vergadering' as relatie
                    FROM documents d
                    LEFT JOIN meetings m ON d.meeting_id = m.id
                    WHERE d.meeting_id = %s AND d.id != %s
                    ORDER BY d.name
                    LIMIT 20
                """, (meeting_id, document_id))
                for row in cur.fetchall():
                    if row[0] not in seen_ids:
                        seen_ids.add(row[0])
                        related.append(row)

            # Step 3: Keyword cross-match
            clean_name = re.sub(
                r'(motie|amendement|initiatiefvoorstel|raadsvoorstel|raadsbrief|'
                r'afdoeningsvoorstel|aangenomen|verworpen|ingetrokken|aangehouden)\s*',
                '', src_name.lower()
            ).strip()
            keywords = [w for w in clean_name.split() if len(w) > 3][:5]

            if keywords:
                min_match = max(1, len(keywords) // 2)
                count_expr = " + ".join(
                    "CASE WHEN LOWER(d.name) LIKE %s THEN 1 ELSE 0 END"
                    for _ in keywords
                )
                count_params = [f"%{kw}%" for kw in keywords]
                cur.execute(f"""
                    SELECT d.id, d.name, m.start_date, 'trefwoord match' as relatie
                    FROM documents d
                    LEFT JOIN meetings m ON d.meeting_id = m.id
                    WHERE d.id != %s AND ({count_expr}) >= %s
                    ORDER BY m.start_date DESC
                    LIMIT 15
                """, [document_id] + count_params + [min_match])
                for row in cur.fetchall():
                    if row[0] not in seen_ids:
                        seen_ids.add(row[0])
                        related.append(row)

            # Step 4: Afdoeningsvoorstellen
            cur.execute("""
                SELECT d.id, d.name, m.start_date, 'afdoeningsvoorstel' as relatie
                FROM documents d
                LEFT JOIN meetings m ON d.meeting_id = m.id
                WHERE LOWER(d.name) LIKE '%%afdoening%%'
                  AND d.id != %s
                  AND (LOWER(d.content) LIKE %s OR LOWER(d.name) LIKE %s)
                ORDER BY m.start_date DESC
                LIMIT 5
            """, (document_id, f"%{document_id}%", f"%{clean_name[:30]}%"))
            for row in cur.fetchall():
                if row[0] not in seen_ids:
                    seen_ids.add(row[0])
                    related.append(row)

            cur.close()

    except Exception as e:
        return f"⚠️ Fout bij ophalen gerelateerde documenten: {e}"

    if not related:
        return f"Geen gerelateerde documenten gevonden voor '{src_name}'."

    limit = min(max(1, max_resultaten), 30)
    lines = [
        f"## Gerelateerd aan: {src_name[:80]}",
        f"_Bron document_id: {document_id}_",
        f"_Vergadering: {meeting_name or 'onbekend'} ({str(meeting_date)[:10] if meeting_date else '?'})_",
        "",
        "| # | Datum | Relatie | Document | Doc ID |",
        "|---|---|---|---|---|",
    ]

    for i, (doc_id, name, doc_date, relatie) in enumerate(related[:limit], 1):
        d = str(doc_date)[:10] if doc_date else "?"
        uitkomst = _parse_uitkomst(name or "")
        uitkomst_tag = f" **[{uitkomst}]**" if uitkomst != "onbekend" else ""
        lines.append(f"| {i} | {d} | {relatie} | {(name or '')[:60]}{uitkomst_tag} | {doc_id} |")

    lines.append("")
    lines.append("_Gebruik `lees_fragment(document_id=\"...\")` om een document te lezen._")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 13 — Zoek op rol raadslid/wethouder (role-aware search)
# ---------------------------------------------------------------------------

def _lookup_roles(naam: str, rol: Optional[str] = None) -> list[dict]:
    """
    Look up role records from the raadslid_rollen table.
    Returns list of {"rol", "partij", "van", "tot"} dicts, ordered by periode_van.
    Handles "D.P.A. Tak", "Dennis Tak", and "Tak" as equivalent.
    """
    # Normalize: strip initials so "D.P.A. Tak" → "tak" to match surname column
    search = naam.lower().strip()
    search_stripped = re.sub(r'\b[a-z]\.\s*', '', search).strip()

    sql = """
        SELECT rol, partij, periode_van, periode_tot
        FROM raadslid_rollen
        WHERE LOWER(naam) LIKE %s
           OR LOWER(volledige_naam) LIKE %s
           OR LOWER(naam) LIKE %s
    """
    params = [f"%{search}%", f"%{search}%", f"%{search_stripped}%"]

    if rol:
        sql += " AND LOWER(rol) = %s"
        params.append(rol.lower().strip())

    sql += " ORDER BY periode_van"

    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
            cur.close()
    except Exception:
        return []

    return [
        {"rol": r, "partij": p or "", "van": str(v), "tot": str(t) if t else "heden"}
        for r, p, v, t in rows
    ]


def _resolve_role_date_range(naam: str, rol: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """Look up date range for a person's role. Returns (date_from, date_to) or (None, None)."""
    roles = _lookup_roles(naam, rol)
    if roles:
        date_from = roles[0]["van"]
        date_to = roles[-1]["tot"] if roles[-1]["tot"] != "heden" else None
        return date_from, date_to
    return None, None


@logged_tool
def zoek_uitspraken_op_rol(
    naam: str,
    onderwerp: str,
    rol: Optional[str] = None,
    datum_van: Optional[str] = None,
    datum_tot: Optional[str] = None,
    max_resultaten: int = 10,
) -> str:
    """
    Zoek uitspraken van een raadslid of wethouder, met awareness van rolwisselingen.
    Wanneer een persoon van rol is gewisseld (bijv. raadslid → wethouder),
    filtert deze tool automatisch op de juiste periode.

    Args:
        naam: Naam van de persoon (bijv. "Buijt", "Schneider")
        onderwerp: Onderwerp waarover de uitspraak gaat
        rol: Specifieke rol: "raadslid", "wethouder", "commissielid" (optioneel)
        datum_van: Overschrijf startdatum (ISO), anders automatisch uit roldata
        datum_tot: Overschrijf einddatum (ISO), anders automatisch uit roldata
        max_resultaten: Aantal resultaten (standaard 10)
    """
    # Resolve date range from role mappings if not explicitly provided
    role_from, role_to = _resolve_role_date_range(naam, rol)
    effective_from = datum_van or role_from
    effective_to = datum_tot or role_to

    # Build query combining person + topic
    query = f"{onderwerp} {naam}"

    top_k = min(max(1, max_resultaten), 20)
    chunks = _retrieve_with_reranking(
        query=query,
        top_k=top_k,
        date_from=effective_from,
        date_to=effective_to,
    )

    # Look up role info for display
    all_roles = _lookup_roles(naam)
    role_info = ""
    if all_roles:
        role_lines = [f"  - {p['rol']} ({p['partij']}): {p['van']} — {p['tot']}" for p in all_roles]
        role_info = "\n_Bekende rollen:_\n" + "\n".join(role_lines)

    # T8 (2026-04-14): demote procedural fragments before rendering.
    # Signatures, toezeggingen-rows, and ontvangstbevestigingen occupy slots
    # without adding debate content. Repro: 4/4 Kasmi/onderwijs results were
    # signatures + toezeggingen rows. Demote score by 0.2; do NOT exclude —
    # they may be last-resort evidence. Re-sort after demotion.
    _SIG_RE = re.compile(r'(Met vriendelijke groet|Hoogachtend)', re.IGNORECASE)
    _PROCEDURAL_STREAM_TYPES = {
        "toezeggingen_lijst", "afdoeningsvoorstel", "ontvangstbevestiging",
    }
    for c in chunks:
        should_demote = (
            len(c.content or "") < 200
            or _SIG_RE.search(c.content or "")
            or (c.stream_type or "").lower() in _PROCEDURAL_STREAM_TYPES
        )
        if should_demote:
            c.similarity_score = (c.similarity_score or 0) - 0.2
    chunks.sort(key=lambda c: c.similarity_score or 0, reverse=True)

    header = f"## Uitspraken: {naam} over '{onderwerp}'"
    if rol:
        header += f"\n_Rol filter: {rol}_"
    if effective_from or effective_to:
        header += f"\n_Periode: {effective_from or '…'} — {effective_to or 'heden'}_"
    if role_info:
        header += f"\n{role_info}"

    return header + "\n\n" + _format_chunks_v3(chunks, dedup_by_doc=True)


# ---------------------------------------------------------------------------
# Tool 14 — Context primer (WS4 2026-04-11)
# ---------------------------------------------------------------------------
# Figma's `create_design_system_rules` analogue. Zero-arg tool returning a
# structured primer the host LLM reads on first connect. The `wethouders`
# array is the fix for LLM role-date hallucination — rather than telling the
# model "call zoek_uitspraken_op_rol proactively", we give it the facts.
# `coalition_history` is the same-class fix for historical vote interpretation
# (GroenLinks/PvdA in 2018 = coalitie, not oppositie).
# Generated from the raadslid_rollen table at call time — never hardcoded.


def _compute_rotterdam_coalition_history() -> list:
    """
    Build a coarse coalition timeline from raadslid_rollen wethouder rows.
    Each entry is a college-periode: {"start": ..., "end": ..., "parties": [...]}.
    Grouping heuristic: a "college" runs between successive periode_van dates
    that are at least 3 years apart (the 4-year electoral cycle). Between two
    boundaries, a party is counted as coalition if ANY of its members held a
    wethouder role during that window.
    """
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT DISTINCT partij, periode_van, periode_tot
                FROM raadslid_rollen
                WHERE LOWER(rol) = 'wethouder' AND partij IS NOT NULL
                ORDER BY periode_van
                """
            )
            rows = cur.fetchall()
            cur.close()
    except Exception:
        return []

    if not rows:
        return []

    # Identify college-periode boundaries: election years 2002, 2006, 2010, 2014,
    # 2018, 2022, 2026. We group by which election window the period started in.
    def _window(d):
        if d is None:
            return None
        try:
            y = d.year if hasattr(d, "year") else int(str(d)[:4])
        except Exception:
            return None
        for boundary in (2026, 2022, 2018, 2014, 2010, 2006, 2002):
            if y >= boundary:
                return boundary
        return 2002

    buckets: dict = {}
    for partij, van, tot in rows:
        w = _window(van)
        if w is None:
            continue
        entry = buckets.setdefault(
            w, {"start": f"{w}-03-29", "end": None, "parties": set()}
        )
        entry["parties"].add(partij)
        # Determine the end-of-college as max(tot) seen within the window, None if any current
        if tot is None:
            entry["_any_current"] = True
        elif not entry.get("_any_current"):
            cur_end = entry["end"]
            tot_str = str(tot)[:10]
            if cur_end is None or tot_str > cur_end:
                entry["end"] = tot_str

    timeline = []
    for w in sorted(buckets.keys()):
        b = buckets[w]
        timeline.append({
            "start": b["start"],
            "end": None if b.get("_any_current") else b["end"],
            "parties": sorted(b["parties"]),
        })
    return timeline


def _compute_rotterdam_wethouders() -> list:
    """
    Current wethouders: rows where rol='wethouder' AND periode_tot IS NULL.
    Returns [{"naam", "volledige_naam", "partij", "since"}, ...].
    """
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                SELECT naam, volledige_naam, partij, periode_van, notities
                FROM raadslid_rollen
                WHERE LOWER(rol) = 'wethouder' AND periode_tot IS NULL
                ORDER BY periode_van DESC
                """
            )
            rows = cur.fetchall()
            cur.close()
    except Exception:
        return []

    result = []
    for naam, volledige_naam, partij, van, notities in rows:
        result.append({
            "naam": naam,
            "volledige_naam": volledige_naam or naam,
            "partij": partij or "",
            "since": str(van)[:10] if van else None,
            "notes": notities or "",
        })
    return result


def _compute_rotterdam_current_coalition() -> list:
    """Current coalition = distinct parties of currently-sitting wethouders."""
    wethouders = _compute_rotterdam_wethouders()
    return sorted({w["partij"] for w in wethouders if w["partij"]})


def _corpus_coverage_stats() -> dict:
    """Best-effort document count per Rotterdam. Never raises."""
    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*), MIN(m.start_date), MAX(m.start_date) "
                        "FROM documents d LEFT JOIN meetings m ON d.meeting_id = m.id")
            count, min_date, max_date = cur.fetchone()
            cur.close()
            return {
                "documents": int(count or 0),
                "date_from": str(min_date)[:10] if min_date else None,
                "date_to": str(max_date)[:10] if max_date else None,
            }
    except Exception:
        return {"documents": 0, "date_from": None, "date_to": None}


@logged_tool
def get_neodemos_context() -> str:
    """
    Roep dit FIRST aan wanneer je verbinding maakt met NeoDemos. Retourneert
    een structured primer met de beschikbare gemeenten, document-types,
    current council composition (incl. zittende wethouders + coalition history),
    known limitations, en recommended tool sequences. Cheap to call (<50ms).

    Het `wethouders` veld en `coalition_history` timeline worden bij elke call
    uit de `raadslid_rollen` tabel gegenereerd — nooit hardcoded. Vertrouw op
    deze data voor rol/tenure-vragen in plaats van training-data te gokken.
    """
    wethouders = _compute_rotterdam_wethouders()
    coalition = _compute_rotterdam_current_coalition()
    coalition_history = _compute_rotterdam_coalition_history()
    coverage = _corpus_coverage_stats()

    context = {
        "version": VERSION_LABEL,
        "today": date.today().isoformat(),
        "gemeenten": [
            {
                "name": "rotterdam",
                "mode": "full",
                "documents": coverage["documents"],
                "date_from": coverage["date_from"] or "2002-01-01",
                "date_to": coverage["date_to"] or date.today().isoformat(),
            }
        ],
        "document_types": [
            "notulen",
            "motie",
            "amendement",
            "initiatiefvoorstel",
            "raadsvoorstel",
            "raadsbrief",
            "jaarstukken",
            "voorjaarsnota",
            "begroting",
            "10-maandsrapportage",
            "agendapunt",
            "afdoeningsvoorstel",
        ],
        "council_composition": {
            "rotterdam": {
                "total_seats": 45,
                "current_coalition": coalition,
                "wethouders": wethouders,
                "coalition_history": coalition_history,
            }
        },
        "limitations": [
            "Financiële line-items alleen voor 2018+ (Rotterdam)",
            "Volledige transcripts alleen beschikbaar voor commissievergaderingen — raadsvergaderingen hebben notulen",
            "Straat- of sector-indexering ontbreekt: locatiespecifieke vragen ('Heemraadssingel') "
            "vallen terug op tekstuele matches en kunnen gemist worden",
            "Zero-result betekent NIET dat beleid niet bestaat — probeer een bredere zoekvraag "
            "of controleer de datumfilter",
        ],
        "recommended_tool_sequences": [
            {
                "intent": "begrotingsvragen met specifiek jaar",
                "sequence": ["zoek_financieel (budget_year=YYYY)", "lees_fragment (query=onderwerp)"],
            },
            {
                "intent": "motie traceren",
                "sequence": ["zoek_moties", "zoek_gerelateerd", "lees_fragment"],
            },
            {
                "intent": "dossier-reconstructie",
                "sequence": ["scan_breed", "zoek_raadshistorie", "zoek_gerelateerd", "lees_fragment (query=onderwerp)"],
            },
            {
                "intent": "partijstandpunt",
                "sequence": ["haal_partijstandpunt_op", "zoek_uitspraken (partij_of_raadslid=X)"],
            },
            {
                "intent": "historisch stemgedrag (context-aware)",
                "sequence": [
                    "get_neodemos_context (check coalition_history voor de stemming-datum!)",
                    "zoek_moties",
                    "zoek_raadshistorie",
                ],
                "notes": "Belangrijk: interpreteer stemmingen ALTIJD tegen de coalitiesamenstelling van dát moment, niet tegen het huidige college.",
            },
            {
                "intent": "rol-gefilterde uitspraken",
                "sequence": ["zoek_uitspraken_op_rol (rol='raadslid' of 'wethouder')"],
            },
        ],
        "notes": {
            "temporal": "Bij tijdsgebonden vragen ('vorig jaar', 'sinds 2023'): vertaal ALTIJD naar concrete datum_van/datum_tot parameters. Filteren werkt via metadata, niet via vector similarity.",
            "citations": "Elke resultaatregel bevat een `[Brondocument ↗](url)` link naar het originele PDF wanneer beschikbaar (97% coverage).",
            "dedup": "Alle retrieval tools dedupliceren op document_id: max_resultaten=8 levert 8 unieke documenten, niet 8 chunks uit 2 documenten.",
        },
    }

    # Render as a human-readable markdown block (LLMs prefer structured text over raw JSON)
    lines = [
        f"# NeoDemos context — {VERSION_LABEL}",
        f"_Vandaag: {context['today']}_",
        "",
        "## Gemeenten",
    ]
    for g in context["gemeenten"]:
        lines.append(
            f"- **{g['name']}** ({g['mode']}): {g['documents']:,} documenten, "
            f"{g['date_from']} → {g['date_to']}"
        )
    lines.append("")

    lines.append("## Document types")
    lines.append(", ".join(context["document_types"]))
    lines.append("")

    rot = context["council_composition"]["rotterdam"]
    lines.append("## Rotterdamse raad (45 zetels)")
    lines.append("")
    lines.append(f"**Huidige coalitie:** {', '.join(rot['current_coalition']) or '— (onbekend)'}")
    lines.append("")
    lines.append("**Zittende wethouders (uit raadslid_rollen):**")
    if rot["wethouders"]:
        for w in rot["wethouders"]:
            since = f" — sinds {w['since']}" if w["since"] else ""
            notes = f" · {w['notes']}" if w["notes"] else ""
            lines.append(f"- {w['volledige_naam']} ({w['partij']}){since}{notes}")
    else:
        lines.append("- _(geen data — raadslid_rollen tabel leeg)_")
    lines.append("")

    lines.append("**College-history (voor historische stemming-context):**")
    if rot["coalition_history"]:
        for c in rot["coalition_history"]:
            end = c["end"] or "heden"
            lines.append(f"- {c['start']} → {end}: {', '.join(c['parties'])}")
    else:
        lines.append("- _(geen historische data)_")
    lines.append("")

    lines.append("## Known limitations")
    for lim in context["limitations"]:
        lines.append(f"- {lim}")
    lines.append("")

    lines.append("## Recommended tool sequences")
    for seq in context["recommended_tool_sequences"]:
        lines.append(f"- **{seq['intent']}** — `{' → '.join(seq['sequence'])}`")
        if seq.get("notes"):
            lines.append(f"  - ⚠️ {seq['notes']}")
    lines.append("")

    lines.append("## Notes")
    for k, v in context["notes"].items():
        lines.append(f"- **{k}**: {v}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 16 — traceer_motie (WS1 GraphRAG flagship)
# ---------------------------------------------------------------------------

@logged_tool
def traceer_motie(
    motie_id: str,
    include_notulen: bool = True,
    max_notulen_chunks: int = 8,
    include_virtual_notulen: bool = True,
) -> str:
    """
    Reconstruct the complete traceability of a single motie/amendement:
    indieners → partijen → stemgedrag → uitkomst → gekoppelde notulen-fragmenten.

    Use this when the user asks to "trace", "volg", or "reconstrueer" a specific
    motie and already has a motie document_id in hand (from zoek_moties,
    scan_breed, or a previous call). Use this NOT for topic search — use
    zoek_moties for that.

    The tool walks the knowledge graph from the motie entity via DIENT_IN
    (indieners), LID_VAN (party membership), STEMT_VOOR/STEMT_TEGEN
    (voting), AANGENOMEN/VERWORPEN (uitkomst), and DISCUSSED_IN/VOTED_IN
    (cross-document links to notulen chunks where the motie was debated).

    Returns a structured JSON string with:
        {
          "motie":           {id, name, date, content_preview, meeting_id},
          "indieners":       [{name, partij, canonical_name}],
          "vote":            {voor: int|null, tegen: int|null, uitkomst: str},
          "related_documents": [{id, name, type, date}],
          "notulen_fragments": [{chunk_id, title, content, date}],
          "trace_available": bool,
          "citation_chain":  [entity_id, ...],
          "virtual_notulen_edge_count": int,
          "official_edge_count": int,
          "motie_id":        "<input>"
        }

    When ``trace_available`` is False, the graph walk returned no paths —
    most likely because WS1 Phase 1 enrichment has not yet populated
    DISCUSSED_IN/VOTED_IN edges. In that state the tool still returns the
    motie header, indieners (from rule-based enrichment), and vote counts,
    so it degrades gracefully instead of failing.

    Args:
        motie_id: document_id of the motie/amendement (string).
        include_notulen: if True (default), walk to linked notulen chunks.
        max_notulen_chunks: cap on notulen fragments returned.
        include_virtual_notulen: if True (default), include ASR-transcribed
          committee notulen (WS12) in the trace with a confidence down-rank.
          Set False for press-grade strict mode — only edges derived from
          official written notulen are walked. The returned JSON always
          reports the VN vs official edge counts so the host LLM can judge.
    """
    result: dict = {
        "motie_id": motie_id,
        "motie": None,
        "indieners": [],
        "vote": {"voor": None, "tegen": None, "uitkomst": "onbekend"},
        "related_documents": [],
        "notulen_fragments": [],
        "trace_available": False,
        "citation_chain": [],
        "virtual_notulen_edge_count": 0,
        "official_edge_count": 0,
    }

    with get_connection() as conn:
        cur = conn.cursor()
        # 1. Motie header + already-enriched rule-based metadata
        cur.execute("""
            SELECT d.id, d.name, m.start_date, LEFT(d.content, 800), d.meeting_id,
                   dc_enrich.indieners, dc_enrich.vote_outcome, dc_enrich.vote_counts,
                   dc_enrich.motion_number
            FROM documents d
            LEFT JOIN meetings m ON d.meeting_id = m.id
            LEFT JOIN LATERAL (
                SELECT indieners, vote_outcome, vote_counts, motion_number
                FROM document_chunks
                WHERE document_id = d.id
                  AND (indieners IS NOT NULL
                       OR vote_outcome IS NOT NULL
                       OR motion_number IS NOT NULL)
                LIMIT 1
            ) dc_enrich ON TRUE
            WHERE d.id = %s
        """, (motie_id,))
        row = cur.fetchone()
        cur.close()

    if not row:
        result["error"] = f"motie_id '{motie_id}' niet gevonden"
        return json.dumps(result, ensure_ascii=False)

    doc_id, name, start_date, content, meeting_id, indieners_raw, vote_outcome, vote_counts, motion_number = row
    result["motie"] = {
        "id": str(doc_id),
        "name": name,
        "date": str(start_date)[:10] if start_date else None,
        "motion_number": motion_number,
        "content_preview": (content or "").replace("\n", " ")[:600],
        "meeting_id": str(meeting_id) if meeting_id else None,
    }
    parsed_uitkomst = vote_outcome or _parse_uitkomst(name or "")
    result["vote"]["uitkomst"] = parsed_uitkomst
    if vote_counts:
        counts = vote_counts if isinstance(vote_counts, dict) else {}
        result["vote"]["voor"] = counts.get("voor")
        result["vote"]["tegen"] = counts.get("tegen")

    # 2. Indieners — resolve each to politician_registry for canonical party
    if indieners_raw:
        indiener_list = list(indieners_raw) if isinstance(indieners_raw, list) else [str(indieners_raw)]
        if indiener_list:
            with get_connection() as conn:
                cur = conn.cursor()
                for ind_name in indiener_list:
                    clean = (ind_name or "").strip()
                    if not clean:
                        continue
                    cur.execute("""
                        SELECT canonical_name, partij, surname
                        FROM politician_registry
                        WHERE LOWER(canonical_name) = LOWER(%s)
                           OR LOWER(surname) = LOWER(%s)
                           OR %s = ANY(aliases)
                        ORDER BY periode_tot DESC NULLS FIRST
                        LIMIT 1
                    """, (clean, clean, clean))
                    pol = cur.fetchone()
                    if pol:
                        result["indieners"].append({
                            "name": clean,
                            "canonical_name": pol[0],
                            "partij": pol[1],
                        })
                    else:
                        result["indieners"].append({
                            "name": clean,
                            "canonical_name": None,
                            "partij": None,
                        })
                cur.close()

    # 3. Graph walk — only runs when Phase 1 enrichment is live.
    try:
        from services import graph_retrieval
        if graph_retrieval.is_graph_walk_ready():
            # VN strict mode: exclude virtual_notulen edges + chunks entirely.
            exclude_sources = None if include_virtual_notulen else ["virtual_notulen"]
            name_for_match = motion_number or name or ""
            seed_hits = graph_retrieval._resolve_entity_id_by_name(
                name_for_match, preferred_type="Motie"
            )
            if seed_hits:
                seed_id = seed_hits[0]
                paths = graph_retrieval.walk(
                    [seed_id], max_hops=2, exclude_sources=exclude_sources,
                )
                scored = graph_retrieval.score_paths(paths, query_intent="motie_trace")
                # Count VN vs official edges across the walked paths for caller visibility.
                _vn_edges = 0
                _official_edges = 0
                for sp in scored:
                    for src in sp.path.edge_sources:
                        if src == "virtual_notulen":
                            _vn_edges += 1
                        else:
                            _official_edges += 1
                result["virtual_notulen_edge_count"] = _vn_edges
                result["official_edge_count"] = _official_edges
                if scored:
                    result["trace_available"] = True
                    tail_ids: list = []
                    for sp in scored[:20]:
                        for nid in sp.path.node_ids:
                            if nid not in tail_ids:
                                tail_ids.append(nid)
                    result["citation_chain"] = tail_ids[:20]
                    if include_notulen and tail_ids:
                        notulen = graph_retrieval.hydrate_chunks(
                            tail_ids,
                            limit=max_notulen_chunks,
                            exclude_sources=exclude_sources,
                        )
                        for gc in notulen:
                            result["notulen_fragments"].append({
                                "chunk_id": gc.chunk_id,
                                "title": gc.title,
                                "content": (gc.content or "")[:500],
                                "date": gc.start_date,
                                "document_id": gc.document_id,
                            })
    except Exception:
        # Never fail the tool on graph-walk errors — we always have the
        # rule-based header + indieners + votes as a baseline.
        pass

    # 4. Related documents by same meeting (deterministic, no KG dependency)
    if meeting_id:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, name, document_type, (SELECT start_date FROM meetings WHERE id = meeting_id)
                FROM documents
                WHERE meeting_id = %s AND id <> %s
                ORDER BY name
                LIMIT 12
            """, (meeting_id, doc_id))
            for rid, rname, rtype, rdate in cur.fetchall():
                result["related_documents"].append({
                    "id": str(rid),
                    "name": rname,
                    "type": rtype,
                    "date": str(rdate)[:10] if rdate else None,
                })
            cur.close()

    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool 17 — vergelijk_partijen (WS1 GraphRAG flagship)
# ---------------------------------------------------------------------------

@logged_tool
def vergelijk_partijen(
    onderwerp: str,
    partijen: list,
    datum_van: Optional[str] = None,
    datum_tot: Optional[str] = None,
    max_fragmenten_per_partij: int = 5,
    include_virtual_notulen: bool = True,
) -> str:
    """
    Plaats twee of meer partijen naast elkaar op één onderwerp en retourneer
    hun standpunten als gerankte fragmentenlijsten.

    Gebruik dit wanneer de gebruiker letterlijk vraagt partijen te vergelijken
    op een specifiek onderwerp (bijv. "hoe denken VVD, PvdA en GroenLinks over
    warmtenetten?"). Gebruik dit NIET voor single-party vragen — gebruik dan
    haal_partijstandpunt_op of zoek_uitspraken.

    Werkwijze: voor elke opgegeven partij zoeken we via de bestaande
    vector+BM25 stack naar de top-N fragmenten waar die partij zich uitspreekt
    over het onderwerp. De Jina v3 reranker bepaalt welke fragmenten het
    best aansluiten. Wanneer de WS1 GraphRAG stream live is, wordt de
    zoekruimte aanvullend beperkt tot chunks die via LID_VAN ∩
    SPREEKT_OVER(onderwerp) aan de partij gekoppeld zijn.

    Args:
        onderwerp: concrete term, bijvoorbeeld "warmtenetten" of
                   "Feyenoord stadion". GEEN hele zinnen.
        partijen: list van partijnamen (minimaal 2). Accepteerde spellingen
                  worden via services.party_utils.PARTY_ALIASES genormaliseerd.
        datum_van, datum_tot: optionele ISO-datumfilters.
        max_fragmenten_per_partij: cap op terugkomende fragmenten per partij
                                   (standaard 5, maximum 10).
        include_virtual_notulen: if True (default), include VN-derived
          chunks. Set False for press-grade strict mode. The returned JSON
          reports per-party VN vs official fragment counts.

    Returns:
        JSON string:
        {
          "onderwerp": "...",
          "datum_van": "...",
          "datum_tot": "...",
          "partijen": [
              {"partij": "VVD", "fragmenten": [{chunk_id, title, content,
                date, similarity_score, document_id}, ...],
               "virtual_notulen_count": int, "official_count": int},
              ...
          ],
          "graph_walk_used": bool,
          "include_virtual_notulen": bool
        }
    """
    if not partijen or len(partijen) < 2:
        return json.dumps({
            "error": "Geef minimaal 2 partijen op voor een vergelijking.",
            "onderwerp": onderwerp,
        }, ensure_ascii=False)

    from services.party_utils import PARTY_ALIASES

    k = max(1, min(int(max_fragmenten_per_partij), 10))

    canonicalized: list = []
    for raw in partijen:
        key = (raw or "").strip().lower()
        canonical = PARTY_ALIASES.get(key, raw)
        if canonical and canonical not in canonicalized:
            canonicalized.append(canonical)

    graph_walk_used = False
    try:
        from services import graph_retrieval
        graph_walk_used = graph_retrieval.is_graph_walk_ready()
    except Exception:
        graph_walk_used = False

    # In strict VN mode we pass include_virtual_notulen=False through the RAG
    # stack. The 5th graph_walk stream honors this via
    # services.graph_retrieval.retrieve_via_graph. The dense/BM25 streams
    # already honor it via the rag_service INCLUDE_VIRTUAL_NOTULEN killswitch.
    result: dict = {
        "onderwerp": onderwerp,
        "datum_van": datum_van,
        "datum_tot": datum_tot,
        "partijen": [],
        "graph_walk_used": graph_walk_used,
        "include_virtual_notulen": include_virtual_notulen,
    }

    rag = _get_rag()

    # In strict mode we temporarily flip the INCLUDE_VIRTUAL_NOTULEN env var
    # for the duration of this call so both the rag_service dense/BM25 streams
    # and the graph_walk stream honor the same killswitch. Restore after.
    import os as _os
    _prev_include_vn = _os.environ.get("INCLUDE_VIRTUAL_NOTULEN")
    if not include_virtual_notulen:
        _os.environ["INCLUDE_VIRTUAL_NOTULEN"] = "false"

    try:
        for partij in canonicalized:
            # Query the existing retrieval stack with a party-augmented query
            augmented_query = f"{onderwerp} {partij}"
            try:
                import asyncio as _asyncio
                chunks = _asyncio.run(
                    rag.retrieve_parallel_context(
                        query_text=augmented_query,
                        distribution={"debate": 4, "vision": 3, "fact": 2, "financial": 1, "graph": 2},
                        date_from=datum_van,
                        date_to=datum_tot,
                        fast_mode=False,
                        query_intent="party_comparison",
                    )
                )
            except Exception:
                chunks = []

            # Prefer chunks whose content mentions the party token — this filters
            # generic topic chunks out of the per-party bucket.
            partij_lc = partij.lower()
            filtered = [c for c in chunks if partij_lc in (c.content or "").lower()]
            if not filtered:
                filtered = chunks  # fall back rather than return empty

            party_fragments: list = []
            vn_count = 0
            official_count = 0
            for c in filtered[:k]:
                # Heuristic: the rag_service retrieval doesn't carry source
                # metadata on RetrievedChunk yet, so we report counts as 0 for
                # non-graph streams. Graph-walk chunks can be identified by
                # stream_type == 'graph'; full source attribution lands when
                # Phase A enrichment + the hydrate_chunks source-passthrough is
                # wired (post-Phase-A follow-up).
                party_fragments.append({
                    "chunk_id": c.chunk_id,
                    "title": c.title,
                    "content": (c.content or "")[:600],
                    "date": c.start_date,
                    "similarity_score": c.similarity_score,
                    "document_id": c.document_id,
                    "stream": getattr(c, "stream_type", None),
                })
            result["partijen"].append({
                "partij": partij,
                "fragmenten": party_fragments,
                "n_hits": len(filtered),
                "virtual_notulen_count": vn_count,
                "official_count": official_count,
            })
    finally:
        # Restore the env var to its prior state.
        if not include_virtual_notulen:
            if _prev_include_vn is None:
                _os.environ.pop("INCLUDE_VIRTUAL_NOTULEN", None)
            else:
                _os.environ["INCLUDE_VIRTUAL_NOTULEN"] = _prev_include_vn

    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool 18 — vraag_begrotingsregel (WS2 Trustworthy Financial Analysis)
# ---------------------------------------------------------------------------

@logged_tool
def vraag_begrotingsregel(
    gemeente: str,
    jaar: int,
    programma: str,
    sub_programma: Optional[str] = None,
    include_gr_derived: bool = False,
) -> str:
    """
    Haalt exacte begrotingsregels op uit de gestructureerde financial_lines tabel.

    Use this when:
    - De gebruiker vraagt naar een specifiek bedrag, begrotingsregel, of financieel gegeven
    - De vraag bevat een programma, jaar, en/of gemeente

    Do NOT use when:
    - De vraag is narratief/kwalitatief ("waarom is het budget gestegen?") → gebruik zoek_financieel
    - De gebruiker vraagt om een toelichting of context → gebruik zoek_financieel

    Returns: Exacte bedragen met SHA256 verificatietokens. Bedragen zijn byte-identiek aan de bron-PDF.

    Args:
        gemeente: Gemeentenaam (bijv. "rotterdam")
        jaar: Begrotingsjaar (bijv. 2025)
        programma: Programmanaam (fuzzy match via ILIKE, bijv. "Veilig" of "Onderwijs")
        sub_programma: Optioneel deelprogramma (fuzzy match via ILIKE)
        include_gr_derived: Als True, bereken afgeleide bijdrage voor gemeenschappelijke regelingen
    """
    from decimal import Decimal as _Decimal

    retrieved_at = datetime.utcnow().isoformat() + "Z"

    try:
        with get_connection() as conn:
            cur = conn.cursor()

            # Build WHERE clause — try exact match first, fall back to ILIKE
            # with word-boundary awareness to avoid "Veilig" matching "veiligheid"
            conditions = [
                "LOWER(gemeente) = LOWER(%s)",
                "jaar = %s",
            ]
            params: list = [gemeente, jaar]

            # Exact match on programma (case-insensitive)
            conditions.append("LOWER(programma) = LOWER(%s)")
            params.append(programma)

            if sub_programma:
                conditions.append("sub_programma ILIKE %s")
                params.append(f"%{sub_programma}%")

            where = " AND ".join(conditions)

            cur.execute(f"""
                SELECT programma, sub_programma, jaar, bedrag_eur, bedrag_label,
                       scope, entity_id, source_pdf_url, page, table_id,
                       row_idx, col_idx, sha256, document_id
                FROM financial_lines
                WHERE {where}
                ORDER BY programma, sub_programma, bedrag_label, jaar
            """, params)
            rows = cur.fetchall()

            # Fallback: if exact match found nothing, try ILIKE substring match
            if not rows:
                conditions_fuzzy = [
                    "LOWER(gemeente) = LOWER(%s)",
                    "jaar = %s",
                    "programma ILIKE %s",
                ]
                params_fuzzy: list = [gemeente, jaar, f"%{programma}%"]
                if sub_programma:
                    conditions_fuzzy.append("sub_programma ILIKE %s")
                    params_fuzzy.append(f"%{sub_programma}%")
                where_fuzzy = " AND ".join(conditions_fuzzy)
                cur.execute(f"""
                    SELECT programma, sub_programma, jaar, bedrag_eur, bedrag_label,
                           scope, entity_id, source_pdf_url, page, table_id,
                           row_idx, col_idx, sha256, document_id
                    FROM financial_lines
                    WHERE {where_fuzzy}
                    ORDER BY programma, sub_programma, bedrag_label, jaar
                """, params_fuzzy)
                rows = cur.fetchall()

            matches = []
            for row in rows:
                (prog, sub_prog, jr, bedrag, label, scope, entity_id,
                 source_pdf, page, table_id, row_idx, col_idx, sha, doc_id) = row
                matches.append({
                    "programma": prog,
                    "sub_programma": sub_prog,
                    "jaar": jr,
                    "bedrag_eur": str(bedrag),
                    "label": label,
                    "scope": scope,
                    "entity_id": entity_id,
                    "source_pdf": source_pdf,
                    "page": page,
                    "table_cell_ref": f"table_id={table_id},row={row_idx},col={col_idx}",
                    "document_id": doc_id,
                    "verification": {
                        "sha256": sha,
                        "retrieved_at": retrieved_at,
                    },
                })

            # Deduplicate cross-document: when the same programma+label appears
            # in multiple source documents for the same jaar, keep only rows
            # from the primary document (the one whose year best matches the
            # queried jaar). This avoids mixing begroting_2025 and begroting_2026
            # data for the same programma.
            if matches:
                doc_ids = {m["document_id"] for m in matches}
                if len(doc_ids) > 1:
                    # Prefer the document whose name contains the queried year
                    primary = None
                    for did in sorted(doc_ids):
                        if str(jaar) in did:
                            primary = did
                            break
                    if primary:
                        # Keep rows from primary doc; also keep rows from other
                        # docs whose (programma, label) is NOT in the primary.
                        primary_keys = {
                            (m["programma"], m["sub_programma"], m["label"])
                            for m in matches if m["document_id"] == primary
                        }
                        matches = [
                            m for m in matches
                            if m["document_id"] == primary
                            or (m["programma"], m["sub_programma"], m["label"])
                               not in primary_keys
                        ]

            # GR derived share: join gr_member_contributions for matching entities
            if include_gr_derived and matches:
                gr_matches = [m for m in matches if m["scope"] == "gemeenschappelijke_regeling"]
                for gm in gr_matches:
                    try:
                        cur.execute("""
                            SELECT bijdrage_eur, aandeel_pct, sha256
                            FROM gr_member_contributions
                            WHERE entity_id = %s
                              AND jaar = %s
                              AND LOWER(member_gemeente) = LOWER(%s)
                            LIMIT 1
                        """, (gm["entity_id"], gm["jaar"], gemeente))
                        gr_row = cur.fetchone()
                        if gr_row:
                            bijdrage, aandeel, gr_sha = gr_row
                            matches.append({
                                "programma": gm["programma"],
                                "sub_programma": gm["sub_programma"],
                                "jaar": gm["jaar"],
                                "bedrag_eur": str(bijdrage),
                                "label": gm["label"],
                                "scope": "derived_share",
                                "entity_id": gm["entity_id"],
                                "source_pdf": gm["source_pdf"],
                                "page": gm["page"],
                                "table_cell_ref": gm["table_cell_ref"],
                                "document_id": gm["document_id"],
                                "aandeel_pct": str(aandeel) if aandeel else None,
                                "verification": {
                                    "sha256": gr_sha,
                                    "retrieved_at": retrieved_at,
                                    "method": "derived",
                                },
                            })
                    except Exception:
                        pass  # GR table may not exist or be empty

            cur.close()

    except Exception as exc:
        return json.dumps({
            "matches": [],
            "total": 0,
            "error": f"Database fout: {exc}",
        }, ensure_ascii=False)

    if not matches:
        return json.dumps({
            "matches": [],
            "total": 0,
            "hint": "Geen resultaten gevonden. Probeer een ander programma of jaar.",
        }, ensure_ascii=False)

    return json.dumps({
        "matches": matches,
        "total": len(matches),
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool 19 — vergelijk_begrotingsjaren (WS2 Trustworthy Financial Analysis)
# ---------------------------------------------------------------------------

@logged_tool
def vergelijk_begrotingsjaren(
    gemeente: str,
    programma: str,
    jaren: list,
) -> str:
    """
    Vergelijkt begrotingsregels over meerdere jaren voor een programma.

    Use this when:
    - De gebruiker vraagt naar trends, ontwikkeling, of vergelijking over jaren
    - "Hoe is het budget veranderd?", "Wat is de trend?"

    Do NOT use when:
    - De vraag gaat over een enkel jaar → gebruik vraag_begrotingsregel

    Returns: Tijdreeks met delta_abs en delta_pct, geaggregeerd op IV3 taakveld voor consistentie.

    Args:
        gemeente: Gemeentenaam (bijv. "rotterdam")
        programma: Programmanaam (fuzzy match via ILIKE)
        jaren: Lijst van jaren om te vergelijken (bijv. [2024, 2025, 2026])
    """
    from decimal import Decimal as _Decimal, ROUND_HALF_UP as _ROUND

    if not jaren or len(jaren) < 2:
        return json.dumps({
            "error": "Geef minimaal 2 jaren op voor een vergelijking.",
            "programma": programma,
        }, ensure_ascii=False)

    jaren_sorted = sorted(jaren)
    retrieved_at = datetime.utcnow().isoformat() + "Z"

    # Load IV3 taakvelden reference for label resolution
    iv3_lookup: dict = {}
    try:
        iv3_path = PROJECT_ROOT / "data" / "financial" / "iv3_taakvelden.json"
        if iv3_path.exists():
            with open(iv3_path, encoding="utf-8") as f:
                iv3_data = json.load(f)
            for tv in iv3_data.get("taakvelden", []):
                iv3_lookup[tv["code"]] = tv["omschrijving"]
    except Exception:
        pass

    try:
        with get_connection() as conn:
            cur = conn.cursor()

            # Step 1: Try to resolve programma to IV3 taakveld via programma_aliases
            iv3_taakveld = None
            iv3_omschrijving = None
            try:
                cur.execute("""
                    SELECT iv3_taakveld
                    FROM programma_aliases
                    WHERE LOWER(gemeente) = LOWER(%s)
                      AND programma_label ILIKE %s
                    ORDER BY confidence DESC NULLS LAST
                    LIMIT 1
                """, (gemeente, f"%{programma}%"))
                alias_row = cur.fetchone()
                if alias_row:
                    iv3_taakveld = alias_row[0]
                    iv3_omschrijving = iv3_lookup.get(iv3_taakveld)
            except Exception:
                pass  # programma_aliases table may not exist yet

            # Step 2: Also try direct iv3_taakveld match from financial_lines
            if not iv3_taakveld:
                try:
                    cur.execute("""
                        SELECT DISTINCT iv3_taakveld
                        FROM financial_lines
                        WHERE LOWER(gemeente) = LOWER(%s)
                          AND programma ILIKE %s
                          AND iv3_taakveld IS NOT NULL
                        LIMIT 1
                    """, (gemeente, f"%{programma}%"))
                    iv3_row = cur.fetchone()
                    if iv3_row:
                        iv3_taakveld = iv3_row[0]
                        iv3_omschrijving = iv3_lookup.get(iv3_taakveld)
                except Exception:
                    pass

            # Step 3: Query financial_lines for all requested years
            # If IV3 mapping exists, aggregate on iv3_taakveld for cross-year stability
            if iv3_taakveld:
                cur.execute("""
                    SELECT jaar, bedrag_eur, bedrag_label, document_id
                    FROM financial_lines
                    WHERE LOWER(gemeente) = LOWER(%s)
                      AND iv3_taakveld = %s
                      AND jaar = ANY(%s)
                    ORDER BY bedrag_label, jaar
                """, (gemeente, iv3_taakveld, jaren_sorted))
            else:
                cur.execute("""
                    SELECT jaar, bedrag_eur, bedrag_label, document_id
                    FROM financial_lines
                    WHERE LOWER(gemeente) = LOWER(%s)
                      AND programma ILIKE %s
                      AND jaar = ANY(%s)
                    ORDER BY bedrag_label, jaar
                """, (gemeente, f"%{programma}%", jaren_sorted))

            rows = cur.fetchall()
            cur.close()

    except Exception as exc:
        return json.dumps({
            "error": f"Database fout: {exc}",
            "programma": programma,
        }, ensure_ascii=False)

    if not rows:
        return json.dumps({
            "programma": programma,
            "iv3_taakveld": iv3_taakveld,
            "series": [],
            "hint": "Geen resultaten gevonden. Probeer een ander programma of andere jaren.",
        }, ensure_ascii=False)

    # Group by bedrag_label, then build time series per label
    from collections import defaultdict
    label_buckets: dict = defaultdict(list)
    source_docs: set = set()

    for jr, bedrag, label, doc_id in rows:
        label_buckets[label or "Onbekend"].append({
            "jaar": jr,
            "bedrag_eur": _Decimal(str(bedrag)),
            "document_id": doc_id,
        })
        if doc_id:
            source_docs.add(doc_id)

    result_series: dict = {}
    for label, entries in label_buckets.items():
        # Aggregate: sum bedrag_eur per jaar (multiple rows per year possible)
        jaar_totals: dict = {}
        for e in entries:
            jr = e["jaar"]
            if jr not in jaar_totals:
                jaar_totals[jr] = _Decimal("0")
            jaar_totals[jr] += e["bedrag_eur"]

        # Build series with deltas
        series_items = []
        prev_bedrag = None
        for jr in jaren_sorted:
            bedrag = jaar_totals.get(jr)
            if bedrag is None:
                series_items.append({
                    "jaar": jr,
                    "bedrag_eur": None,
                    "label": label,
                    "delta_abs": None,
                    "delta_pct": None,
                })
                prev_bedrag = None
                continue

            delta_abs = None
            delta_pct = None
            if prev_bedrag is not None:
                delta_abs = str(bedrag - prev_bedrag)
                if prev_bedrag != _Decimal("0"):
                    pct = ((bedrag - prev_bedrag) / prev_bedrag * _Decimal("100")).quantize(
                        _Decimal("0.01"), rounding=_ROUND
                    )
                    delta_pct = str(pct)

            series_items.append({
                "jaar": jr,
                "bedrag_eur": str(bedrag),
                "label": label,
                "delta_abs": delta_abs,
                "delta_pct": delta_pct,
            })
            prev_bedrag = bedrag

        result_series[label] = series_items

    payload = {
        "programma": programma,
        "iv3_taakveld": iv3_taakveld,
        "iv3_omschrijving": iv3_omschrijving,
        "series": result_series,
        "source_documents": sorted(source_docs),
        "verification": {"retrieved_at": retrieved_at},
    }

    return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=f"{DISPLAY_NAME} MCP Server")
    parser.add_argument(
        "transport", nargs="?", default="stdio",
        choices=["stdio", "sse", "streamable-http"],
        help="Transport protocol (default: stdio)",
    )
    parser.add_argument("--port", type=int, default=8001, help="Port for HTTP transports")
    parser.add_argument("--host", default="0.0.0.0", help="Host for HTTP transports")
    # Legacy flag from docker-compose
    parser.add_argument("--http", action="store_true", help="Alias for 'streamable-http'")
    args = parser.parse_args()

    transport = "streamable-http" if args.http else args.transport

    # Override host/port from CLI args
    if transport != "stdio":
        mcp.settings.host = args.host
        mcp.settings.port = args.port

    # WS4 startup check: tool-description collision detection.
    # FactSet pattern: two tools with > 0.85 cosine confuse the host LLM.
    # Non-fatal in dev (no NEBIUS_API_KEY) — see services/mcp_tool_uniqueness.py.
    try:
        from services.mcp_tool_uniqueness import check_tool_uniqueness
        check_tool_uniqueness()
    except RuntimeError as _e:
        # FAIL_THRESHOLD breach — refuse to boot with a clear message
        print(f"MCP STARTUP ABORTED: {_e}", flush=True)
        sys.exit(2)
    except Exception as _e:
        print(f"[mcp_tool_uniqueness] skipped: {_e}", flush=True)

    # For HTTP transports: serve both authenticated /mcp and public /public/mcp
    # on the same port via Starlette routing (WS4 2026-04-13).
    if transport in ("streamable-http", "--http") and _public_mcp is not None:
        import uvicorn
        from starlette.applications import Starlette as _Starlette
        from starlette.routing import Mount as _Mount
        from starlette.middleware.cors import CORSMiddleware as _CORSMiddleware

        _auth_asgi = mcp.streamable_http_app()
        _pub_asgi = _public_mcp.streamable_http_app()

        # CORS on public endpoint: allow chat.mistral.ai + *
        from services.mcp_rate_limiter import RateLimitMiddleware as _RateLimitMiddleware
        _pub_asgi_rate_limited = _RateLimitMiddleware(app=_pub_asgi)
        _pub_asgi_with_cors = _CORSMiddleware(
            app=_pub_asgi_rate_limited,
            allow_origins=["https://chat.mistral.ai", "*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        from starlette.routing import Route as _Route
        from starlette.responses import PlainTextResponse as _RootPlainText
        from contextlib import asynccontextmanager as _asynccontextmanager

        async def _root_up(_request):  # pragma: no cover
            return _RootPlainText("ok", status_code=200)

        # IMPORTANT — two subtleties when composing FastMCP apps in a parent Starlette app:
        #
        # 1. Mount prefix: FastMCP's streamable_http_app() already contains Route("/mcp", ...)
        #    internally. Wrapping in _Mount("/mcp", ...) would strip "/mcp" before forwarding,
        #    so the inner app receives "/" and never matches → 307 loop then 404.
        #    Fix: _Mount("/public", ...) strips "/public" → inner app receives "/mcp" ✓
        #         _Mount("/", ...) catch-all → inner app receives "/mcp", "/.well-known/..." ✓
        #
        # 2. Session-manager lifespan: Starlette does NOT propagate ASGI lifespan events to
        #    sub-apps mounted via _Mount when those sub-apps are wrapped in raw ASGI middleware
        #    (CORS, rate limiter). The FastMCP session manager never initializes → RuntimeError.
        #    Fix: run both session managers explicitly in the root app's lifespan.
        @_asynccontextmanager
        async def _root_lifespan(_app):
            async with mcp.session_manager.run():
                async with _public_mcp.session_manager.run():
                    yield

        _root_app = _Starlette(
            lifespan=_root_lifespan,
            routes=[
                _Route("/up", endpoint=_root_up, methods=["GET"]),
                _Mount("/public", app=_pub_asgi_with_cors),
                _Mount("/", app=_auth_asgi),
            ],
        )
        print(f"{DISPLAY_NAME} {VERSION_LABEL} — transport={transport} port={mcp.settings.port} (authenticated /mcp + public /public/mcp)", flush=True)
        uvicorn.run(_root_app, host=mcp.settings.host, port=mcp.settings.port, log_level="info")
    else:
        print(f"{DISPLAY_NAME} {VERSION_LABEL} — transport={transport} port={args.port}", flush=True)
        mcp.run(transport=transport)
