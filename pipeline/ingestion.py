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
    Handles tiered chunking and RAG ingestion for all documents.

    Chunking strategy v2 — optimised for Qwen3-8B (4096D, 8K token context).
    Benchmarks show 512-1024 tokens per chunk is optimal for high-dim embeddings.
    Target: ~2,000-2,500 chars (~500-625 Dutch tokens) with 10-15 % overlap.

    Tiers:
    - Atomic   (< 1,000 chars): stored as single chunk, no split.
    - Compact  (1,000 – 2,500 chars): stored as single chunk (~500-625 tok).
    - Recursive (2,500 – 50,000 chars): heuristic recursive split with overlap. Free.
    - Structural (> 50,000 chars): 1 Gemini call for section boundaries,
      then recursive split within each section. ~$0.01/doc.
    """

    # ── Dutch document structure patterns ────────────────────────────────
    SECTION_PATTERNS = re.compile(
        r'(?:'
        r'^\s*\d+\.\s+\S'            # "1. Onderwerp"
        r'|^\s*[A-Z][A-Z\s]{3,}$'    # "FINANCIËLE CONSEQUENTIES"
        r'|^\s*Artikel\s+\d+'         # "Artikel 3"
        r'|^\s*Agendapunt\s+\d+'      # "Agendapunt 5"
        r'|^\s*Besluit\s*:'           # "Besluit:"
        r'|^\s*Overwegingen?\s*:'     # "Overwegingen:"
        r'|^\s*Bijlage\s+\d+'        # "Bijlage 1"
        r')',
        re.MULTILINE
    )

    def __init__(self, db_url: str = None,
                 chunk_only: bool = False):
        if db_url is None:
            import os
            url = os.getenv("DATABASE_URL")
            if url:
                db_url = url
            else:
                user = os.getenv("DB_USER", "postgres")
                pw = os.getenv("DB_PASSWORD", "postgres")
                host = os.getenv("DB_HOST", "localhost")
                port = os.getenv("DB_PORT", "5432")
                name = os.getenv("DB_NAME", "neodemos")
                db_url = f"postgresql://{user}:{pw}@{host}:{port}/{name}"
        """
        Args:
            db_url: Postgres connection string
            chunk_only: If True, only write chunks to Postgres (skip Qdrant embedding).
                        Use this when migrate_embeddings.py handles embedding separately.
        """
        load_dotenv()
        self.db_url = db_url
        self.chunk_only = chunk_only
        self.ai = AIService()
        self.local_ai = LocalAIService(skip_llm=True) if not chunk_only else None
        self.qdrant = None
        if not chunk_only:
            self.qdrant = QdrantClient(url="http://localhost:6333")
        self.collection_name = "notulen_chunks"
        self.chunk_model = "gemini-2.5-flash-lite"

        # ── Chunk size parameters (v2) ───────────────────────────────────
        self.atomic_threshold = 1000       # below this: single chunk, no split
        self.compact_threshold = 2500      # below this: single chunk (~500-625 tok)
        self.structural_threshold = 50000  # above this: use Gemini for section detection
        self.target_chunk_chars = 2000     # target grandchild size (~500 Dutch tokens)
        self.max_chunk_chars = 2500        # hard ceiling per chunk
        self.overlap_chars = 250           # ~10-12 % overlap between adjacent chunks
        self.heuristic = False
        self._transcription_corrections = self._load_transcription_corrections()

    @staticmethod
    def _load_transcription_corrections() -> List[tuple]:
        """Load common_transcription_errors from the political dictionary as compiled regexes."""
        dict_path = Path(__file__).resolve().parent.parent / "data" / "lexicons" / "rotterdam_political_dictionary.json"
        corrections = []
        try:
            with open(dict_path, encoding="utf-8") as f:
                data = json.load(f)
            for pattern, replacement in data.get("common_transcription_errors", {}).items():
                if pattern.startswith("_"):
                    continue
                corrections.append((re.compile(r'\b' + re.escape(pattern) + r'\b', re.IGNORECASE), replacement))
        except Exception:
            pass
        return corrections

    def _apply_transcription_corrections(self, text: str) -> str:
        """Apply known Whisper transcription corrections to text."""
        for pattern, replacement in self._transcription_corrections:
            text = pattern.sub(replacement, text)
        return text

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
                # Delete all FK-referencing rows before deleting chunks (FK constraint)
                cur.execute("""
                    DELETE FROM kg_mentions
                    WHERE chunk_id IN (SELECT id FROM document_chunks WHERE child_id = ANY(%s))
                """, (child_ids,))
                cur.execute("""
                    DELETE FROM kg_relationships
                    WHERE chunk_id IN (SELECT id FROM document_chunks WHERE child_id = ANY(%s))
                """, (child_ids,))
                cur.execute("""
                    DELETE FROM kg_extraction_log
                    WHERE chunk_id IN (SELECT id FROM document_chunks WHERE child_id = ANY(%s))
                """, (child_ids,))
                cur.execute("""
                    DELETE FROM chunk_questions
                    WHERE chunk_id IN (SELECT id FROM document_chunks WHERE child_id = ANY(%s))
                """, (child_ids,))
                cur.execute("""
                    DELETE FROM financial_lines
                    WHERE bron_chunk_id IN (SELECT id FROM document_chunks WHERE child_id = ANY(%s))
                """, (child_ids,))
                cur.execute("""
                    DELETE FROM gr_member_contributions
                    WHERE bron_chunk_id IN (SELECT id FROM document_chunks WHERE child_id = ANY(%s))
                """, (child_ids,))
                cur.execute("DELETE FROM document_chunks WHERE child_id = ANY(%s)", (child_ids,))
                cur.execute("DELETE FROM document_children WHERE id = ANY(%s)", (child_ids,))

            # Delete all FK-referencing rows for orphan chunks (no child_id) first
            cur.execute("""
                DELETE FROM kg_mentions
                WHERE chunk_id IN (
                    SELECT id FROM document_chunks WHERE document_id = %s AND child_id IS NULL
                )
            """, (doc_id,))
            cur.execute("""
                DELETE FROM kg_relationships
                WHERE chunk_id IN (
                    SELECT id FROM document_chunks WHERE document_id = %s AND child_id IS NULL
                )
            """, (doc_id,))
            cur.execute("""
                DELETE FROM kg_extraction_log
                WHERE chunk_id IN (
                    SELECT id FROM document_chunks WHERE document_id = %s AND child_id IS NULL
                )
            """, (doc_id,))
            cur.execute("""
                DELETE FROM chunk_questions
                WHERE chunk_id IN (
                    SELECT id FROM document_chunks WHERE document_id = %s AND child_id IS NULL
                )
            """, (doc_id,))
            cur.execute("""
                DELETE FROM financial_lines
                WHERE bron_chunk_id IN (
                    SELECT id FROM document_chunks WHERE document_id = %s AND child_id IS NULL
                )
            """, (doc_id,))
            cur.execute("""
                DELETE FROM gr_member_contributions
                WHERE bron_chunk_id IN (
                    SELECT id FROM document_chunks WHERE document_id = %s AND child_id IS NULL
                )
            """, (doc_id,))
            cur.execute("DELETE FROM document_chunks WHERE document_id = %s AND child_id IS NULL", (doc_id,))
            conn.commit()
            cur.close()

            # 0. Ensure Parent Document Record (Foreign Key fix)
            agenda_item_id = metadata.get('agenda_item_id') if metadata else None
            self._ensure_document_record(conn, doc_id, doc_name, meeting_id, content, agenda_item_id=agenda_item_id, category=category)

            doc_size = len(content)

            # --- 4-TIER STRATEGY (v2) ---
            if doc_size < self.atomic_threshold:
                # ATOMIC: single chunk, no split
                logger.info(f"  Atomic: {doc_size} chars → 1 chunk.")
                self._store_single_chunk(conn, doc_id, doc_name, meeting_id, content, metadata, chunk_type="full_text")

            elif doc_size <= self.compact_threshold:
                # COMPACT: fits in one embedding (~500-625 tokens), no split
                logger.info(f"  Compact: {doc_size} chars → 1 chunk.")
                self._store_single_chunk(conn, doc_id, doc_name, meeting_id, content, metadata, chunk_type="quote")

            elif doc_size <= self.structural_threshold:
                # RECURSIVE: heuristic split with overlap, no LLM
                chunks = self._recursive_chunk(content, doc_name)
                logger.info(f"  Recursive: {doc_size} chars → {len(chunks)} chunks.")
                self._store_child_and_chunks(conn, doc_id, doc_name, meeting_id, content, metadata, chunks)

            else:
                # STRUCTURAL: Gemini identifies sections, then recursive split within each
                logger.info(f"  Structural: {doc_size} chars. Using Gemini for section detection...")
                sections = self._detect_sections_via_gemini(content, doc_name)
                if not sections:
                    # Fallback: treat as recursive
                    sections = [{"title": doc_name, "text": content}]
                all_chunks = []
                for sec in sections:
                    sec_chunks = self._recursive_chunk(sec["text"], sec.get("title", doc_name))
                    all_chunks.extend(sec_chunks)
                logger.info(f"  Structural: {len(sections)} sections → {len(all_chunks)} chunks.")
                self._store_child_and_chunks(conn, doc_id, doc_name, meeting_id, content, metadata, all_chunks)

            logger.info(f"Successfully ingested document: {doc_id}")
        finally:
            conn.close()

    # ── Storage helpers ─────────────────────────────────────────────────

    def _store_single_chunk(self, conn, doc_id, doc_name, meeting_id, content, metadata, chunk_type="full_text"):
        """Store a small document as one child + one chunk (atomic/compact tier)."""
        cur = conn.cursor()
        meta_json = json.dumps(metadata or {})
        cur.execute("""
            INSERT INTO document_children (document_id, chunk_index, content, metadata)
            VALUES (%s, 0, %s, %s) RETURNING id
        """, (doc_id, content, meta_json))
        child_id = cur.fetchone()[0]
        conn.commit()
        chunks = [{"title": doc_name, "text": content, "questions": [], "chunk_type": chunk_type}]
        self._store_grandchildren(conn, doc_id, doc_name, str(meeting_id), chunks, child_id)
        cur.close()

    def _store_child_and_chunks(self, conn, doc_id, doc_name, meeting_id, full_content, metadata, chunks):
        """Store one child (full content) and its chunked grandchildren."""
        cur = conn.cursor()
        meta_json = json.dumps(metadata or {})
        cur.execute("""
            INSERT INTO document_children (document_id, chunk_index, content, metadata)
            VALUES (%s, 0, %s, %s) RETURNING id
        """, (doc_id, full_content, meta_json))
        child_id = cur.fetchone()[0]
        conn.commit()
        if chunks:
            self._store_grandchildren(conn, doc_id, doc_name, str(meeting_id), chunks, child_id)
        cur.close()

    def ingest_transcript(self, transcript_data: Dict[str, Any], heuristic: bool = False, category: str = "committee_transcript"):
        """
        Specialized transcript ingestion that preserve speaker context.
        Stores per-chunk timestamp ranges and video page URL for fragment linking.
        """
        self.heuristic = heuristic
        meeting_id = transcript_data.get("meeting_id")
        meeting_name = transcript_data.get("meeting_name", "Unknown Meeting")
        doc_id = f"transcript_{meeting_id}"

        # Build video page URL from ibabs_url if available
        ibabs_url = transcript_data.get("ibabs_url") or ""
        webcast_code = transcript_data.get("webcast_code") or ""

        # Flatten transcript into items
        agenda_items = transcript_data.get("agenda_items", [])
        for item in agenda_items:
            item_title = item.get("title", "Untitled")
            
            full_text_blocks = []
            segments = item.get("segments", [])

            # Track timestamp range across segments in this agenda item
            item_start_seconds = item.get("start_time")
            item_end_seconds = item.get("end_time")
            seg_start_secs = [s.get("start_seconds") for s in segments if s.get("start_seconds") is not None]
            seg_end_secs = [s.get("end_seconds") for s in segments if s.get("end_seconds") is not None]
            if seg_start_secs and item_start_seconds is None:
                item_start_seconds = min(seg_start_secs)
            if seg_end_secs and item_end_seconds is None:
                item_end_seconds = max(seg_end_secs)

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
                speaker = seg.get("speaker") or "Unknown"   # treat None as Unknown
                party = seg.get("party") or ""
                text = (seg.get("text") or "").strip()

                # --- Quality Filtering ---
                # Skip empty text or unknown speakers with very short text
                if not text or (speaker == "Unknown" and len(text) < 50):
                    continue
                # Apply known transcription corrections (Morkoets→Morkoç, etc.)
                text = self._apply_transcription_corrections(text)
                # Strip Whisper repetition hallucinations
                # 1. Skip entire segment if a single word dominates (≥70%)
                # 2. Truncate at first 3-consecutive-word repetition block
                words = text.split()
                if len(words) > 10:
                    from collections import Counter
                    top_word, top_count = Counter(words).most_common(1)[0]
                    if top_count / len(words) >= 0.70:
                        continue
                    # Check for embedded repetition: find 3+ consecutive identical words
                    trunc_at = None
                    for _i in range(len(words) - 2):
                        if words[_i] == words[_i + 1] == words[_i + 2]:
                            trunc_at = _i
                            break
                    if trunc_at is not None:
                        text = " ".join(words[:trunc_at]).strip()
                        if not text:
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
                        "doc_type": "virtual_notulen",
                        "audio_tier": audio_tier,
                        "avg_confidence": f"{avg_conf:.2f}",
                        "start_seconds": item_start_seconds,
                        "end_seconds": item_end_seconds,
                        "video_url": ibabs_url or None,
                        "webcast_code": webcast_code or None,
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
        """, (doc_id, doc_name, meeting_id, content, category))
        
        # Handle assignment
        if meeting_id or agenda_item_id:
            cur.execute("""
                INSERT INTO document_assignments (document_id, meeting_id, agenda_item_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (document_id, meeting_id, agenda_item_id) DO NOTHING
            """, (doc_id, meeting_id, agenda_item_id))
            
        conn.commit()
        cur.close()

    # ── Recursive chunker (v2) ─────────────────────────────────────────

    def _find_best_break(self, text: str, start: int, end: int) -> int:
        """Find the best split point in text[start:end], respecting natural boundaries."""
        # Priority 1: Dutch section header (only after we've passed target size)
        for m in self.SECTION_PATTERNS.finditer(text, start + self.target_chunk_chars, end):
            return m.start()

        # Priority 2: Speaker tag boundary (transcripts)
        pos = text.rfind("\n[", start, end)
        if pos > start:
            return pos

        # Priority 3: Double newline (paragraph)
        pos = text.rfind("\n\n", start, end)
        if pos > start:
            return pos + 1  # include the first newline in the previous chunk

        # Priority 4: Single newline
        pos = text.rfind("\n", start, end)
        if pos > start:
            return pos + 1

        # Priority 5: Sentence boundary
        pos = text.rfind(". ", start, end)
        if pos > start:
            return pos + 2

        # Last resort: hard cut
        return end

    def _recursive_chunk(self, content: str, title: str) -> List[Dict]:
        """
        Recursive split into ~2,000-2,500 char chunks with 10-15 % overlap.
        Respects Dutch section headers, speaker tags, paragraphs, sentences.
        No LLM calls — entirely heuristic.
        """
        content = content.strip()
        if len(content) <= self.max_chunk_chars:
            return [{"title": title, "text": content, "questions": [], "chunk_type": "quote"}]

        chunks = []
        start = 0
        prev_tail = ""  # overlap text from previous chunk

        while start < len(content):
            # Skip leading whitespace
            while start < len(content) and content[start] in (' ', '\t'):
                start += 1

            remaining = len(content) - start
            if remaining <= 0:
                break

            # If remainder fits in one chunk (with overlap), take it all
            if remaining <= self.max_chunk_chars:
                chunk_text = (prev_tail + content[start:]).strip()
                if len(chunk_text) >= 20:
                    chunks.append({"title": title, "text": chunk_text, "questions": [], "chunk_type": "quote"})
                break

            # Find best break point within [start, start + max_chunk_chars]
            search_end = min(start + self.max_chunk_chars, len(content))
            split_at = self._find_best_break(content, start, search_end)

            raw_text = content[start:split_at].strip()
            chunk_text = (prev_tail + raw_text).strip()
            if len(chunk_text) >= 20:
                chunks.append({"title": title, "text": chunk_text, "questions": [], "chunk_type": "quote"})

            # Compute overlap from the raw (non-overlapped) text
            if len(raw_text) > self.overlap_chars * 2:
                # Priority: speaker tag boundary — never start overlap mid-tag
                # (rfind(". ") can land inside "L.S." or "R.G.C." causing corrupt tags)
                search_from = max(0, len(raw_text) - self.overlap_chars - 80)
                ol = raw_text.rfind("\n[", search_from)
                if ol == -1 or ol < search_from:
                    ol = raw_text.rfind(". ", search_from)
                if ol == -1 or ol < search_from:
                    ol = raw_text.rfind("\n", search_from)
                if ol == -1 or ol < search_from:
                    ol = len(raw_text) - self.overlap_chars
                prev_tail = raw_text[ol:].strip() + "\n"
            else:
                prev_tail = ""

            start = split_at

        return self._inject_speaker_prefixes(chunks)

    # ── Structural section detection (Gemini, for 50K+ docs only) ────

    # Disk cache for Gemini section-detection results.
    # Key: md5(title + first 500 chars of content) — stable across restarts.
    # Avoids re-calling Gemini when a process restarts mid-run or during WS10 reruns.
    _GEMINI_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "pipeline_state" / "gemini_section_cache"

    def _gemini_cache_key(self, content: str, title: str) -> str:
        fingerprint = f"{title}:{content[:500]}"
        return hashlib.md5(fingerprint.encode()).hexdigest()

    def _gemini_cache_get(self, key: str) -> Optional[List[Dict]]:
        path = self._GEMINI_CACHE_DIR / f"{key}.json"
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        return None

    def _gemini_cache_set(self, key: str, sections: List[Dict]):
        self._GEMINI_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = self._GEMINI_CACHE_DIR / f"{key}.json"
        tmp = str(path) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(sections, f)
        Path(tmp).replace(path)

    def _detect_sections_via_gemini(self, content: str, title: str) -> List[Dict]:
        """
        Uses one Gemini call to identify logical section boundaries in a large document.
        Returns a list of {"title": str, "text": str} sections.
        Does NOT ask Gemini to chunk — only to identify where sections start.

        Results are cached to disk (data/pipeline_state/gemini_section_cache/) keyed by
        md5(title + content[:500]). Re-runs and process restarts are free.
        """
        cache_key = self._gemini_cache_key(content, title)
        cached = self._gemini_cache_get(cache_key)
        if cached is not None:
            logger.info(f"  Gemini cache hit ({len(cached)} sections)")
            return cached

        # Send first + last 3K chars as context, plus sampled section headers
        preview = content[:3000] + "\n\n[...]\n\n" + content[-3000:]
        # Also extract any lines that look like headers
        header_lines = []
        for line in content.split("\n"):
            stripped = line.strip()
            if stripped and (
                self.SECTION_PATTERNS.match(stripped)
                or (stripped.isupper() and 4 < len(stripped) < 80)
                or re.match(r'^\d+\.\d*\s+\S', stripped)
            ):
                header_lines.append(stripped)
        header_sample = "\n".join(header_lines[:50])

        prompt = f"""You are analyzing a large Dutch municipal document to identify its logical section structure.

DOCUMENT TITLE: {title}
DOCUMENT LENGTH: {len(content)} characters

DETECTED HEADERS (sample):
{header_sample}

DOCUMENT PREVIEW (first and last 3000 chars):
{preview}

Return a JSON array of section boundaries. Each item:
- "title": brief section title
- "start_text": the EXACT first 60 characters of that section (so I can find it with str.find())

Rules:
- Identify 5-30 major sections (not every paragraph)
- Sections should be roughly equal in size when possible
- NEVER change or paraphrase the start_text — copy it exactly from the document

Return ONLY the JSON array."""

        try:
            response = self.ai.client.models.generate_content(model=self.chunk_model, contents=prompt)
            response_text = response.text or ""
            j_start = response_text.find('[')
            j_end = response_text.rfind(']') + 1
            if j_start == -1 or j_end == 0:
                return []
            from json_repair import repair_json
            repaired = repair_json(response_text[j_start:j_end])
            boundaries = json.loads(repaired)

            # Build sections from boundaries by finding start_text in content
            sections = []
            positions = []
            for b in boundaries:
                start_text = b.get("start_text", "")
                pos = content.find(start_text)
                if pos >= 0:
                    positions.append({"title": b.get("title", "Section"), "pos": pos})

            if not positions:
                return []

            # Sort by position and extract text between boundaries
            positions.sort(key=lambda x: x["pos"])
            for i, p in enumerate(positions):
                start = p["pos"]
                end = positions[i + 1]["pos"] if i + 1 < len(positions) else len(content)
                text = content[start:end].strip()
                if text:
                    sections.append({"title": p["title"], "text": text})

            # Capture any text before the first detected section
            if positions[0]["pos"] > 100:
                preamble = content[:positions[0]["pos"]].strip()
                if preamble:
                    sections.insert(0, {"title": f"Inleiding — {title}", "text": preamble})

            self._gemini_cache_set(cache_key, sections)
            return sections

        except Exception as e:
            logger.error(f"Gemini section detection failed: {e}. Falling back to header-based split.")
            return self._fallback_section_split(content, title)

    def _fallback_section_split(self, content: str, title: str) -> List[Dict]:
        """Split large docs by detected headers when Gemini is unavailable."""
        split_points = [0]
        for m in self.SECTION_PATTERNS.finditer(content):
            if m.start() > split_points[-1] + 2000:  # min section size
                split_points.append(m.start())
        split_points.append(len(content))

        sections = []
        for i in range(len(split_points) - 1):
            text = content[split_points[i]:split_points[i + 1]].strip()
            if text:
                # Use first line as title
                first_line = text.split("\n")[0].strip()[:80]
                sections.append({"title": first_line or title, "text": text})
        return sections if sections else [{"title": title, "text": content}]

    def _inject_speaker_prefixes(self, chunks: List[Dict]) -> List[Dict]:
        """Ensures every chunk starts with a [Speaker]: tag if present in the block.

        Also cleans up overlap artifacts:
          - Strips leading `. ` fragments from overlap that landed mid-sentence
          - Strips orphaned partial speaker tags (e.g., `. (Larissa) Vlieger]:`)
        """
        processed = []
        last_speaker_tag = None
        for chunk in chunks:
            text = chunk.get("text", "").strip()

            # Clean overlap artifacts: strip leading `. partial tag]:` patterns
            # e.g., ". (Larissa) Vlieger Commissievoorzitter]: Volgens mij..."
            text = re.sub(r'^\.[\s]*(?:[A-Z(].*?\]:\s*)?', '', text, count=1).strip()

            # Also strip bare leading `. ` from mid-sentence overlap
            if text.startswith('. ') and not text.startswith('...'):
                text = text[2:].strip()

            chunk["text"] = text

            match = re.match(r"^(\[.*?\]:)", text)
            if match:
                last_speaker_tag = match.group(1)
            elif last_speaker_tag:
                chunk["text"] = f"{last_speaker_tag} {text}"
            processed.append(chunk)
        return processed

    def _store_grandchildren(self, conn, doc_id: str, doc_name: str, meeting_id: str, chunks: List[Dict], child_id: int):
        """Stores grandchildren chunks in Postgres (and optionally embeds + upserts to Qdrant)."""
        from services.embedding import compute_point_id
        cur = conn.cursor()
        # Build pg_data; keep a parallel list of the in-memory chunk info needed for embedding.
        pg_data = []
        chunk_meta = []  # rows parallel to pg_data: {text, title, chunk_type, chunk_index, questions}
        for idx, chunk in enumerate(chunks):
            text = chunk.get("text", "").strip()
            if len(text) < 20: continue
            title = chunk.get("title", "Untitled")
            chunk_type = chunk.get("chunk_type", "quote")
            pg_data.append((doc_id, idx, title, text, chunk_type, None, int(len(text)/4), child_id))
            chunk_meta.append({
                "text": text, "title": title, "chunk_type": chunk_type,
                "chunk_index": idx, "questions": chunk.get("questions", []),
            })

        # INSERT first (with RETURNING id) so Qdrant points can be keyed by the
        # canonical Scheme A hash (compute_point_id(document_id, db_id)).
        prod_db_ids = []
        if pg_data:
            rows = execute_values(cur, """
                INSERT INTO document_chunks
                    (document_id, chunk_index, title, content, chunk_type, table_json, tokens_estimated, child_id)
                VALUES %s
                ON CONFLICT (document_id, chunk_index) DO UPDATE SET
                    content = EXCLUDED.content, title = EXCLUDED.title, child_id = EXCLUDED.child_id
                RETURNING id
            """, pg_data, fetch=True)
            prod_db_ids = [r[0] for r in rows]

        # Embed + upsert to Qdrant (using production db_ids just returned)
        points = []
        embedded_db_ids = []
        if not self.chunk_only and self.local_ai and self.local_ai.is_available():
            for meta, db_id in zip(chunk_meta, prod_db_ids):
                context_str = f"[Document: {doc_name} | Section: {meta['title']}]\n"
                embedding = self.local_ai.generate_embedding(context_str + meta["text"])
                if embedding is None:
                    continue
                point_id = compute_point_id(doc_id, db_id)
                payload = {
                    "document_id": doc_id, "doc_name": doc_name, "doc_type": "municipal_doc",
                    "meeting_id": meeting_id, "child_id": child_id, "chunk_index": meta["chunk_index"],
                    "chunk_type": meta["chunk_type"], "title": meta["title"], "content": meta["text"],
                    "questions": meta["questions"],
                }
                points.append(PointStruct(id=point_id, vector=embedding, payload=payload))
                embedded_db_ids.append(db_id)

        if points and self.qdrant:
            self.qdrant.upsert(collection_name=self.collection_name, points=points)
            # Mark embedded_at so Phase 2 doesn't re-embed
            cur.execute(
                "UPDATE document_chunks SET embedded_at = NOW() WHERE id = ANY(%s)",
                (embedded_db_ids,),
            )
        conn.commit()
        cur.close()
        logger.info(f"    ✓ Stored {len(pg_data)} Grandchild chunks{' (chunk_only, no embedding)' if self.chunk_only else ''}")
