import psycopg2
import asyncio
import threading
from typing import List, Dict, Any, Optional, Union
from dataclasses import dataclass

# Shared class-level resources to avoid redundant loading and locking issues
_qdrant_client = None
_reranker = None
_init_lock = threading.Lock()

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

from services.local_ai_service import LocalAIService

class RAGService:
    """Retrieve relevant notulen context for agenda items"""
    
    def __init__(self):
        self.db_connection_string = "postgresql://postgres:postgres@localhost:5432/neodemos"
        self.local_ai = LocalAIService()
        self._ensure_resources_initialized()

    def _ensure_resources_initialized(self):
        global _qdrant_client, _reranker
        if _qdrant_client is not None and _reranker is not None:
            return

        with _init_lock:
            if _qdrant_client is None:
                try:
                    from qdrant_client import QdrantClient
                    print("Initializing QdrantClient (Server Mode at localhost:6333)...")
                    _qdrant_client = QdrantClient(url="http://localhost:6333")
                except Exception as e:
                    print(f"Failed to initialize QdrantClient: {e}")

            if _reranker is None:
                try:
                    from sentence_transformers import CrossEncoder
                    import os
                    print("Loading Reranker (CrossEncoder) for the first time...")
                    os.environ['TOKENIZERS_PARALLELISM'] = 'false'
                    _reranker = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2', device='cpu')
                except Exception as e:
                    print(f"Failed to initialize Reranker: {e}")

    async def retrieve_parallel_context(
        self, 
        query_text: str, 
        query_embedding: Optional[List[float]] = None,
        distribution: Dict[str, int] = {"financial": 3, "debate": 3, "fact": 2, "vision": 2},
        overrides: Optional[Dict[str, str]] = None
    ) -> List[RetrievedChunk]:
        """
        Executes parallel searches for different dimensions of the query.
        Accepts optional 'overrides' dict with keys 'financial', 'debate', 'fact' for LLM-rewritten queries.
        """
        tasks = []
        
        # Vision Stream (Ideology) - High priority to capture vision chunks before deduplication
        vision_query = overrides.get("vision") if overrides else f"{query_text} standpunt visie ideaal ideologie programma"
        tasks.append(self._async_retrieve(vision_query or f"{query_text} visie", query_embedding, distribution.get("vision", 2), "vision"))

        # Financial Stream
        financial_query = overrides.get("financial") if overrides else f"{query_text} begroting budget kosten cijfers"
        tasks.append(self._async_retrieve(financial_query or f"{query_text} begroting", query_embedding, distribution.get("financial", 3), "financial"))
        
        # Debate Stream
        debate_query = overrides.get("debate") if overrides else f"{query_text} debat standpunten raadsleden uitspraken"
        tasks.append(self._async_retrieve(debate_query or f"{query_text} debat", query_embedding, distribution.get("debate", 3), "debate"))
        
        # Fact Stream
        fact_query = overrides.get("fact") if overrides else f"{query_text} beleid regels technische details definities"
        tasks.append(self._async_retrieve(fact_query or f"{query_text} beleid", query_embedding, distribution.get("fact", 2), "fact"))
        
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
        
        return all_chunks

    async def _async_retrieve(self, query: str, embedding: Optional[List[float]], k: int, stream_type: str) -> List[RetrievedChunk]:
        """Helper to run synchronous retrieval in a thread."""
        chunks = await asyncio.to_thread(self.retrieve_relevant_context, query, embedding, k)
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
        fallback_to_keywords: bool = True
    ) -> List[RetrievedChunk]:
        """
        Retrieve relevant notulen passages for an agenda item using True Hybrid Search.
        """
        vector_results = []
        keyword_results = []
        
        # 1. Fetch from Vector Search
        if query_embedding is None and self.local_ai.is_available():
            # Generate embedding locally if not provided
            query_embedding = self.local_ai.generate_embedding(query_text)

        if query_embedding is not None:
            vector_results = self._retrieve_by_vector_similarity(query_embedding, top_k=top_k * 3)
            
        # 2. Fetch from BM25 Search
        if fallback_to_keywords:
            vision_sigs = ['visie', 'standpunt', 'programma', 'ideologie', 'leefbaar', 'pvda', 'vvd', 'd66', 'glpvda', 'volt', 'cda', 'denk', 'sp', 'christenunie', 'bij1']
            is_vision = any(sig in query_text.lower() for sig in vision_sigs)
            
            chunk_type = "vision" if is_vision else None
            # Search specific chunk types first or default chunks
            keyword_results = self._retrieve_chunks_by_keywords(query_text, top_k * 3, chunk_type)
            
            # If strict websearch returns too few results, try broader queries
            if len(keyword_results) < top_k:
                # Try plain search (AND logic but softer than websearch)
                keyword_results.extend(self._retrieve_chunks_by_keywords(query_text, top_k * 2, chunk_type, mode='plain'))
                
                # Try OR-logic for key terms (highest recall)
                # Filter out small common words and join with |
                terms = [t for t in query_text.replace('?', '').split() if len(t) > 3]
                if terms:
                    or_query = " | ".join(terms)
                    keyword_results.extend(self._retrieve_chunks_by_keywords(or_query, top_k * 2, chunk_type, mode='or'))
            
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
            
        fused_chunks = fused_chunks[:top_k * 2]
            
        # 4. Rerank using Cross-Encoder
        if _reranker is not None:
            try:
                pairs = [[query_text, chunk.content] for chunk in fused_chunks]
                if pairs:
                    scores = _reranker.predict(pairs)
                    for chunk, score in zip(fused_chunks, scores):
                        chunk.similarity_score = float(score)
                    fused_chunks.sort(key=lambda x: x.similarity_score, reverse=True)
            except Exception as e:
                print(f"Reranking failed: {e}")
            
        return fused_chunks[:top_k]
    
    def _retrieve_by_vector_similarity(
        self, 
        query_embedding: List[float], 
        top_k: int = 10
    ) -> List[RetrievedChunk]:
        """
        Search document_chunks table using Qdrant vector similarity.
        """
        if query_embedding is None:
            return []

        if _qdrant_client is None:
            return []

        try:
            # Search in Qdrant collection
            results_qdrant = _qdrant_client.query_points(
                collection_name="notulen_chunks", # TARGET THE EXISTING COLLECTION
                query=query_embedding,
                limit=top_k,
                score_threshold=0.15
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
                    child_id=payload.get("child_id")
                ))
            
            return results
            
        except Exception as e:
            print(f"Vector similarity search failed: {e}")
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
        """
        try:
            conn = psycopg2.connect(self.db_connection_string)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT 
                    d.id,
                    d.id,  -- chunk_id = document_id for full documents
                    d.name,
                    d.content,
                    ts_rank(text_search, websearch_to_tsquery('dutch', %s)) as similarity_score
                FROM documents d
                WHERE d.name ILIKE '%%notule%%' 
                AND d.content IS NOT NULL
                AND text_search @@ websearch_to_tsquery('dutch', %s)
                ORDER BY similarity_score DESC
                LIMIT %s
            """, [query_text, query_text, top_k])
            
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            
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
            print(f"Keyword search failed: {e}")
            return []

    def _retrieve_chunks_by_keywords(self, query_text: str, top_k: int = 5, chunk_type: Optional[str] = None, mode: str = 'web') -> List[RetrievedChunk]:
        """
        Search document_chunks table directly using BM25.
        Modes: 'web' (AND), 'plain' (AND, soft), 'or' (OR logic).
        """
        try:
            conn = psycopg2.connect(self.db_connection_string)
            cursor = conn.cursor()
            
            type_filter = "AND chunk_type = %s" if chunk_type else ""
            params = [query_text]
            if chunk_type:
                params.append(chunk_type)
            params.append(top_k)
            
            if mode == 'or':
                method = "to_tsquery"
            elif mode == 'plain':
                method = "plainto_tsquery"
            else:
                method = "websearch_to_tsquery"
            
            cursor.execute(f"""
                SELECT id, document_id, title, content, 
                       ts_rank(text_search, {method}('dutch', %s)) as similarity_score, 
                       child_id, chunk_type
                FROM document_chunks
                WHERE text_search @@ {method}('dutch', %s)
                {type_filter}
                ORDER BY similarity_score DESC
                LIMIT %s
            """, [query_text, query_text] + ([chunk_type] if chunk_type else []) + [top_k])
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            
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
        except Exception as e:
            print(f"Chunk keyword search failed: {e}")
            return []
    
    def _get_chunk_questions(self, chunk_id: int) -> List[str]:
        """Get hypothetical questions for a chunk"""
        try:
            conn = psycopg2.connect(self.db_connection_string)
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT question_text
                FROM chunk_questions
                WHERE chunk_id = %s
                ORDER BY id
            """, (chunk_id,))
            
            questions = [row[0] for row in cursor.fetchall()]
            cursor.close()
            conn.close()
            
            return questions
            
        except:
            return []
            
    def get_parent_context(self, child_id: int, cursor=None) -> Optional[str]:
        """Fetch the full Parent chunk (Child section) from document_children by its ID."""
        try:
            if cursor:
                cursor.execute("SELECT content FROM document_children WHERE id = %s", (child_id,))
                row = cursor.fetchone()
                return row[0] if row else None
            
            conn = psycopg2.connect(self.db_connection_string)
            cursor_temp = conn.cursor()
            cursor_temp.execute("SELECT content FROM document_children WHERE id = %s", (child_id,))
            row = cursor_temp.fetchone()
            cursor_temp.close()
            conn.close()
            return row[0] if row else None
        except Exception as e:
            print(f"Failed to fetch parent context: {e}")
            return None

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
        
        # Use a single connection for all lookups in this loop
        try:
            conn = psycopg2.connect(self.db_connection_string)
            cursor = conn.cursor()
        except:
            cursor = None

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
                elif cursor:
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
                    elif cursor:
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
                elif cursor:
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
            
        if cursor:
            cursor.close()
            conn.close()
            
        return final_context, ordered_sources, verification_content
