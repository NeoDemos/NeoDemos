#!/usr/bin/env python3
"""
NeoDemos MCP Server
-------------------
Claude Desktop interface for Rotterdam gemeenteraad meeting preparation.

Design principle: This server is purely a retrieval layer. All analysis,
synthesis, and reasoning is performed by Claude Desktop itself — not by
an external LLM. This keeps tool calls fast (3-8s) and leverages Claude's
superior reasoning over the raw context returned by each tool.

Transport: stdio (Claude Desktop default)
Setup: see claude_desktop_config.json snippet in brain/mcp_setup.md
"""

import sys
import os
import re
import json
import glob
import time
import threading
from datetime import date, datetime
from typing import Optional
from pathlib import Path

# Ensure project root is on sys.path so service imports work
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load environment variables before importing services
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

# When running behind a reverse proxy (Caddy) the Host header will be the
# public domain, not localhost.  Build security settings that allow both
# localhost development and production hostnames.
_allowed_hosts = [
    "127.0.0.1:*", "localhost:*", "[::1]:*",         # local dev
    "mcp.neodemos.nl", "mcp.neodemos.eu",             # production
    "neodemos-mcp:*",                                 # Docker-internal
]
_transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=True,
    allowed_hosts=_allowed_hosts,
)

mcp = FastMCP(
    "neodemos",
    transport_security=_transport_security,
    instructions=(
        "# NeoDemos — Rotterdam Gemeenteraad Assistent\n\n"
        "## Gebruik van dit systeem\n"
        "Je bent een expert-assistent voor Rotterdam gemeenteraad vergaderingen. "
        "De tools doen uitsluitend retrieval — jij voert zelf de analyse, synthese en redenering uit. "
        "Antwoord altijd in het Nederlands tenzij de gebruiker anders vraagt.\n\n"

        "## Diepgaand redeneren (VERPLICHT)\n"
        "Dit systeem vereist uitgebreide redenering vóór elk antwoord. "
        "Activeer je meest geavanceerde redeneermodus: gebruik extended thinking, "
        "deep research mode, of o1-stijl chain-of-thought als je platform dat ondersteunt. "
        "Werk elke vraag in meerdere stappen uit:\n"
        "1. Identificeer welke tools en parameters nodig zijn\n"
        "2. Roep meerdere tools aan om een volledig beeld te krijgen\n"
        "3. Analyseer de opgehaalde data kritisch op inconsistenties en lacunes\n"
        "4. Synthetiseer tot een gestructureerd antwoord met concrete citaten en bronnen\n\n"

        "## Meerdere tool-aanroepen\n"
        "Roep voor complexe vragen ALTIJD meerdere tools aan. Voorbeelden:\n"
        "- Confrontaties tussen partijen → zoek_debatconfrontaties + zoek_moties + tijdlijn_besluitvorming\n"
        "- Standpunt van één partij → haal_partijstandpunt_op + zoek_uitspraken\n"
        "- Beleidsontwikkeling → tijdlijn_besluitvorming + zoek_financieel (indien relevant)\n"
        "Combineer alle resultaten in je eindantwoord.\n\n"

        "## Temporele detectie\n"
        "Vertaal tijdsgebonden taal ALTIJD naar concrete datum_van/datum_tot parameters. "
        f"Vandaag is {date.today().isoformat()}. Voorbeelden:\n"
        "- 'vorig jaar' → datum_van='2025-01-01', datum_tot='2025-12-31'\n"
        "- 'sinds 2023' → datum_van='2023-01-01'\n"
        "- 'recent' → datum_van van ~3 maanden geleden\n"
        "Verwijder temporele termen uit de zoektekst zelf.\n\n"

        "## Citaten en bronvermelding\n"
        "Vermeld bij elke uitspraak: naam raadslid, partij, datum, document-ID of motienummer. "
        "Gebruik letterlijke citaten uit de fragmenten waar mogelijk."
    ),
)

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
    """Drop-in for @logged_tool — registers with FastMCP and logs to mcp_queries.jsonl."""
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

        t0 = time.monotonic()
        result = func(*args, **kwargs)
        latency_ms = int((time.monotonic() - t0) * 1000)
        _log_query(func.__name__, log_params, result, latency_ms)
        return result

    return mcp.tool()(wrapper)


