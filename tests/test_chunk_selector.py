"""
Unit tests for services.chunk_selector — WS6 diverse chunk selection.

Uses a fake reranker so no Jina API calls are made.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

import pytest

from services.chunk_selector import ChunkSelector, _jaccard, _word_set


@dataclass
class FakeChunk:
    chunk_id: int
    content: str
    document_id: str = "doc-1"
    title: str = ""


class FakeReranker:
    """Returns pre-set scores for the extraction query."""

    def __init__(self, scores: List[float]):
        self.scores = scores

    def score_pairs(self, query: str, documents: List[str]) -> List[float]:
        return self.scores[: len(documents)]


def _make_chunks(n: int, unique: bool = True) -> List[FakeChunk]:
    """Create N chunks. If unique=True, each has distinct content."""
    if unique:
        topics = [
            "woningbouw in Rotterdam-Zuid met 5000 nieuwe appartementen",
            "verkeersveiligheid op de Erasmusbrug met nieuwe maatregelen",
            "klimaatadaptatie en groene daken subsidie programma",
            "begroting 2025 met tekort van 30 miljoen euro",
            "jeugdzorg wachtlijsten en extra financiering GGD",
            "cultureel erfgoed bescherming monumenten binnenstad",
            "energietransitie warmtenet Rozenburg en Pernis",
            "veiligheid coffeeshopbeleid en handhaving",
            "onderwijshuisvesting nieuwbouw basisscholen",
            "sportaccommodaties zwembad Charlois renovatie",
        ]
        return [
            FakeChunk(chunk_id=i, content=topics[i % len(topics)] + f" chunk {i}")
            for i in range(n)
        ]
    # All near-identical content
    return [
        FakeChunk(chunk_id=i, content=f"De raad besluit over woningbouw punt {i}")
        for i in range(n)
    ]


# ── Utility tests ───────────────────────────────────────────────────────

def test_word_set():
    ws = _word_set("De raad besluit")
    assert ws == {"de", "raad", "besluit"}


def test_jaccard_identical():
    a = {"de", "raad", "besluit"}
    assert _jaccard(a, a) == 1.0


def test_jaccard_disjoint():
    a = {"de", "raad"}
    b = {"het", "college"}
    assert _jaccard(a, b) == 0.0


def test_jaccard_partial():
    a = {"de", "raad", "besluit"}
    b = {"de", "raad", "stemt"}
    j = _jaccard(a, b)
    assert 0.0 < j < 1.0
    assert abs(j - 2 / 4) < 1e-6  # 2 shared / 4 union


def test_jaccard_empty():
    assert _jaccard(set(), {"a"}) == 0.0
    assert _jaccard(set(), set()) == 0.0


# ── Selector tests ──────────────────────────────────────────────────────

def test_select_returns_all_if_fewer_than_top_k():
    chunks = _make_chunks(5)
    selector = ChunkSelector(reranker=FakeReranker([0.9, 0.8, 0.7, 0.6, 0.5]))
    result = selector.select(chunks, top_k=25)
    assert len(result) == 5


def test_select_returns_top_k():
    chunks = _make_chunks(40)
    scores = [0.9 - i * 0.01 for i in range(40)]
    selector = ChunkSelector(reranker=FakeReranker(scores))
    result = selector.select(chunks, top_k=10, candidates_k=20)
    assert len(result) == 10


def test_select_includes_highest_scored_first():
    chunks = _make_chunks(30)
    scores = [0.0] * 30
    scores[0] = 0.99  # chunk 0 is the best
    selector = ChunkSelector(reranker=FakeReranker(scores), diversity_lambda=1.0)
    result = selector.select(chunks, top_k=5, candidates_k=15)
    assert result[0].chunk_id == 0


def test_select_diversity_avoids_duplicates():
    """With identical content, MMR should still return top_k items
    (diversity penalty is high but lambda balances it)."""
    chunks = _make_chunks(30, unique=False)
    scores = [0.9 - i * 0.01 for i in range(30)]
    selector = ChunkSelector(reranker=FakeReranker(scores), diversity_lambda=0.6)
    result = selector.select(chunks, top_k=10, candidates_k=20)
    assert len(result) == 10


def test_select_diversity_promotes_varied_content():
    """With diversity_lambda < 1, a lower-scored but unique chunk should
    be preferred over a higher-scored but redundant one."""
    chunks = [
        FakeChunk(chunk_id=0, content="woningbouw appartementen Rotterdam-Zuid plan"),
        FakeChunk(chunk_id=1, content="woningbouw appartementen Rotterdam-Zuid uitvoering"),
        FakeChunk(chunk_id=2, content="verkeersveiligheid Erasmusbrug maatregelen nieuw"),
    ]
    # Chunk 0 and 1 are near-identical; chunk 2 is different.
    # Scores: 0=0.9, 1=0.85, 2=0.8
    scores = [0.9, 0.85, 0.80]
    selector = ChunkSelector(reranker=FakeReranker(scores), diversity_lambda=0.5)
    result = selector.select(chunks, top_k=2, candidates_k=3)
    selected_ids = {c.chunk_id for c in result}
    # With diversity, chunk 2 (unique topic) should beat chunk 1 (redundant)
    assert 0 in selected_ids  # highest score always first
    assert 2 in selected_ids  # diverse beats redundant


def test_select_reranker_failure_falls_back():
    """If the reranker blows up, return first top_k chunks instead of crashing."""

    class ExplodingReranker:
        def score_pairs(self, query, documents):
            raise RuntimeError("boom")

    chunks = _make_chunks(40)
    selector = ChunkSelector(reranker=ExplodingReranker())
    result = selector.select(chunks, top_k=10)
    assert len(result) == 10
    assert result[0].chunk_id == 0  # first chunks as fallback
