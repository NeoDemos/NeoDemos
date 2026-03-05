"""
RAG (Retrieval Augmented Generation) Service

Retrieves relevant historical context from notulen for agenda items.
Uses vector similarity search when embeddings are available, falls back to keyword search.
"""

import psycopg2
from typing import List, Dict, Any, Optional, Union
from dataclasses import dataclass


@dataclass
class RetrievedChunk:
    """A chunk of text retrieved from the database"""
    chunk_id: Any  # Can be int, str, or UUID from Qdrant
    document_id: str
    title: str
    content: str
    similarity_score: float = 1.0  # For keyword search
    questions: Optional[List[str]] = None


class RAGService:
    """Retrieve relevant notulen context for agenda items"""
    
    def __init__(self):
        self.db_connection_string = "postgresql://postgres:postgres@localhost:5432/neodemos"
    
    def retrieve_relevant_context(
        self, 
        query_text: str, 
        query_embedding: Optional[List[float]] = None,
        top_k: int = 10,
        fallback_to_keywords: bool = True
    ) -> List[RetrievedChunk]:
        """
        Retrieve relevant notulen passages for an agenda item.
        
        Strategy:
        1. If query embedding provided: Use vector similarity on document_chunks table
        2. Fallback: Use keyword search on full notulen documents
        
        Args:
            query_text: The agenda item text to find relevant context for
            query_embedding: Optional vector embedding of the query
            top_k: Number of results to return
            fallback_to_keywords: Fall back to keyword search if no vector results
        
        Returns:
            List of RetrievedChunk objects with relevant notulen passages
        """
        
        results = []
        
        # Try vector similarity search first if embedding provided
        if query_embedding is not None:
            results = self._retrieve_by_vector_similarity(query_embedding, top_k)
        
        # Fall back to keyword search if not enough results or no embedding
        if len(results) < top_k and fallback_to_keywords:
            keyword_results = self._retrieve_by_keywords(query_text, top_k - len(results))
            results.extend(keyword_results)
        
        return results[:top_k]
    
    def _retrieve_by_vector_similarity(
        self, 
        query_embedding: List[float], 
        top_k: int = 10
    ) -> List[RetrievedChunk]:
        """
        Search document_chunks table using Qdrant vector similarity.
        Falls back to keyword search if Qdrant is not available.
        """
        try:
            from qdrant_client import QdrantClient
            
            # Connect to Qdrant
            qdrant_client = QdrantClient(url="http://localhost:6333")
            
            # Search in Qdrant collection using query_points
            results_qdrant = qdrant_client.query_points(
                collection_name="notulen_chunks",
                query=query_embedding,
                limit=top_k,
                score_threshold=0.5  # Only return results with similarity > 0.5
            )
            
            # Convert Qdrant results to RetrievedChunk objects
            results = []
            for scored_point in results_qdrant.points:
                payload = scored_point.payload or {}
                questions = payload.get("questions") if isinstance(payload.get("questions"), list) else []
                results.append(RetrievedChunk(
                    chunk_id=scored_point.id,
                    document_id=str(payload.get("document_id", "unknown")),
                    title=str(payload.get("title", "Untitled")),
                    content=str(payload.get("content", "")),
                    similarity_score=float(scored_point.score) if scored_point.score else 0.5,
                    questions=questions
                ))
            
            return results
            
        except Exception as e:
            print(f"Vector similarity search failed (Qdrant not available): {e}")
            return []  # Fall back to keyword search
    
    def _retrieve_by_keywords(
        self, 
        query_text: str, 
        top_k: int = 10
    ) -> List[RetrievedChunk]:
        """
        Keyword-based search on full notulen documents.
        Searches document content for keywords from the query.
        """
        try:
            conn = psycopg2.connect(self.db_connection_string)
            cursor = conn.cursor()
            
            # Extract keywords from query (simple approach: split on spaces)
            keywords = query_text.lower().split()[:10]  # First 10 words
            
            # Build keyword search: look for documents mentioning multiple keywords
            keyword_where = " OR ".join([f"d.content ILIKE %s" for _ in keywords])
            keyword_params = [f"%{kw}%" for kw in keywords]
            
            # Search for notulen documents matching keywords
            cursor.execute(f"""
                SELECT 
                    d.id,
                    d.id,  -- chunk_id = document_id for full documents
                    d.name,
                    d.content,
                    1.0 as similarity_score
                FROM documents d
                WHERE d.name ILIKE '%notule%' 
                AND d.content IS NOT NULL
                AND ({keyword_where})
                ORDER BY d.id
                LIMIT %s
            """, keyword_params + [top_k])
            
            rows = cursor.fetchall()
            cursor.close()
            conn.close()
            
            # Convert to RetrievedChunk objects
            # For full documents, return as single chunk
            results = []
            for doc_id, chunk_id, title, content, sim_score in rows:
                results.append(RetrievedChunk(
                    chunk_id=chunk_id,
                    document_id=doc_id,
                    title=title,
                    content=content,
                    similarity_score=sim_score,
                    questions=[]
                ))
            
            return results
            
        except Exception as e:
            print(f"Keyword search failed: {e}")
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
    
    def format_retrieved_context(self, chunks: List[RetrievedChunk]) -> str:
        """
        Format retrieved chunks for inclusion in LLM prompt.
        
        Returns a formatted string with all chunks and their metadata.
        """
        if not chunks:
            return ""
        
        formatted = "RELEVANTE HISTORISCHE CONTEXT UIT GEMEENTERAADSNOTULEN:\n"
        formatted += "=" * 70 + "\n\n"
        
        for i, chunk in enumerate(chunks, 1):
            formatted += f"[{i}] {chunk.title}\n"
            formatted += f"    Document: {chunk.document_id}\n"
            if chunk.questions:
                formatted += f"    Gerelateerde vragen: {', '.join(chunk.questions[:2])}\n"
            formatted += f"\n{chunk.content}\n\n"
            formatted += "-" * 70 + "\n\n"
        
        return formatted
