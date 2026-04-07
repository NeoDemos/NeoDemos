"""
Instrumented RAGService that captures a full pipeline trace per query.

Uses composition: wraps a real RAGService instance and delegates private methods
to it while capturing timing and intermediate results at each stage.
Production code in services/rag_service.py is never modified.
"""

from __future__ import annotations

import time
from typing import Optional, Tuple, Dict, Any

from eval.instrumentation.tracer import (
    QueryTrace, StageResult, RRFEntry, RerankerEntry,
)


def create_instrumented_rag(
    db_url: str = "",
) -> "InstrumentedRAGService":
    """
    Factory for eval pipeline. Uses Nebius API embedding (zero RAM) by default.
    Falls back to local MLX if NEBIUS_API_KEY is not set.
    """
    from services.rag_service import RAGService

    rag = RAGService()
    if db_url:
        rag.db_connection_string = db_url

    return InstrumentedRAGService(rag)


class InstrumentedRAGService:
    """
    Wraps RAGService via composition and adds retrieve_with_trace()
    that returns a QueryTrace alongside the normal List[RetrievedChunk].

    All private method calls are delegated to the wrapped RAGService instance.
    """

    def __init__(self, rag):
        self._rag = rag

    def retrieve_with_trace(
        self,
        question_id: str,
        query_text: str,
        top_k: int = 10,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        fast_mode: bool = False,
        score_threshold: float = 0.15,
        reranker_threshold: float = -2.0,
    ) -> Tuple[list, QueryTrace]:
        """
        Same logic as RAGService.retrieve_relevant_context (lines 140-240)
        but captures intermediate results into a QueryTrace.
        """
        from services.rag_service import _reranker as reranker_instance

        trace = QueryTrace(
            question_id=question_id,
            query_text=query_text,
            config_snapshot={
                "top_k": top_k,
                "fast_mode": fast_mode,
                "date_from": date_from,
                "date_to": date_to,
                "score_threshold": score_threshold,
                "reranker_threshold": reranker_threshold,
            },
        )

        t_total = time.perf_counter()

        # --- Stage 1a: Embedding ---
        t0 = time.perf_counter()
        query_embedding = self._rag.embedder.embed(query_text)
        trace.timings["embedding_ms"] = (time.perf_counter() - t0) * 1000

        if query_embedding:
            trace.query_embedding_hash = QueryTrace.embedding_hash(query_embedding)

        # --- Stage 1b: Vector search ---
        vector_results: list = []
        t0 = time.perf_counter()
        if query_embedding is not None:
            vector_results = self._rag._retrieve_by_vector_similarity(
                query_embedding, top_k=top_k * 3,
                date_from=date_from, date_to=date_to,
            )
        trace.timings["vector_ms"] = (time.perf_counter() - t0) * 1000
        trace.vector_results = [
            StageResult.from_retrieved_chunk(c) for c in vector_results
        ]

        # --- Stage 1c: Keyword search (BM25) ---
        t0 = time.perf_counter()
        keyword_results = self._keyword_search_with_fallback(
            query_text, top_k, date_from, date_to,
        )
        trace.timings["keyword_ms"] = (time.perf_counter() - t0) * 1000
        trace.keyword_results = [
            StageResult.from_retrieved_chunk(c) for c in keyword_results
        ]

        # --- Stage 2: RRF Fusion ---
        t0 = time.perf_counter()
        if vector_results and keyword_results:
            fused_chunks, rrf_entries = self._rrf_with_scores(
                vector_results, keyword_results,
            )
        elif vector_results:
            fused_chunks = vector_results
            rrf_entries = []
        else:
            fused_chunks = keyword_results
            rrf_entries = []
        trace.timings["rrf_ms"] = (time.perf_counter() - t0) * 1000
        trace.rrf_results = rrf_entries

        # Reranker candidate pool: 5× top_k. Safe now that the reranker batches
        # internally (LocalJinaReranker: 20 docs/batch; JinaAPI: 80KB/batch).
        fused_chunks = fused_chunks[:top_k * 5]

        # --- Stage 3: Jina Reranker v3 ---
        t0 = time.perf_counter()
        if reranker_instance is not None and not fast_mode:
            try:
                documents = [chunk.content for chunk in fused_chunks]
                if documents:
                    scores = reranker_instance.score_pairs(query_text, documents)
                    # Capture pre-rerank positions
                    for pos, (chunk, score) in enumerate(zip(fused_chunks, scores)):
                        trace.reranker_results.append(
                            RerankerEntry(
                                chunk_id=chunk.chunk_id,
                                reranker_score=float(score),
                                pre_rerank_position=pos,
                            )
                        )
                        chunk.similarity_score = float(score)

                    fused_chunks.sort(
                        key=lambda x: x.similarity_score, reverse=True
                    )
                    # Jina v3: positive = relevant; filter definite noise
                    fused_chunks = [
                        c for c in fused_chunks
                        if c.similarity_score > reranker_threshold
                    ]
            except Exception as e:
                print(f"[eval] Reranking failed: {e}")
        else:
            trace.reranker_skipped = True
        trace.timings["reranker_ms"] = (time.perf_counter() - t0) * 1000

        # --- Stage 4: Final output ---
        final = fused_chunks[:top_k]
        trace.final_chunks = [
            StageResult.from_retrieved_chunk(c) for c in final
        ]
        trace.timings["total_ms"] = (time.perf_counter() - t_total) * 1000

        return final, trace

    # ------------------------------------------------------------------
    # Helper: keyword search with the same fallback logic as production
    # (rag_service.py lines 172-210)
    # ------------------------------------------------------------------
    def _keyword_search_with_fallback(
        self,
        query_text: str,
        top_k: int,
        date_from: Optional[str],
        date_to: Optional[str],
    ) -> list:
        vision_sigs = [
            "visie", "standpunt", "programma", "ideologie",
            "leefbaar", "pvda", "vvd", "d66", "glpvda", "volt",
            "cda", "denk", "sp", "christenunie", "bij1",
        ]
        is_vision = any(sig in query_text.lower() for sig in vision_sigs)
        chunk_type = "vision" if is_vision else None

        keyword_results = self._rag._retrieve_chunks_by_keywords(
            query_text, top_k * 3, chunk_type,
            date_from=date_from, date_to=date_to,
        )

        if len(keyword_results) < top_k:
            keyword_results.extend(
                self._rag._retrieve_chunks_by_keywords(
                    query_text, top_k * 2, chunk_type,
                    mode="plain", date_from=date_from, date_to=date_to,
                )
            )
            terms = [t for t in query_text.replace("?", "").split() if len(t) > 3]
            if terms:
                or_query = " | ".join(terms)
                keyword_results.extend(
                    self._rag._retrieve_chunks_by_keywords(
                        or_query, top_k * 2, chunk_type,
                        mode="or", date_from=date_from, date_to=date_to,
                    )
                )

        # Deduplicate
        seen = set()
        unique = []
        for c in keyword_results:
            if c.chunk_id not in seen:
                unique.append(c)
                seen.add(c.chunk_id)
        keyword_results = unique

        # Fall back to document-level search
        if not keyword_results:
            keyword_results = self._rag._retrieve_by_keywords(query_text, top_k * 3)

        return keyword_results

    # ------------------------------------------------------------------
    # RRF that also returns per-chunk score details
    # ------------------------------------------------------------------
    def _rrf_with_scores(
        self,
        vector_results: list,
        keyword_results: list,
        k: int = 60,
    ) -> Tuple[list, list]:
        """RRF fusion that also returns per-chunk score breakdown."""
        scores: Dict[Any, float] = {}
        chunk_map: Dict[Any, Any] = {}
        vec_rank: Dict[Any, int] = {}
        kw_rank: Dict[Any, int] = {}

        for rank, chunk in enumerate(vector_results):
            score = 1.0 / (k + rank)
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + score
            chunk_map[chunk.chunk_id] = chunk
            vec_rank[chunk.chunk_id] = rank

        for rank, chunk in enumerate(keyword_results):
            score = 1.0 / (k + rank)
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + score
            if chunk.chunk_id not in chunk_map:
                chunk_map[chunk.chunk_id] = chunk
            kw_rank[chunk.chunk_id] = rank

        fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)

        entries = [
            RRFEntry(
                chunk_id=cid,
                rrf_score=rrf_score,
                vector_rank=vec_rank.get(cid),
                keyword_rank=kw_rank.get(cid),
            )
            for cid, rrf_score in fused
        ]

        ordered_chunks = [chunk_map[cid] for cid, _ in fused]
        return ordered_chunks, entries
