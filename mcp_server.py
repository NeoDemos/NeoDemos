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
import json
import glob
from datetime import date
from typing import Optional
from pathlib import Path

# Ensure project root is on sys.path so service imports work
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load environment variables before importing services
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "neodemos",
    instructions=(
        "Je bent een assistent voor Rotterdam gemeenteraad vergaderingen. "
        "Gebruik de beschikbare tools om relevante raadsinformatie op te halen. "
        "Analyseer en synthetiseer de opgehaalde data zelf — de tools doen alleen retrieval. "
        "Antwoord altijd in het Nederlands tenzij de gebruiker anders vraagt. "
        "TEMPORELE DETECTIE: Wanneer een vraag tijdsgebonden taal bevat "
        "(bijv. 'vorig jaar', 'sinds 2023', 'afgelopen maanden', 'recent', 'eerder'), "
        "vertaal dit ALTIJD naar concrete datum_van/datum_tot parameters bij je tool calls. "
        f"Vandaag is {date.today().isoformat()}. Verwijder temporele termen uit de zoektekst zelf — "
        "die filteren werkt via metadata, niet via vectorsimilariteit."
    ),
)

# ---------------------------------------------------------------------------
# Lazy service singletons — initialized on first tool call to keep startup fast
# ---------------------------------------------------------------------------

_rag = None
_storage = None


def _get_rag():
    """
    Returns a RAGService instance. Uses skip_llm=True to avoid loading the
    Mistral-24B LLM (only the Qwen3-8B embedding model is needed for retrieval).
    """
    global _rag
    if _rag is None:
        from services.rag_service import RAGService
        from services.local_ai_service import LocalAIService
        # Construct RAGService manually to pass skip_llm=True to LocalAIService.
        # This skips loading Mistral-24B (not needed for retrieval/embedding).
        rag = RAGService.__new__(RAGService)
        rag.db_connection_string = os.getenv(
            "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/neodemos"
        )
        rag.local_ai = LocalAIService(skip_llm=True)
        rag._ensure_resources_initialized()
        _rag = rag
    return _rag


def _get_storage():
    global _storage
    if _storage is None:
        from services.storage import StorageService
        _storage = StorageService()
    return _storage


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


def _format_chunks_as_markdown(chunks, max_content: int = 600) -> str:
    """Format a list of RetrievedChunk objects as readable Markdown for Claude."""
    if not chunks:
        return "_Geen resultaten gevonden._"
    lines = []
    for i, chunk in enumerate(chunks, 1):
        date_str = f" · {chunk.start_date[:10]}" if chunk.start_date else ""
        stream = f" [{chunk.stream_type}]" if chunk.stream_type else ""
        lines.append(f"### [{i}]{stream} {chunk.title}{date_str}")
        lines.append(chunk.content[:max_content])
        lines.append(f"_document_id: {chunk.document_id}_\n")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 1 — Algemene raadshistorie zoekfunctie
# ---------------------------------------------------------------------------

@mcp.tool()
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

    return header + "\n\n" + _format_chunks_as_markdown(chunks)


# ---------------------------------------------------------------------------
# Tool 2 — Financiële gegevens
# ---------------------------------------------------------------------------

@mcp.tool()
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

    return header + "\n\n" + _format_chunks_as_markdown(display, max_content=1200)


# ---------------------------------------------------------------------------
# Tool 3 — Uitspraken en citaten van raadsleden
# ---------------------------------------------------------------------------

@mcp.tool()
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

    return header + "\n\n" + _format_chunks_as_markdown(display, max_content=800)


# ---------------------------------------------------------------------------
# Tool 4 — Vergaderdetails ophalen
# ---------------------------------------------------------------------------

@mcp.tool()
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
            lines.append(f"- [{d.get('name', 'Document')}] id={d.get('id')}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 5 — Lijst van vergaderingen
# ---------------------------------------------------------------------------

@mcp.tool()
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

@mcp.tool()
def tijdlijn_besluitvorming(
    onderwerp: str,
    datum_van: Optional[str] = None,
    datum_tot: Optional[str] = None,
) -> str:
    """
    Bouw een chronologische tijdlijn van discussies en besluitvorming over een onderwerp.
    Groepeert raadsfragmenten per jaar, zodat Claude de ontwikkeling over tijd kan analyseren.
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

    timeline = rag.synthesize_timeline(chunks)

    lines = [f"## Tijdlijn: {onderwerp}"]
    if datum_van or datum_tot:
        lines.append(f"_Periode: {datum_van or '…'} — {datum_tot or 'heden'}_")
    lines.append("")

    for bucket in timeline:
        period = bucket["periode"]
        events = bucket["gebeurtenissen"]
        lines.append(f"### {period} ({len(events)} fragmenten)")
        for ev in events[:5]:  # max 5 per year to manage context size
            stream = f" [{ev['stream_type']}]" if ev.get("stream_type") else ""
            lines.append(f"- **{ev['titel']}**{stream}")
            lines.append(f"  {ev['snippet'][:250]}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 7 — Agendapunt: ruwe context voor Claude-analyse
# ---------------------------------------------------------------------------

@mcp.tool()
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
        _format_chunks_as_markdown(hist_chunks, max_content=400),
        "",
        "---",
        "_Analyseer bovenstaande informatie vanuit het perspectief van "
        f"{partij}: afstemming met programmapunten, kritische vragen, "
        "mogelijke amendementen en eigen bijdrage aan het debat._",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Tool 8 — Partijstandpunt ophalen
# ---------------------------------------------------------------------------

@mcp.tool()
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
        lines.append(_format_chunks_as_markdown(vision_chunks[:4], max_content=400))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
