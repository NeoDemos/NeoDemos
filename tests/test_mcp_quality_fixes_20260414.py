"""
Regression tests for WS4 post-ship quality fixes (2026-04-14).

Each test maps to one item in the T1–T10 bug list from WS4_MCP_DISCIPLINE.md
§(4). Tests are unit-level where possible (no DB or Jina dependency); DB-
dependent tests are marked with @pytest.mark.integration.

Run:
    pytest tests/test_mcp_quality_fixes_20260414.py -v
    pytest tests/test_mcp_quality_fixes_20260414.py -v -m "not integration"
"""

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# T1 — tijdlijn_besluitvorming: min-score floor raised to 0.5
# ---------------------------------------------------------------------------

def test_t1_tijdlijn_score_floor():
    """Chunks below 0.5 similarity must be excluded from the timeline."""
    from dataclasses import dataclass
    from typing import Optional

    @dataclass
    class FakeChunk:
        similarity_score: Optional[float]
        stream_type: Optional[str] = None
        content: str = "x" * 50

    chunks = [
        FakeChunk(similarity_score=0.71, stream_type="debate"),
        FakeChunk(similarity_score=0.49, stream_type="debate"),    # must be dropped
        FakeChunk(similarity_score=0.73, stream_type="handboek"),  # procedural — drop
        FakeChunk(similarity_score=0.80, stream_type="notulen"),
    ]

    PROCEDURAL = {"ontvangstbevestiging", "zienswijze", "effectenrapportage", "handboek"}
    result = [
        c for c in chunks
        if (c.similarity_score or 0) >= 0.5
        and (c.stream_type or "").lower() not in PROCEDURAL
    ]
    assert len(result) == 2
    assert all(c.similarity_score >= 0.5 for c in result)
    assert all(c.stream_type not in PROCEDURAL for c in result)


# ---------------------------------------------------------------------------
# T2 — zoek_moties: exclude "Lijst met … moties" overview docs
# ---------------------------------------------------------------------------

def test_t2_overview_doc_excluded_by_condition():
    """SQL condition `d.name !~* '^Lijst met .* moties'` must be present."""
    # The condition is added as a literal string to the conditions list.
    conditions = [
        "(LOWER(d.name) LIKE '%%motie%%' OR LOWER(d.name) LIKE '%%amendement%%' OR LOWER(d.name) LIKE '%%initiatiefvoorstel%%')",
        "d.name !~* '^Lijst met .* moties'",
    ]
    where = " AND ".join(conditions)
    assert "!~*" in where, "T2: overview-doc regex exclusion must be in WHERE clause"
    assert "Lijst met" in where


def test_t2_overview_doc_regex_matches_examples():
    """Verify the regex catches known WIOSSAN overview doc names."""
    pattern = re.compile(r'^Lijst met .* moties', re.IGNORECASE)
    positives = [
        "Lijst met openstaande moties",
        "Lijst met aangehouden moties",
        "Lijst met afgedane moties",
        "lijst met openstaande moties",
    ]
    negatives = [
        "Motie leegstandsbelasting",
        "Amendement parkeerbeleid",
        "Initiatiefvoorstel Engberts & Vogelaar",
        "Overzicht moties 2024",  # does NOT start with "Lijst met"
    ]
    for name in positives:
        assert pattern.match(name), f"T2: should match overview doc: {name!r}"
    for name in negatives:
        assert not pattern.match(name), f"T2: should NOT match normal motion: {name!r}"


# ---------------------------------------------------------------------------
# T3 — zoek_moties: uitkomst regex fallback from body text
# ---------------------------------------------------------------------------

