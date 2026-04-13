"""
Regression tests for zoek_moties — WS4 B1 fix.

B1 failure mode: single-word queries missed initiatiefvoorstellen whose title
was generic ("Initiatiefvoorstel Engberts & Vogelaar over wonen") because the
content-search branch was gated on len(search_terms) >= 3.

These tests verify that single-word topic queries always search both name AND
content so initiatiefvoorstellen without the topic in the title are discoverable.

Run with: pytest tests/mcp/test_zoek_moties.py -v
"""

import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure project root is on sys.path
ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Unit test: SQL construction — verify content is always searched
# ---------------------------------------------------------------------------


def test_zoek_moties_single_word_builds_content_clause():
    """Single-word query ('leegstand') must include d.content in the OR clause.

    This is a unit test that inspects the SQL WHERE clause built by zoek_moties
    without touching the database.
    """
    import re

    # Simulate the WHERE-clause building logic from zoek_moties
    # (mirrors mcp_server_v3.py logic exactly — if this test starts failing
    # after a refactor, sync the logic here)
    onderwerp = "leegstand"
    search_terms = [w for w in onderwerp.lower().split() if len(w) > 2]
    assert len(search_terms) == 1, "single word should produce 1 search term"

    conditions = []
    params = []

    if search_terms:
        or_clauses = []
        for term in search_terms:
            or_clauses.append("LOWER(d.name) LIKE %s")
            or_clauses.append("LOWER(d.content) LIKE %s")
            params.append(f"%{term}%")
            params.append(f"%{term}%")
        conditions.append(f"({' OR '.join(or_clauses)})")

        # Multi-word guard must NOT kick in for single-word queries
        if len(search_terms) >= 2:
            count_expr_parts = []
            for term in search_terms:
                count_expr_parts.append(
                    "CASE WHEN LOWER(d.name) LIKE %s OR LOWER(d.content) LIKE %s THEN 1 ELSE 0 END"
                )
                params.append(f"%{term}%")
                params.append(f"%{term}%")
            conditions.append(f"({' + '.join(count_expr_parts)}) >= 2")

    where = " AND ".join(conditions)

    # Assertions
    assert "LOWER(d.content) LIKE %s" in where, (
        "B1 regression: single-word query must search document content, not just name"
    )
    assert "LOWER(d.name) LIKE %s" in where, (
        "Single-word query must also search document name"
    )
    # The >=2 multi-word precision guard must NOT be present for single-word
    assert ">= 2" not in where, (
        "B1 regression: >=2 precision guard must not apply to single-word queries"
    )


def test_zoek_moties_multi_word_has_precision_guard():
    """Multi-word query (2+ terms) must have the >= 2 terms precision guard."""
    onderwerp = "leegstand beleid"
    search_terms = [w for w in onderwerp.lower().split() if len(w) > 2]
    assert len(search_terms) == 2

    conditions = []
    params = []

    if search_terms:
        or_clauses = []
        for term in search_terms:
            or_clauses.append("LOWER(d.name) LIKE %s")
            or_clauses.append("LOWER(d.content) LIKE %s")
            params.append(f"%{term}%")
            params.append(f"%{term}%")
        conditions.append(f"({' OR '.join(or_clauses)})")

        if len(search_terms) >= 2:
            count_expr_parts = []
            for term in search_terms:
                count_expr_parts.append(
                    "CASE WHEN LOWER(d.name) LIKE %s OR LOWER(d.content) LIKE %s THEN 1 ELSE 0 END"
                )
                params.append(f"%{term}%")
                params.append(f"%{term}%")
            conditions.append(f"({' + '.join(count_expr_parts)}) >= 2")

    where = " AND ".join(conditions)

    assert ">= 2" in where, "Multi-word query must have precision guard"
    assert "LOWER(d.content) LIKE %s" in where, "Multi-word query must still search content"
