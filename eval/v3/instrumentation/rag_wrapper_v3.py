"""
v3 Instrumented RAG Service — routes queries to specialised pipelines.

Strategies:
  standard:        Same as v2 (hybrid search + RRF + Jina rerank)
  party_filtered:  Per-party Qdrant filtered retrieval + merge
  map_reduce:      Large pool retrieval → parallel Gemini map → Claude reduce
  sub_query:       Haiku decompose → parallel retrieval → merge → Sonnet synthesize

API-only: all LLM calls via API. No local model fallback.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional, Tuple, List

from qdrant_client.models import Filter, FieldCondition, MatchValue

from eval.instrumentation.rag_wrapper import create_instrumented_rag, InstrumentedRAGService
from eval.instrumentation.tracer import QueryTrace, StageResult
from services.query_router import route_query, QueryRoute
from services.synthesis import MapReduceSynthesizer
from services.decomposition import MultiHopDecomposer

log = logging.getLogger(__name__)


def create_v3_rag(
    db_url: str = "",
    enable_router: bool = True,
    enable_map_reduce: bool = True,
    enable_decomposition: bool = True,
) -> "V3InstrumentedRAGService":
    """Factory for v3 eval pipeline."""
    base_rag = create_instrumented_rag(db_url)

    router = route_query if enable_router else None
    synthesizer = MapReduceSynthesizer() if enable_map_reduce else None
    decomposer = MultiHopDecomposer() if enable_decomposition else None

    return V3InstrumentedRAGService(
        base_rag=base_rag,
        router_fn=router,
        synthesizer=synthesizer,
        decomposer=decomposer,
    )


class V3InstrumentedRAGService:
    """
    Wraps v2 InstrumentedRAGService with query routing and specialised pipelines.
    """

    def __init__(
        self,
        base_rag: InstrumentedRAGService,
        router_fn=None,
        synthesizer: Optional[MapReduceSynthesizer] = None,
        decomposer: Optional[MultiHopDecomposer] = None,
    ):
        self._base = base_rag
        self._router_fn = router_fn
        self._synthesizer = synthesizer
        self._decomposer = decomposer

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
        metadata: dict = None,
        force_strategy: str = "",
    ) -> Tuple[list, QueryTrace, QueryRoute, str]:
        """
        Route query → retrieve → optionally generate.

        Returns:
          (chunks, trace, route, generated_answer)
          generated_answer is empty for standard strategy (caller handles generation).
        """
        metadata = metadata or {}
        loop = asyncio.new_event_loop()

        try:
            result = loop.run_until_complete(
                self._async_retrieve(
                    question_id=question_id,
                    query_text=query_text,
                    top_k=top_k,
                    date_from=date_from,
                    date_to=date_to,
                    fast_mode=fast_mode,
                    score_threshold=score_threshold,
                    reranker_threshold=reranker_threshold,
                    metadata=metadata,
                    force_strategy=force_strategy,
                )
            )
            return result
        finally:
            loop.close()

    async def _async_retrieve(
        self,
        question_id: str,
        query_text: str,
        top_k: int,
        date_from: Optional[str],
        date_to: Optional[str],
        fast_mode: bool,
        score_threshold: float,
        reranker_threshold: float,
        metadata: dict,
        force_strategy: str,
    ) -> Tuple[list, QueryTrace, QueryRoute, str]:
        """Async core of retrieve_with_trace."""

        # ── Step 1: Route ──
        if self._router_fn and not force_strategy:
            # Pass date hints from question metadata to router
            router_meta = dict(metadata)
            if date_from:
                router_meta["date_from"] = date_from
            if date_to:
                router_meta["date_to"] = date_to
            route = await self._router_fn(query_text, router_meta)
        else:
            from services.query_router import QueryRoute, K_MAP, STRATEGY_MAP
            strategy = force_strategy or "standard"
            route = QueryRoute(
                query_type="manual",
                top_k=top_k,
                strategy=strategy,
                date_from=date_from,
                date_to=date_to,
            )

        # Use router's top_k unless caller explicitly overrides
        effective_top_k = route.top_k if self._router_fn and not force_strategy else top_k
        effective_date_from = route.date_from or date_from
        effective_date_to = route.date_to or date_to

        log.info(f"[{question_id}] Route: {route.query_type} → {route.strategy} (top_k={effective_top_k})")

        generated_answer = ""

        # ── Step 2: Strategy dispatch ──

        if route.strategy == "party_filtered" and route.parties:
            chunks, trace = await self._party_filtered_retrieve(
                question_id, query_text, route,
                effective_date_from, effective_date_to,
                fast_mode, score_threshold, reranker_threshold,
            )

        elif route.strategy == "map_reduce" and self._synthesizer:
            chunks, trace = self._base.retrieve_with_trace(
                question_id, query_text, effective_top_k,
                effective_date_from, effective_date_to,
                fast_mode, score_threshold, reranker_threshold,
            )
            # Map-reduce generation is built in
            log.info(f"[{question_id}] Map-reduce synthesis on {len(chunks)} chunks...")
            generated_answer = await self._synthesizer.synthesize(
                query_text, chunks, category=route.query_type,
            )

        elif route.strategy == "sub_query" and self._decomposer:
            chunks, trace, generated_answer = await self._sub_query_retrieve(
                question_id, query_text, route,
                effective_date_from, effective_date_to,
                reranker_threshold,
            )

        else:
            # Standard retrieval (same as v2)
            chunks, trace = self._base.retrieve_with_trace(
                question_id, query_text, effective_top_k,
                effective_date_from, effective_date_to,
                fast_mode, score_threshold, reranker_threshold,
            )

        # ── Step 3: Financial table boost ──
        if route.boost_tables and chunks:
            chunks, financial_summary = await self._boost_financial_tables(
                question_id, query_text, chunks,
                effective_date_from, effective_date_to,
            )
            if financial_summary:
                # Prepend computed comparisons to any generated answer
                if generated_answer:
                    generated_answer = financial_summary + "\n\n" + generated_answer
                # Store for use in generation step
                trace.config_snapshot["financial_summary"] = financial_summary

        # Store route in trace config
        trace.config_snapshot["route"] = route.to_dict()

        return chunks, trace, route, generated_answer

    # ── Party-filtered retrieval ──────────────────────────────────────

    async def _party_filtered_retrieve(
        self,
        question_id: str,
        query_text: str,
        route: QueryRoute,
        date_from: Optional[str],
        date_to: Optional[str],
        fast_mode: bool,
        score_threshold: float,
        reranker_threshold: float,
    ) -> Tuple[list, QueryTrace]:
        """
        Retrieve chunks filtered by party, then merge and rerank.
        Falls back to standard retrieval if party filter returns too few results.
        """
        rag = self._base._rag  # Access underlying RAGService

        # Get embedding
        t0 = time.perf_counter()
        query_embedding = rag.embedder.embed(query_text)
        embed_ms = (time.perf_counter() - t0) * 1000

        all_chunks = []
        per_party_counts = {}

        for party in route.parties:
            party_filter = Filter(must=[
                FieldCondition(key="party", match=MatchValue(value=party))
            ])
            chunks = rag._retrieve_by_vector_similarity_with_filter(
                query_embedding,
                top_k=route.top_k,
                qdrant_filter=party_filter,
                date_from=date_from,
                date_to=date_to,
            )
            per_party_counts[party] = len(chunks)
            all_chunks.extend(chunks)

        # Also do a standard retrieval to catch chunks without party metadata
        standard_chunks = rag._retrieve_by_vector_similarity(
            query_embedding, top_k=15,
            date_from=date_from, date_to=date_to,
        )

        # Merge and deduplicate
        seen = set()
        merged = []
        for c in all_chunks + standard_chunks:
            if c.chunk_id not in seen:
                seen.add(c.chunk_id)
                merged.append(c)

        log.info(f"[{question_id}] Party retrieval: {per_party_counts}, +{len(standard_chunks)} standard → {len(merged)} merged")

        # Rerank merged pool
        from services.rag_service import _reranker
        if _reranker and merged and not fast_mode:
            try:
                documents = [c.content for c in merged]
                scores = _reranker.score_pairs(query_text, documents)
                for chunk, score in zip(merged, scores):
                    chunk.similarity_score = float(score)
                merged.sort(key=lambda x: x.similarity_score, reverse=True)
                merged = [c for c in merged if c.similarity_score > reranker_threshold]
            except Exception as e:
                log.warning(f"Reranking party-filtered pool failed: {e}")

        # Take top results
        final = merged[:route.top_k]

        # Build a trace (simplified — we skip the full v2 trace stages for party-filtered)
        trace = QueryTrace(
            question_id=question_id,
            query_text=query_text,
            config_snapshot={
                "top_k": route.top_k,
                "fast_mode": fast_mode,
                "date_from": date_from,
                "date_to": date_to,
                "score_threshold": score_threshold,
                "reranker_threshold": reranker_threshold,
                "strategy": "party_filtered",
                "parties": route.parties,
                "per_party_counts": per_party_counts,
            },
        )
        trace.timings["embedding_ms"] = embed_ms
        trace.final_chunks = [StageResult.from_retrieved_chunk(c) for c in final]

        return final, trace

    # ── Sub-query decomposition ───────────────────────────────────────

    async def _sub_query_retrieve(
        self,
        question_id: str,
        query_text: str,
        route: QueryRoute,
        date_from: Optional[str],
        date_to: Optional[str],
        reranker_threshold: float,
    ) -> Tuple[list, QueryTrace, str]:
        """
        Decompose → parallel retrieve → merge → synthesize.
        Returns (chunks, trace, generated_answer).
        """
        rag = self._base._rag  # Access underlying RAGService

        t0 = time.perf_counter()
        chunks, sub_queries = await self._decomposer.decompose_and_retrieve(
            question=query_text,
            rag_service=rag,
            top_k_per_sub=route.top_k,
            date_from=date_from,
            date_to=date_to,
        )
        retrieve_ms = (time.perf_counter() - t0) * 1000

        # CRAG filter: remove chunks irrelevant to the ORIGINAL question
        from services.crag_filter import filter_chunks_by_relevance
        chunks, crag_removed = await filter_chunks_by_relevance(query_text, chunks)
        log.info(f"[{question_id}] CRAG: removed {crag_removed} irrelevant chunks")

        # Synthesize
        log.info(f"[{question_id}] Synthesizing from {len(chunks)} chunks, {len(sub_queries)} sub-queries...")
        answer = self._decomposer.synthesize(query_text, sub_queries, chunks)

        # Build trace
        trace = QueryTrace(
            question_id=question_id,
            query_text=query_text,
            config_snapshot={
                "top_k": route.top_k,
                "date_from": date_from,
                "date_to": date_to,
                "strategy": "sub_query",
                "sub_queries": sub_queries,
                "reranker_threshold": reranker_threshold,
            },
        )
        trace.timings["total_ms"] = retrieve_ms
        trace.final_chunks = [StageResult.from_retrieved_chunk(c) for c in chunks]

        return chunks, trace, answer

    # ── Financial table boost ─────────────────────────────────────────

    async def _boost_financial_tables(
        self,
        question_id: str,
        query_text: str,
        existing_chunks: list,
        date_from: Optional[str],
        date_to: Optional[str],
    ) -> tuple:
        """
        Fetch additional table-type chunks and compute programmatic comparisons.
        Returns (enriched_chunks, financial_summary_markdown).
        """
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        from services.financial_calc import compute_financial_summary

        rag = self._base._rag

        # Retrieve table-specific chunks via filtered search
        table_filter = Filter(must=[
            FieldCondition(key="chunk_type", match=MatchValue(value="table"))
        ])

        query_embedding = rag.embedder.embed(query_text)
        table_chunks = rag._retrieve_by_vector_similarity_with_filter(
            query_embedding,
            top_k=30,
            qdrant_filter=table_filter,
            date_from=date_from,
            date_to=date_to,
        )

        # Targeted filtering: only keep tables whose content matches query concepts
        query_lower = query_text.lower()
        query_terms = [t for t in query_lower.replace("?", "").split() if len(t) > 3]
        relevant_tables = []
        for tc in table_chunks:
            content_lower = getattr(tc, "content", "").lower()
            # Require at least 2 query terms to appear in the table content
            matches = sum(1 for t in query_terms if t in content_lower)
            if matches >= 2:
                relevant_tables.append(tc)

        # Merge relevant table chunks with existing (deduplicate)
        seen = {c.chunk_id for c in existing_chunks}
        added = 0
        for tc in relevant_tables:
            if tc.chunk_id not in seen:
                seen.add(tc.chunk_id)
                existing_chunks.append(tc)
                added += 1

        log.info(f"[{question_id}] Table boost: {len(table_chunks)} retrieved → {len(relevant_tables)} relevant → +{added} added")

        # Compute programmatic financial summary from table_json data
        # We need the raw table_json — fetch from PostgreSQL for table chunks
        financial_summary = ""
        table_chunk_ids = [c.chunk_id for c in existing_chunks
                          if getattr(c, 'content', '').find('[FINANCIAL]') >= 0]

        if table_chunk_ids or added > 0:
            try:
                financial_summary = await asyncio.to_thread(
                    self._compute_tables_from_db,
                    [c.chunk_id for c in table_chunks[:20]],
                )
            except Exception as e:
                log.warning(f"Financial calc failed: {e}")

        return existing_chunks, financial_summary

    def _compute_tables_from_db(self, chunk_ids: list) -> str:
        """Fetch table_json from PostgreSQL and run financial calculator."""
        if not chunk_ids:
            return ""

        import psycopg2
        from services.financial_calc import extract_table_numbers

        db_url = self._base._rag.db_connection_string
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()

        cur.execute("""
            SELECT id, table_json, title FROM document_chunks
            WHERE chunk_type = 'table' AND table_json IS NOT NULL
            ORDER BY id DESC LIMIT 50
        """)

        all_numbers = []
        for row in cur.fetchall():
            chunk_id, table_json, title = row
            if table_json:
                numbers = extract_table_numbers(table_json)
                for n in numbers:
                    n['source'] = title or 'tabel'
                all_numbers.extend(numbers)

        cur.close()
        conn.close()

        if not all_numbers:
            return ""

        # Build year-over-year comparisons
        from services.financial_calc import _fmt_number
        by_label = {}
        for n in all_numbers:
            if n.get('year'):
                by_label.setdefault(n['label'], {})[n['year']] = n['value']

        lines = ["## Berekende financiële vergelijkingen\n"]
        lines.append("*Cijfers programmatisch berekend uit brontabellen — niet door een taalmodel.*\n")

        found = 0
        for label, years in sorted(by_label.items()):
            if len(years) < 2:
                continue
            sorted_years = sorted(years.items())
            parts = []
            for i in range(1, len(sorted_years)):
                y_prev, v_prev = sorted_years[i - 1]
                y_curr, v_curr = sorted_years[i]
                delta = v_curr - v_prev
                if v_prev != 0:
                    pct = (delta / abs(v_prev)) * 100
                    sign = "+" if delta >= 0 else ""
                    parts.append(
                        f"  - {y_prev} → {y_curr}: {_fmt_number(v_prev)} → {_fmt_number(v_curr)} "
                        f"({sign}{_fmt_number(delta)}, {sign}{pct:.1f}%)"
                    )
            if parts:
                lines.append(f"**{label}:**")
                lines.extend(parts)
                found += 1
            if found >= 15:
                break

        if found == 0:
            return ""

        return "\n".join(lines) + "\n"
