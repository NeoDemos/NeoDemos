import json
import logging
import hashlib
import re
from typing import List, Dict, Any, Optional
from pathlib import Path
import psycopg2
from psycopg2.extras import execute_values
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

from services.ai_service import AIService
from services.local_ai_service import LocalAIService
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

class SmartIngestor:
    """
    Handles tiered semantic chunking and RAG ingestion for all documents.
    Implements a 3-tier strategy:
    - Atomic (< 1,000 chars): No chunking.
    - Linear (1,000 - 8,000 chars): 1 Child -> Semantic Grandchildren.
    - Hierarchical (> 8,000 chars): Semantic Children -> Semantic Grandchildren.
    """

    def __init__(self, db_url: str = "postgresql://postgres:postgres@localhost:5432/neodemos"):
        load_dotenv()
        self.db_url = db_url
        self.ai = AIService()
        self.local_ai = LocalAIService()  # Initialize local MLX model
        self.qdrant = QdrantClient(path="./data/qdrant_storage")
        self.collection_name = "notulen_chunks_local"  # NEW COLLECTION FOR LOCAL EMBEDDINGS
        self.embedding_model = "Qwen3-Embedding-8B-MLX"
        self.chunk_model = "gemini-2.5-flash-lite"
        self.atomic_threshold = 1000
        self.child_max_chars = 16000
        self.grandchild_max_chars = 1000
        self.heuristic = False

    def ingest_document(self, doc_id: str, doc_name: str, content: str, meeting_id: str = None, metadata: Dict = None, category: str = "municipal_doc"):
        """
        Generic entry point for ingesting any document.
        """
        logger.info(f"Ingesting document: {doc_name} (ID: {doc_id}) | Category: {category}")
        conn = psycopg2.connect(self.db_url)
        try:
            # --- Cleanup Phase ---
            cur = conn.cursor()
            cur.execute("SELECT id FROM document_children WHERE document_id = %s", (doc_id,))
            child_ids = [r[0] for r in cur.fetchall()]
            if child_ids:
                logger.info(f"  Cleaning up {len(child_ids)} existing child sections and chunks...")
                cur.execute("DELETE FROM document_chunks WHERE child_id = ANY(%s)", (child_ids,))
                cur.execute("DELETE FROM document_children WHERE id = ANY(%s)", (child_ids,))
            
            cur.execute("DELETE FROM document_chunks WHERE document_id = %s AND child_id IS NULL", (doc_id,))
            conn.commit()
            cur.close()

            # 0. Ensure Parent Document Record (Foreign Key fix)
            agenda_item_id = metadata.get('agenda_item_id') if metadata else None
            self._ensure_document_record(conn, doc_id, doc_name, meeting_id, content, agenda_item_id=agenda_item_id, category=category)

            doc_size = len(content)
            
            # --- 3-TIER STRATEGY ---
            if doc_size < self.atomic_threshold:
                # 1. ATOMIC TIER: No chunking
                logger.info(f"  Atomic Tier: {doc_size} chars. Skipping split.")
                self._process_tier_block(conn, doc_id, doc_name, meeting_id, content, metadata, is_atomic=True)
            elif doc_size <= self.child_max_chars:
                # 2. LINEAR TIER: 1 Child -> Semantic split for matches
                logger.info(f"  Linear Tier: {doc_size} chars. 1 Child -> Grandchildren.")
                self._process_tier_block(conn, doc_id, doc_name, meeting_id, content, metadata, is_atomic=False)
            else:
                # 3. HIERARCHICAL TIER: Split into Children -> Semantic split for matches
                logger.info(f"  Hierarchical Tier: {doc_size} chars. Multi-child split.")
                child_sections = self._split_text(content, self.child_max_chars)
                for idx, section_content in enumerate(child_sections):
                    self._process_tier_block(conn, doc_id, doc_name, meeting_id, section_content, metadata, idx_offset=idx)
            
            logger.info(f"Successfully ingested document: {doc_id}")
        finally:
            conn.close()

    def _process_tier_block(self, conn, doc_id, doc_name, meeting_id, content, metadata, is_atomic=False, idx_offset=0):
        """Helper to process a block into Child and Grandchild tiers."""
        cur = conn.cursor()
        
        meta_json = json.dumps(metadata or {})
        cur.execute("""
            INSERT INTO document_children (document_id, chunk_index, content, metadata)
            VALUES (%s, %s, %s, %s) RETURNING id
        """, (doc_id, idx_offset, content, meta_json))
        child_id = cur.fetchone()[0]
        conn.commit()

        if is_atomic:
            # Store itself as a single grandchild
            grandchildren = [{
                "title": doc_name,
                "text": content,
                "questions": [],
                "chunk_type": "full_text"
            }]
        elif self.heuristic and len(content) < 2000:
            # Use heuristic for small blocks to save costs
            grandchildren = self._heuristic_chunking(content, doc_name)
        else:
            # Use Local LLM (or Gemini) for large blocks or when explicitly requested
            grandchildren = self._semantic_chunking_via_gemini(content, doc_name)

        if grandchildren:
            self._store_grandchildren(conn, doc_id, doc_name, str(meeting_id), grandchildren, child_id)
        
        cur.close()

    def ingest_transcript(self, transcript_data: Dict[str, Any], heuristic: bool = False, category: str = "committee_transcript"):
        """
        Specialized transcript ingestion that preserve speaker context.
        """
        self.heuristic = heuristic
        meeting_id = transcript_data.get("meeting_id")
        meeting_name = transcript_data.get("meeting_name", "Unknown Meeting")
        doc_id = f"transcript_{meeting_id}"
        
        # Flatten transcript into items
        agenda_items = transcript_data.get("agenda_items", [])
        for item in agenda_items:
            item_title = item.get("title", "Untitled")
            
            full_text_blocks = []
            segments = item.get("segments", [])
            
            # Calculate average confidence for the agenda item to determine its overall tier
            segment_confidences = [s.get("confidence", 1.0) for s in segments if s.get("text")]
            avg_conf = sum(segment_confidences) / len(segment_confidences) if segment_confidences else 1.0
            
            # Determine Audio Tier (Gold/Silver/Bronze)
            if avg_conf >= 0.85:
                audio_tier = "gold"
            elif avg_conf >= 0.60:
                audio_tier = "silver"
            else:
                audio_tier = "bronze"

            for seg in segments:
                speaker = seg.get("speaker", "Unknown")
                party = seg.get("party", "")
                text = seg.get("text", "").strip()
                
                # --- Quality Filtering ---
                # Skip "Unknown" speakers with very short text (< 50 chars)
                if not text or (speaker == "Unknown" and len(text) < 50):
                    continue

                if text:
                    speaker_str = f"{speaker}"
                    if party:
                        speaker_str += f" ({party})"
                    
                    # Tag Silver/Bronze segments in text for LLM context
                    prefix = f"[{speaker_str}]"
                    if audio_tier != "gold":
                        prefix += f" [Audio:{audio_tier}]"
                    
                    full_text_blocks.append(f"{prefix}: {text}")

            total_text = "\n\n".join(full_text_blocks)
            if total_text:
                self.ingest_document(
                    doc_id=f"{doc_id}_{hashlib.md5(item_title.encode()).hexdigest()[:8]}",
                    doc_name=f"Transcript: {item_title}",
                    content=total_text,
                    meeting_id=meeting_id,
                    metadata={
                        "agenda_item": item_title, 
                        "type": "transcript_segment",
                        "audio_tier": audio_tier,
                        "avg_confidence": f"{avg_conf:.2f}"
                    },
                    category=category
                )

    def _ensure_document_record(self, conn, doc_id: str, doc_name: str, meeting_id: str, content: str, agenda_item_id: str = None, category: str = "municipal_doc"):
        """Creates or updates the parent document record and its assignment."""
        cur = conn.cursor()
        
        # Check if meeting exists
        if meeting_id:
            cur.execute("SELECT 1 FROM meetings WHERE id = %s", (meeting_id,))
            if not cur.fetchone():
                logger.warning(f"Meeting {meeting_id} not found in DB. Ingesting as orphan document.")
                meeting_id = None
            else:
                # Update meeting category if specified (for test tagging)
                if category != "municipal_doc":
                    cur.execute("UPDATE meetings SET category = %s WHERE id = %s", (category, meeting_id))

        cur.execute("""
            INSERT INTO documents (id, name, meeting_id, content, category)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                name = EXCLUDED.name,
                content = EXCLUDED.content,
                category = EXCLUDED.category
        """, (doc_id, doc_name, meeting_id, content[:1000], category))
        
        # Handle assignment
        if meeting_id or agenda_item_id:
            cur.execute("""
                INSERT INTO document_assignments (document_id, meeting_id, agenda_item_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (document_id, meeting_id, agenda_item_id) DO NOTHING
            """, (doc_id, meeting_id, agenda_item_id))
            
        conn.commit()
        cur.close()

    def _split_text(self, text: str, max_chars: int) -> List[str]:
        """Speaker-aware split into sections."""
        if len(text) <= max_chars: return [text]
        sections = []
        start = 0
        while start < len(text):
            end = start + max_chars
            if end >= len(text):
                sections.append(text[start:])
                break
                
            # 1. Try to split at a new speaker tag
            last_break = text.rfind("\n[", start, end)
            
            # 2. Fallback to double newline if no speaker tag found
            if last_break == -1 or last_break <= start:
                last_break = text.rfind("\n\n", start, end)
                
            # 3. Fallback to single newline
            if last_break == -1 or last_break <= start:
                last_break = text.rfind("\n", start, end)
                
            # 4. Hard cut
            if last_break == -1 or last_break <= start:
                last_break = end
                
            sections.append(text[start:last_break].strip())
            start = last_break
        return sections

    def _heuristic_chunking(self, content: str, title: str) -> List[Dict]:
        """Split text into ~1K chunks without API calls."""
        paragraphs = content.split("\n\n")
        chunks = []
        current_chunk_text = ""
        for para in paragraphs:
            if len(current_chunk_text) + len(para) < self.grandchild_max_chars:
                current_chunk_text += (para + "\n\n")
            else:
                if current_chunk_text:
                    chunks.append({"title": title, "text": current_chunk_text.strip(), "questions": [], "chunk_type": "quote"})
                if len(para) > self.grandchild_max_chars:
                    sentences = re.split(r'(?<=[.!?])\s+', para)
                    sub_text = ""
                    for sent in sentences:
                        if len(sub_text) + len(sent) < self.grandchild_max_chars:
                            sub_text += (sent + " ")
                        else:
                            if sub_text:
                                chunks.append({"title": title, "text": sub_text.strip(), "questions": [], "chunk_type": "quote"})
                            sub_text = sent + " "
                    current_chunk_text = sub_text
                else:
                    current_chunk_text = para + "\n\n"
        if current_chunk_text:
            chunks.append({"title": title, "text": current_chunk_text.strip(), "questions": [], "chunk_type": "quote"})
        return self._inject_speaker_prefixes(chunks)

    def _semantic_chunking_via_gemini(self, content: str, title: str) -> List[Dict]:
        """Calls Local LLM (if available) or Gemini to split the child section into semantic 1K chunks."""
        prompt = f"""You are chunking a Dutch municipal document for a RAG system.
Each chunk must be a meaningful semantic unit (e.g., one complete point or statement).

CRITICAL RULES:
1. Every chunk MUST be under 1,000 characters.
2. DON'T change a single word. DON'T summarize.
3. If this is a transcript (contains [Speaker] tags), YOU MUST REPEAT the prefix at the beginning of EVERY resulting chunk.
4. Cover ALL content — every word must appear in exactly one chunk.

Return ONLY a valid JSON array. Each item must have:
- "title": string (brief topic of this chunk)
- "text": string (the EXACT, UNMODIFIED text)
- "questions": string[] (3-5 hypothetical questions this chunk answers)
- "chunk_type": "quote"

DOCUMENT TO CHUNK (Topic: {title}):

{content}

Return ONLY the JSON array."""

        try:
            # TRY LOCAL MLX MODEL FIRST
            if self.local_ai.is_available():
                logger.info("Using Local MLX LLM (Qwen) for semantic chunking...")
                response_text = self.local_ai.generate_content(prompt)
            else:
                logger.info("Using Gemini API for semantic chunking...")
                response = self.ai.client.models.generate_content(model=self.chunk_model, contents=prompt)
                response_text = response.text or ""

            if not response_text:
                raise ValueError("No response from LLM")

            j_start = response_text.find('[')
            j_end = response_text.rfind(']') + 1
            if j_start == -1 or j_end == 0: return []
            from json_repair import repair_json
            repaired = repair_json(response_text[j_start:j_end])
            chunks = json.loads(repaired)
            return self._inject_speaker_prefixes(chunks)
        except Exception as e:
            logger.error(f"LLM chunking error: {e}. Falling back to heuristic chunking.")
            return self._heuristic_chunking(content, title)

    def _inject_speaker_prefixes(self, chunks: List[Dict]) -> List[Dict]:
        """Ensures every chunk starts with a [Speaker]: tag if present in the block."""
        processed = []
        last_speaker_tag = None
        for chunk in chunks:
            text = chunk.get("text", "").strip()
            match = re.match(r"^(\[.*?\]:)", text)
            if match:
                last_speaker_tag = match.group(1)
            elif last_speaker_tag:
                chunk["text"] = f"{last_speaker_tag} {text}"
            processed.append(chunk)
        return processed

    def _store_grandchildren(self, conn, doc_id: str, doc_name: str, meeting_id: str, chunks: List[Dict], child_id: int):
        """Embeds and stores grandchildren in Qdrant and Postgres."""
        cur = conn.cursor()
        points = []
        pg_data = []
        for idx, chunk in enumerate(chunks):
            text = chunk.get("text", "").strip()
            if len(text) < 20: continue
            title = chunk.get("title", "Untitled")
            questions = chunk.get("questions", [])
            context_str = f"[Document: {doc_name} | Section: {title}]\n"
            embedding_text = context_str + text
            
            # --- LOCAL EMBEDDING SWITCH ---
            if self.local_ai.is_available():
                embedding = self.local_ai.generate_embedding(embedding_text)
            else:
                embedding = self.ai.generate_embedding(embedding_text)
            
            if embedding is None: continue
            hash_str = hashlib.md5(f"{doc_id}_{child_id}_{idx}".encode()).hexdigest()
            point_id = int(hash_str[:15], 16)
            payload = {
                "document_id": doc_id, "doc_name": doc_name, "doc_type": "municipal_doc",
                "meeting_id": meeting_id, "child_id": child_id, "chunk_index": idx,
                "chunk_type": "quote", "title": title, "content": text, "questions": questions
            }
            points.append(PointStruct(id=point_id, vector=embedding, payload=payload))
            pg_data.append((doc_id, idx, title, text, "quote", None, int(len(text)/4), child_id))

        if points:
            self.qdrant.upsert(collection_name=self.collection_name, points=points)
        if pg_data:
            execute_values(cur, """
                INSERT INTO document_chunks 
                    (document_id, chunk_index, title, content, chunk_type, table_json, tokens_estimated, child_id)
                VALUES %s
                ON CONFLICT (document_id, chunk_index) DO UPDATE SET
                    content = EXCLUDED.content, title = EXCLUDED.title, child_id = EXCLUDED.child_id
            """, pg_data)
        conn.commit()
        cur.close()
        logger.info(f"    ✓ Stored {len(pg_data)} Grandchild chunks")
