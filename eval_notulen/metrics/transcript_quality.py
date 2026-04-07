"""
Transcript Quality Metrics for Virtual Notulen

Metrics computed from the raw transcript JSON (from staging cache):

  - NEER (Named Entity Error Rate):
      Fraction of political entity occurrences that are misspelled.
      Checks against rotterdam_political_dictionary.json common_transcription_errors.

  - Speaker Attribution Rate:
      Fraction of segments with a valid speaker label.
      Whisper-only transcripts often have lower attribution.

  - Segment Quality:
      Text density (avg chars/words per segment), short-segment rate,
      Whisper confidence distribution.

  - Agenda Coverage:
      Fraction of agenda items with substantive content (>50 chars).

All functions accept the transcript dict format produced by the pipeline
(keys: meeting_name, date, agenda_items[{title, segments[{speaker, party, text,
start_seconds, confidence}]}]).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List


# ── Dictionary loading ────────────────────────────────────────────────────────

def load_political_dictionary(path: str = None) -> Dict:
    """Load the Rotterdam political dictionary from disk."""
    if path is None:
        path = str(
            Path(__file__).resolve().parent.parent.parent
            / "data" / "lexicons" / "rotterdam_political_dictionary.json"
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ── NEER ──────────────────────────────────────────────────────────────────────

def compute_neer(segments: List[Dict], dictionary: Dict) -> Dict:
    """
    Named Entity Error Rate: fraction of political entity occurrences that are
    misspelled according to the common_transcription_errors mapping.

    The dictionary's common_transcription_errors is expected to be:
        {correct_form: [wrong_form1, wrong_form2, ...]}

    Returns NEER = error_count / (correct_count + error_count).
    Lower is better (0.0 = perfect).
    """
    if not segments or not dictionary:
        return {"neer": None, "total_occurrences": 0, "error_count": 0, "error_examples": []}

    # Build lookup: wrong_lower → correct
    error_map: Dict[str, str] = {}
    for correct, wrongs in dictionary.get("common_transcription_errors", {}).items():
        for wrong in wrongs:
            error_map[wrong.lower()] = correct

    # All known-correct entity tokens (parties, surnames, terms)
    known_tokens: set = set()
    for party in dictionary.get("parties", []):
        # Multi-word parties: add each word token individually
        for tok in party.lower().split():
            known_tokens.add(re.sub(r"[^\w-]", "", tok))
    for surname in dictionary.get("council_members", {}).get("surnames", []):
        known_tokens.add(surname.lower())
    for term in dictionary.get("municipal_terms", []):
        known_tokens.add(term.lower())

    correct_count = 0
    error_count = 0
    error_examples: List[Dict] = []

    for seg in segments:
        text = seg.get("text", "")
        party_field = seg.get("party", "")

        # Check party field directly
        if party_field:
            party_lower = party_field.lower()
            if party_lower in error_map:
                error_count += 1
                error_examples.append({"wrong": party_field, "correct": error_map[party_lower]})
            else:
                correct_count += 1

        # Scan text tokens — skip anything shorter than 4 chars to avoid
        # false positives from common letter sequences that happen to match
        # short phonetic variants in the error map (e.g. "u" → "Erasmusbrug")
        for word in text.split():
            token = re.sub(r"[^\w-]", "", word).lower()
            if len(token) < 4:
                continue
            if token in error_map:
                error_count += 1
                if len(error_examples) < 15:
                    error_examples.append({"wrong": word, "correct": error_map[token]})
            elif token in known_tokens:
                correct_count += 1

    total = correct_count + error_count
    neer = round(error_count / total, 4) if total > 0 else None

    return {
        "neer": neer,
        "total_occurrences": total,
        "correct_count": correct_count,
        "error_count": error_count,
        "error_examples": error_examples[:10],
    }


# ── Speaker attribution ───────────────────────────────────────────────────────

_UNKNOWN_LABELS = {"", "spreker onbekend", "unknown", "onbekend", "speaker unknown"}


def compute_speaker_attribution(segments: List[Dict]) -> Dict:
    """
    Speaker Attribution Rate: fraction of segments with a non-empty, non-generic
    speaker label.

    Also reports party attribution rate separately (party field non-empty).
    """
    if not segments:
        return {
            "attribution_rate": 0.0,
            "party_attribution_rate": 0.0,
            "total_segments": 0,
            "attributed_segments": 0,
            "unattributed_segments": 0,
        }

    total = len(segments)
    attributed = sum(
        1 for seg in segments
        if (seg.get("speaker") or "").strip().lower() not in _UNKNOWN_LABELS
    )
    with_party = sum(
        1 for seg in segments
        if (seg.get("party") or "").strip()
    )

    return {
        "attribution_rate": round(attributed / total, 4),
        "party_attribution_rate": round(with_party / total, 4),
        "total_segments": total,
        "attributed_segments": attributed,
        "unattributed_segments": total - attributed,
    }


# ── Segment quality ───────────────────────────────────────────────────────────

def compute_segment_quality(segments: List[Dict]) -> Dict:
    """
    Segment-level quality metrics:
      - Total word/char count
      - Average chars and words per segment
      - Short segment rate (< 10 words — likely a transcription fragment)
      - Whisper confidence stats (if present)
    """
    if not segments:
        return {}

    texts = [seg.get("text", "").strip() for seg in segments]
    nonempty_texts = [t for t in texts if t]

    char_counts = [len(t) for t in nonempty_texts]
    word_counts = [len(t.split()) for t in nonempty_texts]

    confidences = [
        seg["confidence"] for seg in segments
        if isinstance(seg.get("confidence"), (int, float))
    ]

    total_segs = len(segments)
    short_count = sum(1 for wc in word_counts if wc < 10)

    return {
        "total_segments": total_segs,
        "nonempty_segments": len(nonempty_texts),
        "total_words": sum(word_counts),
        "total_chars": sum(char_counts),
        "avg_words_per_segment": round(sum(word_counts) / len(word_counts), 1) if word_counts else 0,
        "avg_chars_per_segment": round(sum(char_counts) / len(char_counts), 1) if char_counts else 0,
        "short_segment_rate": round(short_count / total_segs, 4) if total_segs > 0 else 0.0,
        "avg_confidence": round(sum(confidences) / len(confidences), 4) if confidences else None,
        "low_confidence_rate": (
            round(sum(1 for c in confidences if c < 0.5) / len(confidences), 4)
            if confidences else None
        ),
        "has_confidence_scores": len(confidences) > 0,
    }


# ── Agenda coverage ───────────────────────────────────────────────────────────

def compute_agenda_coverage(transcript: Dict) -> Dict:
    """
    Agenda Coverage: fraction of agenda items that have substantive content
    (at least one segment with more than 50 characters of text).
    """
    items = transcript.get("agenda_items", [])
    if not items:
        return {"agenda_items_total": 0, "items_with_content": 0, "coverage_rate": 0.0, "items": []}

    items_with_content = sum(
        1 for item in items
        if any(
            len(seg.get("text", "").strip()) > 50
            for seg in item.get("segments", [])
        )
    )

    return {
        "agenda_items_total": len(items),
        "items_with_content": items_with_content,
        "coverage_rate": round(items_with_content / len(items), 4),
        "items": [
            {
                "title": item.get("title", ""),
                "segment_count": len(item.get("segments", [])),
            }
            for item in items
        ],
    }


# ── Speaker diversity ─────────────────────────────────────────────────────────

def compute_speaker_diversity(segments: List[Dict]) -> Dict:
    """
    Count unique speakers and their segment counts.
    Useful for detecting monologue-heavy transcripts (sign of poor diarization).
    """
    if not segments:
        return {}

    from collections import Counter
    speaker_counts: Counter = Counter()
    party_counts: Counter = Counter()

    for seg in segments:
        sp = (seg.get("speaker") or "").strip()
        pt = (seg.get("party") or "").strip()
        if sp and sp.lower() not in _UNKNOWN_LABELS:
            speaker_counts[sp] += 1
        if pt:
            party_counts[pt] += 1

    top_speaker = speaker_counts.most_common(1)[0] if speaker_counts else None
    monologue_rate = (
        round(top_speaker[1] / len(segments), 4)
        if top_speaker and len(segments) > 0 else 0.0
    )

    return {
        "unique_speakers": len(speaker_counts),
        "unique_parties": len(party_counts),
        "top_speaker": top_speaker[0] if top_speaker else None,
        "top_speaker_segment_count": top_speaker[1] if top_speaker else 0,
        "monologue_rate": monologue_rate,  # fraction dominated by a single speaker
        "speakers": dict(speaker_counts.most_common(10)),
        "parties": dict(party_counts.most_common()),
    }


# ── Combined runner ───────────────────────────────────────────────────────────

def flatten_segments(transcript: Dict) -> List[Dict]:
    """Extract all segments from a transcript across all agenda items."""
    all_segments = []
    for item in transcript.get("agenda_items", []):
        all_segments.extend(item.get("segments", []))
    return all_segments


def run_all_transcript_quality(transcript: Dict, dictionary: Dict = None) -> Dict:
    """
    Run all transcript quality metrics and return a combined result dict.

    Args:
        transcript: The transcript JSON from the staging cache.
        dictionary: The Rotterdam political dictionary. Loaded from disk if not provided.

    Returns:
        Dict with keys: neer, speaker_attribution, speaker_diversity,
                        segment_quality, agenda_coverage.
    """
    if dictionary is None:
        dictionary = load_political_dictionary()

    all_segments = flatten_segments(transcript)

    return {
        "neer": compute_neer(all_segments, dictionary),
        "speaker_attribution": compute_speaker_attribution(all_segments),
        "speaker_diversity": compute_speaker_diversity(all_segments),
        "segment_quality": compute_segment_quality(all_segments),
        "agenda_coverage": compute_agenda_coverage(transcript),
    }
