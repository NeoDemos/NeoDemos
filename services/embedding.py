"""
Embedding service with two backends:
  - Local: Qwen3-Embedding-8B via MLX (Apple Silicon, ~5GB RAM)
  - API:   Qwen3-Embedding-8B via Nebius Token Factory (zero local RAM)

Usage:
    embedder = create_embedder()  # auto-detects: NEBIUS_API_KEY → API, else local
    vector = embedder.embed(text)
    # vector: List[float], 4096 dimensions
"""

from __future__ import annotations

import os
import logging
import hashlib
from collections import OrderedDict
from typing import List, Optional, Protocol

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical embedding model — PINNED.
# Do NOT change without re-embedding all vectors in notulen_chunks.
# A different model produces a different vector space; stored vectors become invalid.
# To upgrade: build a new Qdrant collection, re-embed, benchmark, then cut over.
# ---------------------------------------------------------------------------
EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-8B"
EMBEDDING_DIM = 4096
QDRANT_COLLECTION = "notulen_chunks"

# In-memory LRU cache shared across backends
_EMBED_CACHE: OrderedDict = OrderedDict()
_EMBED_CACHE_MAX = 512


def _cache_key(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


class Embedder(Protocol):
    def embed(self, text: str) -> Optional[List[float]]: ...
    def is_available(self) -> bool: ...


# ---------------------------------------------------------------------------
# Backend 1: Nebius API (Qwen3-Embedding-8B, zero local RAM)
# ---------------------------------------------------------------------------

class NebiusEmbedder:
    """Qwen3-Embedding-8B via Nebius Token Factory OpenAI-compatible API."""

    def __init__(self, api_key: str):
        from openai import OpenAI
        self.client = OpenAI(
            base_url="https://api.tokenfactory.nebius.com/v1/",
            api_key=api_key,
        )
        self.model = EMBEDDING_MODEL

    def embed(self, text: str) -> Optional[List[float]]:
        key = _cache_key(text)
        if key in _EMBED_CACHE:
            _EMBED_CACHE.move_to_end(key)
            return list(_EMBED_CACHE[key])

        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=text,
            )
            embedding = response.data[0].embedding

            _EMBED_CACHE[key] = tuple(embedding)
            if len(_EMBED_CACHE) > _EMBED_CACHE_MAX:
                _EMBED_CACHE.popitem(last=False)

            return embedding
        except Exception as e:
            logger.error(f"Nebius embedding failed: {e}")
            print(f"[embedding] Nebius API error: {e}")
            return None

    def embed_batch(self, texts: List[str], batch_size: int = 64) -> List[Optional[List[float]]]:
        """Embed multiple texts in batches via the Nebius API.

        Returns a list of embeddings (or None for failures) aligned with input.
        """
        results: List[Optional[List[float]]] = [None] * len(texts)
        uncached_indices = []
        uncached_texts = []

        for i, text in enumerate(texts):
            key = _cache_key(text)
            if key in _EMBED_CACHE:
                _EMBED_CACHE.move_to_end(key)
                results[i] = list(_EMBED_CACHE[key])
            else:
                uncached_indices.append(i)
                uncached_texts.append(text)

        # Process uncached in batches
        for b_start in range(0, len(uncached_texts), batch_size):
            batch = uncached_texts[b_start : b_start + batch_size]
            try:
                response = self.client.embeddings.create(
                    model=self.model,
                    input=batch,
                )
                for j, item in enumerate(response.data):
                    idx = uncached_indices[b_start + j]
                    emb = item.embedding
                    results[idx] = emb
                    _EMBED_CACHE[_cache_key(batch[j])] = tuple(emb)
                    if len(_EMBED_CACHE) > _EMBED_CACHE_MAX:
                        _EMBED_CACHE.popitem(last=False)
            except Exception as e:
                logger.error(f"Nebius batch embedding failed: {e}")

        return results

    def is_available(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# Backend 2: Local MLX (existing LocalAIService wrapper)
# ---------------------------------------------------------------------------

class LocalEmbedder:
    """Wraps existing LocalAIService for backward compatibility."""

    def __init__(self):
        from services.local_ai_service import LocalAIService
        self._ai = LocalAIService(skip_llm=True)

    def embed(self, text: str) -> Optional[List[float]]:
        return self._ai.generate_embedding(text)

    def is_available(self) -> bool:
        return self._ai.is_available()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_embedder(
    backend: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Embedder:
    """
    Create an embedder instance.

    Args:
        backend: "api" or "local". If None, auto-detects:
                 NEBIUS_API_KEY set → API, else → local.
        api_key: Nebius API key (only for API backend).
    """
    api_key = api_key or os.getenv("NEBIUS_API_KEY")

    if backend is None:
        backend = "api" if api_key else "local"

    if backend == "api":
        if not api_key:
            raise ValueError("NEBIUS_API_KEY required for API backend.")
        print(f"[embedding] Using Nebius API ({EMBEDDING_MODEL}, {EMBEDDING_DIM}D)")
        return NebiusEmbedder(api_key=api_key)
    else:
        print(f"[embedding] Using local MLX ({EMBEDDING_MODEL}, {EMBEDDING_DIM}D)")
        return LocalEmbedder()
