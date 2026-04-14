import asyncio
import logging
import os
import threading
from typing import List, Dict, Any, Optional, Union
from dataclasses import dataclass

from services.db_pool import get_connection

# Virtual notulen killswitch — set to "false" to exclude AI-generated transcripts
# from RAG results without deleting any data.
INCLUDE_VIRTUAL_NOTULEN = os.getenv("INCLUDE_VIRTUAL_NOTULEN", "true").lower() in ("true", "1", "yes")

logger = logging.getLogger(__name__)

# Shared class-level resources to avoid redundant loading and locking issues
_qdrant_client = None
_reranker = None        # services.reranker.Reranker instance (Jina v3 API)
_init_lock = threading.Lock()

# WS4 2026-04-11: defense-in-depth — pin method to the known-safe set before
# interpolating into the f-string SQL template. A future refactor that reads
# `method` from a request parameter would otherwise introduce SQL injection.
_ALLOWED_TS_METHODS = {"to_tsquery", "plainto_tsquery", "websearch_to_tsquery"}

@dataclass
class RetrievedChunk:
    """A chunk of text retrieved from the database"""
    chunk_id: Any  # Can be int, str, or UUID from Qdrant
    document_id: str
    title: str
    content: str
    similarity_score: float = 1.0  # For keyword search
    questions: Optional[List[str]] = None
    child_id: Optional[int] = None
    stream_type: Optional[str] = None  # e.g., 'financial', 'debate', 'fact'
    start_date: Optional[str] = None   # ISO date from Qdrant payload (e.g. "2022-03-15T00:00:00")

from services.embedding import create_embedder, EMBEDDING_DIM, QDRANT_COLLECTION

