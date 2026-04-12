"""
services/temporal_parser.py
===========================

Standalone, lightweight temporal-filter extractor for Dutch municipal search
queries. Extracted from ``services.ai_service.AIService.extract_temporal_filters``
so that the MCP server (and any other lean consumer) can use it as a
server-side fallback when weaker models (Mistral / Le Chat) fail to translate
Dutch temporal phrases like "vorig jaar" into ``datum_van`` / ``datum_tot``.

Why a separate module?
    The full ``AIService`` pulls in embedding pipelines, Gemini client config,
    and requires ``GEMINI_API_KEY``. The MCP server should be able to import
    this module cheaply and fall back to a pure-regex parser when Gemini is
    unavailable.

Public surface:
    - ``has_temporal_signal(query)`` — pure-Python heuristic, no LLM.
    - ``extract_year_range(query, today)`` — regex-based Dutch/English date
      word parser; handles the ~80% common cases with zero LLM cost.
    - ``extract_temporal_filters(query, today=None)`` — sync, LLM-backed
      (lazy-imports ``google.genai``). Returns the full
      ``{"query", "date_from", "date_to"}`` dict.
    - ``extract_temporal_filters_async(query, today=None)`` — async wrapper
      around the sync version via ``asyncio.to_thread``.
    - ``parse(query, today=None)`` — top-level helper: regex first, Gemini
      second, no-op third. This is the function MCP imports.
"""

from __future__ import annotations

import asyncio
import calendar
import json
import logging
import os
import re
from datetime import date, timedelta
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Verbatim from services/ai_service.py::AIService.extract_temporal_filters
# (lines 621-626 as of 2026-04-11). Keep in sync with the source until we
# migrate all call sites to this module.
TEMPORAL_SIGNALS = [
    "vorig", "afgelopen", "sinds", "recent", "eerder", "laatste",
    "dit jaar", "vorige maand", "begin 20", "eind 20", "na 20",
    "voor 20", "in 20", "from 20", "since 20", "last year",
    "this year", "recent", "ago",
]


# ---------------------------------------------------------------------------
# Fast-path heuristic (no LLM, no regex)
# ---------------------------------------------------------------------------

def has_temporal_signal(query: str) -> bool:
    """Return True if the query contains any known temporal keyword.

    Uses the same ``temporal_signals`` list as ``AIService``, verbatim, so
    behaviour stays identical to the current fast path.
    """
    if not query:
        return False
    lowered = query.lower()
    return any(signal in lowered for signal in TEMPORAL_SIGNALS)


# ---------------------------------------------------------------------------
# Regex-based Dutch date-word parser (no LLM)
# ---------------------------------------------------------------------------

def _year_bounds(year: int) -> Tuple[str, str]:
    """Return ISO start/end dates for a calendar year."""
    return f"{year:04d}-01-01", f"{year:04d}-12-31"


def extract_year_range(
    query: str,
    today: date,
) -> Tuple[Optional[str], Optional[str]]:
    """Regex-based fallback that handles the common Dutch temporal patterns.

    Handled patterns (ordered by specificity):
        - "sinds 20XX" / "na 20XX" / "since 20XX" / "from 20XX"
              → (20XX-01-01, None)
        - "voor 20XX" / "tot 20XX"
              → (None, 20XX-12-31)
        - "in 20XX"
              → (20XX-01-01, 20XX-12-31)
        - "vorig jaar" / "last year"
              → (today.year-1 start, today.year-1 end)
        - "dit jaar" / "this year"
              → (today.year start, today.year end)
        - "afgelopen maand" / "afgelopen maanden"
              → rough backoff (today - 90d, today); logged as approximate.

    Returns ``(None, None)`` if no pattern matches. This is the free fallback
    for Mistral / weaker models — no tokens spent.
    """
    if not query:
        return None, None

    q = query.lower()

    # "sinds 20XX" / "na 20XX" / "since 20XX" / "from 20XX"  → open-ended from
    m = re.search(r"\b(?:sinds|na|since|from)\s+(20\d{2})\b", q)
    if m:
        year = int(m.group(1))
        return f"{year:04d}-01-01", None

    # "voor 20XX" / "tot 20XX" → open-ended until
    m = re.search(r"\b(?:voor|tot)\s+(20\d{2})\b", q)
    if m:
        year = int(m.group(1))
        return None, f"{year:04d}-12-31"

    # "in 20XX" → full calendar year
    m = re.search(r"\bin\s+(20\d{2})\b", q)
    if m:
        year = int(m.group(1))
        return _year_bounds(year)

    # "vorig jaar" / "last year"
    if re.search(r"\bvorig\s+jaar\b", q) or re.search(r"\blast\s+year\b", q):
        return _year_bounds(today.year - 1)

    # "dit jaar" / "this year"
    if re.search(r"\bdit\s+jaar\b", q) or re.search(r"\bthis\s+year\b", q):
        return _year_bounds(today.year)

    # "afgelopen maand" / "afgelopen maanden" → rough 90-day backoff
    if re.search(r"\bafgelopen\s+maand(?:en)?\b", q):
        logger.debug(
            "extract_year_range: 'afgelopen maand(en)' matched — using "
            "approximate 90-day backoff window (today - 90d, today)"
        )
        start = today - timedelta(days=90)
        return start.isoformat(), today.isoformat()

    return None, None


# ---------------------------------------------------------------------------
# Sync LLM-backed extractor (lazy-imports google.genai)
# ---------------------------------------------------------------------------

_NOOP_KEYS = ("query", "date_from", "date_to")


