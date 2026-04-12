"""
Unit tests for services.source_span_verifier — WS6.

These tests are offline-only. They stub the reranker so no Jina API call
happens, and they avoid the nltk data bundle by using the regex splitter
path (assertions only depend on split_sentences being SOMETHING reasonable
for Dutch, which both branches satisfy).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

import pytest

from services.source_span_verifier import (
    MIN_SENTENCE_CHARS,
    SourceSpanVerifier,
    VerifiedSentence,
    VerificationResult,
    _regex_sentence_split,
    split_sentences,
)


# ── Test doubles ────────────────────────────────────────────────────────

@dataclass
class FakeChunk:
    chunk_id: int
    content: str
    document_id: str = "doc-1"
    title: str = ""


class FakeReranker:
    """Deterministic fake: returns `score_map[sentence][chunk_idx]`.

    If a sentence isn't in `score_map`, it returns a uniform low score
    for every chunk (caller gets stripped).
    """

    def __init__(self, score_map=None, default_score: float = 0.05):
        self.score_map = score_map or {}
        self.default_score = default_score
        self.calls: List[tuple] = []

    def score_pairs(self, query: str, documents: List[str]) -> List[float]:
        self.calls.append((query, len(documents)))
        row = self.score_map.get(query)
        if row is None:
            return [self.default_score] * len(documents)
        if len(row) != len(documents):
            raise AssertionError(
                f"score_map row for '{query[:30]}' has {len(row)} entries "
                f"but got {len(documents)} documents"
            )
        return list(row)


# ── Sentence splitter tests ─────────────────────────────────────────────

def test_regex_splitter_basic():
    txt = "De raad stemt voor. Het bedrag is 5 miljoen. Wethouder Achbar kondigt aan."
    out = _regex_sentence_split(txt)
    assert len(out) == 3
    assert out[0].startswith("De raad")
    assert out[2].endswith("aan.")


def test_regex_splitter_handles_abbrev():
    # "bijv." should NOT end a sentence.
    txt = "De voorstellen bijv. de woningbouw en de verkeersplannen worden besproken. Daarna volgt de stemming."
    out = _regex_sentence_split(txt)
    assert len(out) == 2
    assert "bijv." in out[0]


def test_regex_splitter_empty():
    assert _regex_sentence_split("") == []
    assert _regex_sentence_split("   ") == []


def test_split_sentences_public_entry_returns_non_empty_on_dutch():
    # Whether nltk punkt is installed or not, we must get at least one
    # sentence out of a simple input.
    out = split_sentences("Dit is een korte zin. Dit is een tweede zin.")
    assert len(out) >= 1


# ── Verifier tests ──────────────────────────────────────────────────────

def _chunks() -> List[FakeChunk]:
    return [
        FakeChunk(chunk_id=101, content="De raad besluit het bestemmingsplan vast te stellen voor 5 miljoen euro."),
        FakeChunk(chunk_id=102, content="Wethouder Achbar licht toe dat de woningbouw in 2027 start."),
        FakeChunk(chunk_id=103, content="GroenLinks-PvdA dient een amendement in over klimaatdoelen."),
    ]


def test_empty_summary_returns_unverified():
    v = SourceSpanVerifier(reranker=FakeReranker())
    out = v.verify("", _chunks())
    assert out.verified is False
    assert out.total_sentences == 0
    assert out.text == ""


def test_empty_chunks_strips_everything():
    v = SourceSpanVerifier(reranker=FakeReranker())
    out = v.verify("De raad besluit 5 miljoen te investeren. Dit gebeurt in 2027.", [])
    assert out.verified is False
    assert out.stripped_count == out.total_sentences
    # Fallback: never empty — original text preserved.
    assert out.text.startswith("De raad") or "investeren" in out.text


def test_all_sentences_pass_threshold():
    chunks = _chunks()
    sentences_text = (
        "De raad besluit het bestemmingsplan vast te stellen voor 5 miljoen euro. "
        "Wethouder Achbar licht toe dat de woningbouw in 2027 start."
    )
    # Score both sentences high against chunk 0 / chunk 1 respectively.
    sents = split_sentences(sentences_text)
    assert len(sents) >= 2
    score_map = {sents[0]: [0.92, 0.10, 0.05], sents[1]: [0.12, 0.88, 0.04]}
    verifier = SourceSpanVerifier(reranker=FakeReranker(score_map), threshold=0.4)
    out = verifier.verify(sentences_text, chunks)

    assert out.verified is True
    assert out.stripped_count == 0
    assert out.total_sentences >= 2
    # Every kept sentence has a citation from the expected chunks.
    cited = {vs.citation_chunk_id for vs in out.sentences if vs.kept}
    assert 101 in cited
    assert 102 in cited


def test_sentence_below_threshold_gets_stripped():
    chunks = _chunks()
    text = (
        "De raad besluit het bestemmingsplan vast te stellen voor 5 miljoen euro. "
        "De Marsmissie vertrekt volgend jaar vanaf Rotterdam."
    )
    sents = split_sentences(text)
    assert len(sents) == 2
    score_map = {
        sents[0]: [0.9, 0.1, 0.1],
        sents[1]: [0.05, 0.05, 0.05],  # hallucination — below threshold
    }
    verifier = SourceSpanVerifier(reranker=FakeReranker(score_map), threshold=0.4)
    out = verifier.verify(text, chunks)

    # 1 of 2 stripped = 50% > 30% MAX_STRIP_RATIO → verified=False
    assert out.stripped_count == 1
    assert out.verified is False
    # Kept sentence remains in rebuilt text; stripped one is gone.
    assert "bestemmingsplan" in out.text
    assert "Marsmissie" not in out.text


def test_short_sentence_skipped_not_stripped():
    """Sentences shorter than MIN_SENTENCE_CHARS are kept without scoring."""
    chunks = _chunks()
    short = "Ja."  # 3 chars — below MIN_SENTENCE_CHARS
    long_sent = "De raad besluit 5 miljoen te investeren in woningbouw."
    assert len(short) < MIN_SENTENCE_CHARS

    verifier = SourceSpanVerifier(
        reranker=FakeReranker({long_sent: [0.92, 0.1, 0.1]}),
        threshold=0.4,
    )
    out = verifier.verify(f"{short} {long_sent}", chunks)

    # Both kept — the short one is waved through, the long one scored high.
    assert out.stripped_count == 0
    assert out.verified is True


def test_verifier_survives_reranker_exception():
    """If the reranker blows up mid-summary, the affected sentences are stripped
    rather than the whole call crashing."""

    class ExplodingReranker:
        def score_pairs(self, query, documents):
            raise RuntimeError("simulated jina outage")

    chunks = _chunks()
    verifier = SourceSpanVerifier(reranker=ExplodingReranker(), threshold=0.4)
    out = verifier.verify(
        "De raad besluit vijf miljoen euro te investeren. Dit is een tweede zin.",
        chunks,
    )
    # All scorable sentences stripped → fallback text is original summary.
    assert out.verified is False
    assert out.stripped_count >= 1


def test_strip_ratio_at_threshold_is_verified():
    """Exactly 30% strip ratio (the MAX_STRIP_RATIO limit) should still pass."""
    chunks = _chunks()
    # 10 sentences, 3 stripped = 30% ratio → verified=True
    sents = [
        f"Zin nummer {i} over het bestemmingsplan in Rotterdam."
        for i in range(10)
    ]
    text = " ".join(sents)
    parsed = split_sentences(text)
    score_map = {}
    for i, s in enumerate(parsed):
        # Last 3 sentences score below threshold
        if i >= 7:
            score_map[s] = [0.1, 0.1, 0.1]
        else:
            score_map[s] = [0.8, 0.1, 0.1]

    verifier = SourceSpanVerifier(
        reranker=FakeReranker(score_map),
        threshold=0.4,
        max_strip_ratio=0.30,
    )
    out = verifier.verify(text, chunks)
    assert out.stripped_count == 3
    assert out.total_sentences == 10
    assert out.strip_ratio == 0.30
    assert out.verified is True  # at-threshold is inclusive
