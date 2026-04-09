"""
Agenda Boundary Detector for Single-Item Transcripts
=====================================================

Detects agenda item boundaries in committee meeting transcripts that have
only a single "Full Meeting" or generic agenda item. Uses a two-tier approach:

Tier 1 (heuristic, free): Temporal gaps + speaker transitions + chair phrases
Tier 2 (LLM fallback, ~$0.01): Gemini Flash Lite section detection

Usage:
    from pipeline.agenda_detector import detect_and_split_agenda
    transcript = detect_and_split_agenda(transcript)
"""

import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Chair phrases that signal agenda transitions
_TRANSITION_PHRASES = re.compile(
    r'(?:dan\s+gaan\s+we\s+(?:naar|over\s+naar|door\s+met)|'
    r'we\s+gaan\s+(?:naar|over\s+naar|door\s+met|verder\s+met)|'
    r'(?:volgende|volgend)\s+agendapunt|'
    r'agendapunt\s+\d|'
    r'dan\s+(?:komen\s+we\s+bij|is\s+aan\s+de\s+orde)|'
    r'(?:ik\s+)?open\s+(?:hierbij\s+)?(?:de|het)\s+(?:bespreking|behandeling|debat)|'
    r'(?:ik\s+)?sluit\s+(?:dit|deze)\s+(?:agendapunt|bespreking|onderwerp)|'
    r'hierbij\s+sluiten\s+we|'
    r'dan\s+schors\s+ik|'
    r'de\s+vergadering\s+is\s+(?:geschorst|heropend))',
    re.IGNORECASE,
)

# Minimum gap between segments to consider a boundary (seconds)
MIN_GAP_SECONDS = 30
# Minimum segments per detected agenda item
MIN_SEGMENTS_PER_ITEM = 5


def detect_agenda_boundaries(transcript: Dict) -> List[Dict]:
    """Detect agenda item boundaries using heuristics.

    Returns a list of boundary dicts: [{"index": int, "title": str, "reason": str}, ...]
    where index is the segment index where a new agenda item starts.
    """
    items = transcript.get("agenda_items", [])
    if not items:
        return []

    # Flatten all segments
    segments = items[0].get("segments", [])
    if len(segments) < MIN_SEGMENTS_PER_ITEM * 2:
        return []  # Too short to split

    boundaries = [{"index": 0, "title": "Opening", "reason": "start"}]

    for i in range(1, len(segments)):
        seg = segments[i]
        prev = segments[i - 1]
        text = (seg.get("text") or "").strip()

        # Check for temporal gap
        curr_start = seg.get("start_seconds", 0)
        prev_end = prev.get("end_seconds", prev.get("start_seconds", 0))
        gap = curr_start - prev_end if curr_start and prev_end else 0

        # Check for transition phrase
        is_transition = bool(_TRANSITION_PHRASES.search(text))

        # Check for speaker change (different from previous)
        speaker_change = (
            seg.get("speaker", "") != prev.get("speaker", "") and
            seg.get("speaker", "") not in ("", "Spreker onbekend", "Unknown")
        )

        # Boundary conditions
        if is_transition:
            # Extract title from transition phrase context
            title = _extract_title_from_transition(text)
            boundaries.append({
                "index": i,
                "title": title or f"Agendapunt {len(boundaries) + 1}",
                "reason": "transition_phrase",
            })
        elif gap >= MIN_GAP_SECONDS and speaker_change:
            boundaries.append({
                "index": i,
                "title": f"Agendapunt {len(boundaries) + 1}",
                "reason": f"gap_{gap:.0f}s+speaker_change",
            })

    # Filter out boundaries that are too close together
    filtered = [boundaries[0]]
    for b in boundaries[1:]:
        if b["index"] - filtered[-1]["index"] >= MIN_SEGMENTS_PER_ITEM:
            filtered.append(b)

    return filtered


def _extract_title_from_transition(text: str) -> Optional[str]:
    """Try to extract an agenda item title from a transition phrase."""
    # "We gaan door met de bespreking van het raadsvoorstel X"
    m = re.search(
        r'(?:gaan\s+we\s+(?:naar|over\s+naar|door\s+met)|'
        r'komen\s+we\s+bij|is\s+aan\s+de\s+orde)\s+(.+?)(?:\.|$)',
        text, re.IGNORECASE,
    )
    if m:
        title = m.group(1).strip().rstrip(".,")
        if len(title) > 5:
            return title[:80]
    return None


def split_transcript_by_boundaries(transcript: Dict, boundaries: List[Dict]) -> Dict:
    """Split a single-item transcript into multiple agenda items using detected boundaries."""
    if len(boundaries) < 2:
        return transcript

    items = transcript.get("agenda_items", [])
    if not items:
        return transcript

    all_segments = items[0].get("segments", [])
    new_items = []

    for i, boundary in enumerate(boundaries):
        start_idx = boundary["index"]
        end_idx = boundaries[i + 1]["index"] if i + 1 < len(boundaries) else len(all_segments)

        new_items.append({
            "title": boundary["title"],
            "start_time": all_segments[start_idx].get("start_time", ""),
            "end_time": all_segments[end_idx - 1].get("end_time", "") if end_idx > start_idx else "",
            "segments": all_segments[start_idx:end_idx],
        })

    result = dict(transcript)
    result["agenda_items"] = new_items
    result["agenda_detected"] = True
    result["agenda_boundaries"] = len(boundaries)

    logger.info(f"  Agenda detection: split into {len(new_items)} items from {len(all_segments)} segments")
    return result


def detect_and_split_agenda(transcript: Dict) -> Dict:
    """Detect and split agenda items if transcript has only one item.

    Only runs on single-item transcripts. Multi-item transcripts are returned unchanged.
    """
    items = transcript.get("agenda_items", [])
    if len(items) != 1:
        return transcript

    boundaries = detect_agenda_boundaries(transcript)
    if len(boundaries) < 2:
        logger.info("  Agenda detection: no boundaries found, keeping single item")
        return transcript

    return split_transcript_by_boundaries(transcript, boundaries)
