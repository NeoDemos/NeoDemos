"""
Query Router — classifies incoming queries and returns routing parameters.

Two-tier routing:
  Tier 1: Rule-based fast path (<1ms) — metadata hints, keyword matching
  Tier 2: LLM classification (Haiku) — when rules are ambiguous

API-only: uses Claude Haiku via Anthropic API. No local models.
"""

import os
import re
import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from typing import List, Optional, Tuple

import anthropic
from dotenv import load_dotenv

load_dotenv()

from services.party_utils import extract_party_from_query, CANONICAL_PARTIES

log = logging.getLogger(__name__)

# ── Dynamic K per query type ──────────────────────────────────────────

K_MAP = {
    "factoid": 10,
    "temporal": 15,
    "party_stance": 25,
    "broad_aggregation": 50,
    "multi_hop": 15,
    "absence": 10,
    "acronym_abbreviation": 10,
    "balanced_view": 15,
    "specific_event": 10,
    "informal_opinion": 10,
}

STRATEGY_MAP = {
    "factoid": "standard",
    "temporal": "standard",
    "party_stance": "party_filtered",
    "broad_aggregation": "map_reduce",
    "multi_hop": "sub_query",
    "absence": "standard",
    "acronym_abbreviation": "standard",
    "balanced_view": "sub_query",
    "specific_event": "standard",
    "informal_opinion": "standard",
}

# ── Query Route dataclass ─────────────────────────────────────────────


@dataclass
class QueryRoute:
    query_type: str
    top_k: int
    parties: List[str] = field(default_factory=list)
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    sub_queries: List[str] = field(default_factory=list)
    confidence: float = 1.0
    strategy: str = "standard"
    boost_tables: bool = False  # Prioritize table_json chunks for financial queries

    def to_dict(self) -> dict:
        return asdict(self)


# ── Tier 1: Rule-based routing ────────────────────────────────────────

_AGGREGATION_SIGNALS = [
    "overzicht", "alle", "volledig", "samenvatting", "belangrijkste",
    "uitdagingen", "kansen", "geef me een", "noem alle",
]

_MULTI_HOP_SIGNALS = [
    "en wat", "verhoudt", "vergelijk", "daarnaast",
    "in combinatie met", "welke partijen stemden",
]

_FINANCIAL_SIGNALS = [
    "budget", "begroting", "financi", "kosten", "uitgaven", "inkomsten",
    "jaarstukken", "voorjaarsnota", "10-maands", "eindejaarsbrief",
    "miljoen", "miljard", "euro", "bedrag", "cijfers",
]

_ABSENCE_SIGNALS = [
    "is er ooit", "heeft de gemeente ooit", "bestaan er plannen",
]


# ── Temporal phrase extraction ────────────────────────────────────────

_DUTCH_NUMBERS = {
    "een": 1, "één": 1, "twee": 2, "drie": 3, "vier": 4, "vijf": 5,
    "zes": 6, "zeven": 7, "acht": 8, "negen": 9, "tien": 10,
}


def _parse_dutch_number(s: str) -> Optional[int]:
    """Parse a digit or Dutch number word to int."""
    if s.isdigit():
        return int(s)
    return _DUTCH_NUMBERS.get(s.lower())


