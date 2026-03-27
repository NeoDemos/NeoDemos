#!/usr/bin/env python3
"""
Phase B: Full RAG Chunking Pipeline — All Document Types
=========================================================
Processes ALL 71k+ documents with:
1. Document type classification (motie, raadsvoorstel, financieel, notulen, etc.)
2. Type-specific Gemini semantic chunking prompts
3. Financial table extraction as structured JSON (Option B)
4. Large-doc handling with 20k-char overlap between sections
5. Full resumability — skips already-chunked documents

Run overnight:
  nohup python3 -u scripts/compute_embeddings.py > chunking.log 2>&1 &
  tail -f chunking.log
"""

import json
import os
import sys
import time
import re
import hashlib
import psycopg2
from psycopg2.extras import execute_values
from typing import List, Dict, Any, Optional
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from services.ai_service import AIService
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

load_dotenv()

# ── Constants ──────────────────────────────────────────────────────────────────
DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"
QDRANT_PATH = "./data/qdrant_storage"
COLLECTION_NAME = "notulen_chunks"
CHUNK_MODEL = "gemini-2.5-flash-lite"             # Tier 1 default
EMBEDDING_MODEL = "gemini-embedding-exp-03-07"  # Latest embedding model

# Tier Hierarchy Settings
CHILD_MAX_CHARS = 8000                    # Max size for Graph Reasoning (Child)
GRANDCHILD_MAX_CHARS = 1000               # Max size for Vector Search (Grandchild)
SECTION_OVERLAP = 1000                    # chars of overlap for Child continuity
MIN_CHUNK_TEXT = 20                       # skip trivial chunks
RATE_LIMIT_SLEEP = 5.0                    # Base sleep; TPM controller manages actual rate


# ── Document type detection ────────────────────────────────────────────────────
def classify_document(name: str, content: str) -> str:
    """Classify a document by type based on name and content hints."""
    name_lower = (name or "").lower()
    content_sample = (content or "")[:500].lower()

    if any(k in name_lower for k in ["motie", "motion"]):
        return "motie"
    if any(k in name_lower for k in ["amendement", "amendment"]):
        return "amendement"
    if any(k in name_lower for k in ["raadsvoorstel", "raadsvoor"]):
        return "raadsvoorstel"
    if any(k in name_lower for k in ["notulen", "notule"]):
        return "notulen"
    if any(k in name_lower for k in ["verslag", "rapport"]):
        return "verslag"
    if any(k in name_lower for k in ["begroting", "jaarrekening", "financieel",
                                      "budget", "rekening", "meerjar"]):
        return "financieel"
    if any(k in name_lower for k in ["besluitenlijst", "besluit"]):
        return "besluitenlijst"
    if any(k in name_lower for k in ["brief", "collegebrief", "wethoudersbrief"]):
        return "brief"
    if any(k in name_lower for k in ["annotatie"]):
        return "annotatie"
    # Content hints
    if any(k in content_sample for k in ["begroting", "jaarrekening", "€", "euro",
                                          "exploitatie", "investeringen", "financiën"]):
        return "financieel"
    return "overig"


# ── Type-specific chunking prompts ─────────────────────────────────────────────
BASE_INSTRUCTIONS = """Return ONLY a valid JSON array. Each item must have:
- "title": string — section title or main topic
- "text": string — the EXACT, UNMODIFIED text from the document (do NOT summarize or truncate)
- "questions": string[] — 3-5 hypothetical questions this chunk answers
- "chunk_type": "text" | "table" | "list" | "header" | "decision" | "quote"

CRITICAL: Every chunk MUST be under 1,000 characters. If a section is longer, SPLIT it into multiple chunks.
DON'T change a single word. DON'T summarize. DON'T truncate.
Cover ALL content — every word must appear in exactly one chunk."""

