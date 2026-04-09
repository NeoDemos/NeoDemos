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
import re
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
PASS2_MODEL = "gemini-2.5-flash-lite"


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

    # ── VTT Pre-Cleaning ─────────────────────────────────────────────────

    def _preclean_segment_text(self, text: str) -> str:
        """Remove VTT captioning artifacts before LLM processing.

        Handles: repeated-char noise (*** ***, ED ED ED), dot-gap sequences,
        Whisper hallucination loops (hum hum hum), and normalises ellipsis.
        """
        if not text:
            return text

        # 1. Star artifacts: "*** *** ***" or "* * * *"
        text = re.sub(r'(\*\s*){3,}', '', text)

        # 2. Repeated-caps noise: "ED ED ED ED" (3+ identical upper tokens)
        text = re.sub(r'\b([A-Z]{2,})(?:\s+\1){2,}\b', '', text)

        # 3. Whisper hallucination loops: "hum hum hum …" (3+ repeated words)
        text = re.sub(r'\b(\w+)(?:\s+\1){2,}\b', r'\1', text, flags=re.IGNORECASE)

        # 4. VTT dot-gap sequences: "... ...text... ...more"  →  "text more"
        #    Also bare ". . . ." sequences
        text = re.sub(r'(?:\.\s*){3,}', '… ', text)       # normalise to single ellipsis
        text = re.sub(r'…\s*…+', '… ', text)               # collapse consecutive ellipses

        # 5. Strip leading/trailing ellipsis (VTT gap markers, not meaningful punctuation)
        text = re.sub(r'^\s*…\s*', '', text)
        text = re.sub(r'\s*…\s*$', '', text)
        # Replace mid-sentence ellipsis with space (VTT dropped words, not meaningful pause)
        text = re.sub(r'\s*…\s*', ' ', text)

        # 6. Disfluency removal: strip fillers like "uh", "uh,", "een uh inspreker"
        #    Pattern: optional comma before, the filler, optional comma/period after
        text = re.sub(r',?\s*\b(?:eh|uhm?|uh|hmm?)\b[,.]?\s*', ' ', text, flags=re.IGNORECASE)
        # Strip filler words at segment start when followed by lowercase continuation
        text = re.sub(r'^(?:nou\s+ja|zeg\s+maar|eigenlijk)\s*[,.]\s*(?=[a-z])', '', text, flags=re.IGNORECASE)

        # 7. Trim whitespace runs introduced by removals
        text = re.sub(r'  +', ' ', text).strip()

        # 8. Drop segments that became empty or near-empty after cleaning
        if len(text) < 3:
            return ''

        return text

    def _preclean_transcript(self, transcript: Dict) -> Dict:
        """Run pre-cleaning on all segments. Returns a modified copy.

        Computes artifact_rate based on segments DROPPED (empty after cleaning),
        not segments merely modified (e.g. disfluency removal).
        """
        result = dict(transcript)
        total = 0
        cleaned = 0
        dropped = 0

        for item in result.get('agenda_items', []):
            new_segments = []
            for seg in item.get('segments', []):
                total += 1
                original = seg.get('text', '')
                clean = self._preclean_segment_text(original)
                if clean != original:
                    cleaned += 1
                if clean:
                    seg_copy = dict(seg)
                    seg_copy['text'] = clean
                    new_segments.append(seg_copy)
                else:
                    dropped += 1
            item['segments'] = new_segments

        # artifact_rate = fraction of segments DROPPED (truly garbage),
        # not merely modified (disfluency cleanup doesn't count)
        artifact_rate = dropped / total if total else 0
        result['preclean_stats'] = {
            'total_segments': total,
            'segments_cleaned': cleaned,
            'segments_dropped': dropped,
            'artifact_rate': round(artifact_rate, 3),
        }
        logger.info(f"  Pre-clean: {cleaned}/{total} segments touched, "
                     f"{dropped} dropped, artifact rate {artifact_rate:.1%}")
        return result

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

    def _segments_to_text(self, segments: List[Dict], numbered: bool = True) -> str:
        """Convert segments to readable text for LLM processing.

        When numbered=True, each line gets a [SEG-NNN] prefix as an anchor
        to prevent the LLM from merging adjacent same-speaker segments.
        """
        lines = []
        seg_idx = 0
        for seg in segments:
            speaker = seg.get("speaker", "")
            party = seg.get("party", "")
            text = seg.get("text", "").strip()
            if not text:
                continue

            prefix = f"[{speaker}]" if speaker else "[Spreker onbekend]"
            if party:
                prefix = f"[{speaker} ({party})]"

            seg_idx += 1
            if numbered:
                lines.append(f"[SEG-{seg_idx:03d}]{prefix}: {text}")
            else:
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

    # ── Prompt Builders (small-batch mode) ─────────────────────────────

    def _build_pass1_prompt(self, batch_text: str, meeting_context: str) -> str:
        """Build Pass 1 prompt for a small batch of segments."""
        dict_context = self._build_dictionary_context()
        return f"""Je bent een transcriptie-editor die automatische ondertiteling corrigeert van een vergadering van de Rotterdamse gemeenteraad.

CONTEXT:
{meeting_context}

WOORDENLIJST (gebruik deze exacte schrijfwijzen):
{dict_context}

INSTRUCTIES:
1. Corrigeer spel- en typfouten, vooral bij namen van raadsleden en partijen
2. Herstel leestekens (punten, komma's, vraagtekens)
3. Schrijf afkortingen correct (B en W, COR, iBabs)
4. KRITIEK: Behoud ELKE [SEG-NNN] regel als apart segment. Produceer EXACT hetzelfde aantal regels.
5. Verander NIETS aan de inhoudelijke betekenis

TRANSCRIPT:
{batch_text}

GECORRIGEERD TRANSCRIPT:"""

    def _build_pass2_prompt(self, batch_text: str, meeting_context: str) -> str:
        """Build Pass 2 prompt for a small batch of segments."""
        dict_context = self._build_dictionary_context()
        return f"""Je bent een professionele notulist van de gemeente Rotterdam. Zet deze transcriptie-segmenten om naar geschreven Nederlands.

CONTEXT:
{meeting_context}

WOORDENLIJST:
{dict_context}

INSTRUCTIES:
1. Zet gesproken Nederlands om naar geschreven Nederlands
2. HERSTEL ONTBREKENDE WOORDEN: vul ontbrekende lidwoorden, voorzetsels en hulpwerkwoorden aan waar de bedoeling duidelijk is
3. KRITIEK: Behoud ELKE [SEG-NNN] regel. Zelfde aantal regels in output als input. VOEG NIET SAMEN.
4. Behoud alle namen, cijfers, datums en politieke standpunten exact

TRANSCRIPT:
{batch_text}

GESCHREVEN NOTULEN:"""

    # ── Pass 1: Segment-Level Correction (legacy chunk mode) ─────────

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
4. Behoud de [SEG-NNN][Spreker (Partij)]: prefix-structuur EXACT
5. KRITIEK: Elke [SEG-NNN] prefix is een apart segment. Produceer EXACT hetzelfde aantal [SEG-NNN] regels als in de input. VOEG GEEN segmenten samen. Elk input-segment moet exact één output-segment opleveren.
6. Verander NIETS aan de inhoudelijke betekenis
7. Voeg geen informatie toe die er niet was

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
2. HERSTEL ONTBREKENDE WOORDEN:
   - Ondertiteling laat soms woorden weg (lidwoorden, voorzetsels, werkwoorden)
   - Als een zin grammaticaal onvolledig is maar de bedoeling duidelijk, vul het ontbrekende woord aan
   - Bij twijfel: laat de zin intact, voeg GEEN woorden toe waarvan je de betekenis niet zeker weet
   - Let speciaal op: ontbrekende lidwoorden (de/het/een), voorzetsels (in/op/van/aan), en hulpwerkwoorden (is/heeft/wordt)