def _noop(query: str) -> Dict[str, Optional[str]]:
    return {"query": query, "date_from": None, "date_to": None}


def extract_temporal_filters(
    query: str,
    today: Optional[date] = None,
) -> Dict[str, Optional[str]]:
    """Detect temporal language in a Dutch query and extract date filters.

    Returns ``{"query": cleaned_query, "date_from": iso_or_none,
    "date_to": iso_or_none}``. On *any* failure (no API key, no SDK, LLM
    error, malformed JSON), returns the no-op
    ``{"query": query, "date_from": None, "date_to": None}``.

    This is the sync version. The MCP server is sync today but
    ``extract_temporal_filters_async`` exists for future-proofing.

    The ``google.genai`` SDK is **lazy-imported inside the function** so that
    merely importing ``services.temporal_parser`` does not require the SDK or
    a valid ``GEMINI_API_KEY``.
    """
    # Fast-path: no temporal signal → skip LLM entirely.
    if not has_temporal_signal(query):
        return _noop(query)

    # Fast-path: no API key → skip LLM entirely.
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return _noop(query)

    today = today or date.today()
    today_iso = today.isoformat()

    prompt = f"""Vandaag is {today_iso}. Analyseer deze zoekvraag en extraheer temporele filters.

Vraag: "{query}"

Als de vraag een tijdsperiode impliceert, geef dan:
- query: de vraag ZONDER temporele termen (behoud de inhoudelijke zoektermen)
- date_from: startdatum in ISO formaat (YYYY-MM-DD) of null
- date_to: einddatum in ISO formaat (YYYY-MM-DD) of null

Voorbeelden:
- "parkeerbeleid vorig jaar" → {{"query": "parkeerbeleid", "date_from": "{today.year - 1}-01-01", "date_to": "{today.year - 1}-12-31"}}
- "wat is er recent besloten over woningbouw" → {{"query": "besloten woningbouw", "date_from": "{today.year}-01-01", "date_to": null}}
- "klimaatbeleid" → {{"query": "klimaatbeleid", "date_from": null, "date_to": null}}

Antwoord ALLEEN met een JSON object, geen uitleg."""

    try:
        # Lazy import — importing this module must not require the SDK.
        from google import genai  # type: ignore

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=prompt,
        )
        json_match = re.search(r"\{[\s\S]*?\}", response.text or "")
        if json_match:
            result = json.loads(json_match.group())
            return {
                "query": result.get("query") or query,
                "date_from": result.get("date_from"),
                "date_to": result.get("date_to"),
            }
    except Exception as e:
        logger.debug(
            "temporal_parser: LLM extraction failed (non-critical): %s", e
        )

    return _noop(query)


# ---------------------------------------------------------------------------
# Async wrapper
# ---------------------------------------------------------------------------

async def extract_temporal_filters_async(
    query: str,
    today: Optional[date] = None,
) -> Dict[str, Optional[str]]:
    """Async wrapper around :func:`extract_temporal_filters`.

    Runs the (blocking) Gemini call in a worker thread via
    ``asyncio.to_thread`` so it plays nicely with async MCP handlers.
    """
    return await asyncio.to_thread(extract_temporal_filters, query, today)


# ---------------------------------------------------------------------------
# Top-level helper (what MCP imports)
# ---------------------------------------------------------------------------

def parse(
    query: str,
    today: Optional[date] = None,
) -> Dict[str, Optional[str]]:
    """Top-level entry point for temporal parsing.

    Strategy (cheapest → most expensive):
        1. Regex pass (:func:`extract_year_range`) — zero cost, handles the
           common cases. If it returns a range, use it and tag
           ``source: "regex"``.
        2. LLM pass (:func:`extract_temporal_filters`) — only if Gemini is
           available. Tag ``source: "llm"`` on a non-trivial result.
        3. No-op — return the query unchanged with null dates. Tag
           ``source: "noop"``.

    The returned dict always contains ``query``, ``date_from``, ``date_to``,
    and ``source`` keys.
    """
    today = today or date.today()

    # 1. Regex fast-path.
    date_from, date_to = extract_year_range(query, today)
    if date_from is not None or date_to is not None:
        logger.debug(
            "temporal_parser.parse: regex matched (%s, %s) for query=%r",
            date_from, date_to, query,
        )
        return {
            "query": query,
            "date_from": date_from,
            "date_to": date_to,
            "source": "regex",
        }

    # 2. LLM path (only if we have both a signal and an API key).
    if has_temporal_signal(query) and os.getenv("GEMINI_API_KEY"):
        result = extract_temporal_filters(query, today=today)
        if result.get("date_from") or result.get("date_to"):
            return {**result, "source": "llm"}

    # 3. No-op.
    return {
        "query": query,
        "date_from": None,
        "date_to": None,
        "source": "noop",
    }


# ---------------------------------------------------------------------------
# Manual sanity check (no LLM calls)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Exercise has_temporal_signal and extract_year_range on sample queries.
    # Does NOT touch Gemini — safe to run without GEMINI_API_KEY set.
    today = date(2026, 4, 11)
    samples = [
        "parkeerbeleid vorig jaar",
        "klimaatbeleid",
        "woningbouw sinds 2023",
        "moties in 2024",
        "afgelopen maanden besluiten over zorg",
    ]
    print(f"today = {today.isoformat()}")
    print("-" * 60)
    for q in samples:
        sig = has_temporal_signal(q)
        rng = extract_year_range(q, today)
        print(f"query           : {q!r}")
        print(f"  has_signal    : {sig}")
        print(f"  year_range    : {rng}")
        print()
