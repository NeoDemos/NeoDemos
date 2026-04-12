"""
Unit tests for services.summarizer — WS6.

Offline: stubs out Gemini and uses a fake reranker so nothing hits the
network. Uses `asyncio.run(...)` directly (no pytest-asyncio dep).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import List

import pytest

from services.source_span_verifier import SourceSpanVerifier, split_sentences
from services.summarizer import (
    Summarizer,
    SummaryResult,
    TIER_SKIP_MAX_CHARS,
    TIER_EXCERPT_MAX_CHARS,
    _V020_MODES,
)


def _run(coro):
    return asyncio.run(coro)


@dataclass
class FakeChunk:
    chunk_id: int
    content: str
    document_id: str = "doc-1"
    title: str = ""


class FakeReranker:
    def __init__(self, score_map=None, default_score: float = 0.05):
        self.score_map = score_map or {}
        self.default_score = default_score

    def score_pairs(self, query: str, documents: List[str]) -> List[float]:
        row = self.score_map.get(query)
        if row is None:
            return [self.default_score] * len(documents)
        return list(row)


def _chunks() -> List[FakeChunk]:
    """Two chunks totalling ~3.5K chars → direct tier (3K-30K)."""
    return [
        FakeChunk(
            chunk_id=201,
            content=(
                "De raad besluit het bestemmingsplan vast te stellen voor 5 miljoen euro. "
                + "Dit voorstel omvat de herontwikkeling van het gebied rondom de Maashaven, "
                "inclusief de bouw van 450 nieuwe woningen, waarvan 30 procent sociaal, "
                "de aanleg van een park van 2 hectare en de verbetering van de kadeconstructie. "
                "Het college heeft de financiële dekking gevonden in de reserve stedelijke "
                "vernieuwing en een bijdrage van het Rijk via de Woningbouwimpuls. " * 5
            ),
        ),
        FakeChunk(
            chunk_id=202,
            content=(
                "Wethouder Achbar licht toe dat de woningbouw in 2027 start. "
                + "De planning voorziet in drie fasen: fase 1 betreft de sloop van de "
                "bestaande bedrijfspanden en sanering van de bodem, fase 2 de aanleg van "
                "de infrastructuur en het park, en fase 3 de daadwerkelijke woningbouw. "
                "De wethouder benadrukt dat de planning ambitieus is maar haalbaar, "
                "mits de vergunningprocedures voorspoedig verlopen. " * 5
            ),
        ),
    ]


def _short_chunks() -> List[FakeChunk]:
    """Content 500–3K chars → excerpt tier."""
    # Must be >= 500 chars (skip threshold) and < 3000 chars (excerpt threshold).
    text = (
        "De raad stelt de begroting vast voor het jaar 2026. "
        "Het totale bedrag is 1,2 miljoen euro, verdeeld over acht programmalijnen. "
        "De commissie Financiën adviseert positief over het voorstel. "
        "Wethouder Van der Berg benadrukt dat de extra middelen noodzakelijk zijn "
        "voor de uitvoering van het coalitieakkoord op het gebied van woningbouw, "
        "mobiliteit en duurzaamheid. De oppositiepartijen hebben vragen gesteld "
        "over de dekking van het tekort op de jeugdzorg. "
        "De raad zal op 15 maart 2026 stemmen over het definitieve voorstel."
    )
    assert 500 <= len(text) < 3000, f"Fixture content length {len(text)} out of excerpt range"
    return [FakeChunk(chunk_id=301, content=text)]


def _tiny_chunks() -> List[FakeChunk]:
    """Content < 500 chars → skip tier."""
    return [FakeChunk(chunk_id=401, content="Kort.")]


# ── Tier classification ─────────────────────────────────────────────────

def test_classify_tier_skip():
    chunks = _tiny_chunks()
    assert Summarizer.classify_tier(chunks) == "skip"


def test_classify_tier_excerpt():
    chunks = _short_chunks()
    total = sum(len(c.content) for c in chunks)
    assert TIER_SKIP_MAX_CHARS <= total < TIER_EXCERPT_MAX_CHARS
    assert Summarizer.classify_tier(chunks) == "excerpt"


def test_classify_tier_direct():
    # Two decent-sized chunks, well under 30K total
    chunks = _chunks()
    assert Summarizer.classify_tier(chunks) == "direct"


def test_classify_tier_extract():
    # A single chunk > 30K chars
    big = FakeChunk(chunk_id=501, content="x" * 35_000)
    assert Summarizer.classify_tier([big]) == "extract"


# ── Excerpt tier (no LLM call) ──────────────────────────────────────────

def test_excerpt_returns_first_sentences():
    chunks = _short_chunks()
    result = Summarizer.build_excerpt(chunks)
    assert result.tier == "excerpt"
    assert result.verified is True
    assert result.stripped_count == 0
    assert "begroting" in result.text
    assert len(result.sentences) <= 3


def test_excerpt_empty_chunks():
    chunks = [FakeChunk(chunk_id=1, content="")]
    result = Summarizer.build_excerpt(chunks)
    assert result.text == ""
    assert result.verified is False


# ── Mode gating ─────────────────────────────────────────────────────────

def test_deferred_modes_raise():
    s = Summarizer()
    with pytest.raises(NotImplementedError):
        _run(s.summarize_async(_chunks(), mode="themes"))


def test_unknown_mode_raises():
    s = Summarizer()
    with pytest.raises(ValueError):
        _run(s.summarize_async(_chunks(), mode="weird"))


def test_v020_mode_constant_is_short_and_long_only():
    assert _V020_MODES == {"short", "long"}


# ── Empty input ─────────────────────────────────────────────────────────

def test_no_chunks_returns_empty_unverified():
    s = Summarizer()
    out = _run(s.summarize_async([], mode="short"))
    assert out.verified is False
    assert out.text == ""


# ── Skip tier returns empty ─────────────────────────────────────────────

def test_skip_tier_returns_empty():
    s = Summarizer()
    out = _run(s.summarize_async(_tiny_chunks(), mode="short"))
    assert out.tier == "skip"
    assert out.text == ""


# ── Excerpt tier via summarize_async ────────────────────────────────────

def test_excerpt_tier_via_summarize_async():
    s = Summarizer()
    out = _run(s.summarize_async(_short_chunks(), mode="short"))
    assert out.tier == "excerpt"
    assert out.verified is True
    assert "begroting" in out.text


# ── Direct tier: happy path ─────────────────────────────────────────────

def test_direct_tier_happy_path(monkeypatch):
    chunks = _chunks()
    fake_text = (
        "De raad besluit het bestemmingsplan vast te stellen voor 5 miljoen euro. "
        "Wethouder Achbar licht toe dat de woningbouw in 2027 start."
    )

    async def _fake_short(self, _chunks, _tier, _max_tokens):
        return fake_text, _chunks

    monkeypatch.setattr(Summarizer, "_generate_short", _fake_short)

    sents = split_sentences(fake_text)
    assert len(sents) == 2
    score_map = {
        sents[0]: [0.92, 0.10],
        sents[1]: [0.12, 0.88],
    }

    summarizer = Summarizer(
        verifier=SourceSpanVerifier(reranker=FakeReranker(score_map), threshold=0.4),
    )
    out = _run(summarizer.summarize_async(chunks, mode="short"))

    assert isinstance(out, SummaryResult)
    assert out.tier == "direct"
    assert out.verified is True
    assert out.stripped_count == 0
    assert "bestemmingsplan" in out.text
    assert len(out.sources) == 2


# ── Direct tier: hallucination stripped ─────────────────────────────────

def test_direct_tier_strips_hallucination(monkeypatch):
    chunks = _chunks()
    fake_text = (
        "De raad besluit het bestemmingsplan vast te stellen voor 5 miljoen euro. "
        "De Marsmissie vertrekt volgend jaar vanaf Rotterdam."
    )

    async def _fake_short(self, _chunks, _tier, _max_tokens):
        return fake_text, _chunks

    monkeypatch.setattr(Summarizer, "_generate_short", _fake_short)

    sents = split_sentences(fake_text)
    score_map = {
        sents[0]: [0.9, 0.1],
        sents[1]: [0.05, 0.05],
    }
    summarizer = Summarizer(
        verifier=SourceSpanVerifier(reranker=FakeReranker(score_map), threshold=0.4),
    )
    out = _run(summarizer.summarize_async(chunks, mode="short"))

    assert out.stripped_count == 1
    assert out.verified is False
    assert "Marsmissie" not in out.text
    assert "bestemmingsplan" in out.text


# ── enforce_source_spans=False ──────────────────────────────────────────

def test_enforce_off_returns_raw_unverified(monkeypatch):
    async def _fake_short(self, _chunks, _tier, _max_tokens):
        return "Ongecontroleerde tekst.", _chunks

    monkeypatch.setattr(Summarizer, "_generate_short", _fake_short)

    summarizer = Summarizer(verifier=SourceSpanVerifier(reranker=FakeReranker()))
    out = _run(summarizer.summarize_async(
        _chunks(), mode="short", enforce_source_spans=False,
    ))
    assert out.text == "Ongecontroleerde tekst."
    assert out.verified is False


# ── Long mode ───────────────────────────────────────────────────────────

def test_long_mode_delegates_to_synthesizer(monkeypatch):
    chunks = _chunks()
    produced = "De raad besluit 5 miljoen euro te investeren in woningbouw."

    async def _fake_long(self, _question, _chunks):
        return produced

    monkeypatch.setattr(Summarizer, "_generate_long", _fake_long)

    sents = split_sentences(produced)
    score_map = {sents[0]: [0.85, 0.1]}

    summarizer = Summarizer(
        verifier=SourceSpanVerifier(reranker=FakeReranker(score_map), threshold=0.4),
    )
    out = _run(summarizer.summarize_async(
        chunks, mode="long", question="Wat besluit de raad?",
    ))
    assert out.mode == "long"
    assert out.verified is True


# ── Generation failure ──────────────────────────────────────────────────

def test_generation_failure_returns_empty(monkeypatch):
    async def _fake_short(self, _chunks, _tier, _max_tokens):
        return "", _chunks

    monkeypatch.setattr(Summarizer, "_generate_short", _fake_short)

    summarizer = Summarizer(verifier=SourceSpanVerifier(reranker=FakeReranker()))
    out = _run(summarizer.summarize_async(_chunks(), mode="short"))
    assert out.text == ""
    assert out.verified is False


# ── build_prompt + verify_and_build_result (batch API flow) ─────────────

def test_build_prompt_direct_tier():
    chunks = _chunks()
    s = Summarizer()
    prompt, verify_chunks = s.build_prompt(chunks)
    assert "fragmenten" in prompt.lower()
    assert len(verify_chunks) >= 1


def test_build_prompt_raises_for_excerpt():
    chunks = _short_chunks()
    s = Summarizer()
    with pytest.raises(ValueError, match="Excerpt"):
        s.build_prompt(chunks)


def test_build_prompt_raises_for_skip():
    chunks = _tiny_chunks()
    s = Summarizer()
    with pytest.raises(ValueError, match="Skip"):
        s.build_prompt(chunks)


def test_verify_and_build_result():
    chunks = _chunks()
    text = "De raad besluit het bestemmingsplan vast te stellen voor 5 miljoen euro."
    sents = split_sentences(text)
    score_map = {sents[0]: [0.92, 0.10]}

    s = Summarizer(
        verifier=SourceSpanVerifier(reranker=FakeReranker(score_map), threshold=0.4),
    )
    result = s.verify_and_build_result(text, chunks, mode="short", tier="direct")
    assert result.verified is True
    assert result.text == text
    assert result.tier == "direct"
