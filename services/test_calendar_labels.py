"""Tests for services.calendar_labels.

Run standalone: `python3 -m services.test_calendar_labels`
or `python3 services/test_calendar_labels.py`.

Deliberately plain assert-at-module-scope to avoid a pytest dependency
(the project doesn't use pytest broadly yet). Each assertion is named
with a clear failure message so a broken test tells you what to fix.
"""

from __future__ import annotations

from datetime import date

from services.calendar_labels import (
    NL_MONTHS,
    NL_WEEKDAYS,
    format_date_nl,
    normalize_and_dedupe,
    normalize_label,
)


# ── (a) weekday-prefix → "Raadsvergadering" ────────────────────────────────
def _test_weekday_prefix_substitution() -> None:
    # empty committee + weekday name → Raadsvergadering (full-raad convention)
    assert normalize_label("", "donderdag 16 april 2026") == "Raadsvergadering", \
        "weekday-prefix raw_name should become 'Raadsvergadering' when committee is empty"
    assert normalize_label("", "Dinsdag 3 juni 2025") == "Raadsvergadering", \
        "weekday-prefix match should be case-insensitive"
    # a committee always wins over the weekday fallback
    assert normalize_label("Commissie WIISA", "donderdag 16 april 2026") == "Commissie WIISA", \
        "non-empty committee should take precedence over weekday-prefix substitution"
    # non-weekday name preserved
    assert normalize_label("", "Rekenkamercommissie") == "Rekenkamercommissie", \
        "non-weekday raw_name should be preserved verbatim"
    # weekday prefix only applies when it's actually the prefix, not a substring
    assert normalize_label("", "Iets over maandagen in het verkeer").startswith("Iets"), \
        "weekday as substring (not prefix) should not trigger substitution"


# ── (b) (date, label) dedup ─────────────────────────────────────────────────
def _test_date_label_dedupe() -> None:
    rows = [
        # iBabs row
        {"id": "ibabs-1", "name": "Commissie WIISA", "committee": "Commissie WIISA",
         "start_date": "2026-04-16"},
        # ORI row — same logical meeting, different source id
        {"id": "ori-42", "name": "Commissie WIISA", "committee": "Commissie WIISA",
         "start_date": "2026-04-16"},
        # Different date, same committee — kept
        {"id": "ibabs-2", "name": "Commissie WIISA", "committee": "Commissie WIISA",
         "start_date": "2026-04-23"},
        # Same date, different committee — kept
        {"id": "ibabs-3", "name": "Commissie ZOCS", "committee": "Commissie ZOCS",
         "start_date": "2026-04-16"},
    ]
    out = normalize_and_dedupe(rows, today=date(2026, 4, 14))
    assert len(out) == 3, f"expected 3 unique (date,label) rows, got {len(out)}"
    ids = [r["id"] for r in out]
    assert ids[0] == "ibabs-1", "first encountered (date,label) should survive dedup (stable)"
    assert "ori-42" not in ids, "duplicate (date,label) second occurrence should drop"


# ── (c) empty-committee fallback ────────────────────────────────────────────
def _test_empty_committee_fallback() -> None:
    # empty committee AND empty name → Raadsvergadering
    assert normalize_label("", "") == "Raadsvergadering", \
        "fully empty inputs should fall back to 'Raadsvergadering'"
    assert normalize_label(None, None) == "Raadsvergadering", \
        "None inputs should be coerced to empty and fall back"
    # empty committee, real name → use name
    assert normalize_label("", "Ad hoc werkgroep luchtkwaliteit") == "Ad hoc werkgroep luchtkwaliteit", \
        "empty committee should yield to the raw name if it's present and not a weekday"


# ── date rendering + coercion edge cases ────────────────────────────────────
def _test_format_date_nl() -> None:
    assert format_date_nl(date(2026, 4, 16)) == "16 april 2026"
    assert format_date_nl(date(2023, 1, 1)) == "1 januari 2023"
    assert NL_MONTHS[11] == "december"
    assert NL_WEEKDAYS[0] == "maandag"


def _test_normalize_and_dedupe_shape_and_skip() -> None:
    rows = [
        # unparseable date → skipped
        {"id": "bad-1", "name": "x", "committee": "x", "start_date": "not-a-date"},
        # missing date → skipped
        {"id": "bad-2", "name": "x", "committee": "x", "start_date": None},
        # valid
        {"id": "ok-1", "name": "Commissie BWB", "committee": "Commissie BWB",
         "start_date": "2025-06-03"},
    ]
    today = date(2026, 4, 14)
    out = normalize_and_dedupe(rows, today=today)
    assert len(out) == 1, f"invalid rows should be skipped, got {len(out)}"
    r = out[0]
    assert set(r.keys()) == {"id", "label", "date_short", "date_nl", "date_iso", "is_past"}, \
        f"output shape drifted — got {sorted(r.keys())}"
    assert r["is_past"] is True, "2025-06-03 is past relative to 2026-04-14"
    assert r["date_nl"] == "3 juni 2025"
    assert r["date_iso"] == "2025-06-03"


def main() -> int:
    tests = [
        ("(a) weekday-prefix → Raadsvergadering", _test_weekday_prefix_substitution),
        ("(b) (date, label) dedup", _test_date_label_dedupe),
        ("(c) empty-committee fallback", _test_empty_committee_fallback),
        ("format_date_nl + NL constants", _test_format_date_nl),
        ("normalize_and_dedupe shape + skip", _test_normalize_and_dedupe_shape_and_skip),
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {name}: {exc}")
        except Exception as exc:  # pragma: no cover — surfaces genuine bugs
            failed += 1
            print(f"ERROR {name}: {exc!r}")
    if failed:
        print(f"\n{failed} test(s) failed")
        return 1
    print(f"\nAll {len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