# ---------------------------------------------------------------------------
# Lazy service singletons — initialized on first tool call to keep startup fast
# ---------------------------------------------------------------------------

_rag = None
_storage = None
_claude = None

# In-tool synthesis via Claude API.
#
# Set MCP_SYNTHESIZE=true when the client cannot synthesize well itself (e.g. Perplexity).
# Leave unset (default false) for Claude Desktop — it does its own synthesis natively,
# so extra API calls would be redundant cost.
#
# Model: Haiku 4.5 for low latency/cost. Swap to "claude-sonnet-4-6" for higher quality.
_SYNTHESIS_ENABLED = os.getenv("MCP_SYNTHESIZE", "false").lower() == "true"
_SYNTHESIS_MODEL = os.getenv("MCP_SYNTHESIS_MODEL", "claude-haiku-4-5")


def _get_rag():
    """
    Returns a RAGService instance. Uses skip_llm=True to avoid loading the
    Mistral-24B LLM (only the Qwen3-8B embedding model is needed for retrieval).
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


def _get_claude():
    global _claude
    if _claude is None:
        import anthropic
        _claude = anthropic.Anthropic()
    return _claude


def _claude_synthesize(system: str, user: str) -> str:
    """
    Call Claude for in-tool synthesis. Returns the text response.
    When MCP_SYNTHESIZE is not set (default), returns the raw context so
    Claude Desktop can do its own synthesis natively.
    Falls back to raw content on API error so the tool never crashes.
    """
    if not _SYNTHESIS_ENABLED:
        return user
    try:
        client = _get_claude()
        resp = client.messages.create(
            model=_SYNTHESIS_MODEL,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return next(b.text for b in resp.content if b.type == "text")
    except Exception as exc:
        return f"_[Claude synthesis unavailable: {exc}]_\n\n{user}"


def _load_party_profile(partij: str) -> dict:
    """
    Load the party profile JSON for the given party name.
    Tries several filename conventions; returns empty dict if not found.
    """
    partij_slug = partij.lower().replace(" ", "_").replace("-", "_")
    candidates = [
        PROJECT_ROOT / "data" / "profiles" / f"party_profile_{partij_slug}.json",
        PROJECT_ROOT / "data" / "profiles" / f"{partij_slug.upper()}_PROFILE_DEMO.json",
    ]
    # Also glob for any file containing the party slug
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


_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I
)
_IBABS_DOC_BASE = "https://rotterdamraad.bestuurlijkeinformatie.nl/Document/View/"


def _get_doc_urls(chunks) -> dict:
    """Return {document_id: url} for a list of chunks via DB lookup + UUID fallback."""
    doc_ids = list({
        c.document_id for c in chunks
        if c.document_id and c.document_id != "unknown"
    })
    if not doc_ids:
        return {}
    try:
        meta = _get_storage().get_documents_metadata(doc_ids)
        url_map = {m["id"]: m["url"] for m in meta if m.get("url")}
    except Exception:
        url_map = {}
    # Construct ibabs URL for UUID-shaped IDs not found in the DB
    for doc_id in doc_ids:
        if doc_id not in url_map and _UUID_RE.match(doc_id):
            url_map[doc_id] = _IBABS_DOC_BASE + doc_id
    return url_map


_MOTIE_RE = re.compile(r'\b(\d{2}[a-z]{2}\d{6,})\b', re.IGNORECASE)
_VOTE_RE = re.compile(r'\b(aangenomen|verworpen|ingetrokken|aangehouden)\b', re.IGNORECASE)
_SPEAKER_RE = re.compile(r'\bde (?:heer|vrouw|wethouder|burgemeester)\s+([A-Z][a-zÀ-ÿ\-]+)\b')


def _extract_highlights(text: str) -> list[str]:
    """Extract motie numbers, vote outcomes, and speaker names from chunk text."""
    highlights = []
    moties = _MOTIE_RE.findall(text)
    if moties:
        highlights.append("Moties: " + ", ".join(dict.fromkeys(moties)))
    votes = _VOTE_RE.findall(text)
    if votes:
        highlights.append("Stemuitslag: " + ", ".join(dict.fromkeys(v.lower() for v in votes)))
    speakers = _SPEAKER_RE.findall(text)
    if speakers:
        highlights.append("Sprekers: " + ", ".join(dict.fromkeys(speakers[:6])))
    return highlights


def _format_chunks_as_markdown(chunks, max_content: int = 2500, url_map: dict = None) -> str:
    """Format a list of RetrievedChunk objects as readable Markdown for Claude."""
    if not chunks:
        return "_Geen resultaten gevonden._"
    lines = []
    for i, chunk in enumerate(chunks, 1):
        date_str = f" · {chunk.start_date[:10]}" if chunk.start_date else ""
        stream = f" [{chunk.stream_type}]" if chunk.stream_type else ""
        lines.append(f"### [{i}]{stream} {chunk.title}{date_str}")
        content = chunk.content[:max_content]
        highlights = _extract_highlights(content)
        if highlights:
            lines.append("> " + " · ".join(highlights))
        lines.append(content)
        url = (url_map or {}).get(chunk.document_id)
        if url:
            lines.append(f"_Bron: [document {chunk.document_id}]({url})_\n")
        else:
            lines.append(f"_document_id: {chunk.document_id}_\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 1 — Algemene raadshistorie zoekfunctie
# ---------------------------------------------------------------------------

@logged_tool
def zoek_raadshistorie(
    vraag: str,
    datum_van: Optional[str] = None,
    datum_tot: Optional[str] = None,
    max_resultaten: int = 8,
) -> str:
    """
    Doorzoek de Rotterdam gemeenteraad notulen op een vraag of onderwerp.
    Retourneert relevante tekstfragmenten met bronvermelding en datum.

    BELANGRIJK: Als de gebruiker temporele taal gebruikt (bijv. "vorig jaar",
    "sinds 2023", "de afgelopen maanden", "recent"), vertaal dit ALTIJD naar
    concrete datum_van/datum_tot waarden. Voorbeelden:
    - "vorig jaar" → datum_van="2025-01-01", datum_tot="2025-12-31"
    - "sinds 2023" → datum_van="2023-01-01"
    - "de afgelopen 6 maanden" → datum_van="2025-10-01"
    - "recent" → datum_van van ~3 maanden geleden

    Args:
        vraag: Zoekterm of vraag in het Nederlands (zonder temporele termen — die gaan in datum_van/datum_tot)
        datum_van: Startdatum filter, ISO formaat (bijv. "2022-01-01")
        datum_tot: Einddatum filter, ISO formaat (bijv. "2022-12-31")
        max_resultaten: Aantal resultaten (max 20, standaard 8)
    """
    rag = _get_rag()
    top_k = min(max(1, max_resultaten), 20)

    chunks = rag.retrieve_relevant_context(
        query_text=vraag,
        top_k=top_k,
        date_from=datum_van,
        date_to=datum_tot,
        fast_mode=True,
    )

    header = f"## Raadshistorie: '{vraag}'"
    if datum_van or datum_tot:
        header += f"\n_Periode: {datum_van or '…'} — {datum_tot or 'heden'}_"

    url_map = _get_doc_urls(chunks)
    return header + "\n\n" + _format_chunks_as_markdown(chunks, url_map=url_map)


# ---------------------------------------------------------------------------
# Tool 2 — Financiële gegevens
# ---------------------------------------------------------------------------

@logged_tool
def zoek_financieel(
    onderwerp: str,
    datum_van: Optional[str] = None,
    datum_tot: Optional[str] = None,
) -> str:
    """
    Zoek financiële gegevens, begrotingen en budgetmutaties in de raadsstukken.
    Tabeldata wordt gestructureerd teruggegeven waar beschikbaar (Markdown-tabel).
    Gebruik dit voor vragen over kosten, budgetten, bezuinigingen, subsidies.

    Args:
        onderwerp: Financieel onderwerp (bijv. "jeugdzorg begroting 2023")
        datum_van: Startdatum filter, ISO formaat
        datum_tot: Einddatum filter, ISO formaat
    """
    rag = _get_rag()
    query = f"{onderwerp} begroting budget kosten cijfers financieel subsidie"

    chunks = rag.retrieve_relevant_context(
        query_text=query,
        top_k=8,
        date_from=datum_van,
        date_to=datum_tot,
        fast_mode=True,
    )

    # Prefer financial-typed chunks; fall back to all if none
    financial = [c for c in chunks if c.stream_type == "financial"]
    display = financial if financial else chunks

    header = f"## Financiële gegevens: '{onderwerp}'"
    if datum_van or datum_tot:
        header += f"\n_Periode: {datum_van or '…'} — {datum_tot or 'heden'}_"

    url_map = _get_doc_urls(display)
    return header + "\n\n" + _format_chunks_as_markdown(display, url_map=url_map)


# ---------------------------------------------------------------------------
# Tool 3 — Uitspraken en citaten van raadsleden
# ---------------------------------------------------------------------------

@logged_tool
def zoek_uitspraken(
    onderwerp: str,
    partij_of_raadslid: Optional[str] = None,
    datum_van: Optional[str] = None,
    datum_tot: Optional[str] = None,
) -> str:
    """
    Zoek uitspraken en citaten van raadsleden in de debatnotulen.
    Geeft fragmenten terug inclusief context en vergaderdatum.
    Gebruik voor vragen als: "wat zei PvdA over de wooncrisis in 2023?"

    Args:
        onderwerp: Onderwerp waarover de uitspraak gaat
        partij_of_raadslid: Filter op naam van partij of raadslid (optioneel)
        datum_van: Startdatum filter, ISO formaat
        datum_tot: Einddatum filter, ISO formaat
    """
    rag = _get_rag()
    query = f"{onderwerp} debat standpunten uitspraken raadslid"
    if partij_of_raadslid:
        query += f" {partij_of_raadslid}"

    chunks = rag.retrieve_relevant_context(
        query_text=query,
        top_k=10,
        date_from=datum_van,
        date_to=datum_tot,
        fast_mode=True,
    )

    # Prefer debate-typed chunks
    debate = [c for c in chunks if c.stream_type == "debate"]
    display = debate if debate else chunks

    header = f"## Uitspraken over: '{onderwerp}'"
    if partij_of_raadslid:
        header += f"\n_Filter: {partij_of_raadslid}_"
    if datum_van or datum_tot:
        header += f"\n_Periode: {datum_van or '…'} — {datum_tot or 'heden'}_"

    url_map = _get_doc_urls(display)
    return header + "\n\n" + _format_chunks_as_markdown(display, url_map=url_map)


# ---------------------------------------------------------------------------
# Tool 4 — Vergaderdetails ophalen
# ---------------------------------------------------------------------------

@logged_tool
def haal_vergadering_op(
    vergadering_id: Optional[str] = None,
    datum: Optional[str] = None,
) -> str:
    """
    Haal details van een specifieke vergadering op: agenda, commissie, documenten.
    Geef vergadering_id OF datum (JJJJ-MM-DD). Bij datum wordt de eerste match teruggegeven.

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
            name = d.get("name", "Document")
            doc_id = d.get("id", "")
            url = d.get("url", "")
            if url:
                lines.append(f"- [{name}]({url})")
            else:
                lines.append(f"- {name} (id={doc_id})")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 5 — Lijst van vergaderingen
