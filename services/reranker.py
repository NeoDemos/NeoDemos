"""
Reranker service with two backends:
  - Local: Jina Reranker v3 via transformers (Apple Silicon / GPU / CPU)
  - API:   Jina Rerank API (cloud deployment)

Usage:
    reranker = create_reranker()  # auto-detects: API key present → API, else local
    scores = reranker.score_pairs(query, documents)
    # scores: List[float], one per document, higher = more relevant
"""

from __future__ import annotations

import os
import logging
from typing import List, Optional, Protocol

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Protocol: any reranker must implement score_pairs
# ---------------------------------------------------------------------------

class Reranker(Protocol):
    def score_pairs(self, query: str, documents: List[str]) -> List[float]: ...


# ---------------------------------------------------------------------------
# Backend 1: Local Jina Reranker v3 via transformers
# ---------------------------------------------------------------------------

_local_model = None
_local_lock = None


class LocalJinaReranker:
    """Jina Reranker v3 running locally via transformers."""

    MODEL_ID = "jinaai/jina-reranker-v3"

    def __init__(self):
        global _local_model, _local_lock
        import threading
        if _local_lock is None:
            _local_lock = threading.Lock()

        if _local_model is None:
            with _local_lock:
                if _local_model is None:
                    self._load_model()
        self.model = _local_model

    def _load_model(self):
        global _local_model
        import torch
        from transformers import AutoModel

        # Use Apple GPU (MPS) if available, else CPU
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        logger.info(f"Loading Jina Reranker v3 locally on {device} ({self.MODEL_ID})...")
        print(f"Loading Jina Reranker v3 locally on {device} ({self.MODEL_ID})...")

        # float16 halves memory (~1.2GB vs ~2.4GB) with negligible quality loss
        _local_model = AutoModel.from_pretrained(
            self.MODEL_ID,
            dtype="float16",
            trust_remote_code=True,
        )
        _local_model = _local_model.to(device)
        _local_model.eval()
        logger.info("Jina Reranker v3 loaded.")
        print("Jina Reranker v3 loaded.")

    # Small batches + explicit MPS cache flush prevent memory accumulation.
    # MPS doesn't release intermediate attention buffers between calls unless asked.
    BATCH_SIZE = 10

    def score_pairs(self, query: str, documents: List[str]) -> List[float]:
        """Score query-document pairs in batches, flushing MPS cache after each."""
        if not documents:
            return []

        if len(documents) <= self.BATCH_SIZE:
            scores = self._score_batch(query, documents, offset=0)
            self._flush_mps()
            return [scores.get(i, 0.0) for i in range(len(documents))]

        all_scores = {}
        for start in range(0, len(documents), self.BATCH_SIZE):
            batch = documents[start : start + self.BATCH_SIZE]
            batch_scores = self._score_batch(query, batch, offset=start)
            all_scores.update(batch_scores)
            self._flush_mps()

        return [all_scores.get(i, 0.0) for i in range(len(documents))]

    def _score_batch(self, query: str, documents: List[str], offset: int) -> dict:
        """Score one batch, return {original_index: score}."""
        results = self.model.rerank(query, documents, top_n=len(documents))
        return {offset + r["index"]: float(r["relevance_score"]) for r in results}

    @staticmethod
    def _flush_mps():
        """Release MPS buffer pool to prevent memory accumulation across batches."""
        try:
            import torch
            if torch.backends.mps.is_available():
                torch.mps.empty_cache()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Backend 2: Jina Rerank API (for cloud deployment)
# ---------------------------------------------------------------------------