def _extract_dates_from_text(query: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract date_from/date_to from Dutch temporal phrases in query text.
    Handles both digits and Dutch number words (e.g. "vier jaar").
    Returns (date_from, date_to) as ISO strings or (None, None).
    """
    lower = query.lower()
    today = date.today()

    # Number pattern: digit or Dutch word
    num = r'(\d+|een|één|twee|drie|vier|vijf|zes|zeven|acht|negen|tien)'

    # "afgelopen N jaar" / "de laatste N jaar"
    m = re.search(r'(?:afgelopen|laatste)\s+' + num + r'\s+jaar', lower)
    if m:
        years = _parse_dutch_number(m.group(1))
        if years:
            return (today - timedelta(days=365 * years)).isoformat(), today.isoformat()

    # "sinds YYYY"
    m = re.search(r'sinds\s+(\d{4})', lower)
    if m:
        return f"{m.group(1)}-01-01", today.isoformat()

    # "in YYYY"
    m = re.search(r'\bin\s+(\d{4})\b', lower)
    if m:
        return f"{m.group(1)}-01-01", f"{m.group(1)}-12-31"

    # "afgelopen N maanden"
    m = re.search(r'(?:afgelopen|laatste)\s+' + num + r'\s+maanden', lower)
    if m:
        months = _parse_dutch_number(m.group(1))
        if months:
            return (today - timedelta(days=30 * months)).isoformat(), today.isoformat()

    # "van YYYY tot YYYY"
    m = re.search(r'van\s+(\d{4})\s+tot\s+(\d{4})', lower)
    if m:
        return f"{m.group(1)}-01-01", f"{m.group(2)}-12-31"

    return None, None


# ── Tier 1: Rule-based routing (compound-aware) ──────────────────────

def _rule_based_route(query: str, metadata: dict) -> Optional[QueryRoute]:
    """
    Tier 1: fast deterministic routing from metadata and keyword signals.
    Collects ALL signals first, then composes a compound route.
    Returns None if ambiguous (triggers Tier 2).
    """
    lower = query.lower()

    # ── Collect all signals (never return early) ──

    # Financial
    is_financial = any(s in lower for s in _FINANCIAL_SIGNALS)

    # Party: from metadata or query text
    parties = []
    if metadata.get("party"):
        p = extract_party_from_query(metadata["party"])
        if p:
            parties.append(p)
    if not parties:
        p = extract_party_from_query(query)
        if p:
            parties.append(p)

    # Dates: from metadata first, then extract from query text
    date_from = metadata.get("date_from")
    date_to = metadata.get("date_to")
    if not date_from and not date_to:
        date_from, date_to = _extract_dates_from_text(query)

    # Absence
    is_absence = any(s in lower for s in _ABSENCE_SIGNALS)

    # Aggregation
    agg_score = sum(1 for s in _AGGREGATION_SIGNALS if s in lower)
    is_aggregation = agg_score >= 2

    # Multi-hop
    is_multi_hop = any(s in lower for s in _MULTI_HOP_SIGNALS)

    # Balanced view
    balanced_signals = ["zowel", "positieve als negatieve", "voor- en nadelen", "voor en tegen"]
    is_balanced = any(s in lower for s in balanced_signals)

    # ── Compose route by priority ──

    # Absence overrides everything
    if is_absence:
        return QueryRoute(
            query_type="absence",
            top_k=K_MAP["absence"],
            strategy="standard",
            parties=parties,
            date_from=date_from,
            date_to=date_to,
        )

    # Compound detection: queries that combine multiple dimensions
    compound_signals = [
        "welke maatregelen", "niet gehaald", "wel of niet",
        "voorgesteld en", "aangenomen of verworpen",
    ]
    is_compound = (
        parties
        and (date_from or date_to)
        and any(s in lower for s in compound_signals)
    )

    # Multi-hop: includes party+temporal compound queries
    # "Welke maatregelen zijn voorgesteld en hebben het niet gehaald?" = multi_hop
    # Even if a party is detected, the query needs decomposition
    if is_multi_hop or is_compound or (parties and (is_aggregation or is_balanced)):
        return QueryRoute(
            query_type="multi_hop" if is_multi_hop else "compound",
            top_k=K_MAP["multi_hop"],
            parties=parties,
            date_from=date_from,
            date_to=date_to,
            strategy="sub_query",
            confidence=0.9 if is_multi_hop else 0.8,
            boost_tables=is_financial,
        )

    # Aggregation
    if is_aggregation:
        return QueryRoute(
            query_type="broad_aggregation",
            top_k=K_MAP["broad_aggregation"],
            strategy="map_reduce",
            parties=parties,
            date_from=date_from,
            date_to=date_to,
        )

    # Balanced view → sub_query decomposition
    if is_balanced:
        return QueryRoute(
            query_type="balanced_view",
            top_k=K_MAP["balanced_view"],
            strategy="sub_query",
            parties=parties,
            date_from=date_from,
            date_to=date_to,
        )

    # Pure party stance (party detected, no complex signals)
    if parties:
        stance_signals = [
            "standpunt", "stelde", "denkt", "vindt", "positie",
            "opstelling", "voor of tegen", "wat denkt", "hoe staat",
        ]
        if any(s in lower for s in stance_signals):
            return QueryRoute(
                query_type="party_stance",
                top_k=K_MAP["party_stance"],
                parties=parties,
                date_from=date_from,
                date_to=date_to,
                strategy="party_filtered",
                boost_tables=is_financial,
            )

    # Pure temporal (dates detected, no other signals)
    if date_from or date_to:
        return QueryRoute(
            query_type="temporal",
            top_k=K_MAP["temporal"],
            date_from=date_from,
            date_to=date_to,
            strategy="standard",
            boost_tables=is_financial,
        )

    # No confident match — return None to trigger Tier 2
    return None


# ── Tier 2: LLM classification ───────────────────────────────────────

_ROUTER_PROMPT = """Je bent een query-classifier voor een RAG-systeem over de Rotterdamse gemeentepolitiek.

Classificeer de volgende vraag in precies één van deze typen:
- factoid: eenvoudige feitelijke vraag
- temporal: tijdgebonden vraag (specifiek jaar of periode)
- party_stance: vraag over het standpunt van een politieke partij
- broad_aggregation: vraag die een breed overzicht vereist van veel bronnen
- multi_hop: vraag die informatie uit meerdere documenten moet combineren
- absence: vraag over iets dat waarschijnlijk niet bestaat in de raadsstukken
- balanced_view: vraag die zowel voor- als nadelen/perspectieven vraagt
- specific_event: vraag over een specifiek evenement of gebeurtenis
- informal_opinion: informele of opiniegebaseerde vraag
- acronym_abbreviation: vraag over een afkorting of acroniem

Geef je antwoord als JSON:
{
  "query_type": "...",
  "parties": ["partijnaam", ...],
  "confidence": 0.0-1.0,
  "reasoning": "korte uitleg"
}

Als er partijnamen in de vraag voorkomen, normaliseer ze dan naar: """ + ", ".join(CANONICAL_PARTIES) + """

Vraag: """


async def _llm_classify(query: str) -> Optional[dict]:
    """
    Tier 2: Haiku classification. API-only.
    Returns parsed JSON or None on failure.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        log.warning("No ANTHROPIC_API_KEY — skipping LLM router")
        return None

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            temperature=0.0,
            messages=[{"role": "user", "content": _ROUTER_PROMPT + query}],
        )

        text = response.content[0].text.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        return json.loads(text)

    except Exception as e:
        log.warning(f"LLM router failed: {e}")
        return None


# ── Public API ────────────────────────────────────────────────────────

async def route_query(query: str, metadata: dict = None) -> QueryRoute:
    """
    Classify a query and return routing parameters.
    Tries rule-based first, falls back to LLM.
    """
    metadata = metadata or {}

    # Tier 1: rules
    route = _rule_based_route(query, metadata)
    if route:
        log.info(f"Router [rules]: {route.query_type} (confidence={route.confidence})")
        return route

    # Tier 2: LLM
    result = await _llm_classify(query)
    if result:
        query_type = result.get("query_type", "factoid")
        parties = result.get("parties", [])
        confidence = result.get("confidence", 0.5)

        route = QueryRoute(
            query_type=query_type,
            top_k=K_MAP.get(query_type, 10),
            parties=parties,
            date_from=metadata.get("date_from"),
            date_to=metadata.get("date_to"),
            confidence=confidence,
            strategy=STRATEGY_MAP.get(query_type, "standard"),
        )
        log.info(f"Router [LLM]: {route.query_type} (confidence={route.confidence})")
        return route

    # Fallback: factoid
    log.info("Router [fallback]: factoid")
    return QueryRoute(
        query_type="factoid",
        top_k=K_MAP["factoid"],
        strategy="standard",
    )
