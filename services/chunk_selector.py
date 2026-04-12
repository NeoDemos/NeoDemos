"""
Diverse chunk selection for long-document summarization — WS6.

Given a document's full chunk set and a Jina v3 reranker, selects the
top-K most *relevant AND diverse* chunks for summarization. This is the
"extract" step of the Extract-then-Abstract tier: the LLM only sees the
selected chunks, and the source-span verifier checks against exactly
those same chunks — clean verification scope.

Uses a greedy MMR-like algorithm (Maximal Marginal Relevance) with
word-jaccard redundancy penalty instead of embedding cosine similarity.
This avoids fetching 3072-dim vectors from Postgres/Qdrant and is fast
enough for the nightly batch (< 100ms per document even for 3,331 chunks).

Public surface:

    selector = ChunkSelector(reranker=create_reranker())
    selected = selector.select(chunks, top_k=25)
    # selected: list of chunks, ordered by descending relevance

Usage in the summarizer:
    - Tier "extract" (docs > 30K effective chars): select 25 diverse chunks
    - Tier "direct" (docs ≤ 30K chars): no selection needed (all chunks fit)
    - Tier "excerpt" (docs < 3K chars): no LLM call at all
"""

from __future__ import annotations

import logging
import threading
from typing import Any, List, Optional, Protocol, Set

logger = logging.getLogger(__name__)

# Limit concurrent Jina reranker calls to avoid 429 token-rate-limit errors
# (Jina paid tier: 2M tokens/minute). Extract-tier docs can be very large;
# more than 2-3 simultaneous calls burst past the limit.
_JINA_SEMAPHORE = threading.Semaphore(2)

# The generic document-summarization query used to rank chunks by
# relevance-to-summary. Deliberately broad so it works across all
# Rotterdam document types (notulen, begrotingen, raadsvoorstellen,
# moties, jaarstukken, etc.).
DEFAULT_EXTRACTION_QUERY = (
    "Wat zijn de belangrijkste besluiten, voorstellen, bedragen, "
    "betrokken partijen en conclusies in dit document?"
)


class RerankerProtocol(Protocol):
    def score_pairs(self, query: str, documents: List[str]) -> List[float]: ...


def _word_set(text: str) -> Set[str]:
    """Lowercase word set for jaccard redundancy penalty."""
    return set(text.lower().split())


def _jaccard(a: Set[str], b: Set[str]) -> float:
    """Jaccard similarity between two word sets. 0 = no overlap, 1 = identical."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class ChunkSelector:
    """
    Reranker-scored diverse chunk selection for long documents.

    The algorithm:
    1. Score every chunk against the extraction query via Jina v3.
    2. Take the top `candidates_k` by reranker score (default 50).
    3. Greedily select `top_k` chunks using MMR: each iteration picks the
       candidate that maximizes `λ * relevance - (1-λ) * max_redundancy`,
       where redundancy is measured as word-jaccard overlap with the
       already-selected set.

    This ensures the selected chunks cover the document's breadth (not
    just the single most-discussed topic) while still prioritizing the
    chunks most relevant to "what is this document about?"
    """

    def __init__(
        self,
        reranker: Optional[RerankerProtocol] = None,
        extraction_query: str = DEFAULT_EXTRACTION_QUERY,
        diversity_lambda: float = 0.6,
    ):
        self._reranker = reranker
        self.extraction_query = extraction_query
        self.diversity_lambda = diversity_lambda

    def _get_reranker(self) -> RerankerProtocol:
        if self._reranker is None:
            from services.reranker import create_reranker
            self._reranker = create_reranker()
        return self._reranker

    def select(
        self,
        chunks: List[Any],
        top_k: int = 25,
        candidates_k: int = 50,
    ) -> List[Any]:
        """
        Select the top-K most relevant AND diverse chunks.

        Returns chunks in descending relevance order. If `len(chunks) <= top_k`,
        returns all chunks unchanged (no selection needed).
        """
        if len(chunks) <= top_k:
            return list(chunks)

        chunk_texts = [getattr(c, "content", "") or "" for c in chunks]
        non_empty = [(i, t) for i, t in enumerate(chunk_texts) if t.strip()]
        if not non_empty:
            return list(chunks[:top_k])

        # Cap chunks sent to the reranker to stay under Jina's 2M token/min
        # rate limit. For very large docs (>100 chunks), stride-sample to get
        # a representative 100-chunk subset before scoring.
        MAX_RERANK_CHUNKS = 100
        if len(non_empty) > MAX_RERANK_CHUNKS:
            step = len(non_empty) / MAX_RERANK_CHUNKS
            non_empty = [non_empty[int(i * step)] for i in range(MAX_RERANK_CHUNKS)]

        try:
            reranker = self._get_reranker()
            with _JINA_SEMAPHORE:
                scores = reranker.score_pairs(
                    self.extraction_query,
                    [t for _, t in non_empty],
                )
        except Exception as e:
            logger.warning(f"chunk_selector: reranker failed ({e}); falling back to first {top_k} chunks")
            return list(chunks[:top_k])

        # Map scores back to original chunk indices.
        scored = [
            (non_empty[j][0], scores[j])
            for j in range(len(non_empty))
        ]
        scored.sort(key=lambda x: -x[1])

        # Take top candidates_k by relevance.
        candidates = scored[:candidates_k]

        if len(candidates) <= top_k:
            return [chunks[idx] for idx, _ in candidates]

        # Greedy MMR selection.
        return self._mmr_select(chunks, chunk_texts, candidates, top_k)

    def _mmr_select(
        self,
        chunks: List[Any],
        chunk_texts: List[str],
        candidates: List[tuple[int, float]],
        top_k: int,
    ) -> List[Any]:
        """Greedy MMR: pick the most relevant candidate that is least
        redundant with already-selected chunks."""
        lam = self.diversity_lambda

        # Precompute word sets for all candidate chunks.
        word_sets = {idx: _word_set(chunk_texts[idx]) for idx, _ in candidates}

        selected_indices: List[int] = []
        selected_word_sets: List[Set[str]] = []
        remaining = list(candidates)

        # Seed with the highest-scoring chunk.
        first_idx, _ = remaining.pop(0)
        selected_indices.append(first_idx)
        selected_word_sets.append(word_sets[first_idx])

        while len(selected_indices) < top_k and remaining:
            best_mmr = -float("inf")
            best_pos = 0

            for pos, (idx, rel_score) in enumerate(remaining):
                ws = word_sets[idx]
                # Max jaccard overlap with any already-selected chunk.
                max_overlap = max(
                    _jaccard(ws, sel_ws) for sel_ws in selected_word_sets
                )
                mmr = lam * rel_score - (1.0 - lam) * max_overlap
                if mmr > best_mmr:
                    best_mmr = mmr
                    best_pos = pos

            chosen_idx, _ = remaining.pop(best_pos)
            selected_indices.append(chosen_idx)
            selected_word_sets.append(word_sets[chosen_idx])

        return [chunks[idx] for idx in selected_indices]