# ---------------------------------------------------------------------------

@logged_tool
def lijst_vergaderingen(
    jaar: Optional[int] = None,
    commissie: Optional[str] = None,
    max_resultaten: int = 25,
) -> str:
    """
    Geeft een lijst van vergaderingen, optioneel gefilterd op jaar of commissie.
    Gebruik dit om te weten welke vergaderingen er zijn geweest of gepland staan.

    Args:
        jaar: Filterjaar (bijv. 2023)
        commissie: Filter op commissienaam (gedeeltelijke match, hoofdletterongevoelig)
        max_resultaten: Maximaal aantal resultaten (standaard 25, max 100)
    """
    storage = _get_storage()
    limit = min(max(1, max_resultaten), 100)
    meetings = storage.get_meetings(limit=limit, year=jaar)

    if commissie:
        meetings = [
            m for m in meetings
            if commissie.lower() in (m.get("committee") or "").lower()
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
        date = (m.get("start_date") or "")[:10]
        name = (m.get("name") or "")[:55]
        committee = (m.get("committee") or "")[:35]
        mid = m.get("id", "")
        lines.append(f"| {date} | {name} | {committee} | {mid} |")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 6 — Chronologische tijdlijn van besluitvorming
# ---------------------------------------------------------------------------

@logged_tool
def tijdlijn_besluitvorming(
    onderwerp: str,
    datum_van: Optional[str] = None,
    datum_tot: Optional[str] = None,
) -> str:
    """
    Bouw een chronologische tijdlijn van discussies en besluitvorming over een onderwerp.
    Gebruikt Claude om de opgehaalde fragmenten te synthetiseren tot een narratieve tijdlijn
    met sleutelmomenten, verschuivingen in standpunten en stemuitkomsten.
    Ideaal voor vragen als: "hoe is het beleid rondom X geëvolueerd?"

    Args:
        onderwerp: Beleidsonderwerp voor de tijdlijn
        datum_van: Startdatum (ISO), bijv. "2020-01-01"
        datum_tot: Einddatum (ISO), bijv. "2024-12-31"
    """
    rag = _get_rag()

    chunks = rag.retrieve_relevant_context(
        query_text=onderwerp,
        top_k=24,
        date_from=datum_van,
        date_to=datum_tot,
        fast_mode=True,
    )

    if not chunks:
        return f"Geen fragmenten gevonden voor '{onderwerp}'."

    # Sort chronologically before handing to Claude
    chunks.sort(key=lambda c: c.start_date or "")
    url_map = _get_doc_urls(chunks)
    raw_context = _format_chunks_as_markdown(chunks, url_map=url_map)

    period_str = ""
    if datum_van or datum_tot:
        period_str = f" (periode: {datum_van or '…'} — {datum_tot or 'heden'})"

    synthesis = _claude_synthesize(
        system=(
            "Je bent een expert in Rotterdamse gemeentepolitiek. "
            "Synthetiseer de aangeleverde raadsfragmenten tot een heldere chronologische tijdlijn. "
            "Structureer per jaar of fase. Benoem voor elk moment: datum, wat er besloten of gezegd werd, "
            "welke partijen een rol speelden, en of er moties werden ingediend of verworpen. "
            "Signaleer verschuivingen in coalitie- of oppositiestandpunten over tijd. "
            "Gebruik concrete citaten en motienummers waar aanwezig in de fragmenten. "
            "Schrijf in het Nederlands."
        ),
        user=(
            f"Maak een tijdlijn voor het onderwerp: **{onderwerp}**{period_str}\n\n"
            f"Bronfragmenten:\n\n{raw_context}"
        ),
    )

    return f"## Tijdlijn: {onderwerp}{period_str}\n\n{synthesis}"


# ---------------------------------------------------------------------------
# Tool 6b — Zoek moties en amendementen
# ---------------------------------------------------------------------------

@logged_tool
def zoek_moties(
    onderwerp: str,
    partij: Optional[str] = None,
    datum_van: Optional[str] = None,
    datum_tot: Optional[str] = None,
) -> str:
    """
    Zoek moties en amendementen over een onderwerp. Retourneert een gestructureerd overzicht
    met motienummers, indieners, stemuitkomst (aangenomen/verworpen) en samenvatting per motie.
    Gebruikt Claude om de uitkomsten te extraheren uit de debatnotulen.

    Gebruik dit voor vragen als: "welke moties zijn ingediend over asielopvang?"
    of "heeft de gemeenteraad ooit een motie aangenomen over X?"

    Args:
        onderwerp: Onderwerp van de motie (bijv. "asielopvang", "woningbouw", "klimaat")
        partij: Filter op indiener (partijnaam, optioneel)
        datum_van: Startdatum filter, ISO formaat
        datum_tot: Einddatum filter, ISO formaat
    """
    rag = _get_rag()

    query = f"motie amendement {onderwerp} ingediend aangenomen verworpen"
    if partij:
        query += f" {partij}"

    chunks = rag.retrieve_relevant_context(
        query_text=query,
        top_k=16,
        date_from=datum_van,
        date_to=datum_tot,
        fast_mode=True,
    )

    if not chunks:
        return f"Geen moties gevonden voor '{onderwerp}'."

    # Prefer debate chunks where moties are most likely to appear
    debate = [c for c in chunks if c.stream_type == "debate"]
    display = debate if debate else chunks
    display.sort(key=lambda c: c.start_date or "")

    url_map = _get_doc_urls(display)
    raw_context = _format_chunks_as_markdown(display, url_map=url_map)

    partij_filter = f" ingediend door {partij}" if partij else ""
    period_str = ""
    if datum_van or datum_tot:
        period_str = f" (periode: {datum_van or '…'} — {datum_tot or 'heden'})"

    synthesis = _claude_synthesize(
        system=(
            "Je bent een expert in Rotterdamse gemeentepolitiek. "
            "Extraheer alle moties en amendementen uit de aangeleverde raadsfragmenten. "
            "Geef voor elke motie een apart blok met:\n"
            "- **Motienummer** (bijv. 24bb009191) indien vermeld\n"
            "- **Datum** van indiening/behandeling\n"
            "- **Indieners** (partij en/of raadslid)\n"
            "- **Stemuitkomst**: aangenomen / verworpen / ingetrokken / aangehouden\n"
            "- **Korte inhoud**: wat verzocht de motie?\n"
            "- **Context**: hoe reageerde de coalitie/oppositie?\n\n"
            "Als er geen expliciete motienummers staan, gebruik dan de datum + titel als identifier. "
            "Sluit af met een samenvattingstabel. Schrijf in het Nederlands."
        ),
        user=(
            f"Extraheer alle moties over **{onderwerp}**{partij_filter}{period_str} "
            f"uit de volgende fragmenten:\n\n{raw_context}"
        ),
    )

    return f"## Moties: {onderwerp}{partij_filter}{period_str}\n\n{synthesis}"


# ---------------------------------------------------------------------------
# Tool 7 — Agendapunt: ruwe context voor Claude-analyse
# ---------------------------------------------------------------------------

@logged_tool
def analyseer_agendapunt(
    agendapunt_id: str,
    partij: str = "GroenLinks-PvdA",
) -> str:
    """
    Verzamelt alle benodigde informatie voor een diepgaande analyse van een agendapunt.
    Retourneert: documentinhoud, partijprofiel en historische RAG-context.
    Claude voert zelf de analyse uit op basis van deze gestructureerde data.
    Dit is sneller en kwalitatief beter dan een externe LLM-aanroep.

    Args:
        agendapunt_id: ID van het agendapunt (te vinden via lijst_vergaderingen + haal_vergadering_op)
        partij: Partijnaam voor de lenssessie (standaard "GroenLinks-PvdA")
    """
    storage = _get_storage()
    rag = _get_rag()

    item_data = storage.get_agenda_item_with_sub_documents(agendapunt_id)
    if not item_data:
        return f"Agendapunt '{agendapunt_id}' niet gevonden."

    item_name = item_data.get("name") or "Onbekend agendapunt"
    meeting_name = item_data.get("meeting_name") or ""
    meeting_date = (item_data.get("start_date") or "")[:10]

    # --- Documents ---
    docs = item_data.get("documents", [])
    doc_sections = []
    for d in docs[:4]:
        content = (d.get("content") or "").strip()[:4000]
        doc_sections.append(f"#### {d.get('name', 'Document')} (id={d.get('id')})\n{content}")

    # --- Party profile ---
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

    # --- Historical RAG context ---
    hist_chunks = rag.retrieve_relevant_context(
        query_text=item_name,
        top_k=5,
        fast_mode=True,
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
        _format_chunks_as_markdown(hist_chunks, url_map=_get_doc_urls(hist_chunks)),
        "",
        "---",
        "_Analyseer bovenstaande informatie vanuit het perspectief van "
        f"{partij}: afstemming met programmapunten, kritische vragen, "
        "mogelijke amendementen en eigen bijdrage aan het debat._",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 8 — Debatconfrontaties tussen partijen
# ---------------------------------------------------------------------------

@logged_tool
def zoek_debatconfrontaties(
    onderwerp: str,
    partij_a: str,
    partij_b: Optional[str] = None,
    datum_van: Optional[str] = None,
    datum_tot: Optional[str] = None,
) -> str:
    """
    Zoek specifieke debatconfrontaties waarbij twee partijen tegenover elkaar stonden.
    Haalt uitspraken van beide partijen op in één tool-aanroep, gegroepeerd per partij.
    Ideaal voor vragen als: "hoe viel PvdA D66 aan op asielopvang, en hoe reageerde D66?"

    Args:
        onderwerp: Het onderwerp van de confrontatie (bijv. "asielopvang spreidingswet")
        partij_a: Eerste partij (bijv. "D66")
        partij_b: Tweede partij of oppositieblok (bijv. "PvdA", optioneel)
        datum_van: Startdatum filter, ISO formaat
        datum_tot: Einddatum filter, ISO formaat
    """
    rag = _get_rag()

    def _fetch(partij: str):
        query = f"{onderwerp} debat confrontatie aanval verdediging {partij}"
        chunks = rag.retrieve_relevant_context(
            query_text=query,
            top_k=12,
            date_from=datum_van,
            date_to=datum_tot,
            fast_mode=True,
        )
        debate = [c for c in chunks if c.stream_type == "debate"]
        # Sort chronologically so the model sees narrative development
        results = debate if debate else chunks[:8]
        results.sort(key=lambda c: c.start_date or "")
        return results

    chunks_a = _fetch(partij_a)
    url_map_a = _get_doc_urls(chunks_a)

    lines = [
        f"## Debatconfrontaties: '{onderwerp}'",
        f"_Partijen: {partij_a}" + (f" vs. {partij_b}" if partij_b else "") + "_",
    ]
    if datum_van or datum_tot:
        lines.append(f"_Periode: {datum_van or '…'} — {datum_tot or 'heden'}_")
    lines.append("")

    lines.append(f"### Uitspraken — {partij_a}")
    lines.append(_format_chunks_as_markdown(chunks_a, url_map=url_map_a))

    if partij_b:
        chunks_b = _fetch(partij_b)
        url_map_b = _get_doc_urls(chunks_b)
        lines.append(f"\n### Uitspraken — {partij_b}")
        lines.append(_format_chunks_as_markdown(chunks_b, url_map=url_map_b))

    lines.append(
        "\n_Analyseer op basis van bovenstaande fragmenten: "
        "welke specifieke aanvallen deed elke partij, welke moties werden ingediend, "
        "en hoe positioneerden ze zich ten opzichte van het coalitiebeleid?_"
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 9 — Partijstandpunt ophalen
# ---------------------------------------------------------------------------

@logged_tool
def haal_partijstandpunt_op(
    beleidsgebied: str,
    partij: str = "GroenLinks-PvdA",
) -> str:
    """
    Haalt het geregistreerde standpunt van een partij op voor een beleidsgebied,
    aangevuld met relevante uitspraken uit de notulen.
    Claude kan op basis hiervan consistentie over tijd beoordelen.

    Args:
        beleidsgebied: Beleidsgebied (bijv. "Wonen", "Klimaat", "Onderwijs", "Zorg")
        partij: Partijnaam (standaard "GroenLinks-PvdA")
    """
    rag = _get_rag()
    profile = _load_party_profile(partij)
    posities = profile.get("posities", {})

    # Fuzzy match on beleidsgebied key
    zoek = beleidsgebied.lower()
    matched = {
        k: v for k, v in posities.items()
        if zoek in k.lower() or k.lower() in zoek
    }

    lines = [f"## Partijstandpunt: {partij} — {beleidsgebied}\n"]

    if not matched:
        lines.append(f"_Geen profielentries gevonden voor '{beleidsgebied}'._")
        lines.append("_Beschikbare gebieden:_ " + ", ".join(list(posities.keys())[:15]))
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

    # Supplement with live RAG results for richer context
    query = f"{beleidsgebied} {partij} standpunt visie programma"
    chunks = rag.retrieve_relevant_context(
        query_text=query,
        top_k=5,
        fast_mode=True,
    )
    vision_chunks = [c for c in chunks if c.stream_type in ("vision", "debate")]
    if vision_chunks:
        lines.append("### Aanvullende context uit notulen")
        lines.append(_format_chunks_as_markdown(vision_chunks[:4], url_map=_get_doc_urls(vision_chunks[:4])))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
#
# Transports:
#   stdio (default) — Claude Desktop and ChatGPT Desktop
#   --http           — streamable-HTTP for ChatGPT Web/API and Perplexity
#
# HTTP auth (optional):
#   Set MCP_API_KEY env var to require "Authorization: Bearer <key>" on all requests.
#
# Examples:
#   python mcp_server.py                        # stdio (Claude/ChatGPT Desktop)
#   python mcp_server.py --http                 # HTTP on 0.0.0.0:8080
#   python mcp_server.py --http --port 9000     # custom port
#   MCP_API_KEY=secret python mcp_server.py --http  # with auth
# ---------------------------------------------------------------------------


def _build_http_app():
    """Return the ASGI app wrapped with token auth middleware.

    Validates Bearer tokens against the api_tokens DB table.
    Falls back to MCP_API_KEY env var for backward compatibility.
    """
    from starlette.responses import Response as _Response

    base_app = mcp.streamable_http_app()
    legacy_api_key = os.getenv("MCP_API_KEY", "").strip()

    class _TokenAuthMiddleware:
        """ASGI middleware: validate Bearer token against DB or legacy env var."""

        def __init__(self, app):
            self._app = app
            self._auth_service = None

        def _get_auth(self):
            if self._auth_service is None:
                from services.auth_service import AuthService
                self._auth_service = AuthService()
            return self._auth_service

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                headers = dict(scope.get("headers", []))
                auth = headers.get(b"authorization", b"").decode()

                if not auth.startswith("Bearer "):
                    resp = _Response(
                        '{"error":"Authorization: Bearer <token> header required"}',
                        status_code=401,
                        media_type="application/json",
                    )
                    await resp(scope, receive, send)
                    return

                token = auth[7:]

                # Legacy env var check (backward compat)
                if legacy_api_key and token == legacy_api_key:
                    await self._app(scope, receive, send)
                    return

                # DB token validation
                user = self._get_auth().validate_api_token(token, required_scope="mcp")
                if not user or not user.get("mcp_access"):
                    resp = _Response(
                        '{"error":"Invalid or unauthorized token"}',
                        status_code=403,
                        media_type="application/json",
                    )
                    await resp(scope, receive, send)
                    return

            await self._app(scope, receive, send)

    return _TokenAuthMiddleware(base_app)


if __name__ == "__main__":
    import argparse
    import threading

    parser = argparse.ArgumentParser(description="NeoDemos MCP Server")
    parser.add_argument(
        "--http",
        action="store_true",
        help=(
            "Pure HTTP mode: bind on --host (default 0.0.0.0) for public deployment. "
            "Without this flag the server runs stdio + HTTP-on-localhost simultaneously."
        ),
    )
    parser.add_argument(
        "--host",
        default=os.getenv("MCP_HTTP_HOST", "0.0.0.0"),
        help="HTTP bind host for --http mode (default: 0.0.0.0, override via MCP_HTTP_HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MCP_HTTP_PORT", "8080")),
        help="HTTP port (default: 8080, override via MCP_HTTP_PORT)",
    )
    args = parser.parse_args()

    if args.http:
        # Pure HTTP — intended for public/cloud deployment behind a reverse proxy.
        # Claude.ai web and Perplexity web will connect to this once you have a public URL.
        import uvicorn

        print(
            f"[neodemos] HTTP server → http://{args.host}:{args.port}/mcp",
            file=sys.stderr,
        )
        uvicorn.run(_build_http_app(), host=args.host, port=args.port)
    else:
        # stdio (Claude Desktop / ChatGPT Desktop) + HTTP-on-localhost in a daemon thread.
        # Perplexity Desktop and Claude.ai web (dev tunnel) connect to localhost HTTP.
        http_port = args.port

        def _start_local_http():
            import uvicorn
            uvicorn.run(
                _build_http_app(),
                host="localhost",
                port=http_port,
                log_level="warning",
            )

        threading.Thread(target=_start_local_http, daemon=True, name="mcp-http").start()
        print(
            f"[neodemos] HTTP also on http://localhost:{http_port}/mcp "
            "(Perplexity Desktop / Claude.ai web via tunnel)",
            file=sys.stderr,
        )
        mcp.run(transport="stdio")
