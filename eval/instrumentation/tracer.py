"""Data models for capturing pipeline traces at each retrieval stage."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class StageResult:
    """A single chunk result at a specific pipeline stage."""
    chunk_id: int
    content_preview: str
    score: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_retrieved_chunk(cls, chunk, score_override: Optional[float] = None) -> "StageResult":
        return cls(
            chunk_id=chunk.chunk_id,
            content_preview=chunk.content[:200] if chunk.content else "",
            score=score_override if score_override is not None else getattr(chunk, "similarity_score", 0.0),
            metadata={
                "document_id": getattr(chunk, "document_id", None),
                "title": getattr(chunk, "title", None),
                "chunk_index": getattr(chunk, "chunk_index", None),
                "start_date": getattr(chunk, "start_date", None),
                "party": getattr(chunk, "party", None),
            },
        )


@dataclass
class RRFEntry:
    """Per-chunk RRF fusion detail."""
    chunk_id: int
    rrf_score: float
    vector_rank: Optional[int] = None
    keyword_rank: Optional[int] = None


@dataclass
class RerankerEntry:
    """Per-chunk reranker detail."""
    chunk_id: int
    reranker_score: float
    pre_rerank_position: int


@dataclass
class QueryTrace:
    """Full pipeline trace for a single query."""
    question_id: str
    query_text: str
    query_embedding_hash: str = ""

    # Stage 1: Raw search results
    vector_results: List[StageResult] = field(default_factory=list)
    keyword_results: List[StageResult] = field(default_factory=list)

    # Stage 2: RRF fusion
    rrf_results: List[RRFEntry] = field(default_factory=list)

    # Stage 3: Reranking
    reranker_results: List[RerankerEntry] = field(default_factory=list)
    reranker_skipped: bool = False

    # Stage 4: Final output
    final_chunks: List[StageResult] = field(default_factory=list)

    # Timings in milliseconds
    timings: Dict[str, float] = field(default_factory=dict)

    # Config snapshot for this query
    config_snapshot: Dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def embedding_hash(embedding: List[float]) -> str:
        """MD5 of first 32 floats — identity check, not security."""
        sig = str(embedding[:32]).encode()
        return hashlib.md5(sig).hexdigest()

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    # --- Convenience accessors for metrics ---

    @property
    def total_ms(self) -> float:
        return self.timings.get("total_ms", 0.0)

    @property
    def vector_chunk_ids(self) -> set:
        return {r.chunk_id for r in self.vector_results}

    @property
    def keyword_chunk_ids(self) -> set:
        return {r.chunk_id for r in self.keyword_results}

    @property
    def final_chunk_ids(self) -> set:
        return {r.chunk_id for r in self.final_chunks}