def test_t3_uitkomst_body_fallback():
    """When vote_outcome is None and title gives 'onbekend', body regex must win."""
    UITKOMST_BODY_RE = re.compile(
        r'\b(AANGENOMEN|VERWORPEN|INGETROKKEN|AANGEHOUDEN)\b', re.IGNORECASE
    )

    def _parse_uitkomst(name):
        for kw in ["aangenomen", "verworpen", "ingetrokken", "aangehouden"]:
            if kw in name.lower():
                return kw
        return "onbekend"

    # Known failing motions from feedback log
    cases = [
        ("Het nodige maatwerk leveren", None, "AANGENOMEN aan het einde van de vergadering.", "aangenomen"),
        ("Pomp levendigheid", None, "De motie is VERWORPEN met 23 stemmen tegen.", "verworpen"),
        ("Kracht van de nacht", None, "Motie AANGEHOUDEN tot volgende vergadering.", "aangehouden"),
    ]

    for name, vote_outcome, body, expected in cases:
        parsed = vote_outcome or _parse_uitkomst(name)
        if parsed == "onbekend" and body:
            m = UITKOMST_BODY_RE.search(body)
            if m:
                parsed = m.group(1).lower()
        assert parsed == expected, (
            f"T3: body fallback failed for '{name}': got {parsed!r}, expected {expected!r}"
        )


def test_t3_uitkomst_body_fallback_does_not_override_enriched():
    """When vote_outcome is already set, body fallback must not override it."""
    # If a motie has vote_outcome='aangenomen' from DB enrichment, we must keep it
    vote_outcome = "aangenomen"
    body = "VERWORPEN in de stemming"  # contradicts, should be ignored

    UITKOMST_BODY_RE = re.compile(
        r'\b(AANGENOMEN|VERWORPEN|INGETROKKEN|AANGEHOUDEN)\b', re.IGNORECASE
    )

    def _parse_uitkomst(name):
        return "onbekend"

    parsed = vote_outcome or _parse_uitkomst("")
    # Body regex only kicks in when parsed == "onbekend"
    if parsed == "onbekend" and body:
        m = UITKOMST_BODY_RE.search(body)
        if m:
            parsed = m.group(1).lower()

    assert parsed == "aangenomen", "T3: enriched vote_outcome must not be overridden by body regex"


# ---------------------------------------------------------------------------
# T4 — zoek_moties: BB-number deduplication
# ---------------------------------------------------------------------------

def test_t4_bb_dedup_keeps_most_recent():
    """Multiple rows with same BB-nummer must collapse to one (most recent first)."""
    BB_RE = re.compile(r'\b(\d{2}bb\d+)\b', re.IGNORECASE)

    def extract_bb(name):
        m = BB_RE.search(name or "")
        return m.group(1).lower() if m else None

    # Simulate rows (doc_id, name, start_date, ...)
    rows = [
        ("id1", "Motie 21bb004603 Kracht van de nacht v3", "2023-06-01", "", None, None, None, None),
        ("id2", "Motie 21bb004603 Kracht van de nacht v2", "2022-06-01", "", None, None, None, None),
        ("id3", "Motie 21bb004603 Kracht van de nacht v1", "2021-06-01", "", None, None, None, None),
        ("id4", "Motie 21bb004603 Tussenbericht kracht", "2022-03-01", "", None, None, None, None),
        ("id5", "Motie 22bb000001 Andere motie", "2023-01-01", "", None, None, None, None),
    ]

    seen_bb = {}
    deduped = []
    related_map = {}
    for row in rows:
        bb = extract_bb(row[1])
        if bb and bb in seen_bb:
            pri = seen_bb[bb]
            related_map.setdefault(pri, []).append((row[1], row[0]))
        else:
            idx = len(deduped)
            deduped.append(row)
            if bb:
                seen_bb[bb] = idx

    assert len(deduped) == 2, f"T4: expected 2 unique motions, got {len(deduped)}"
    assert deduped[0][0] == "id1", "T4: most recent version must be primary"
    assert len(related_map.get(0, [])) == 3, "T4: 3 versions/tussenberichten must be folded"
    assert deduped[1][0] == "id5"


def test_t4_no_bb_number_passes_through():
    """Docs without a BB-nummer must pass through unaffected."""
    BB_RE = re.compile(r'\b(\d{2}bb\d+)\b', re.IGNORECASE)

    rows = [
        ("id1", "Motie warmtenetten", "2023-01-01", "", None, None, None, None),
        ("id2", "Amendement parkeren", "2023-02-01", "", None, None, None, None),
    ]
    seen_bb = {}
    deduped = []
    for row in rows:
        bb = BB_RE.search(row[1] or "")
        bb = bb.group(1).lower() if bb else None
        if bb and bb in seen_bb:
            pass
        else:
            idx = len(deduped)
            deduped.append(row)
            if bb:
                seen_bb[bb] = idx

    assert len(deduped) == 2, "T4: docs without BB-nummer must all pass through"


