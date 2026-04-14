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

import hashlib
import os
import logging
import threading
import time as _time
from collections import deque
from typing import List, Optional, Protocol

logger = logging.getLogger(__name__)

# Module-level TTL cache for Jina rerank results.
# Key: sha256(query + \x00 + doc_1 + \x00 + doc_2 + ...) — order-preserving so
# scores map back to input positions correctly.
# Hits are common because: (a) WS6 batch jobs replay similar queries, (b) MCP
# users ask overlapping questions within minutes, (c) `vergelijk_partijen`
# repeats the same {topic} + {party} pattern across N parties.
_RERANK_CACHE: dict = {}
_RERANK_CACHE_LOCK = threading.Lock()
_RERANK_CACHE_TTL = int(os.getenv("JINA_CACHE_TTL_SEC", "300"))
_RERANK_CACHE_MAX = int(os.getenv("JINA_CACHE_MAX_ENTRIES", "1024"))


def _rerank_cache_key(query: str, documents: List[str]) -> str:
    h = hashlib.sha256()
    h.update(query.encode("utf-8"))
    h.update(b"\x00")
    for d in documents:
        h.update(d.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _rerank_cache_get(key: str) -> Optional[List[float]]:
    with _RERANK_CACHE_LOCK:
        entry = _RERANK_CACHE.get(key)
        if entry is None:
            return None
        scores, ts = entry
        if _time.time() - ts > _RERANK_CACHE_TTL:
            _RERANK_CACHE.pop(key, None)
            return None
        return list(scores)


def _rerank_cache_set(key: str, scores: List[float]) -> None:
    with _RERANK_CACHE_LOCK:
        if len(_RERANK_CACHE) >= _RERANK_CACHE_MAX:
            now = _time.time()
            for k in [k for k, (_, ts) in _RERANK_CACHE.items() if now - ts > _RERANK_CACHE_TTL]:
                _RERANK_CACHE.pop(k, None)
            if len(_RERANK_CACHE) >= _RERANK_CACHE_MAX:
                # Drop oldest 10% as a hard cap fallback
                victims = sorted(_RERANK_CACHE.items(), key=lambda kv: kv[1][1])[: max(1, _RERANK_CACHE_MAX // 10)]
                for k, _ in victims:
                    _RERANK_CACHE.pop(k, None)
        _RERANK_CACHE[key] = (list(scores), _time.time())


# ---------------------------------------------------------------------------
# Token-bucket throttle for Jina TPM (paid tier ceiling: 2M tokens/min).
# Sliding 60s window — preventive, self-regulating, no fixed concurrency cap.
# Default budget = 90% of plafond to leave headroom for token-estimate error
# (Dutch text via Jina BPE is roughly len/4 ± 20%).
# ---------------------------------------------------------------------------

_JINA_TPM_BUDGET = int(os.getenv("JINA_TPM_BUDGET", "1800000"))
_JINA_WINDOW_SEC = 60.0


class _TokenBucket:
    """Sliding-window token-rate limiter. Thread-safe."""

    def __init__(self, budget_per_window: int, window_sec: float = _JINA_WINDOW_SEC) -> None:
        self.budget = budget_per_window
        self.window_sec = window_sec
        self._entries: deque = deque()  # (timestamp, tokens)
        self._spent = 0
        self._lock = threading.Lock()

    def acquire(self, tokens: int) -> None:
        """Block until `tokens` fits within the rolling window."""
        if tokens <= 0:
            return
        # If a single request exceeds the whole budget, log and let it pass —
        # blocking forever would be worse than a 429.
        if tokens > self.budget:
            logger.warning(
                "[reranker] single call estimate %d tokens exceeds budget %d — sending anyway",
                tokens, self.budget,
            )
            with self._lock:
                self._entries.append((_time.time(), tokens))
                self._spent += tokens
            return

        warned = False
        while True:
            with self._lock:
                now = _time.time()
                while self._entries and now - self._entries[0][0] > self.window_sec:
                    _, t = self._entries.popleft()
                    self._spent -= t
                if self._spent + tokens <= self.budget:
                    self._entries.append((now, tokens))
                    self._spent += tokens
                    return
                wait = self.window_sec - (now - self._entries[0][0]) + 0.05
            if not warned:
                logger.info("[reranker] TPM budget reached, throttling ~%.2fs", wait)
                warned = True
            _time.sleep(min(max(wait, 0.05), 1.0))


_jina_token_bucket = _TokenBucket(_JINA_TPM_BUDGET)


def _estimate_tokens(query: str, documents: List[str]) -> int:
    """Conservative BPE estimate for Dutch text: ~4 chars/token."""
    total_chars = len(query) + sum(len(d) for d in documents)
    return max(1, total_chars // 4)


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
        logger.info("Loading Jina Reranker v3 locally on %s (%s)...", device, self.MODEL_ID)

        # float16 halves memory (~1.2GB vs ~2.4GB) with negligible quality loss
        _local_model = AutoModel.from_pretrained(
            self.MODEL_ID,
            dtype="float16",
            trust_remote_code=True,
        )
        _local_model = _local_model.to(device)
        _local_model.eval()
        logger.info("Jina Reranker v3 loaded.")

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
        """Score query-document pairs via Jina API, batching by payload size.

        Wrapped in a TTL cache (see _RERANK_CACHE) — identical (query, docs)
        calls within the TTL window skip the API entirely.
        """
        if not documents:
            return []

        cache_key = _rerank_cache_key(query, documents)
        cached = _rerank_cache_get(cache_key)
        if cached is not None and len(cached) == len(documents):
            return cached

        batches = self._make_batches(documents)

        if len(batches) == 1:
            scores = self._api_call_with_retry(query, batches[0][1])
            _rerank_cache_set(cache_key, scores)
            return scores

        # Multiple batches: 0.2s gap (paid tier: 2M TPM, 500 RPM — plenty of headroom)
        all_scores: dict = {}
        for i, (indices, batch_docs) in enumerate(batches):
            if i > 0:
                _time.sleep(0.2)
            batch_scores = self._api_call_with_retry(query, batch_docs)
            all_scores.update(zip(indices, batch_scores))
        scores = [all_scores.get(i, 0.0) for i in range(len(documents))]
        _rerank_cache_set(cache_key, scores)
        return scores

    def _api_call_with_retry(self, query: str, documents: List[str]) -> List[float]:
        """Retry with exponential backoff on 429 rate limit — never fall back to local.

        Each attempt first acquires from the module-level TPM token bucket so we
        preventively stay under Jina's 2M tokens/min ceiling. Retries also
        re-acquire because failed calls still count toward Jina's TPM.
        """
        import requests

        estimated = _estimate_tokens(query, documents)

        for attempt in range(4):
            _jina_token_bucket.acquire(estimated)
            try:
                return self._api_call(query, documents)
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 429:
                    wait = 2 ** attempt  # 1s, 2s, 4s, 8s
                    logger.info("[reranker] Rate limited, retrying in %ss...", wait)
                    _time.sleep(wait)
                else:
                    raise
        _jina_token_bucket.acquire(estimated)
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
