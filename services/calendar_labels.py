"""Calendar label normalization helpers.

Provides normalize_and_dedupe() — a pure-Python post-processing step applied
to the meeting list returned by get_meetings_filtered() and any other read-path
that surfaces meetings to the UI.

Problem: iBabs sometimes sets meeting.name to a raw weekday+date string such as
"donderdag 17 december 2026" instead of the committee or meeting type. These
names are useless for a user scanning the calendar and should be replaced with
the committee field (which is usually correct).

This module is intentionally side-effect-free: it never writes to the DB.
"""

import re
from typing import List, Dict, Any

# Dutch weekday names (full, lowercase)
_WEEKDAY_RE = re.compile(
    r"^(maandag|dinsdag|woensdag|donderdag|vrijdag|zaterdag|zondag)\b",
    re.IGNORECASE,
)


def _is_weekday_name(name: str) -> bool:
    """Return True if the meeting name is just a weekday/date string."""
    if not name:
        return False
    return bool(_WEEKDAY_RE.match(name.strip()))


def normalize_and_dedupe(meetings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Normalize meeting names and remove client-visible duplicates.

    Steps (all read-path, no DB writes):

    1. **Weekday-name normalization** — if ``meeting.name`` matches a Dutch
       weekday prefix (e.g. "donderdag 17 december 2026"), replace it with
       ``meeting.committee`` if that field is non-empty, otherwise leave it as-is
       (better to show the raw date than nothing).

    2. **Soft deduplication** — after normalization, collapse meetings that share
       the same (normalized_name, committee, start_date[:10]) into the row with
       the highest ``doc_count``.  This is a display-layer guard; the canonical
       DB-level dedup lives in B5/B7 (Phase B of WS14).

    Args:
        meetings: List of meeting dicts as returned by ``get_meetings_filtered``.
                  Dicts are mutated in-place for step 1; step 2 returns a new list.

    Returns:
        Normalized, deduplicated list of meeting dicts ordered by start_date DESC.
    """
    # Step 1: weekday-name normalization (mutates in place)
    for m in meetings:
        name = m.get("name") or ""
        if _is_weekday_name(name):
            committee = (m.get("committee") or "").strip()
            if committee:
                m["name"] = committee

    # Step 2: soft deduplication by (name, committee, date)
    seen: Dict[tuple, int] = {}   # key -> index in result list
    result: List[Dict[str, Any]] = []

    for m in meetings:
        date_key = (m.get("start_date") or "")[:10]
        key = (
            (m.get("name") or "").lower().strip(),
            (m.get("committee") or "").lower().strip(),
            date_key,
        )
        if key in seen:
            existing = result[seen[key]]
            # Keep the row with the higher doc_count (more data wins)
            if (m.get("doc_count") or 0) > (existing.get("doc_count") or 0):
                result[seen[key]] = m
        else:
            seen[key] = len(result)
            result.append(m)

    return result