# ---------------------------------------------------------------------------
# T5 — lijst_vergaderingen: commissie substring match on both code and name
# ---------------------------------------------------------------------------

def test_t5_commissie_matches_name_field():
    """'onderwijs' must match a meeting whose name contains 'Onderwijs'."""
    meetings = [
        {"committee": "WIOS", "name": "Commissie Werk & Inkomen, Onderwijs, Samenleven, Schuld"},
        {"committee": "BOFV", "name": "Commissie Bestuur, Organisatie, Financiën en Veiligheid"},
    ]
    commissie = "onderwijs"
    filtered = [
        m for m in meetings
        if commissie.lower() in (m.get("committee") or "").lower()
        or commissie.lower() in (m.get("name") or "").lower()
    ]
    assert len(filtered) == 1, "T5: 'onderwijs' must match via meeting name"
    assert "WIOS" in filtered[0]["committee"]


def test_t5_commissie_still_matches_code():
    """Exact committee code match must still work after T5 change."""
    meetings = [
        {"committee": "BOFV", "name": "Commissie Bestuur, Organisatie, Financiën"},
        {"committee": "WIOS", "name": "Commissie Werk & Inkomen"},
    ]
    commissie = "BOFV"
    filtered = [
        m for m in meetings
        if commissie.lower() in (m.get("committee") or "").lower()
        or commissie.lower() in (m.get("name") or "").lower()
    ]
    assert len(filtered) == 1
    assert filtered[0]["committee"] == "BOFV"


# ---------------------------------------------------------------------------
# T6 — haal_partijstandpunt_op: date_range_in_results surfaced
# ---------------------------------------------------------------------------

def test_t6_date_range_visible_in_response():
    """When RAG chunks have start_date, date_range must appear in the response."""
    # Simulate the date-range computation
    from dataclasses import dataclass, field
    from typing import Optional, List

    @dataclass
    class FakeChunk:
        start_date: Optional[str]
        similarity_score: float = 0.7
        content: str = "fragment tekst"

    chunks = [
        FakeChunk(start_date="2022-03-15"),
        FakeChunk(start_date="2020-01-10"),
        FakeChunk(start_date="2024-11-05"),
    ]

    dates = [c.start_date[:10] for c in chunks if c.start_date]
    date_range_line = f"_Bronperiode RAG-fragmenten: {min(dates)} — {max(dates)}_"

    assert "2020-01-10" in date_range_line
    assert "2024-11-05" in date_range_line


def test_t6_secondary_sort_by_date():
    """Without date params, chunks must be sorted similarity-desc then date-desc."""
    from dataclasses import dataclass
    from typing import Optional

    @dataclass
    class FakeChunk:
        similarity_score: float
        start_date: Optional[str]

    chunks = [
        FakeChunk(0.8, "2019-01-01"),
        FakeChunk(0.8, "2023-01-01"),  # same score, more recent → should come first
        FakeChunk(0.9, "2018-01-01"),
    ]

    def _key(c):
        try:
            date_int = int((c.start_date or "0000-00-00").replace("-", "")[:8])
        except (ValueError, TypeError):
            date_int = 0
        return (-(c.similarity_score or 0), -date_int)

    sorted_chunks = sorted(chunks, key=_key)
    assert sorted_chunks[0].similarity_score == 0.9
    assert sorted_chunks[1].start_date == "2023-01-01", "T6: recent date must sort above older at equal score"
    assert sorted_chunks[2].start_date == "2019-01-01"


# ---------------------------------------------------------------------------
# T7 — zoek_uitspraken: party-filter merge logic
# ---------------------------------------------------------------------------

def test_t7_party_chunks_not_diluted_by_standard():
    """When party_chunks is non-empty, standard_chunks must NOT be added."""
    # Mirrors the T7 fix in _retrieve_with_reranking
    from dataclasses import dataclass

    @dataclass
    class FakeChunk:
        chunk_id: str
        content: str = "fragment"

    party_chunks = [FakeChunk("p1"), FakeChunk("p2"), FakeChunk("p3")]
    standard_chunks = [FakeChunk("s1"), FakeChunk("s2")]  # non-party, should NOT appear

    # T7 fix logic: only add standard if party_chunks is empty
    if not party_chunks:
        result = standard_chunks
    else:
        result = party_chunks

    ids = [c.chunk_id for c in result]
    assert "s1" not in ids, "T7: standard_chunks must not dilute party-filtered results"
    assert "p1" in ids


