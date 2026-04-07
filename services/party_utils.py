"""
Party alias normalisation and extraction utilities.

Shared between:
- scripts/enrich_qdrant_metadata.py  (batch enrichment)
- services/query_router.py           (query-time party detection)
"""

import re
from typing import List, Optional

# Canonical party names → all known aliases (lowercase)
PARTY_ALIASES = {
    "pvda": "PvdA",
    "partij van de arbeid": "PvdA",
    "pvda/groenlinks": "GroenLinks-PvdA",
    "groenlinks/pvda": "GroenLinks-PvdA",
    "groenlinks-pvda": "GroenLinks-PvdA",
    "gl-pvda": "GroenLinks-PvdA",
    "gl/pvda": "GroenLinks-PvdA",
    "glpvda": "GroenLinks-PvdA",
    "groenlinks": "GroenLinks",
    "vvd": "VVD",
    "d66": "D66",
    "denk": "DENK",
    "sp": "SP",
    "volt": "Volt",
    "cda": "CDA",
    "bij1": "BIJ1",
    "50plus": "50PLUS",
    "50+": "50PLUS",
    "christenunie": "ChristenUnie",
    "sgp/christenunie": "SGP/ChristenUnie",
    "christenunie/sgp": "SGP/ChristenUnie",
    "sgp": "SGP",
    "leefbaar": "Leefbaar Rotterdam",
    "leefbaar rotterdam": "Leefbaar Rotterdam",
    "lr": "Leefbaar Rotterdam",
    "nida": "NIDA",
    "partij voor de dieren": "Partij voor de Dieren",
    "pvdd": "Partij voor de Dieren",
}

CANONICAL_PARTIES = sorted(set(PARTY_ALIASES.values()))

# Regex: "De heer/Mevrouw NAME (PARTY)" — captures speaker name + party
SPEAKER_PARTY_RE = re.compile(
    r'(?:De heer|Mevrouw|de heer|mevrouw)\s+'
    r'([\w][\w\s.\'\-]{1,40}?)\s*'
    r'\(([^)]{2,40})\)',
)

# Regex: bracket notation "[Speaker (PARTY)]:" used in some newer transcripts
BRACKET_SPEAKER_RE = re.compile(
    r'\[([^\]]+?)\s*\(([^)]{2,40})\)\]\s*:',
)

# Roles to filter out (these appear in parentheses but are not parties)
_ROLE_PATTERNS = {
    "voorzitter", "commissievoorzitter", "wethouder", "burgemeester",
    "secretaris", "griffier", "lid", "plaatsvervangend",
    "raadslid", "commissielid",
}


def normalize_party(raw: str) -> Optional[str]:
    """Normalize a raw party string to its canonical form."""
    if not raw:
        return None
    key = raw.strip().lower()
    return PARTY_ALIASES.get(key)


def _is_role(text: str) -> bool:
    """Check if parenthesised text is a role rather than a party."""
    return text.strip().lower() in _ROLE_PATTERNS


def extract_parties_from_text(text: str) -> List[str]:
    """
    Extract all party mentions from chunk text.
    Returns deduplicated list of canonical party names, ordered by first appearance.
    """
    if not text:
        return []

    seen = set()
    result = []

    for pattern in (SPEAKER_PARTY_RE, BRACKET_SPEAKER_RE):
        for match in pattern.finditer(text):
            raw_party = match.group(2).strip()
            if _is_role(raw_party):
                continue
            canonical = normalize_party(raw_party)
            if canonical and canonical not in seen:
                seen.add(canonical)
                result.append(canonical)

    return result


def extract_speakers_from_text(text: str) -> List[dict]:
    """
    Extract speaker-party pairs from text.
    Returns list of {"speaker": str, "party": str|None}.
    """
    if not text:
        return []

    seen = set()
    result = []

    for pattern in (SPEAKER_PARTY_RE, BRACKET_SPEAKER_RE):
        for match in pattern.finditer(text):
            name = match.group(1).strip().rstrip(".")
            raw_party = match.group(2).strip()
            if _is_role(raw_party):
                continue
            key = name.lower()
            if key not in seen:
                seen.add(key)
                result.append({
                    "speaker": name,
                    "party": normalize_party(raw_party),
                })

    return result


def extract_party_from_query(query: str) -> Optional[str]:
    """
    Detect a party name mentioned in a user query.
    Uses word-boundary matching to avoid false positives (e.g. "sp" in "aandachtspunten").
    Returns the canonical party name or None.
    """
    if not query:
        return None

    lower = query.lower()

    # Try longest aliases first to avoid partial matches
    for alias in sorted(PARTY_ALIASES.keys(), key=len, reverse=True):
        # Word-boundary match to prevent substring false positives
        pattern = r'(?<![a-zà-ÿ])' + re.escape(alias) + r'(?![a-zà-ÿ])'
        if re.search(pattern, lower):
            return PARTY_ALIASES[alias]

    return None


def primary_party(parties: List[str]) -> Optional[str]:
    """Return the most frequently mentioned party, or None."""
    if not parties:
        return None
    if len(parties) == 1:
        return parties[0]
    # For multiple parties, first in text order is primary
    return parties[0]
