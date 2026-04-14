"""
Unit tests for WS10 MCP resilience changes (2026-04-14):
  1. Cross-stream single rerank in RAGService.retrieve_parallel_context
  2. TTL cache on JinaAPIReranker.score_pairs
  3. Token-bucket throttle on Jina TPM

No real Jina/Qdrant/Postgres calls — everything mocked.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import List
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# 1. Cross-stream single rerank
# ---------------------------------------------------------------------------

class _FakeChunk:
    def __init__(self, chunk_id, content):
        self.chunk_id = chunk_id
        self.document_id = f"doc-{chunk_id}"
        self.title = ""
        self.content = content
        self.similarity_score = 0.0
        self.questions = []
        self.child_id = None
        self.stream_type = None
        self.start_date = None


def test_parallel_context_does_single_rerank(monkeypatch):
    """retrieve_parallel_context must call score_pairs ONCE on the deduplicated
    union, not once per stream (was 5x → 1x reduction)."""
    from services import rag_service

    # Each stream returns 2 chunks, with overlap between streams to exercise dedup
    stream_outputs = {
        "vision": [_FakeChunk(1, "v1"), _FakeChunk(2, "shared")],
        "financial": [_FakeChunk(3, "f1"), _FakeChunk(2, "shared")],
        "debate": [_FakeChunk(4, "d1"), _FakeChunk(5, "d2")],
        "fact": [_FakeChunk(6, "ft1")],
    }

    async def fake_async_retrieve(self, query, embedding, k, stream_type, *args, **kwargs):
        chunks = stream_outputs.get(stream_type, [])
        for c in chunks:
            c.stream_type = stream_type
        return chunks

    async def fake_async_retrieve_graph(self, *args, **kwargs):
        return []

    rerank_calls = []

    class CountingReranker:
        def score_pairs(self, query, documents):
            rerank_calls.append((query, list(documents)))
            return [1.0 / (i + 1) for i in range(len(documents))]

    monkeypatch.setattr(rag_service.RAGService, "_async_retrieve", fake_async_retrieve)
    monkeypatch.setattr(rag_service.RAGService, "_async_retrieve_graph", fake_async_retrieve_graph)
    monkeypatch.setattr(rag_service, "_reranker", CountingReranker())
    monkeypatch.setattr(rag_service.RAGService, "_ensure_resources_initialized", lambda self: None)

    svc = rag_service.RAGService.__new__(rag_service.RAGService)
    svc.embedder = None

    chunks = asyncio.run(svc.retrieve_parallel_context(
        query_text="jeugdzorg budget",
        query_embedding=[0.0] * 8,
        fast_mode=False,
    ))

    # Exactly one rerank call, with the ORIGINAL user query
    assert len(rerank_calls) == 1, f"expected 1 rerank call, got {len(rerank_calls)}"
    used_query, used_docs = rerank_calls[0]
    assert used_query == "jeugdzorg budget"
    # 6 unique chunks (id=2 was shared between vision and financial)
    assert len(used_docs) == 6
    assert chunks[0].similarity_score >= chunks[-1].similarity_score  # sorted by score desc


def test_parallel_context_skips_rerank_in_fast_mode(monkeypatch):
    from services import rag_service

    async def fake_async_retrieve(self, query, embedding, k, stream_type, *args, **kwargs):
        return [_FakeChunk(1, "x")]

    async def fake_async_retrieve_graph(self, *args, **kwargs):
        return []

    calls = []

    class CountingReranker:
        def score_pairs(self, query, documents):
            calls.append(1)
            return [0.5]

    monkeypatch.setattr(rag_service.RAGService, "_async_retrieve", fake_async_retrieve)
    monkeypatch.setattr(rag_service.RAGService, "_async_retrieve_graph", fake_async_retrieve_graph)
    monkeypatch.setattr(rag_service, "_reranker", CountingReranker())
    monkeypatch.setattr(rag_service.RAGService, "_ensure_resources_initialized", lambda self: None)

    svc = rag_service.RAGService.__new__(rag_service.RAGService)
    svc.embedder = None

    asyncio.run(svc.retrieve_parallel_context(
        query_text="q", query_embedding=[0.0], fast_mode=True,
    ))
    assert calls == [], "fast_mode must skip rerank entirely"


# ---------------------------------------------------------------------------
# 2. TTL cache
# ---------------------------------------------------------------------------

def test_cache_hit_returns_same_scores():
    from services import reranker
    reranker._RERANK_CACHE.clear()
    key = reranker._rerank_cache_key("q", ["a", "b"])
    reranker._rerank_cache_set(key, [0.9, 0.5])
    assert reranker._rerank_cache_get(key) == [0.9, 0.5]


def test_cache_key_order_sensitive():
    from services import reranker
    k1 = reranker._rerank_cache_key("q", ["a", "b"])
    k2 = reranker._rerank_cache_key("q", ["b", "a"])
    assert k1 != k2, "different doc order must produce different cache key"


def test_cache_ttl_expiry(monkeypatch):
    from services import reranker
    reranker._RERANK_CACHE.clear()
    monkeypatch.setattr(reranker, "_RERANK_CACHE_TTL", 0)  # immediate expiry
    key = reranker._rerank_cache_key("q", ["a"])
    reranker._rerank_cache_set(key, [0.5])
    time.sleep(0.01)
    assert reranker._rerank_cache_get(key) is None


def test_cache_capacity_eviction(monkeypatch):
    from services import reranker
    reranker._RERANK_CACHE.clear()
    monkeypatch.setattr(reranker, "_RERANK_CACHE_MAX", 4)
    for i in range(10):
        k = reranker._rerank_cache_key(f"q{i}", ["d"])
        reranker._rerank_cache_set(k, [float(i)])
    assert len(reranker._RERANK_CACHE) <= 4


def test_score_pairs_uses_cache(monkeypatch):
    """Second call with same (query, docs) must skip the API."""
    from services import reranker
    reranker._RERANK_CACHE.clear()

    api_hits = []

    def fake_api_call(self, query, documents):
        api_hits.append(1)
        return [0.42] * len(documents)

    monkeypatch.setattr(reranker.JinaAPIReranker, "_api_call", fake_api_call)

    r = reranker.JinaAPIReranker(api_key="fake")
    docs = ["doc one", "doc two"]
    s1 = r.score_pairs("query", docs)
    s2 = r.score_pairs("query", docs)
    assert s1 == s2 == [0.42, 0.42]
    assert len(api_hits) == 1, f"second call should hit cache, got {len(api_hits)} api calls"


# ---------------------------------------------------------------------------
# 3. Token-bucket
# ---------------------------------------------------------------------------

def test_bucket_allows_under_budget():
    from services.reranker import _TokenBucket
    b = _TokenBucket(budget_per_window=1000, window_sec=10.0)
    t0 = time.time()
    b.acquire(400)
    b.acquire(400)
    assert time.time() - t0 < 0.1, "under-budget calls must not block"


def test_bucket_blocks_until_window_slides():
    from services.reranker import _TokenBucket
    b = _TokenBucket(budget_per_window=1000, window_sec=1.0)
    b.acquire(800)
    t0 = time.time()
    b.acquire(500)  # 800+500=1300 > 1000 → must wait ~1s for first entry to age out
    elapsed = time.time() - t0
    assert 0.5 < elapsed < 2.0, f"expected ~1s wait, got {elapsed:.2f}s"


def test_bucket_oversized_passthrough():
    """A single request bigger than the whole budget must NOT block forever."""
    from services.reranker import _TokenBucket
    b = _TokenBucket(budget_per_window=100, window_sec=10.0)
    t0 = time.time()
    b.acquire(500)
    assert time.time() - t0 < 0.1


def test_bucket_thread_safety():
    """20 threads hammering acquire() must respect total budget within window."""
    from services.reranker import _TokenBucket
    b = _TokenBucket(budget_per_window=1000, window_sec=2.0)

    def worker():
        b.acquire(100)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - t0
    # 20 × 100 = 2000 tokens; budget 1000/2s → must take >=2s for 2nd half to fit
    assert elapsed >= 1.5, f"expected throttling under contention, finished in {elapsed:.2f}s"


def test_estimator_basic():
    from services.reranker import _estimate_tokens
    assert _estimate_tokens("", []) == 1
    assert _estimate_tokens("hi", ["x" * 4000]) > 900