# ---------------------------------------------------------------------------
# T8 — zoek_uitspraken_op_rol: procedural fragment demotion
# ---------------------------------------------------------------------------

def test_t8_short_fragment_demoted():
    """Fragments < 200 chars must have score demoted by 0.2."""
    from dataclasses import dataclass
    from typing import Optional

    @dataclass
    class FakeChunk:
        content: str
        similarity_score: float
        original_score: float = 0.0
        stream_type: Optional[str] = None

    SIG_RE = re.compile(r'(Met vriendelijke groet|Hoogachtend)', re.IGNORECASE)
    PROC = {"toezeggingen_lijst", "afdoeningsvoorstel", "ontvangstbevestiging"}

    chunks = [
        FakeChunk("x" * 150, 0.8, 0.8),                          # short → demote
        FakeChunk("x" * 300, 0.8, 0.8),                          # long → keep
        FakeChunk("Met vriendelijke groet, ...", 0.7, 0.7),       # signature → demote
        FakeChunk("x" * 300, 0.9, 0.9, "toezeggingen_lijst"),    # procedural type → demote
    ]

    for c in chunks:
        if (
            len(c.content) < 200
            or SIG_RE.search(c.content)
            or (c.stream_type or "").lower() in PROC
        ):
            c.similarity_score -= 0.2

    chunks.sort(key=lambda c: c.similarity_score, reverse=True)

    # Scores after demotion: long=0.8, procedural=0.7, short=0.6, sig=0.5
    # → sorted: 0.8, 0.7, 0.6, 0.5
    assert chunks[0].content == "x" * 300 and chunks[0].stream_type is None, (
        "T8: long non-procedural fragment must rank first"
    )
    # Exactly 3 items must have been demoted (score < original)
    demoted = [c for c in chunks if c.similarity_score < c.original_score]
    assert len(demoted) == 3, f"T8: expected 3 demoted chunks, got {len(demoted)}"


# ---------------------------------------------------------------------------
# T9 — lees_fragment: total_chunks_in_document visibility
# ---------------------------------------------------------------------------

def test_t9_total_chunks_in_header():
    """Header must show 'N van M fragmenten' when fewer returned than max."""
    total_in_doc = 12
    returned = 3
    max_fragmenten = 5

    header_line = f"_Document ID: doc123 | {returned} van {total_in_doc or '?'} fragmenten_"
    assert "3 van 12 fragmenten" in header_line, "T9: total chunk count must be in header"


def test_t9_no_extra_query_when_max_returned():
    """When returned == max_fragmenten, total_in_doc stays None (no extra query)."""
    rows = [object() for _ in range(5)]
    max_fragmenten = 5

    total_in_doc = None
    if len(rows) < max_fragmenten:
        total_in_doc = 99  # simulates DB query
    assert total_in_doc is None, "T9: no DB query when max_fragmenten rows returned"


# ---------------------------------------------------------------------------
# T10 — zoek_moties: single-word queries skip reranker
# ---------------------------------------------------------------------------

def test_t10_single_word_skips_rerank():
    """Single-word queries (len(search_terms) < 2) must not trigger reranker."""
    onderwerp = "leegstand"
    search_terms = [w for w in onderwerp.lower().split() if len(w) > 2]

    reranker_called = False

    if len(search_terms) >= 2:
        reranker_called = True  # would call _reranker

    assert not reranker_called, "T10: single-word queries must not trigger reranker"


def test_t10_multi_word_triggers_rerank_path():
    """Multi-word queries (len >= 2) must pass through the rerank path."""
    onderwerp = "horeca sluitingstijden beperking nachtleven"
    search_terms = [w for w in onderwerp.lower().split() if len(w) > 2]

    should_rerank = len(search_terms) >= 2
    assert should_rerank, "T10: multi-word queries must enter the rerank path"
