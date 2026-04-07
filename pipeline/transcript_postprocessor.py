"""
Two-Pass LLM Transcript Post-Processor
========================================

Converts raw VTT/Whisper transcription output into clean, readable Dutch text.

Pass 1 (Gemini Flash Lite — cheap, fast):
  - Fix punctuation, obvious word errors, capitalize proper nouns
  - Process in 10-15 minute chunks with 20% overlap

Pass 2 (Gemini Flash — higher quality):
  - Convert spoken Dutch to written register
  - Remove disfluencies while preserving meaningful interjections
  - Correct names against Rotterdam political dictionary
  - Ensure coherence across chunk boundaries

Usage:
    from pipeline.transcript_postprocessor import TranscriptPostProcessor
    processor = TranscriptPostProcessor()
    cleaned = processor.process(transcript_json)
"""

import os
import json
import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────

CHUNK_MINUTES = 12          # Minutes of transcript per LLM chunk
OVERLAP_MINUTES = 2         # Overlap between adjacent chunks
MAX_PARALLEL_CALLS = 3      # Concurrent Gemini calls
RETRY_ATTEMPTS = 3
RETRY_DELAY_S = 2
INTER_CALL_DELAY_S = 0.5   # Rate limit protection

PASS1_MODEL = "gemini-2.5-flash-lite"
PASS2_MODEL = "gemini-2.5-flash-lite"  # Can upgrade to gemini-2.5-flash if quality demands