class JinaAPIReranker:
    """Jina Rerank API — drop-in replacement for cloud deployment."""

    API_URL = "https://api.jina.ai/v1/rerank"

    def __init__(self, api_key: str, model: str = "jina-reranker-v3"):
        self.api_key = api_key
        self.model = model

    # Keep batches under ~80KB of text to stay well within Jina API limits.
    # Never truncate documents — the relevant sentence might be anywhere in the chunk.
    # Jina v3 is a cross-encoder: each doc is scored independently against the query,
    # so scores are directly comparable across batches.
    # Paid tier: 500 RPM, 2M TPM — generous headroom.
    MAX_BATCH_CHARS = 80_000

    def score_pairs(self, query: str, documents: List[str]) -> List[float]:
        """Score query-document pairs via Jina API, batching by payload size."""
        import time as _time

        if not documents:
            return []

        batches = self._make_batches(documents)

        if len(batches) == 1:
            return self._api_call_with_retry(query, batches[0][1])

        # Multiple batches: 0.2s gap (paid tier: 2M TPM, 500 RPM — plenty of headroom)
        all_scores: dict = {}
        for i, (indices, batch_docs) in enumerate(batches):
            if i > 0:
                _time.sleep(0.2)
            batch_scores = self._api_call_with_retry(query, batch_docs)
            all_scores.update(zip(indices, batch_scores))
        return [all_scores.get(i, 0.0) for i in range(len(documents))]

    def _api_call_with_retry(self, query: str, documents: List[str]) -> List[float]:
        """Retry with exponential backoff on 429 rate limit — never fall back to local."""
        import time as _time
        import requests

        for attempt in range(4):
            try:
                return self._api_call(query, documents)
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 429:
                    wait = 2 ** attempt  # 1s, 2s, 4s, 8s
                    print(f"  [reranker] Rate limited, retrying in {wait}s...")
                    _time.sleep(wait)
                else:
                    raise
        return self._api_call(query, documents)  # final attempt, raises if still failing

    def _make_batches(self, documents: List[str]):
        """
        Split documents into batches so each batch stays under MAX_BATCH_CHARS.
        Returns list of (original_indices, docs) tuples.
        """
        batches = []
        current_indices: List[int] = []
        current_docs: List[str] = []
        current_chars = 0

        for i, doc in enumerate(documents):
            doc_chars = len(doc)
            # If a single doc exceeds the limit, it gets its own batch
            if current_chars + doc_chars > self.MAX_BATCH_CHARS and current_docs:
                batches.append((current_indices, current_docs))
                current_indices = []
                current_docs = []
                current_chars = 0
            current_indices.append(i)
            current_docs.append(doc)
            current_chars += doc_chars

        if current_docs:
            batches.append((current_indices, current_docs))

        return batches

    def _api_call(self, query: str, documents: List[str]) -> List[float]:
        """Single API call for a batch of documents."""
        import requests

        response = requests.post(
            self.API_URL,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            json={
                "model": self.model,
                "query": query,
                "documents": documents,
                "top_n": len(documents),
            },
            timeout=60,
        )

        if not response.ok:
            # Include response body in error for debugging
            try:
                error_body = response.json()
            except Exception:
                error_body = response.text[:500]
            raise requests.HTTPError(
                f"{response.status_code} {response.reason} — {error_body}",
                response=response,
            )

        data = response.json()
        score_by_index = {r["index"]: r["relevance_score"] for r in data["results"]}
        return [float(score_by_index.get(i, 0.0)) for i in range(len(documents))]


# ---------------------------------------------------------------------------
# Factory: API-only (paid tier: 500 RPM, 2M TPM)
# ---------------------------------------------------------------------------

def create_reranker(
    api_key: Optional[str] = None,
) -> Reranker:
    """
    Create a Jina Reranker instance (API-only, no local fallback).

    The local Jina model class is kept in the codebase for reference but is
    not used in production or eval. All reranking goes through the Jina API.
    """
    api_key = api_key or os.getenv("JINA_API_KEY")

    if not api_key:
        raise ValueError(
            "JINA_API_KEY required. Set it in .env or pass api_key parameter. "
            "Local Jina fallback has been removed — API-only."
        )

    logger.info("Using Jina Rerank API (paid tier, no local fallback).")
    return JinaAPIReranker(api_key=api_key)