class RAGService:
    """Retrieve relevant notulen context for agenda items"""

    def __init__(self):
        self.embedder = create_embedder()  # Nebius API (NEBIUS_API_KEY) in deployment
        self._ensure_resources_initialized()

    def _ensure_resources_initialized(self):
        global _qdrant_client, _reranker
        if _qdrant_client is not None and _reranker is not None:
            return

        with _init_lock:
            if _qdrant_client is None:
                try:
                    import os
                    from qdrant_client import QdrantClient
                    qdrant_url = os.getenv("QDRANT_URL", "http://localhost:6333")
                    qdrant_api_key = os.getenv("QDRANT_API_KEY", None)
                    print(f"Initializing QdrantClient ({qdrant_url})...")
                    _qdrant_client = QdrantClient(
                        url=qdrant_url,
                        api_key=qdrant_api_key,
                        timeout=60,
                    )
                    # Verify the collection was built with the expected embedding model.
                    # Catches accidental model upgrades before they silently corrupt retrieval.
                    info = _qdrant_client.get_collection(QDRANT_COLLECTION)
                    actual_dim = info.config.params.vectors.size
                    if actual_dim != EMBEDDING_DIM:
                        raise RuntimeError(
                            f"[embedding] Dimension mismatch: collection '{QDRANT_COLLECTION}' "
                            f"has {actual_dim}D vectors but current model produces {EMBEDDING_DIM}D. "
                            f"Re-embed all chunks before switching models."
                        )
                    print(f"[embedding] Collection dimension verified: {actual_dim}D ✓")
                except RuntimeError:
                    raise  # dimension mismatch is fatal — let it propagate
                except Exception as e:
                    print(f"Failed to initialize QdrantClient: {e}")

            if _reranker is None:
                try:
                    from services.reranker import create_reranker
                    print("Loading Reranker (Jina v3)...")
                    _reranker = create_reranker()
                except Exception as e:
                    print(f"Failed to initialize Reranker: {e}")

    async def retrieve_parallel_context(
        self,
        query_text: str,
        query_embedding: Optional[List[float]] = None,
        distribution: Dict[str, int] = {"financial": 3, "debate": 3, "fact": 2, "vision": 2, "graph": 2},
        overrides: Optional[Dict[str, str]] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        fast_mode: bool = False,
        query_intent: str = "",
        gemeente: Optional[str] = None,
    ) -> List[RetrievedChunk]:
        """
        Executes parallel searches for different dimensions of the query.
        Accepts optional 'overrides' dict with keys 'financial', 'debate', 'fact' for LLM-rewritten queries.
        date_from/date_to (ISO strings) are forwarded to all stream searches.
        fast_mode=True skips Jina reranking in each stream (saves latency for interactive queries).

        WS1 v0.2.0: a fifth 'graph_walk' stream runs alongside the four dense/BM25
        streams, pulling chunks via ``services.graph_retrieval``. The stream is
        gated on ``GRAPH_WALK_ENABLED`` + ``GRAPH_WALK_MIN_EDGES`` (see
        services/graph_retrieval.py) so Phase 0 is safe to ship with an empty KG
        — until enrichment lands, the stream returns an empty list cleanly.
        """
        tasks = []

        # Each stream skips its own rerank (stream_fast_mode=True). We rerank
        # ONCE across the deduplicated union below — cuts Jina calls 5× and
        # scores all candidates against the user's actual query rather than
        # the per-stream augmented retrieval prompts. (WS10 mitigation.)
        stream_fast_mode = True

        # Vision Stream (Ideology) - High priority to capture vision chunks before deduplication
        vision_query = overrides.get("vision") if overrides else f"{query_text} standpunt visie ideaal ideologie programma"
        tasks.append(self._async_retrieve(vision_query or f"{query_text} visie", query_embedding, distribution.get("vision", 2), "vision", date_from, date_to, stream_fast_mode))

        # Financial Stream
        financial_query = overrides.get("financial") if overrides else f"{query_text} begroting budget kosten cijfers"
        tasks.append(self._async_retrieve(financial_query or f"{query_text} begroting", query_embedding, distribution.get("financial", 3), "financial", date_from, date_to, stream_fast_mode))

        # Debate Stream
        debate_query = overrides.get("debate") if overrides else f"{query_text} debat standpunten raadsleden uitspraken"
        tasks.append(self._async_retrieve(debate_query or f"{query_text} debat", query_embedding, distribution.get("debate", 3), "debate", date_from, date_to, stream_fast_mode))

        # Fact Stream
        fact_query = overrides.get("fact") if overrides else f"{query_text} beleid regels technische details definities"
        tasks.append(self._async_retrieve(fact_query or f"{query_text} beleid", query_embedding, distribution.get("fact", 2), "fact", date_from, date_to, stream_fast_mode))

        # Graph-walk Stream (WS1). Returns [] when the KG is not yet populated
        # (GRAPH_WALK_ENABLED unset OR kg_relationships < GRAPH_WALK_MIN_EDGES).
        tasks.append(self._async_retrieve_graph(
            query_text, distribution.get("graph", 2), query_intent, gemeente,
        ))

        # Gather all results
        results_lists = await asyncio.gather(*tasks)

        # Flatten and deduplicate
        all_chunks = []
        seen_chunk_ids = set()
        for chunk_list in results_lists:
            for chunk in chunk_list:
                if chunk.chunk_id not in seen_chunk_ids:
                    all_chunks.append(chunk)
                    seen_chunk_ids.add(chunk.chunk_id)

        # Single cross-stream rerank against the original user query.
        # Skipped when caller asked for fast_mode or when reranker is unavailable.
        if not fast_mode and _reranker is not None and all_chunks:
            try:
                documents = [c.content for c in all_chunks]
                scores = await asyncio.to_thread(_reranker.score_pairs, query_text, documents)
                for chunk, score in zip(all_chunks, scores):
                    chunk.similarity_score = float(score)
                all_chunks.sort(key=lambda c: c.similarity_score, reverse=True)
                all_chunks = [c for c in all_chunks if c.similarity_score > -0.1]
            except Exception:
                logger.exception("[rag] cross-stream rerank failed; returning fused order")

        return all_chunks

    async def _async_retrieve_graph(
        self, query: str, k: int, query_intent: str, gemeente: Optional[str],
    ) -> List[RetrievedChunk]:
        """
        Graph-walk stream helper. Runs services.graph_retrieval.retrieve_via_graph
        in a thread and adapts the returned GraphChunk instances to
        RetrievedChunk so they merge cleanly with the other 4 streams.
        Returns an empty list on any failure — never raises into the gather.
        """
        def _run() -> List[RetrievedChunk]:
            try:
                from services import graph_retrieval
                graph_chunks = graph_retrieval.retrieve_via_graph(
                    query, k=k, query_intent=query_intent, gemeente=gemeente,
                )
            except Exception:
                logger.exception("[rag] graph_walk stream failed")
                return []
            out: List[RetrievedChunk] = []
            for g in graph_chunks:
                out.append(RetrievedChunk(
                    chunk_id=g.chunk_id,
                    document_id=g.document_id,
                    title=g.title,
                    content=g.content,
                    similarity_score=g.similarity_score,
                    questions=g.questions or [],
                    child_id=g.child_id,
                    stream_type="graph",
                    start_date=g.start_date,
                ))
            return out
        return await asyncio.to_thread(_run)

    async def _async_retrieve(
        self, query: str, embedding: Optional[List[float]], k: int, stream_type: str,
        date_from: Optional[str] = None, date_to: Optional[str] = None,
        fast_mode: bool = False,
    ) -> List[RetrievedChunk]:
        """Helper to run synchronous retrieval in a thread."""
        chunks = await asyncio.to_thread(
            self.retrieve_relevant_context, query, embedding, k, True,
            date_from, date_to, fast_mode,
        )
        for c in chunks:
            c.stream_type = stream_type
        return chunks
    
    def _reciprocal_rank_fusion(self, vector_results: List[RetrievedChunk], keyword_results: List[RetrievedChunk], k: int = 60) -> List[RetrievedChunk]:
        """Combine results using Reciprocal Rank Fusion."""
        scores = {}
        chunk_map = {}
        
        for rank, chunk in enumerate(vector_results):
            score = 1.0 / (k + rank)
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + score
            chunk_map[chunk.chunk_id] = chunk
            
        for rank, chunk in enumerate(keyword_results):
            score = 1.0 / (k + rank)
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + score
            if chunk.chunk_id not in chunk_map:
                chunk_map[chunk.chunk_id] = chunk
                
        fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [chunk_map[chunk_id] for chunk_id, _ in fused]

    def retrieve_relevant_context(
        self,
        query_text: str,
        query_embedding: Optional[List[float]] = None,
        top_k: int = 10,
        fallback_to_keywords: bool = True,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        fast_mode: bool = False,
    ) -> List[RetrievedChunk]:
        """
        Retrieve relevant notulen passages for an agenda item using True Hybrid Search.

        Args:
            date_from: Optional ISO date string (e.g. "2022-01-01") to filter results by meeting date.
            date_to:   Optional ISO date string (e.g. "2022-12-31") to filter results by meeting date.
            fast_mode: If True, skips Jina reranking (saves latency). Use for
                       interactive/MCP queries; leave False for deep analysis via FastAPI.
        """
        vector_results = []
        keyword_results = []

        # 1. Fetch from Vector Search
        if query_embedding is None:
            query_embedding = self.embedder.embed(query_text)

        if query_embedding is not None:
            vector_results = self._retrieve_by_vector_similarity(
                query_embedding, top_k=top_k * 3, date_from=date_from, date_to=date_to
            )

        # 2. Fetch from BM25 Search
        if fallback_to_keywords:
            vision_sigs = ['visie', 'standpunt', 'programma', 'ideologie', 'leefbaar', 'pvda', 'vvd', 'd66', 'glpvda', 'volt', 'cda', 'denk', 'sp', 'christenunie', 'bij1']
            is_vision = any(sig in query_text.lower() for sig in vision_sigs)

            chunk_type = "vision" if is_vision else None
            # Search specific chunk types first or default chunks
            keyword_results = self._retrieve_chunks_by_keywords(
                query_text, top_k * 3, chunk_type, date_from=date_from, date_to=date_to
            )

            # If strict websearch returns too few results, try broader queries
            if len(keyword_results) < top_k:
                # Try plain search (AND logic but softer than websearch)
                keyword_results.extend(self._retrieve_chunks_by_keywords(
                    query_text, top_k * 2, chunk_type, mode='plain', date_from=date_from, date_to=date_to
                ))

                # Try OR-logic for key terms (highest recall)
                # Filter out small common words and join with |
                terms = [t for t in query_text.replace('?', '').split() if len(t) > 3]
                if terms:
                    or_query = " | ".join(terms)
                    keyword_results.extend(self._retrieve_chunks_by_keywords(
                        or_query, top_k * 2, chunk_type, mode='or', date_from=date_from, date_to=date_to
                    ))

            # Deduplicate by chunk_id
            seen_ids = set()
            unique_results = []
            for c in keyword_results:
                if c.chunk_id not in seen_ids:
                    unique_results.append(c)
                    seen_ids.add(c.chunk_id)
            keyword_results = unique_results

            # Fall back to huge document search if chunks fail
            if not keyword_results:
                keyword_results = self._retrieve_by_keywords(query_text, top_k * 3)

        # 3. Fuse Results (RRF)
        if vector_results and keyword_results:
            fused_chunks = self._reciprocal_rank_fusion(vector_results, keyword_results)
        elif vector_results:
            fused_chunks = vector_results
        else:
            fused_chunks = keyword_results

        # Reranker candidate pool: 5× top_k. Safe now that the reranker batches
        # internally (LocalJinaReranker: 20 docs/batch; JinaAPI: 80KB/batch).
        fused_chunks = fused_chunks[:top_k * 5]

        # 4. Rerank using Jina Reranker v3 (skipped in fast_mode to save latency)
        if _reranker is not None and not fast_mode:
            try:
                documents = [chunk.content for chunk in fused_chunks]
                if documents:
                    scores = _reranker.score_pairs(query_text, documents)
                    for chunk, score in zip(fused_chunks, scores):
                        chunk.similarity_score = float(score)

                    # Sort by reranker score (Jina v3: higher = more relevant, 0-1 range)
                    fused_chunks.sort(key=lambda x: x.similarity_score, reverse=True)

                    # Filter out low-relevance results (Jina v3: positive = relevant)
                    fused_chunks = [c for c in fused_chunks if c.similarity_score > -0.1]
            except Exception:
                logger.exception("Reranking failed")

        return fused_chunks[:top_k]
    
    def _retrieve_by_vector_similarity_with_filter(
        self,
        query_embedding: List[float],
        top_k: int = 25,
        qdrant_filter: Optional[Any] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> List[RetrievedChunk]:
        """
        Vector search with arbitrary Qdrant filter (e.g. party filter).
        Merges the provided filter with date filters if both are present.
        Falls back to unfiltered search if filter returns < 5 results.
        """
        from qdrant_client.models import Filter, FieldCondition, DatetimeRange

        # Build date filter conditions
        date_conditions = []
        if date_from or date_to:
            from datetime import datetime
            date_conditions.append(FieldCondition(
                key="start_date",
                range=DatetimeRange(
                    gte=datetime.fromisoformat(date_from) if date_from else None,
                    lte=datetime.fromisoformat(date_to) if date_to else None,
                )
            ))

        # Merge filters
        combined_must = list(qdrant_filter.must or []) if qdrant_filter and hasattr(qdrant_filter, 'must') and qdrant_filter.must else []
        combined_must.extend(date_conditions)
        if not INCLUDE_VIRTUAL_NOTULEN:
            from qdrant_client.models import MatchValue
            combined_must.append(FieldCondition(key="is_virtual_notulen", match=MatchValue(value=False)))
        merged_filter = Filter(must=combined_must) if combined_must else None

        results = self._retrieve_by_vector_similarity(
            query_embedding, top_k, date_from=None, date_to=None,
            _override_filter=merged_filter,
        )

        # Fallback: if filter too restrictive, try without the custom filter
        if len(results) < 5 and qdrant_filter:
            fallback_filter = Filter(must=date_conditions) if date_conditions else None
            results = self._retrieve_by_vector_similarity(
                query_embedding, top_k, date_from=None, date_to=None,
                _override_filter=fallback_filter,
            )

        return results

    def _retrieve_by_vector_similarity(
        self,
        query_embedding: List[float],
        top_k: int = 10,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        _override_filter: Optional[Any] = None,
    ) -> List[RetrievedChunk]:
        """
        Search document_chunks table using Qdrant vector similarity.
        Optional date_from/date_to (ISO strings) filter on the start_date payload field.
        Points without a start_date are excluded when a date filter is active.
        _override_filter: if provided, used instead of building date filter internally.
        """
        if query_embedding is None:
            return []

        if _qdrant_client is None:
            return []

        try:
            from datetime import datetime
            from qdrant_client.models import Filter, FieldCondition, DatetimeRange, SearchParams, QuantizationSearchParams

            query_filter = _override_filter
            if query_filter is None:
                must_conditions = []
                if date_from or date_to:
                    must_conditions.append(FieldCondition(
                        key="start_date",
                        range=DatetimeRange(
                            gte=datetime.fromisoformat(date_from) if date_from else None,
                            lte=datetime.fromisoformat(date_to) if date_to else None,
                        )
                    ))
                if not INCLUDE_VIRTUAL_NOTULEN:
                    from qdrant_client.models import MatchValue
                    must_conditions.append(FieldCondition(
                        key="is_virtual_notulen",
                        match=MatchValue(value=False),
                    ))
                if must_conditions:
                    query_filter = Filter(must=must_conditions)

            # Search in Qdrant collection with precision-tuned HNSW + quantization rescoring
            results_qdrant = _qdrant_client.query_points(
                collection_name="notulen_chunks",
                query=query_embedding,
                limit=top_k,
                score_threshold=0.15,
                query_filter=query_filter,
                search_params=SearchParams(
                    hnsw_ef=256,
                    quantization=QuantizationSearchParams(
                        rescore=True,
                        oversampling=2.0,
                    ),
                ),
            )
            
            # Convert Qdrant results to RetrievedChunk objects
            results = []
            for scored_point in results_qdrant.points:
                payload = scored_point.payload or {}
                content = str(payload.get("content", ""))
                
                # Include financial table data if it exists
                table_json = payload.get("table_json")
                if table_json:
                    formatted_table = self._format_json_table(table_json)
                    content += f"\n\n[FINANCIAL] TABEL DATA:\n{formatted_table}"

                results.append(RetrievedChunk(
                    chunk_id=scored_point.id,
                    document_id=str(payload.get("document_id", "unknown")),
                    title=str(payload.get("title", "Untitled")),
                    content=content,
                    similarity_score=float(scored_point.score) if scored_point.score else 0.5,
                    questions=payload.get("questions", []),
                    child_id=payload.get("child_id"),
                    start_date=str(payload.get("start_date", "")) or None,
                ))
            
            return results
            
        except Exception as e:
            logger.exception("Vector similarity search failed")
            return []

    def _format_json_table(self, table_data: Union[str, Dict, List]) -> str:
        """Converts raw table JSON into a readable Markdown table."""
        try:
            if isinstance(table_data, str):
                import json
                data = json.loads(table_data)
            else:
                data = table_data

            if not data or not isinstance(data, list):
                return str(data)

            # Assume first row is header or infer from dict keys
            if isinstance(data[0], dict):
                headers = list(data[0].keys())
                rows = [list(row.values()) for row in data]
            elif isinstance(data[0], list):
                headers = data[0]
                rows = data[1:]
            else:
                return str(data)

            header_str = "| " + " | ".join(map(str, headers)) + " |\n"
            separator_str = "| " + " | ".join(["---"] * len(headers)) + " |\n"
            
            row_strs = []
            for row in rows:
                row_strs.append("| " + " | ".join(map(str, row)) + " |")
            
            return header_str + separator_str + "\n".join(row_strs)
        except Exception as e:
            return f"(Tabel kon niet geformatteerd worden: {e})\n{str(table_data)}"
    
    def _retrieve_by_keywords(
        self,
        query_text: str,
        top_k: int = 10
    ) -> List[RetrievedChunk]:
        """
        Keyword-based search on full notulen documents using BM25.

        Document-level BM25 fallback, invoked only when chunk-level BM25 returns
        nothing. Previously hard-filtered to ``d.name ILIKE '%%notule%%'`` which
        silently excluded moties, amendementen, initiatiefvoorstellen, and
        raadsvoorstellen — a class of retrieval failures nobody could see. The
        filter is removed; if doc-type scoping is ever needed, it should be an
        explicit parameter, not a hidden default.

        Logs when this fallback fires so we can tell if it is still load-bearing.
        """
        logger.info(
            "[rag._retrieve_by_keywords] doc-level BM25 fallback fired (query=%r, top_k=%d)",
            query_text[:120], top_k,
        )
        try:
            with get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT
                            d.id,
                            d.id,  -- chunk_id = document_id for full documents
                            d.name,
                            d.content,
                            ts_rank(text_search, websearch_to_tsquery('dutch', %s)) as similarity_score
                        FROM documents d
                        WHERE d.content IS NOT NULL
                          AND text_search @@ websearch_to_tsquery('dutch', %s)
                        ORDER BY similarity_score DESC
                        LIMIT %s
                    """, [query_text, query_text, top_k])

                    rows = cursor.fetchall()

            results = []
            for doc_id, chunk_id, title, content, sim_score in rows:
                results.append(RetrievedChunk(
                    chunk_id=chunk_id,
                    document_id=str(doc_id),
                    title=title,
                    content=content,
                    similarity_score=float(sim_score) if sim_score else 0.0,
                    questions=[]
                ))
            return results
        except Exception as e:
            logger.exception("Keyword search failed")
            return []

    def _retrieve_chunks_by_keywords(
        self, query_text: str, top_k: int = 5, chunk_type: Optional[str] = None,
        mode: str = 'web', date_from: Optional[str] = None, date_to: Optional[str] = None,
    ) -> List[RetrievedChunk]:
        """
        Search document_chunks table directly using BM25.
        Modes: 'web' (AND), 'plain' (AND, soft), 'or' (OR logic).
        Optional date_from/date_to (ISO strings) filter via JOIN to meetings.start_date.
        """
        try:
            if mode == 'or':
                method = "to_tsquery"
            elif mode == 'plain':
                method = "plainto_tsquery"
            else:
                method = "websearch_to_tsquery"

            # Build optional filters
            type_filter = "AND dc.chunk_type = %s" if chunk_type else ""
            # Virtual notulen killswitch — exclude AI-generated transcripts when disabled
            vn_join = ""
            vn_filter = ""
            if not INCLUDE_VIRTUAL_NOTULEN:
                vn_join = "JOIN documents vn_doc ON dc.document_id = vn_doc.id"
                vn_filter = "AND vn_doc.category <> 'committee_transcript'"
            date_join = ""
            date_filter = ""
            if date_from or date_to:
                date_join = "JOIN documents doc ON dc.document_id = doc.id JOIN meetings m ON doc.meeting_id = m.id"
                date_parts = []
                if date_from:
                    date_parts.append("m.start_date >= %s")
                if date_to:
                    date_parts.append("m.start_date <= %s")
                date_filter = "AND " + " AND ".join(date_parts)

            params = [query_text, query_text, query_text, query_text]
            if chunk_type:
                params.append(chunk_type)
            if date_from:
                params.append(date_from)
            if date_to:
                params.append(date_to)
            params.append(top_k)

            assert method in _ALLOWED_TS_METHODS, (
                f"SECURITY: ts-query method '{method}' not in allowed whitelist {_ALLOWED_TS_METHODS}"
            )

            with get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute(f"""
                        SELECT dc.id, dc.document_id, dc.title, dc.content,
                               ts_rank(dc.text_search_enriched, {method}('dutch', %s) || {method}('simple', %s)) as similarity_score,
                               dc.child_id, dc.chunk_type
                        FROM document_chunks dc
                        {vn_join}
                        {date_join}
                        WHERE dc.text_search_enriched @@ ({method}('dutch', %s) || {method}('simple', %s))
                        {type_filter}
                        {vn_filter}
                        {date_filter}
                        ORDER BY similarity_score DESC
                        LIMIT %s
                    """, params)
                    rows = cursor.fetchall()

            results = []
            for cid, doc_id, title, content, score, child_id, c_type in rows:
                results.append(RetrievedChunk(
                    chunk_id=cid,
                    document_id=str(doc_id),
                    title=title,
                    content=content,
                    similarity_score=float(score) if score else 0.0,
                    child_id=child_id,
                    stream_type=c_type
                ))
            return results
        except Exception:
            logger.exception("Chunk keyword search failed")
            return []
    
    def _get_chunk_questions(self, chunk_id: int) -> List[str]:
        """Get hypothetical questions for a chunk"""
        try:
            with get_connection() as conn:
                with conn.cursor() as cursor:
                    cursor.execute("""
                        SELECT question_text
                        FROM chunk_questions
                        WHERE chunk_id = %s
                        ORDER BY id
                    """, (chunk_id,))

                    questions = [row[0] for row in cursor.fetchall()]

            return questions

        except Exception:
            logger.exception("_get_chunk_questions failed")
            return []
            
    def get_parent_context(self, child_id: int, cursor=None) -> Optional[str]:
        """Fetch the full Parent chunk (Child section) from document_children by its ID."""
        try:
            if cursor:
                cursor.execute("SELECT content FROM document_children WHERE id = %s", (child_id,))
                row = cursor.fetchone()
                return row[0] if row else None

            with get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT content FROM document_children WHERE id = %s", (child_id,))
                    row = cur.fetchone()
                    return row[0] if row else None
        except Exception as e:
            print(f"Failed to fetch parent context: {e}")
            return None

    def synthesize_timeline(self, chunks: List[RetrievedChunk]) -> List[Dict]:
        """
        Group retrieved chunks by year and return a chronologically sorted list of event buckets.
        Used by the MCP tijdlijn_besluitvorming tool so Claude can reason over the timeline.

        Returns:
            List of dicts: [{"periode": "2022", "gebeurtenissen": [{"titel", "snippet", ...}]}]
        """
        from collections import defaultdict
        buckets: Dict[str, list] = defaultdict(list)
        for chunk in chunks:
            year = (chunk.start_date or "0000-00-00")[:4]
            buckets[year].append({
                "titel": chunk.title,
                "snippet": chunk.content[:300],
                "document_id": chunk.document_id,
                "chunk_id": str(chunk.chunk_id),
                "stream_type": chunk.stream_type,
            })
        return [{"periode": y, "gebeurtenissen": e} for y, e in sorted(buckets.items())]

    def format_retrieved_context(self, chunks: List[RetrievedChunk], storage_service: Any = None) -> tuple[str, List[Dict], str]:
        """
        Alias for expand_to_hierarchical_context to match AIService expectations.
        """
        return self.expand_to_hierarchical_context(chunks, storage_service)

    def expand_to_hierarchical_context(self, chunks: List[RetrievedChunk], storage_service: Any = None) -> tuple[str, List[Dict], str]:
        """
        Smart Aggregator: Collapses fragments into Parents or Grandparents.
        Returns (context_string, ordered_sources_metadata, raw_text_for_verification).
        """
        if not chunks:
            return "", [], ""
            
        total_hits = len(chunks)
        doc_stats = {}
        parent_stats = {}
        
        # Statistics
        for c in chunks:
            doc_id = str(c.document_id)
            doc_stats[doc_id] = doc_stats.get(doc_id, 0) + 1
            
            p_id = c.child_id
            if p_id:
                parent_stats[p_id] = parent_stats.get(p_id, 0) + 1

        processed_docs = set()
        processed_parents = set()
        ordered_sources = []
        final_context = "HIËRARCHISCH GEORGANISEERDE CONTEXT (GEKRIMPTE EN GETRAPTE RETRIEVAL):\n"
        final_context += "=" * 80 + "\n\n"
        verification_content = ""
        
        idx = 1
        
        # Use a single pooled connection for all lookups in this loop
        with get_connection() as conn:
            cursor = conn.cursor()
            try:
                for c in chunks:
                    doc_id = str(c.document_id)
                    p_id = c.child_id

                    # GRANDPARENT TIER: Full document expansion
                    if doc_stats[doc_id] / total_hits > 0.35:
                        if doc_id in processed_docs:
                            continue

                        content = None
                        if storage_service:
                            content = storage_service.get_document_full_content(doc_id)
                        else:
                            cursor.execute("SELECT content FROM documents WHERE id = %s", (doc_id,))
                            row = cursor.fetchone()
                            content = row[0] if row else None

                        if content:
                            stream_map = {
                                'vision': 'POLITIEK PROGRAMMA / PARTIJVISIE',
                                'debate': 'NOTULEN / DEBATSDYNAMIEK',
                                'financial': 'FINANCIËLE HOOFDLIJNEN',
                                'fact': 'FEITELIJKE CONTEXT'
                            }
                            lbl = stream_map.get(c.stream_type, c.stream_type.upper()) if c.stream_type else "ALGEMEEN"

                            final_context += f"[{idx}] VOLLEDIG DOCUMENT [{lbl}]: {c.title}\n"
                            final_context += f"    Status: Sleuteldocument (>35% relevantie)\n"
                            date_str = getattr(c, 'start_date', 'Onbekend')
                            final_context += f"    Datum: {date_str}\n\n"

                            full_text = content[:40000]
                            final_context += f"{full_text}\n"
                            final_context += "\n" + "-" * 80 + "\n\n"

                            verification_content += "\n" + full_text

                            ordered_sources.append({
                                "id": doc_id,
                                "name": f"{c.title} (Volledig Document)",
                                "url": getattr(c, 'url', '#'),
                                "text": full_text,
                                "type": c.stream_type
                            })
                            processed_docs.add(doc_id)
                            idx += 1
                        continue

                    # PARENT TIER: Full section expansion
                    should_expand_parent = (p_id and parent_stats[p_id] > 1) or (p_id and c.stream_type == 'debate')

                    if should_expand_parent:
                        if p_id in processed_parents:
                            continue

                        content = self.get_parent_context(p_id, cursor=cursor)
                        if content:
                            # Attribution header
                            doc_header = ""
                            if storage_service:
                                full_content = storage_service.get_document_full_content(doc_id)
                                if full_content: doc_header = full_content[:1000]
                            else:
                                cursor.execute("SELECT content FROM documents WHERE id = %s", (doc_id,))
                                row = cursor.fetchone()
                                if row: doc_header = row[0][:1000]

                            stream_map = {
                                'vision': 'POLITIEK PROGRAMMA / PARTIJVISIE',
                                'debate': 'NOTULEN / DEBATSDYNAMIEK',
                                'financial': 'FINANCIËLE HOOFDLIJNEN',
                                'fact': 'FEITELIJKE CONTEXT'
                            }
                            lbl = stream_map.get(c.stream_type, c.stream_type.upper()) if c.stream_type else "ALGEMEEN"

                            final_context += f"[{idx}] VOLLEDIGE SECTIE [{lbl}]: {c.title}\n"
                            final_context += f"    Status: Meerdere tekstsnippers gevonden (Parent)\n"
                            date_str = getattr(c, 'start_date', 'Onbekend')
                            final_context += f"    Datum: {date_str}\n\n"
                            final_context += f"--- DOCUMENT START / HEADERS ---\n{doc_header}\n--- SECTION START ---\n"
                            final_context += f"{content}\n"
                            final_context += "\n" + "-" * 80 + "\n\n"

                            verification_content += "\n" + content
                            ordered_sources.append({
                                "id": doc_id,
                                "name": f"{c.title} (Sectie uit document)",
                                "url": getattr(c, 'url', '#'),
                                "text": content,
                                "type": c.stream_type
                            })
                            processed_parents.add(p_id)
                            idx += 1
                        continue

                    # GRANDCHILD TIER: Single fragment (Isolated hit)
                    # Fetch document header for attribution
                    doc_header = ""
                    if doc_id not in processed_docs:
                        if storage_service:
                            full_content = storage_service.get_document_full_content(doc_id)
                            if full_content: doc_header = full_content[:1000]
                        else:
                            cursor.execute("SELECT content FROM documents WHERE id = %s", (doc_id,))
                            row = cursor.fetchone()
                            if row: doc_header = row[0][:1000]

                    stream_map = {
                        'vision': 'POLITIEK PROGRAMMA / PARTIJVISIE',
                        'debate': 'NOTULEN / DEBATSDYNAMIEK',
                        'financial': 'FINANCIËLE HOOFDLIJNEN',
                        'fact': 'FEITELIJKE CONTEXT'
                    }
                    lbl = stream_map.get(c.stream_type, c.stream_type.upper()) if c.stream_type else "ALGEMEEN"

                    final_context += f"[{idx}] SPECIFIEK FRAGMENT [{lbl}]: {c.title}\n"
                    date_str = getattr(c, 'start_date', 'Onbekend')
                    final_context += f"    Datum: {date_str}\n\n"
                    final_context += f"--- DOCUMENT START / HEADERS ---\n{doc_header}\n--- FRAGMENT START ---\n"
                    final_context += f"{c.content}\n"
                    final_context += "\n" + "-" * 80 + "\n\n"

                    verification_content += "\n" + c.content
                    ordered_sources.append({
                        "id": doc_id,
                        "name": c.title,
                        "url": getattr(c, 'url', '#'),
                        "text": c.content,
                        "type": c.stream_type
                    })
                    idx += 1
            finally:
                cursor.close()

        return final_context, ordered_sources, verification_content