class TranscriptPostProcessor:
    """Two-pass LLM post-processor for Dutch municipal meeting transcripts."""

    def __init__(self, dictionary_path: str = None):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            logger.warning("GEMINI_API_KEY not set. Post-processing will be skipped.")

        # Load Rotterdam political dictionary
        if dictionary_path is None:
            dictionary_path = str(
                Path(__file__).parent.parent / "data" / "lexicons" / "rotterdam_political_dictionary.json"
            )
        self.dictionary = self._load_dictionary(dictionary_path)

    def _load_dictionary(self, path: str) -> Dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Could not load political dictionary: {e}")
            return {}

    def _build_dictionary_context(self) -> str:
        """Build a concise dictionary reference for LLM prompts."""
        parts = []
        if self.dictionary.get("parties"):
            parts.append(f"Partijen: {', '.join(self.dictionary['parties'])}")
        if self.dictionary.get("council_members", {}).get("surnames"):
            parts.append(f"Raadsleden (achternamen): {', '.join(self.dictionary['council_members']['surnames'])}")
        if self.dictionary.get("roles"):
            parts.append(f"Rollen: {', '.join(self.dictionary['roles'])}")
        if self.dictionary.get("municipal_terms"):
            # Take the most important terms to stay within token limits
            terms = self.dictionary["municipal_terms"][:20]
            parts.append(f"Termen: {', '.join(terms)}")
        return "\n".join(parts)

    # ── Chunking ─────────────────────────────────────────────────────────

    def _chunk_segments(self, segments: List[Dict], chunk_minutes: int = CHUNK_MINUTES,
                        overlap_minutes: int = OVERLAP_MINUTES) -> List[List[Dict]]:
        """Split segments into time-based chunks with overlap."""
        if not segments:
            return []

        chunk_seconds = chunk_minutes * 60
        overlap_seconds = overlap_minutes * 60
        chunks = []
        current_chunk = []
        chunk_start = segments[0].get("start_seconds", 0)

        for seg in segments:
            seg_start = seg.get("start_seconds", 0)

            # Start a new chunk if we've exceeded the chunk duration
            if seg_start - chunk_start >= chunk_seconds and current_chunk:
                chunks.append(current_chunk)
                # Find overlap start point
                overlap_start = seg_start - overlap_seconds
                current_chunk = [s for s in current_chunk if s.get("start_seconds", 0) >= overlap_start]
                chunk_start = current_chunk[0].get("start_seconds", 0) if current_chunk else seg_start

            current_chunk.append(seg)

        if current_chunk:
            chunks.append(current_chunk)

        return chunks

    def _segments_to_text(self, segments: List[Dict]) -> str:
        """Convert segments to readable text for LLM processing."""
        lines = []
        for seg in segments:
            speaker = seg.get("speaker", "")
            party = seg.get("party", "")
            text = seg.get("text", "").strip()
            if not text:
                continue

            prefix = f"[{speaker}]" if speaker else "[Spreker onbekend]"
            if party:
                prefix = f"[{speaker} ({party})]"
            lines.append(f"{prefix}: {text}")

        return "\n".join(lines)

    # ── Gemini API Calls ─────────────────────────────────────────────────

    async def _call_gemini(self, prompt: str, model: str = PASS1_MODEL) -> str:
        """Call Gemini API with retry logic."""
        try:
            import google.genai as genai
        except ImportError:
            logger.error("google-genai not installed")
            return ""

        client = genai.Client(api_key=self.api_key)

        for attempt in range(RETRY_ATTEMPTS):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                )
                if response.text:
                    return response.text
            except Exception as e:
                if attempt == RETRY_ATTEMPTS - 1:
                    logger.warning(f"Gemini call failed after {RETRY_ATTEMPTS} attempts: {e}")
                    return ""
                await asyncio.sleep(RETRY_DELAY_S * (attempt + 1))

        return ""

    # ── Pass 1: Segment-Level Correction ─────────────────────────────────

    async def _pass1_correct_chunk(self, chunk_text: str, chunk_idx: int,
                                    prev_tail: str, meeting_context: str) -> str:
        """Pass 1: Fix punctuation, word errors, capitalize proper nouns."""
        dict_context = self._build_dictionary_context()

        prompt = f"""Je bent een transcriptie-editor die automatische ondertiteling corrigeert van een vergadering van de Rotterdamse gemeenteraad.

CONTEXT:
{meeting_context}

WOORDENLIJST (gebruik deze exacte schrijfwijzen):
{dict_context}

{"VORIGE TEKST (voor continuïteit): ..." + prev_tail[-300:] if prev_tail else ""}

INSTRUCTIES:
1. Corrigeer spel- en typfouten, vooral bij namen van raadsleden en partijen
2. Herstel leestekens (punten, komma's, vraagtekens)
3. Schrijf afkortingen correct (B en W, COR, iBabs)
4. Behoud de [Spreker (Partij)]: prefix-structuur EXACT
5. Verander NIETS aan de inhoudelijke betekenis
6. Voeg geen informatie toe die er niet was

TRANSCRIPT:
{chunk_text}

GECORRIGEERD TRANSCRIPT:"""

        result = await self._call_gemini(prompt, model=PASS1_MODEL)
        if not result:
            return chunk_text  # Return original on failure
        return result.strip()

    # ── Pass 2: Register Conversion ──────────────────────────────────────

    async def _pass2_polish_chunk(self, chunk_text: str, chunk_idx: int,
                                   prev_tail: str, meeting_context: str) -> str:
        """Pass 2: Convert spoken Dutch to written register, remove disfluencies."""
        dict_context = self._build_dictionary_context()

        prompt = f"""Je bent een professionele notulist van de gemeente Rotterdam. Je taak is om gecorrigeerde transcripties om te zetten naar leesbare, geschreven notulen.

CONTEXT:
{meeting_context}

WOORDENLIJST:
{dict_context}

{"VORIGE TEKST (voor continuïteit): ..." + prev_tail[-300:] if prev_tail else ""}

INSTRUCTIES:
1. Zet gesproken Nederlands om naar geschreven Nederlands:
   - Verwijder opvulwoorden (eh, uhm, nou ja, zeg maar, eigenlijk als opvulwoord)
   - Verwijder valse starts en herhalingen
   - Maak onvolledige zinnen af waar de bedoeling duidelijk is
2. BEHOUD:
   - Alle politieke standpunten en uitspraken exact zoals bedoeld
   - Specifieke namen, cijfers, datums en bedragen
   - De toon en strekking van het debat
   - De [Spreker (Partij)]: prefix-structuur
3. VERWIJDER NIET:
   - Inhoudelijke uitspraken die als "ja" of "nee" een stemming of reactie zijn
   - Interrupties die inhoudelijk relevant zijn
4. Formatteer als professionele notulen:
   - Elke sprekersbijdrage als apart blok
   - Duidelijke alinea-indeling bij thema-wisselingen

TRANSCRIPT:
{chunk_text}

GESCHREVEN NOTULEN:"""

        result = await self._call_gemini(prompt, model=PASS2_MODEL)
        if not result:
            return chunk_text
        return result.strip()

    # ── Main Processing Pipeline ─────────────────────────────────────────

    def _extract_meeting_context(self, transcript: Dict) -> str:
        """Build a meeting context string from transcript metadata."""
        parts = []
        if transcript.get("meeting_name"):
            parts.append(f"Vergadering: {transcript['meeting_name']}")
        if transcript.get("date"):
            parts.append(f"Datum: {transcript['date']}")
        if transcript.get("speakers"):
            parts.append(f"Sprekers: {', '.join(transcript['speakers'][:15])}")
        return "\n".join(parts)

    def _flatten_segments(self, transcript: Dict) -> List[Dict]:
        """Extract all segments from the transcript, preserving agenda item context."""
        all_segments = []
        for item in transcript.get("agenda_items", []):
            item_title = item.get("title", "")
            for seg in item.get("segments", []):
                seg_copy = dict(seg)
                seg_copy["_agenda_item"] = item_title
                all_segments.append(seg_copy)
        return all_segments

    def _rebuild_transcript(self, transcript: Dict, processed_text: str) -> Dict:
        """Rebuild the transcript structure from processed text.

        The processed text has [Speaker (Party)]: blocks. We map them back
        into the transcript's agenda_items structure.
        """
        import re

        # Parse processed text back into segments
        pattern = re.compile(r'\[([^\]]+)\]:\s*(.*?)(?=\n\[|\Z)', re.DOTALL)
        new_segments = []
        for match in pattern.finditer(processed_text):
            speaker_str = match.group(1).strip()
            text = match.group(2).strip()
            if not text:
                continue

            # Parse speaker and party from "Name (Party)" or just "Name"
            party_match = re.match(r'^(.+?)\s*\(([^)]+)\)$', speaker_str)
            if party_match:
                speaker = party_match.group(1).strip()
                party = party_match.group(2).strip()
            else:
                speaker = speaker_str
                party = ""

            new_segments.append({
                "speaker": speaker,
                "party": party,
                "text": text,
                "confidence": 1.0,  # Post-processed = high confidence
            })

        # Distribute back into agenda items proportionally
        result = dict(transcript)
        original_items = transcript.get("agenda_items", [])
        if not original_items or not new_segments:
            return result

        # Simple approach: distribute segments proportionally by original segment count
        total_orig = sum(len(item.get("segments", [])) for item in original_items)
        if total_orig == 0:
            return result

        seg_idx = 0
        new_items = []
        for item in original_items:
            orig_count = len(item.get("segments", []))
            proportion = orig_count / total_orig
            take = max(1, round(proportion * len(new_segments)))
            item_segments = new_segments[seg_idx:seg_idx + take]
            seg_idx += take

            new_item = dict(item)
            new_item["segments"] = item_segments
            new_items.append(new_item)

        # Add any remaining segments to the last item
        if seg_idx < len(new_segments) and new_items:
            new_items[-1]["segments"].extend(new_segments[seg_idx:])

        result["agenda_items"] = new_items
        result["post_processed"] = True
        result["total_segments"] = len(new_segments)
        return result

    async def _process_pass(self, chunks: List[List[Dict]], meeting_context: str,
                             pass_fn, pass_name: str) -> str:
        """Process all chunks through a single pass, maintaining continuity."""
        logger.info(f"  {pass_name}: processing {len(chunks)} chunks...")

        processed_chunks = []
        prev_tail = ""

        # Process sequentially to maintain continuity between chunks
        for i, chunk in enumerate(chunks):
            if not chunk:
                continue
            chunk_text = self._segments_to_text(chunk) if isinstance(chunk[0], dict) else chunk
            if isinstance(chunk_text, list):
                chunk_text = "\n".join(chunk_text)

            result = await pass_fn(chunk_text, i, prev_tail, meeting_context)
            processed_chunks.append(result)

            # Keep tail for next chunk's continuity
            prev_tail = result[-500:] if result else ""

            if i < len(chunks) - 1:
                await asyncio.sleep(INTER_CALL_DELAY_S)

        return "\n\n".join(processed_chunks)

    async def process_async(self, transcript: Dict) -> Dict:
        """Run both post-processing passes on a transcript (async version)."""
        if not self.api_key:
            logger.warning("No Gemini API key — skipping post-processing")
            return transcript

        meeting_context = self._extract_meeting_context(transcript)
        all_segments = self._flatten_segments(transcript)

        if not all_segments:
            logger.warning("No segments to post-process")
            return transcript

        logger.info(f"Post-processing {len(all_segments)} segments in 2 passes...")

        # ── Pass 1: Segment correction ───────────────────────────────────
        chunks = self._chunk_segments(all_segments)
        pass1_text = await self._process_pass(
            chunks, meeting_context, self._pass1_correct_chunk, "Pass 1 (correction)"
        )

        # ── Pass 2: Register conversion ──────────────────────────────────
        # Re-chunk the pass 1 output as plain text blocks
        lines = pass1_text.split("\n")
        text_chunks = []
        chunk_size = 80  # ~80 lines per chunk for pass 2
        for i in range(0, len(lines), chunk_size):
            text_chunks.append(lines[i:i + chunk_size])

        pass2_text = await self._process_pass(
            text_chunks, meeting_context, self._pass2_polish_chunk, "Pass 2 (register)"
        )

        # ── Rebuild transcript structure ─────────────────────────────────
        result = self._rebuild_transcript(transcript, pass2_text)
        logger.info(f"Post-processing complete. {result.get('total_segments', 0)} segments in output.")
        return result

    def process(self, transcript: Dict) -> Dict:
        """Run both post-processing passes on a transcript (sync wrapper)."""
        try:
            loop = asyncio.get_running_loop()
            return loop.run_until_complete(self.process_async(transcript))
        except RuntimeError:
            return asyncio.run(self.process_async(transcript))
