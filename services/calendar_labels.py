"""Calendar label + date normalization helpers.

Extracted from `routes/api.py::/api/calendar/upcoming` as part of WS14 Phase C6.

Two concerns this module owns:
1. Label normalization — pick a human-readable label for a meeting row given
   (committee, raw_name). If the raw name looks like a Dutch weekday + date
   string (e.g. "donderdag 16 april 2026"), we substitute "Raadsvergadering"
   so the UI doesn't render the date twice.
2. Deduplication on (date_iso, label) — storage occasionally returns
   near-duplicates (iBabs + ORI for the same logical meeting). The API layer
   needs a stable de-duped shape until WS14 Phase B5 meeting-dedupe ships.

Pure functions. No DB, no FastAPI. Trivially testable.
"""

from __future__ import annotations

from datetime import date as _date_cls, datetime as _dt
from typing import Any, Iterable, List, Optional

NL_WEEKDAYS = (
    "maandag", "dinsdag", "woensdag", "donderdag",
    "vrijdag", "zaterdag", "zondag",
)

NL_MONTHS = [
    "januari", "februari", "maart", "april", "mei", "juni",
    "juli", "augustus", "september", "oktober", "november", "december",
]


def _looks_like_weekday_prefix(raw_name: str) -> bool:
    """True when raw_name starts with a Dutch weekday token (case-insensitive)."""
    if not raw_name:
        return False
    lower = raw_name.lower()
    return any(lower.startswith(d) for d in NL_WEEKDAYS)


def normalize_label(committee: str, raw_name: str) -> str:
    """Pick the display label for a calendar row.

    Precedence:
      1. Non-empty committee (already the best human label).
      2. Raw name, unless it looks like a weekday+date — then "Raadsvergadering".
      3. Fallback to "Raadsvergadering".
    """
    committee = (committee or "").strip()
    raw_name = (raw_name or "").strip()

    if _looks_like_weekday_prefix(raw_name):
        raw_name = "Raadsvergadering"

    return committee or raw_name or "Raadsvergadering"


def _coerce_to_date(sd: Any) -> Optional[_date_cls]:
    """Best-effort coercion of a meeting `start_date` value to a `date`."""
    if sd is None:
        return None
    if isinstance(sd, _date_cls) and not isinstance(sd, _dt):
        return sd
    if isinstance(sd, _dt):
        return sd.date()
    if isinstance(sd, str):
        try:
            head = sd.split("T")[0].split(" ")[0]
            return _dt.fromisoformat(head).date()
        except ValueError:
            return None
    if hasattr(sd, "date") and callable(getattr(sd, "date", None)):
        try:
            return sd.date()
        except Exception:  # pragma: no cover — defensive
            return None
    return None


def format_date_nl(d: _date_cls) -> str:
    """Render a date as '16 april 2026' (civic doc style)."""
    return f"{d.day} {NL_MONTHS[d.month - 1]} {d.year}"


def normalize_and_dedupe(
    rows: Iterable[dict],
    today: Optional[_date_cls] = None,
) -> List[dict]:
    """Turn raw storage rows into calendar-widget dicts with dedup on (date, label).

    Each returned dict has:
      - id        : original row id (unchanged)
      - label     : normalized label (see `normalize_label`)
      - date_short: '%d %b' (English short, used in compact widgets)
      - date_nl   : '16 april 2026'
      - date_iso  : '2026-04-16'
      - is_past   : bool, relative to `today` (defaults to UTC today)

    Rows without a parseable start_date are skipped silently (they are
    unusable on a calendar anyway). Dedup key is (date_iso, label).
    Stable — preserves input order.
    """
    if today is None:
        today = _dt.utcnow().date()

    out: List[dict] = []
    seen: set = set()
    for r in rows:
        sd = _coerce_to_date(r.get("start_date"))
        if sd is None:
            continue
        label = normalize_label(r.get("committee") or "", r.get("name") or "")
        key = (sd.isoformat(), label)
        if key in seen:
            continue
        seen.add(key)
        out.append({
            "id": r.get("id"),
            "label": label,
            "date_short": sd.strftime("%d %b"),
            "date_nl": format_date_nl(sd),
            "date_iso": sd.isoformat(),
            "is_past": sd < today,
        })
    return out
