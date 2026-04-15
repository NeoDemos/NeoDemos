"""Regression tests for haal_vergadering_op — P0 fix 2026-04-15.

Failure mode (Erik incident 2026-04-15 16:00):
  haal_vergadering_op read meeting.get("documents", []) but StorageService
  nests documents under meeting['agenda'][i]['documents']. Top-level key
  was always empty → the LLM saw agenda items without documents and
  concluded "vergadering staat nog niet in de database". The Van Eikeren
  debataanvraag for the 2026-04-16 gemeenteraad was in DB, correctly
  linked via document_assignments, but invisible to the LLM.

These tests pin the rendering contract:
  - every document under every agenda item must appear in the output
  - sub-item documents must appear too
  - a totaal-lijst with full count must appear
  - duplicate doc ids across items must dedup in the totaal-lijst
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


def _meeting_fixture() -> dict:
    return {
        "id": "f9b8b1c0-0073-4528-96cb-c78e3f9aafd8",
        "name": "Gemeenteraad",
        "start_date": "2026-04-16",
        "committee": "Gemeenteraad",
        "agenda": [
            {
                "id": "08433f17",
                "number": None,
                "name": "Kort bespreekpunt Van Eikeren",
                "documents": [
                    {
                        "id": "a72de388-9846-41ff-ad84-b5dea38b3318",
                        "name": "Aanvraag kort bespreekpunt Van Eikeren",
                    },
                ],
                "sub_items": [
                    {
                        "id": "sub-1",
                        "name": "Bijlage bij bespreekpunt",
                        "documents": [
                            {"id": "bijlage-1", "name": "Bijlage over woningbouw"},
                        ],
                    }
                ],
            },
            {
                "id": "agenda-2",
                "number": "2",
                "name": "Vaststelling notulen",
                "documents": [
                    {"id": "notulen-1", "name": "Notulen 30 maart 2026"},
                    {"id": "notulen-2", "name": "Notulen 1 april 2026"},
                    {"id": "a72de388-9846-41ff-ad84-b5dea38b3318", "name": "Dup test"},
                ],
                "sub_items": [],
            },
        ],
    }


def test_all_documents_render_under_agenda_items():
    """Every doc from storage must appear in MCP output."""
    from mcp_server_v3 import haal_vergadering_op

    with patch("mcp_server_v3._get_storage") as mock_storage:
        mock_storage.return_value.get_meeting_details.return_value = _meeting_fixture()
        out = haal_vergadering_op(vergadering_id="f9b8b1c0-0073-4528-96cb-c78e3f9aafd8")

    assert "Aanvraag kort bespreekpunt Van Eikeren" in out
    assert "a72de388-9846-41ff-ad84-b5dea38b3318" in out
    assert "Bijlage over woningbouw" in out
    assert "Notulen 30 maart 2026" in out
    assert "Notulen 1 april 2026" in out


def test_totals_list_shows_full_count_and_dedups():
    """The 'Alle documenten' section must show unique count (dedupe by id)."""
    from mcp_server_v3 import haal_vergadering_op

    with patch("mcp_server_v3._get_storage") as mock_storage:
        mock_storage.return_value.get_meeting_details.return_value = _meeting_fixture()
        out = haal_vergadering_op(vergadering_id="f9b8b1c0-0073-4528-96cb-c78e3f9aafd8")

    # 4 unique docs: Van Eikeren, bijlage-1, notulen-1, notulen-2
    # Van Eikeren appears twice in the fixture (agenda item 1 + as dup in item 2)
    # — must dedup to 4, not 5.
    assert "### Alle documenten (4)" in out


def test_empty_meeting_reports_zero_docs_not_silent():
    """A meeting with zero docs must say so explicitly, not omit the section."""
    from mcp_server_v3 import haal_vergadering_op

    empty_meeting = {
        "id": "empty",
        "name": "Lege vergadering",
        "start_date": "2026-01-01",
        "committee": "Test",
        "agenda": [],
    }
    with patch("mcp_server_v3._get_storage") as mock_storage:
        mock_storage.return_value.get_meeting_details.return_value = empty_meeting
        out = haal_vergadering_op(vergadering_id="empty")

    assert "Geen documenten gekoppeld" in out