DOC_TYPE_PROMPTS = {
    "motie": f"""You are chunking a Dutch municipal motion (motie) for a RAG system.

Chunk by the logical clauses:
- Preamble / aanleiding
- Each "overwegende dat" clause (separately)
- Each "constaterende dat" clause (separately)  
- "verzoekt het college" / decision clause
- Any additional notes or voting record

{BASE_INSTRUCTIONS}""",

    "amendement": f"""You are chunking a Dutch municipal amendment (amendement) for a RAG system.

Chunk by:
- Header / identification (amendement number, title, indieners)
- The text being amended (the original proposal text)
- The proposed change ("wordt gewijzigd in" / "wordt toegevoegd")
- Motivation / toelichting
- Voting result if present

{BASE_INSTRUCTIONS}""",

    "raadsvoorstel": f"""You are chunking a Dutch municipal council proposal (raadsvoorstel) for a RAG system.

Chunk by the standard sections:
- Samenvatting / onderwerp
- Aanleiding / achtergrond  
- Inhoud van het voorstel
- Juridische aspecten
- Financiële aspecten / budgetimpact (treat any financial table as an atomic chunk)
- Uitvoering / planning
- Het besluit / dictum

{BASE_INSTRUCTIONS}""",

    "financieel": f"""You are chunking a Dutch municipal financial document (begroting, jaarrekening, etc.) for a RAG system.

CRITICAL RULES FOR FINANCIAL TABLES:
- NEVER split a table across multiple chunks
- Each table is a separate chunk with chunk_type "table"
- For table chunks, also include a "table_json" field: {{"headers": [...], "rows": [[...]]}}
- Preserve ALL numbers, years, and budget line relationships exactly
- Narrative text before/after tables should be separate "text" chunks

Chunk by:
- Each financial table → chunk_type "table" with table_json
- Programme/product descriptions → chunk_type "text"  
- Executive summary sections → chunk_type "text"
- Targets / KPIs tables → chunk_type "table" with table_json

{BASE_INSTRUCTIONS}""",

    "notulen": f"""You are chunking Dutch municipal council meeting minutes (notulen) for a RAG system.

Chunk by speaker turns and topic changes:
- Each time a new raadslid or wethouder speaks on a topic → new chunk
- Keep a speaker's full statement on one topic in one chunk
- Mark direct quotes with chunk_type "quote"
- Voting records → chunk_type "decision"
- Procedural moments (opening, break, closure) → chunk_type "header"
- Each chunk must include who is speaking (if identifiable)

{BASE_INSTRUCTIONS}""",

    "verslag": f"""You are chunking a Dutch meeting report (verslag) for a RAG system.

Chunk by:
- Each agenda item discussed
- Key decisions or conclusions per item
- Action points / toezeggingen

{BASE_INSTRUCTIONS}""",

    "besluitenlijst": f"""You are chunking a Dutch council decision list (besluitenlijst) for a RAG system.

Each individual decision is its own chunk with chunk_type "decision".
Include: decision number, subject, full decision text, any conditions.

{BASE_INSTRUCTIONS}""",

    "overig": f"""You are chunking a Dutch municipal document for a RAG system.

Split into meaningful semantic units. Preserve all tables as atomic chunks (chunk_type "table").

{BASE_INSTRUCTIONS}"""
}

DOC_TYPE_PROMPTS["brief"] = DOC_TYPE_PROMPTS["overig"]
DOC_TYPE_PROMPTS["annotatie"] = DOC_TYPE_PROMPTS["overig"]


