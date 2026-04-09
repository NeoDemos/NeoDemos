"""
Speaker Inference Enricher
==========================

Recovers speaker attribution for VTT transcripts that have no speaker tags
by parsing parliamentary address patterns embedded in the text.

Dutch committee meetings follow a consistent protocol:
  - Chair calls on speakers: "De heer Tak." / "Mevrouw De Jong." (standalone)
  - Chair hands over: "Dan geef ik het woord aan wethouder Kasmi"
  - Inscrekers introduce themselves: "Mijn naam is Natascha Canta"
  - Implicit chair speech: procedural text between handovers

The enricher assigns speakers to segments using a state machine:
  current_speaker = last identified speaker from address cue
  When a new cue is found → switch current_speaker
  Segments between cues → attributed to current_speaker

After inference, the transcript JSON is updated in-place and the staging
chunks can be re-ingested with speaker prefixes.

Usage:
    enricher = SpeakerInferenceEnricher()
    updated_transcript = enricher.enrich(transcript_json)
    stats = enricher.last_stats  # attribution rate, cues found, etc.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# ── Regex patterns ────────────────────────────────────────────────────────────

# Standalone address: "De heer Tak." / "Mevrouw De Jong." (short segment, mostly a name)
_STANDALONE_ADDRESS = re.compile(
    r'^(?:(?:de heer|mevrouw|wethouder|burgemeester|commissievoorzitter|voorzitter)\s+)?'
    r'([A-Z][a-zÀ-ÿ\u0080-\u024F][a-zÀ-ÿ\u0080-\u024F\-]{1,}(?:\s+[A-Z][a-zÀ-ÿ\u0080-\u024F\-]+)?)'
    r'[.,]?\s*$',
    re.IGNORECASE
)

# Address within longer text: "Dan geef ik het woord aan de heer Buijt"
_HANDOVER = re.compile(
    r'(?:geef\s+(?:ik\s+)?(?:het\s+woord\s+)?aan|'
    r'het\s+woord\s+(?:is\s+)?aan|'
    r'als\s+eerste\s+woord\s+aan|'
    r'als\s+(?:eerste|volgende|laatste)\s+(?:aan\s+)?)'
    r'\s*(?:de\s+heer|mevrouw|wethouder|burgemeester|commissievoorzitter)?\s*'
    r'([A-Z][a-zÀ-ÿ\u0080-\u024F][a-zÀ-ÿ\u0080-\u024F\-]{1,})',
    re.IGNORECASE
)

# Inline address cue anywhere in text: "De heer Tak." or "Mevrouw De Jong,"
_INLINE_ADDRESS = re.compile(
    r'\b(de\s+heer|mevrouw|wethouder|burgemeester)\s+'
    r'([A-Z][a-zÀ-ÿ\u0080-\u024F][a-zÀ-ÿ\u0080-\u024F\-]{1,})',
    re.IGNORECASE
)

# Self-introduction by inspreker: "Mijn naam is Firstname Lastname"
_SELF_INTRO = re.compile(
    r'mijn naam is\s+([A-Z][a-zÀ-ÿ\u0080-\u024F]+(?:\s+[A-Za-zÀ-ÿ\u0080-\u024F\-]+){1,3})',
    re.IGNORECASE
)

# Procedural phrases spoken by chair (don't switch speaker, chair speaks these)
_CHAIR_PHRASES = re.compile(
    r'^(dank\s+u\s+(?:wel)?[.,]?|'
    r'u\s+kunt\s+de\s+microfoon|'
    r'dan\s+(?:gaan\s+we|ga\s+ik|kunnen\s+we)|'
    r'(?:goed|prima|oké)[.,]?\s*(?:dan|dank)?|'
    r'(?:eventjes|even)\s+kijken)',
    re.IGNORECASE
)

# Surnames to SKIP (too generic / common Dutch words misidentified as names)
_SKIP_NAMES = {
    "dan", "het", "een", "die", "dat", "zijn", "maar", "ook", "nog", "wel",
    "al", "dus", "want", "als", "met", "van", "de", "der", "den", "ter",
    "ja", "nee", "goed", "prima", "dank", "kort",
    # Phase 4A: common Dutch interjections/greetings that become false speaker names
    "jawel", "jazeker", "inderdaad", "precies", "absoluut",
    "collega", "goedemiddag", "goedemorgen", "goedenavond",
    "dankuwel", "alstublieft", "natuurlijk", "uiteraard",
    "akkoord", "correct", "helemaal", "helaas", "makka",
    "aanname", "kijken", "eventjes",
}


class SpeakerInferenceEnricher:
    """
    Infers speaker attribution from parliamentary address patterns.

    Works on transcript JSON dicts as produced by the pipeline
    (agenda_items[].segments[].{text, speaker, party, ...}).
    """

    def __init__(self, political_dict_path: Optional[str] = None):
        self._known_surnames: set = set()
        self._surname_to_party: Dict[str, str] = {}
        if political_dict_path:
            self._load_political_dict(political_dict_path)
        else:
            # Auto-discover from default location
            default = Path(__file__).resolve().parent.parent / "data" / "lexicons" / "rotterdam_political_dictionary.json"
            if default.exists():
                self._load_political_dict(str(default))

        self.last_stats: Dict = {}

    def _load_political_dict(self, path: str):
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            # Legacy surnames list
            surnames = d.get("council_members", {}).get("surnames", [])
            for s in surnames:
                last = s.split()[-1].lower()
                self._known_surnames.add(last)
                self._known_surnames.add(s.lower())

            # Enriched members dict (Phase 3A): surname -> {full_name, party, role}
            members = d.get("council_members", {}).get("members", {})
            for surname, info in members.items():
                last = surname.split()[-1].lower()
                self._known_surnames.add(last)
                self._known_surnames.add(surname.lower())
                party = info.get("party", "")
                if party:
                    self._surname_to_party[last] = party
                    self._surname_to_party[surname.lower()] = party
        except Exception as e:
            log.warning(f"Could not load political dictionary: {e}")

    # ── Core enrichment ───────────────────────────────────────────────────

    def enrich(self, transcript: Dict) -> Dict:
        """
        Enrich transcript in-place: assign inferred speakers to unattributed segments.
        Returns the modified transcript dict.
        """
        total_segs = 0
        inferred_segs = 0
        cues_found = 0

        for item in transcript.get("agenda_items", []):
            segments = item.get("segments", [])
            if not segments:
                continue

            # Only process items that have NO speaker attribution at all
            attributed = sum(1 for s in segments if s.get("speaker"))
            if attributed > 0:
                continue  # already has speakers, skip

            n_inferred, n_cues = self._infer_item_speakers(segments)
            inferred_segs += n_inferred
            cues_found += n_cues
            total_segs += len(segments)

        # Update top-level speaker stats
        all_segs = [s for item in transcript.get("agenda_items", []) for s in item.get("segments", [])]
        now_attributed = sum(1 for s in all_segs if s.get("speaker"))
        rate = now_attributed / len(all_segs) if all_segs else 0.0

        self.last_stats = {
            "total_segments": len(all_segs),
            "inferred_segments": inferred_segs,
            "cues_found": cues_found,
            "attribution_rate_after": rate,
        }

        # Update transcript metadata
        transcript["speaker_source"] = "inferred"
        transcript["total_speakers_detected"] = len({
            s.get("speaker") for s in all_segs if s.get("speaker")
        })

        return transcript

    def _infer_item_speakers(self, segments: List[Dict]) -> Tuple[int, int]:
        """
        Run the address-cue state machine over one agenda item's segments.
        Modifies segments in-place. Returns (n_inferred, n_cues).
        """
        current_speaker: Optional[str] = None
        current_role: Optional[str] = None
        n_inferred = 0
        n_cues = 0

        for i, seg in enumerate(segments):
            text = (seg.get("text") or "").strip()
            if not text:
                continue

            # 1. Check for self-introduction (inspreker)
            intro_match = _SELF_INTRO.search(text)
            if intro_match:
                name = intro_match.group(1).strip()
                if self._valid_name(name.split()[-1]):
                    current_speaker = name
                    current_role = "inspreker"
                    seg["speaker"] = name
                    seg["role"] = "inspreker"
                    n_cues += 1
                    n_inferred += 1
                    continue

            # 2. Check for explicit handover phrase
            handover_match = _HANDOVER.search(text)
            if handover_match:
                name = handover_match.group(1).strip()
                if self._valid_name(name):
                    current_speaker = name
                    current_role = self._infer_role(text, name)
                    n_cues += 1
                    # The handover text itself is spoken by the chair
                    seg["speaker"] = "Voorzitter"
                    seg["role"] = "voorzitter"
                    n_inferred += 1
                    continue

            # 3. Check for standalone short address segment: "De heer Tak."
            if len(text) < 60:
                standalone = self._extract_standalone_address(text)
                if standalone:
                    current_speaker = standalone
                    current_role = self._infer_role(text, standalone)
                    n_cues += 1
                    # This segment is the chair calling on someone — don't attribute to the person
                    seg["speaker"] = "Voorzitter"
                    seg["role"] = "voorzitter"
                    n_inferred += 1
                    continue

            # 4. Check for inline address mid-segment: ends with "De heer Buijt."
            inline = self._extract_inline_address(text)
            if inline:
                # Rest of this segment was spoken before the address, by current speaker
                if current_speaker:
                    seg["speaker"] = current_speaker
                    seg["role"] = current_role or "unknown"
                    n_inferred += 1
                current_speaker = inline
                current_role = self._infer_role(text, inline)
                n_cues += 1
                continue

            # 5. Chair procedural phrases — always the chair
            if _CHAIR_PHRASES.match(text) and 5 < len(text) < 80:
                seg["speaker"] = "Voorzitter"
                seg["role"] = "voorzitter"
                n_inferred += 1
                continue

            # 6. Attribute to current known speaker
            if current_speaker:
                seg["speaker"] = current_speaker
                seg["role"] = current_role or "unknown"
                n_inferred += 1

        return n_inferred, n_cues

    # ── Helpers ───────────────────────────────────────────────────────────

    # ── Phase 3B: Party attribution from dictionary ────────────────────

    def fill_party_from_dictionary(self, transcript: Dict) -> Dict:
        """Fill missing party fields by looking up speaker surnames in the dictionary."""
        filled = 0
        for item in transcript.get("agenda_items", []):
            for seg in item.get("segments", []):
                speaker = (seg.get("speaker") or "").strip()
                if not speaker or seg.get("party"):
                    continue  # already has party or no speaker

                # Try last token of speaker name
                last_token = speaker.split()[-1].lower().strip(".,")
                party = self._surname_to_party.get(last_token)
                if not party:
                    # Try full surname (for "van Dommelen" etc.)
                    party = self._surname_to_party.get(speaker.lower())
                if party:
                    seg["party"] = party
                    filled += 1

        if filled:
            log.info(f"  Party fill: {filled} segments enriched from dictionary")
        return transcript

    # ── Phase 4D: Garbage speaker filter ─────────────────────────────

    def filter_garbage_speakers(self, transcript: Dict) -> Dict:
        """Replace garbage speaker names with 'Spreker onbekend'.

        A speaker is considered garbage if:
        - Not in the political dictionary
        - Not matching inspreker/wethouder/voorzitter role patterns
        - Name is in _SKIP_NAMES or fails _valid_name()
        """
        cleaned = 0
        for item in transcript.get("agenda_items", []):
            for seg in item.get("segments", []):
                speaker = (seg.get("speaker") or "").strip()
                if not speaker or speaker in ("Voorzitter", "Spreker onbekend", "Unknown", "Inspreker"):
                    continue

                last_token = speaker.split()[-1].lower().strip(".,")
                # Keep if in dictionary
                if last_token in self._known_surnames:
                    continue
                if speaker.lower() in self._known_surnames:
                    continue
                # Keep if it has a known role
                role = (seg.get("role") or "").lower()
                if role in ("inspreker", "wethouder", "burgemeester", "voorzitter"):
                    continue
                # Fail: garbage name
                if not self._valid_name(last_token):
                    seg["speaker"] = "Spreker onbekend"
                    seg["party"] = ""
                    seg.pop("role", None)
                    cleaned += 1

        if cleaned:
            log.info(f"  Garbage filter: {cleaned} fake speaker names removed")
        return transcript

    # ── Inspreker Resolution ─────────────────────────────────────────────

    def resolve_insprekers(self, transcript: Dict) -> Dict:
        """Resolve generic 'Inspreker' labels to actual names.

        Scans for:
        1. Self-introductions within inspreker segments: "Mijn naam is Koen de Boo"
        2. Chair announcements before inspreker segments: "de heer De Boo" / "mevrouw Barrett"
        """
        resolved = 0
        for item in transcript.get("agenda_items", []):
            segments = item.get("segments", [])
            current_inspreker_name = None

            for i, seg in enumerate(segments):
                speaker = (seg.get("speaker") or "").strip()
                text = (seg.get("text") or "").strip()

                # Check if chair announces an inspreker in this segment
                # Pattern: "de heer/mevrouw X ... het woord" or "dan gaan we naar de heer X"
                if speaker and speaker.lower() != "inspreker":
                    # Chair/council member segment — check if they announce an inspreker
                    chair_intro = re.search(
                        r'(?:de\s+heer|mevrouw)\s+([A-Z][a-zÀ-ÿ\u0080-\u024F\-]{2,}'
                        r'(?:\s+[A-Za-zÀ-ÿ\u0080-\u024F\-]+)?)'
                        r'.*?(?:het\s+woord|mag\s+inspreken|inspreker|inspreek)',
                        text, re.IGNORECASE,
                    )
                    if chair_intro:
                        current_inspreker_name = chair_intro.group(1).strip()
                    continue

                if speaker.lower() != "inspreker":
                    current_inspreker_name = None
                    continue

                # This is an "Inspreker" segment — try to resolve
                # 1. Self-introduction in THIS segment
                intro = _SELF_INTRO.search(text)
                if intro:
                    name = intro.group(1).strip()
                    seg["speaker"] = name
                    seg["role"] = "inspreker"
                    current_inspreker_name = name
                    resolved += 1
                    continue

                # 2. Chair announced a name just before
                if current_inspreker_name:
                    seg["speaker"] = current_inspreker_name
                    seg["role"] = "inspreker"
                    resolved += 1
                    continue

        if resolved:
            log.info(f"  Inspreker resolution: {resolved} segments given actual names")
        return transcript

    # ── Helpers ───────────────────────────────────────────────────────────

    def _valid_name(self, surname: str) -> bool:
        """Return True if this looks like a real surname (not a common word)."""
        lower = surname.lower().strip(".,")
        if lower in _SKIP_NAMES:
            return False
        if len(lower) < 3:
            return False
        return True

    def _infer_role(self, text: str, name: str) -> str:
        lower = text.lower()
        if "wethouder" in lower:
            return "wethouder"
        if "burgemeester" in lower:
            return "burgemeester"
        if "voorzitter" in lower and name.lower() != "voorzitter":
            return "commissievoorzitter"
        if name.lower() in self._known_surnames or name.split()[-1].lower() in self._known_surnames:
            return "council_member"
        return "unknown"

    def _extract_standalone_address(self, text: str) -> Optional[str]:
        """Extract name from a short standalone address segment."""
        # Pattern: optional role + Name (possibly with preposition like "de", "van")
        # E.g. "De heer Tak.", "Mevrouw De Jong.", "Wethouder Kasmi."
        role_re = re.compile(
            r'^(?:(?:de\s+heer|mevrouw|wethouder|burgemeester|commissievoorzitter|voorzitter)\s+)?'
            r'((?:[A-Z][a-z\u00C0-\u024F]+\s+)?[A-Z][a-z\u00C0-\u024F\-]{2,})'
            r'[.,]?\s*$',
            re.IGNORECASE
        )
        m = role_re.match(text.strip())
        if m:
            name = m.group(1).strip()
            if self._valid_name(name.split()[-1]):
                return name
        return None

    def _extract_inline_address(self, text: str) -> Optional[str]:
        """Check if segment ends with an address cue for the NEXT speaker."""
        # Pattern: "...some text. De heer Buijt." at end of segment
        end_re = re.compile(
            r'(?:de\s+heer|mevrouw|wethouder|burgemeester)\s+'
            r'([A-Z][a-z\u00C0-\u024F\-]{2,})[.,]?\s*$',
            re.IGNORECASE
        )
        m = end_re.search(text)
        if m:
            name = m.group(1).strip()
            if self._valid_name(name):
                return name
        return None