3. BEHOUD:
   - Alle politieke standpunten en uitspraken exact zoals bedoeld
   - Specifieke namen, cijfers, datums en bedragen
   - De toon en strekking van het debat
   - De [SEG-NNN][Spreker (Partij)]: prefix-structuur
4. KRITIEK — SEGMENTEN NIET SAMENVOEGEN:
   - Behoud elk [SEG-NNN] als apart segment
   - VOEG NOOIT meerdere segmenten samen tot één alinea
   - De segment-nummering moet intact blijven: zelfde aantal [SEG-NNN] regels in output als in input
5. VERWIJDER NIET:
   - Inhoudelijke uitspraken die als "ja" of "nee" een stemming of reactie zijn
   - Interrupties die inhoudelijk relevant zijn

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

    def _rebuild_transcript(self, transcript: Dict, processed_text: str,
                            expected_count: int = 0) -> Dict:
        """Rebuild the transcript structure from processed text.

        Parses [SEG-NNN][Speaker (Party)]: blocks back into segments.
        Falls back to old format if the LLM stripped segment numbers.
        """

        # Try numbered format first: [SEG-NNN][Speaker (Party)]: text
        numbered_pattern = re.compile(
            r'\[SEG-\d+\]\[([^\]]+)\]:\s*(.*?)(?=\n\[SEG-|\Z)', re.DOTALL
        )
        # Fallback: [Speaker (Party)]: text (old format)
        fallback_pattern = re.compile(
            r'\[([^\]]+)\]:\s*(.*?)(?=\n\[|\Z)', re.DOTALL
        )

        matches = list(numbered_pattern.finditer(processed_text))
        if not matches:
            logger.info("  LLM stripped segment numbers, using fallback parser")
            matches = list(fallback_pattern.finditer(processed_text))

        new_segments = []
        for match in matches:
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

        # Segment collapse validation
        if expected_count and new_segments:
            retention = len(new_segments) / expected_count
            if retention < 0.7:
                logger.warning(
                    f"  SEGMENT COLLAPSE: {expected_count} input -> {len(new_segments)} output "
                    f"({retention:.0%} retention). LLM may have merged segments."
                )

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

        # ── Pre-clean: strip VTT artifacts (free, no LLM) ───────────
        transcript = self._preclean_transcript(transcript)
        preclean_stats = transcript.get('preclean_stats', {})
        all_segments = self._flatten_segments(transcript)

        # Guard rail: skip LLM passes for heavily-damaged single-item transcripts.
        # When >40% of segments are artifacts AND there's only one agenda item,
        # the LLM tends to collapse everything into a summary instead of cleaning
        # segment-by-segment. Pre-clean alone is safer here.
        n_items = len(transcript.get('agenda_items', []))
        artifact_rate = preclean_stats.get('artifact_rate', 0)
        if artifact_rate > 0.40 and n_items <= 1:
            logger.warning(
                f"  High artifact rate ({artifact_rate:.0%}) with single agenda item — "
                f"skipping LLM passes to prevent summary collapse. Pre-clean only."
            )
            return transcript

        logger.info(f"Post-processing {len(all_segments)} segments in small-batch mode...")

        # ── Small-batch processing ───────────────────────────────────────
        # Send groups of BATCH_SIZE segments per LLM call. Small enough that
        # the LLM can't aggressively merge, large enough for context.
        BATCH_SIZE = 5
        batches = []
        for i in range(0, len(all_segments), BATCH_SIZE):
            batches.append(all_segments[i:i + BATCH_SIZE])

        logger.info(f"  {len(batches)} batches of ~{BATCH_SIZE} segments (2 passes each)...")

        # Parse each batch result back into per-segment dicts directly
        numbered_seg_re = re.compile(r'\[SEG-\d+\]\[([^\]]+)\]:\s*(.*?)(?=\n\[SEG-|\Z)', re.DOTALL)
        fallback_seg_re = re.compile(r'\[([^\]]+)\]:\s*(.*?)(?=\n\[|\Z)', re.DOTALL)

        new_segments = []
        for bi, batch in enumerate(batches):
            batch_text = self._segments_to_text(batch)

            # Pass 1: correction
            p1_result = await self._call_gemini(
                self._build_pass1_prompt(batch_text, meeting_context), model=PASS1_MODEL
            )
            if not p1_result:
                p1_result = batch_text

            # Pass 2: register conversion
            p2_result = await self._call_gemini(
                self._build_pass2_prompt(p1_result.strip(), meeting_context), model=PASS2_MODEL
            )
            if not p2_result:
                p2_result = p1_result

            # Parse output into segments
            matches = list(numbered_seg_re.finditer(p2_result))
            if not matches:
                matches = list(fallback_seg_re.finditer(p2_result))

            if matches:
                for m in matches:
                    speaker_str = m.group(1).strip()
                    text = m.group(2).strip()
                    if not text:
                        continue
                    party_m = re.match(r'^(.+?)\s*\(([^)]+)\)$', speaker_str)
                    if party_m:
                        speaker, party = party_m.group(1).strip(), party_m.group(2).strip()
                    else:
                        speaker, party = speaker_str, ""
                    new_segments.append({"speaker": speaker, "party": party,
                                         "text": text, "confidence": 1.0})
            else:
                # Fallback: keep original batch segments with cleaned text
                for seg in batch:
                    new_segments.append(dict(seg))

            if (bi + 1) % 20 == 0:
                logger.info(f"    [{bi + 1}/{len(batches)}] batches done")

            await asyncio.sleep(INTER_CALL_DELAY_S)

        # ── Rebuild transcript structure (distribute into agenda items) ──
        result = dict(transcript)
        original_items = transcript.get("agenda_items", [])
        total_orig = sum(len(item.get("segments", [])) for item in original_items)

        if total_orig and new_segments:
            seg_idx = 0
            new_items = []
            for item in original_items:
                orig_count = len(item.get("segments", []))
                proportion = orig_count / total_orig
                take = max(1, round(proportion * len(new_segments)))
                new_item = dict(item)
                new_item["segments"] = new_segments[seg_idx:seg_idx + take]
                seg_idx += take
                new_items.append(new_item)
            if seg_idx < len(new_segments) and new_items:
                new_items[-1]["segments"].extend(new_segments[seg_idx:])
            result["agenda_items"] = new_items

        result["post_processed"] = True
        result["total_segments"] = len(new_segments)
        retention = len(new_segments) / len(all_segments) if all_segments else 0
        logger.info(f"Post-processing complete. {len(all_segments)} input -> "
                     f"{len(new_segments)} output segments ({retention:.0%} retention).")
        return result

    def process(self, transcript: Dict) -> Dict:
        """Run both post-processing passes on a transcript (sync wrapper)."""
        try:
            loop = asyncio.get_running_loop()
            return loop.run_until_complete(self.process_async(transcript))
        except RuntimeError:
            return asyncio.run(self.process_async(transcript))