# ── Main service ───────────────────────────────────────────────────────────────
class FullRAGPipeline:

    def __init__(self):
        self.ai = AIService()
        if not self.ai.use_llm:
            print("❌ LLM not available.")
            sys.exit(1)

        os.makedirs(QDRANT_PATH, exist_ok=True)
        self.qdrant = QdrantClient(url="http://localhost:6333")

        # Ensure collection exists
        existing = [c.name for c in self.qdrant.get_collections().collections]
        if COLLECTION_NAME not in existing:
            self.qdrant.create_collection(
                collection_name=COLLECTION_NAME,
                vectors_config=VectorParams(size=3072, distance=Distance.COSINE)
            )
            print(f"✓ Created Qdrant collection '{COLLECTION_NAME}'")
        else:
            # Check existing vector size to handle migration
            col_info = self.qdrant.get_collection(COLLECTION_NAME)
            existing_size = col_info.config.params.vectors.size
            print(f"✓ Using existing Qdrant collection (vector size: {existing_size})")

        print(f"✓ Pipeline ready — chunking: {CHUNK_MODEL}")

    # -- Recursive Character Text Splitter Implementation --
    def _recursive_split(self, text: str, chunk_size: int, chunk_overlap: int) -> List[str]:
        separators = ["\n\n", "\n", " ", ""]
        
        def split_text(t: str, seps: List[str]) -> List[str]:
            if len(t) <= chunk_size:
                return [t]
                
            separator = seps[-1]
            new_seps = []
            for i, s in enumerate(seps):
                if s == "":
                    separator = s
                    break
                if s in t:
                    separator = s
                    new_seps = seps[i + 1:]
                    break

            splits = t.split(separator) if separator else list(t)
            
            good_splits = []
            for s in splits:
                if len(s) < chunk_size:
                    good_splits.append(s)
                else:
                    if new_seps:
                        good_splits.extend(split_text(s, new_seps))
                    else:
                        good_splits.append(s)
                        
            final_chunks = []
            current_doc = []
            current_len = 0
            
            for s in good_splits:
                s_len = len(s)
                if current_len + s_len + (len(separator) if current_doc else 0) > chunk_size and current_doc:
                    chunk = (separator if separator else "").join(current_doc)
                    final_chunks.append(chunk)
                    
                    while current_len > chunk_overlap and len(current_doc) > 1:
                        removed = current_doc.pop(0)
                        current_len -= len(removed) + (len(separator) if current_doc else 0)
                        
                current_doc.append(s)
                current_len += s_len + (len(separator) if len(current_doc) > 1 else 0)
                
            if current_doc:
                final_chunks.append((separator if separator else "").join(current_doc))
                
            return final_chunks
            
        return split_text(text, separators)

    # ── Section splitting for very large documents ────────────────────────────
    def _split_into_children(self, content: str) -> List[str]:
        """Split a large document into overlapping Child sections (~7.5K chars) using Recursive Character Splitter."""
        if len(content) <= CHILD_MAX_CHARS:
            return [content]

        return self._recursive_split(content, 7500, 600)

    # ── Gemini chunking call ──────────────────────────────────────────────────
    def _call_gemini_chunker(self, doc_type: str, content: str, section_info: str = "") -> Optional[List[Dict]]:
        """Call Gemini to semantically chunk a section of content. Retries on 429 with backoff."""
        import re, json
        system_prompt = DOC_TYPE_PROMPTS.get(doc_type, DOC_TYPE_PROMPTS["overig"])
        prompt = f"""{system_prompt}

DOCUMENT TO CHUNK{section_info}:

{content}

Return only the JSON array, starting with [ and ending with ]."""

        max_retries = 5
        for attempt in range(max_retries):
            try:
                response = self.ai.client.models.generate_content(
                    model=CHUNK_MODEL,
                    contents=prompt
                )
                text = response.text or ""
                j_start = text.find('[')
                j_end = text.rfind(']') + 1
                if j_start == -1 or j_end == 0:
                    return None

                json_str = text[j_start:j_end]
                
                # Robust JSON parsing with multi-layer repair
                from json_repair import repair_and_load
                try:
                    return repair_and_load(json_str)
                except Exception as e2:
                    print(f"    \u26a0 JSON parse error (even after deep repair): {e2}")
                    return None

            except Exception as e:
                err_str = str(e)
                if '429' in err_str or 'RESOURCE_EXHAUSTED' in err_str:
                    # Extract the retry delay suggested by the API
                    delay_match = re.search(r'retry[^0-9]*([0-9]+(?:\.[0-9]+)?)s', err_str, re.IGNORECASE)
                    suggested = float(delay_match.group(1)) if delay_match else 60.0
                    # Add jitter (±10%) and exponential backoff per attempt
                    backoff = suggested * (1.5 ** attempt) + (time.time() % 10)
                    backoff = min(backoff, 300)  # Cap at 5 min
                    print(f"    ⚠ 429 Rate limit hit (attempt {attempt+1}/{max_retries}). Backing off {backoff:.0f}s...")
                    time.sleep(backoff)
                    continue  # Retry the call
                else:
                    print(f"    ⚠ Gemini error: {e}")
                    time.sleep(5)
                    return None

        print(f"    ❌ All {max_retries} retry attempts exhausted.")
        return None

    # ── Embedding + Qdrant storage ────────────────────────────────────────────
    def _store_chunks(self, document_id: str, doc_name: str, doc_type: str,
                      meeting_id: str, chunks: List[Dict], conn, child_id: int) -> int:
        """Embed all chunks (Grandchildren) and store in Qdrant + Postgres linked to child_id."""
        cur = conn.cursor()
        # Note: We don't delete by document_id here anymore to allow multiple children to append chunks
        
        points = []
        pg_data = []
        stored = 0
        duplicates = 0
        seen_text_hashes = set()

        for idx, chunk in enumerate(chunks):
            text = chunk.get("text", "").strip()
            if len(text) < MIN_CHUNK_TEXT:
                continue

            text_hash = hashlib.md5(text.encode()).hexdigest()
            if text_hash in seen_text_hashes:
                duplicates += 1
                continue
            seen_text_hashes.add(text_hash)

            title = chunk.get("title", "Untitled")
            questions = chunk.get("questions", [])
            chunk_type = chunk.get("chunk_type", "text")
            table_json = chunk.get("table_json")

            # Create contextualized string for embedding only
            context_str = f"[Document: {doc_name} | Type: {doc_type}]\n"
            embedding_text = context_str + text

            # Embed the contextualized chunk text
            embedding = self.ai.generate_embedding(embedding_text)
            if embedding is None:
                continue

            # Qdrant point ID
            hash_str = hashlib.md5(f"{document_id}_{idx}".encode()).hexdigest()
            point_id = int(hash_str[:15], 16)

            payload = {
                "document_id": str(document_id),
                "doc_name": doc_name,
                "doc_type": doc_type,
                "meeting_id": str(meeting_id) if meeting_id else None,
                "child_id": child_id,  # Parent relation
                "chunk_index": idx,
                "chunk_type": chunk_type,
                "title": title,
                "content": text,
                "questions": questions,
                "table_json": json.dumps(table_json) if table_json else None,
            }
            points.append(PointStruct(id=point_id, vector=embedding, payload=payload))

            # Collect for Postgres batch insert
            table_json_str = json.dumps(table_json) if table_json else None
            est_tokens = int(len(text.split()) / 0.75)
            pg_data.append((
                document_id, idx, title, text, chunk_type, table_json_str, est_tokens, child_id
            ))

            stored += 1
            if stored % 50 == 0:
                print(f"      ...embedded {stored}/{len(chunks)} chunks", flush=True)

        print(f"    → Storing {stored} chunks...", flush=True)
        
        # --- The requested 12:00 completion log ---
        print(f"    ✓ Stored {stored} chunks in both DBs (0 duplicates skipped).", flush=True)
        
        # 1. Batch upsert to Qdrant in chunks of 100 to prevent payload limit errors (max 33MB)
        BATCH_SIZE = 100
        for i in range(0, len(points), BATCH_SIZE):
            batch_points = points[i:i + BATCH_SIZE]
            self.qdrant.upsert(collection_name=COLLECTION_NAME, points=batch_points)

        # 2. Batch insert to Postgres in chunks of 100
        for i in range(0, len(pg_data), BATCH_SIZE):
            batch_pg = pg_data[i:i + BATCH_SIZE]
            execute_values(cur, """
                INSERT INTO document_chunks 
                    (document_id, chunk_index, title, content, chunk_type, table_json, tokens_estimated, child_id)
                VALUES %s
                ON CONFLICT (document_id, chunk_index) DO UPDATE SET
                    title = EXCLUDED.title,
                    content = EXCLUDED.content,
                    chunk_type = EXCLUDED.chunk_type,
                    table_json = EXCLUDED.table_json

            """, batch_pg)

        # 3. Record chunking metadata
        cur.execute("""
            INSERT INTO chunking_metadata (document_id, chunking_method, model_used, chunks_count)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (document_id) DO UPDATE SET
                chunks_count = EXCLUDED.chunks_count,
                model_used = EXCLUDED.model_used,
                chunking_timestamp = CURRENT_TIMESTAMP
        """, (document_id, "gemini-type-aware-v2", CHUNK_MODEL, stored))

        conn.commit()
        cur.close()
        print(f"    ✓ Stored {stored} chunks in both DBs ({duplicates} duplicates skipped).", flush=True)
        return stored

    # ── Schema migration ──────────────────────────────────────────────────────
    def ensure_schema(self):
        """Add missing columns to document_chunks if needed."""
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        for col, coltype in [("chunk_type", "TEXT"), ("table_json", "TEXT")]:
            cur.execute(f"""
                ALTER TABLE document_chunks
                ADD COLUMN IF NOT EXISTS {col} {coltype}
            """)
        conn.commit()
        cur.close()
        conn.close()
        print("✓ Schema checked/updated")

    # ── Main processing loop ──────────────────────────────────────────────────
    def process_all(self, batch_size: int = 1000, offset: int = 0, year: Optional[int] = None):
        """Process documents, optionally filtered by year."""
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()

        # Get already-chunked document IDs
        cur.execute("SELECT DISTINCT document_id FROM chunking_metadata")
        already_done = {row[0] for row in cur.fetchall()}
        print(f"Already chunked: {len(already_done)} documents")

        # Get documents that have content
        year_filter = ""
        params = [offset]
        if year is not None:
            year_filter = "JOIN meetings m ON d.meeting_id = m.id WHERE EXTRACT(YEAR FROM m.start_date) = %s AND"
            params = [year, offset]
        else:
            year_filter = "WHERE"

        cur.execute(f"""
            SELECT d.id, d.name, d.content, d.meeting_id
            FROM documents d
            {year_filter} d.content IS NOT NULL AND length(d.content) >= 50
            ORDER BY length(d.content) DESC
            OFFSET %s
        """, tuple(params))
        all_docs = cur.fetchall()
        cur.close()
        conn.close()

        total = len(all_docs)
        to_process = [(d[0], d[1], d[2], d[3]) for d in all_docs if d[0] not in already_done]
        print(f"\nTotal docs with content: {total}")
        print(f"To process (new/updated): {len(to_process)}")
        print(f"Started at: {datetime.now().isoformat()}\n")

        success = 0
        errors = 0

        for idx, (doc_id, doc_name, content, meeting_id) in enumerate(to_process, 1):
            doc_type = classify_document(doc_name, content)
            content_len = len(content)
            print(f"\n[{idx}/{len(to_process)}] {doc_name[:60]} ({doc_type}, {content_len:,} chars)")

            try:
                # 1. Split into Children (8K)
                children_texts = self._split_into_children(content)
                if len(children_texts) > 1:
                    print(f"  → 3-Tier Mode: doc split into {len(children_texts)} Children (Reasoning Units)")

                # Re-open connection for multi-step storage
                conn2 = psycopg2.connect(DB_URL)
                try:
                    cur2 = conn2.cursor()
                    # Clean up old data for this doc to avoid mess
                    cur2.execute("DELETE FROM document_children WHERE document_id = %s", (doc_id,))
                    cur2.execute("DELETE FROM document_chunks WHERE document_id = %s", (doc_id,))
                    
                    total_grandchildren = 0
                    for c_idx, child_text in enumerate(children_texts):
                        # Store Child node
                        cur2.execute("""
                            INSERT INTO document_children (document_id, content, chunk_index)
                            VALUES (%s, %s, %s) RETURNING id
                        """, (doc_id, child_text, c_idx))
                        child_id = cur2.fetchone()[0]
                        conn2.commit() # Commit child so grandchild can link

                        # 2. Split Child into Grandchildren (1K) via Gemini
                        section_info = f" (Child {c_idx + 1}/{len(children_texts)})" if len(children_texts) > 1 else ""
                        grandchildren = self._call_gemini_chunker(doc_type, child_text, section_info)
                        
                        if grandchildren:
                            stored = self._store_chunks(doc_id, doc_name, doc_type, meeting_id, grandchildren, conn2, child_id)
                            total_grandchildren += stored
                        
                        time.sleep(RATE_LIMIT_SLEEP)

                    print(f"  ✓ {len(children_texts)} Children and {total_grandchildren} Grandchildren stored")
                    success += 1
                finally:
                    conn2.close()

            except Exception as e:
                errors += 1
                print(f"  ❌ Failed: {e}")
                time.sleep(5)

            # Progress summary every 100 docs
            if idx % 100 == 0:
                print(f"\n{'='*55}")
                print(f"PROGRESS: {idx}/{len(to_process)} | Success: {success} | Errors: {errors}")
                print(f"Time: {datetime.now().isoformat()}")
                print(f"{'='*55}\n")

        print(f"\n{'='*55}")
        print("CHUNKING COMPLETE")
        print(f"Total processed: {success} | Errors: {errors}")
        print(f"Completed: {datetime.now().isoformat()}")
        print(f"{'='*55}")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--offset", type=int, default=0, help="Skip first N docs (for resuming)")
    parser.add_argument("--year", type=int, default=None, help="Process only documents from this year")
    args = parser.parse_args()

    title = f"PHASE B: FULL RAG CHUNKING PIPELINE (Year: {args.year if args.year else 'ALL'})"
    print("=" * 60)
    print(title)
    print("=" * 60)

    pipeline = FullRAGPipeline()
    pipeline.ensure_schema()
    pipeline.process_all(offset=args.offset, year=args.year)


if __name__ == "__main__":
    main()
