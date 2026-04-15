"""Regression tests for services/ibabs_service.py — WS5a Phase B.7.

Protects against the 2026-04-15 Erik regressions:

1. ``_parse_agenda_page`` must stamp ``meeting_id`` on every agenda item
   and ``meeting_id`` + ``agenda_item_id`` on every document, or Phase 2
   of ``scheduled_refresh`` silently drops everything on the floor.

2. ``get_upcoming_meetings`` must return stadsberaad (agendatypeId
   100199686) as well as the default raadsvergadering type (100002367) —
   the old ``get_meetings_for_year`` hardcoded one agendatypeId and
   missed everything else.

Fixtures under ``tests/fixtures/ibabs/`` are frozen snapshots of the
public portal captured 2026-04-15. Refresh them if iBabs ships a new DOM.
"""
from __future__ import annotations

import pathlib

from services.ibabs_service import IBabsService

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures" / "ibabs"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parse_agenda_uuid_stamps_meeting_id_on_items() -> None:
    svc = IBabsService()
    meeting_id = "f9b8b1c0-0073-4528-96cb-c78e3f9aafd8"
    result = svc._parse_agenda_page(_load("agenda_uuid_2026.html"), meeting_id)

    assert result["id"] == meeting_id
    assert result["agenda"], "UUID raadsperiode 2026-2030 agenda must not be empty"
    for item in result["agenda"]:
        assert item["meeting_id"] == meeting_id, (
            f"agenda item {item['id']} missing meeting_id — "
            "storage.insert_agenda_item would drop it"
        )


def test_parse_agenda_uuid_stamps_ids_on_documents() -> None:
    svc = IBabsService()
    meeting_id = "f9b8b1c0-0073-4528-96cb-c78e3f9aafd8"
    result = svc._parse_agenda_page(_load("agenda_uuid_2026.html"), meeting_id)

    doc_count = 0
    for item in result["agenda"]:
        for doc in item["documents"]:
            doc_count += 1
            assert doc["meeting_id"] == meeting_id
            # Every doc attached to a real agenda item (not "general") must
            # also carry agenda_item_id so document_assignments is written
            if item["id"] != "general":
                assert doc.get("agenda_item_id") == item["id"]

    assert doc_count > 0, "UUID page contains /Agenda/Document/ links — parser should surface them"


def test_parse_calendar_page_returns_all_agendatypes() -> None:
    svc = IBabsService()
    meetings = svc._parse_calendar_page(_load("calendar_page.html"))

    ids = {m["id"] for m in meetings}
    # Stadsberaad (agendatypeId=100199686) — the agendatype the old
    # get_meetings_for_year() hardcoded call missed entirely.
    assert "ae86588c-da48-47e1-ac6a-fc0d183f5273" in ids, (
        "BWB stadsberaad 15 april 2026 must be in the calendar scrape — "
        "this is the exact miss Erik reported"
    )
    # UUID raadsvergadering — the new raadsperiode 2026-2030 format.
    assert "f9b8b1c0-0073-4528-96cb-c78e3f9aafd8" in ids, (
        "UUID raadsvergadering 16 april 2026 must be discoverable"
    )


def test_parse_calendar_page_extracts_committee_and_location() -> None:
    svc = IBabsService()
    meetings = svc._parse_calendar_page(_load("calendar_page.html"))

    stadsberaad = next(
        m for m in meetings if m["id"] == "ae86588c-da48-47e1-ac6a-fc0d183f5273"
    )
    assert stadsberaad["committee"] == "Commissie Bouwen & Wonen"
    assert stadsberaad["location"] and "Charlois" in stadsberaad["location"]
    assert stadsberaad["subtitle"] and "Startberaad" in stadsberaad["subtitle"]
    assert stadsberaad["start_date"] and stadsberaad["start_date"].startswith("2026-04-15")
