"""
Unit tests for services/graph_retrieval.py — the WS1 GraphRAG fifth stream.

Scope: pure logic + SQL-shape tests with a mocked DB. No live Postgres.
Run with: pytest tests/test_graph_retrieval.py -v

What these tests pin down:

1. ``walk()`` honors the 2-hop cap (MAX_HOPS_V02) even when callers pass a
   higher value.
2. Empty-seed-list paths return an empty list cleanly — no DB hit, no crash.
3. ``score_paths()`` applies the hop penalty AND the intent boost correctly
   and sorts descending.
4. ``hydrate_chunks()`` joins via ``kg_mentions`` (not the fictional
   ``chunk_entities`` the handoff drafts referenced), so a refactor that
   accidentally reintroduces ``chunk_entities`` will fail loudly.
5. ``is_graph_walk_ready()`` short-circuits to False when
   GRAPH_WALK_ENABLED is falsy — Phase 0 safety net.
6. ``retrieve_via_graph()`` returns [] gracefully when the KG is not ready
   or when no entities resolve.
7. Schema-drift protection: the generated SQL in ``walk()`` and
   ``hydrate_chunks()`` uses the real column names (``relation_type``,
   ``kg_mentions``), not the handoff drift names.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest

# Defensive: force GRAPH_WALK_ENABLED during import so we can test the ready
# path, then override per-test. We re-import the module to make sure any
# test-level env tweaks are respected.
os.environ.setdefault("GRAPH_WALK_ENABLED", "1")
os.environ.setdefault("GRAPH_WALK_MIN_EDGES", "0")

from services import graph_retrieval  # noqa: E402


# ---------------------------------------------------------------------------
# DB mocking helper
# ---------------------------------------------------------------------------


class FakeCursor:
    def __init__(self, rows=None, capture=None):
        self._rows = rows or []
        self._executed_sql = None
        self._executed_params = None
        self._capture = capture

    def execute(self, sql, params=None):
        self._executed_sql = sql
        self._executed_params = params
        if self._capture is not None:
            self._capture.append((sql, params))

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class FakeConn:
    def __init__(self, cursor: FakeCursor):
        self._cursor = cursor

    def cursor(self, *args, **kwargs):
        return self._cursor

    def commit(self):
        pass

    def rollback(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@contextmanager
def fake_get_connection(cursor: FakeCursor):
    yield FakeConn(cursor)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWalk:
    def test_empty_seeds_returns_empty_list_without_db_hit(self):
        captured: list = []
        cur = FakeCursor(rows=[], capture=captured)
        with patch.object(graph_retrieval, "get_connection", lambda: fake_get_connection(cur)):
            result = graph_retrieval.walk([], max_hops=2)
        assert result == []
        assert captured == []  # no SQL should have fired

    def test_none_seeds_filtered(self):
        captured: list = []
        cur = FakeCursor(rows=[], capture=captured)
        with patch.object(graph_retrieval, "get_connection", lambda: fake_get_connection(cur)):
            result = graph_retrieval.walk([None, None], max_hops=2)
        assert result == []

    def test_walk_is_hard_capped_at_2_hops(self):
        """Caller asks for max_hops=5 — must be clamped to MAX_HOPS_V02 (2)."""
        captured: list = []
        cur = FakeCursor(rows=[], capture=captured)
        with patch.object(graph_retrieval, "get_connection", lambda: fake_get_connection(cur)):
            graph_retrieval.walk([1, 2], max_hops=5)
        # One execute call fired; params[1] is the max_hops clamped value
        assert len(captured) == 1
        _sql, params = captured[0]
        # params order: [seed_list, max_hops, (optional edge_types), path_limit]
        assert params[1] == graph_retrieval.MAX_HOPS_V02 == 2

    def test_walk_sql_uses_real_column_name_relation_type(self):
        """Regression guard: handoff drift used 'relationship_type'; live schema has 'relation_type'."""
        captured: list = []
        cur = FakeCursor(rows=[], capture=captured)
        with patch.object(graph_retrieval, "get_connection", lambda: fake_get_connection(cur)):
            graph_retrieval.walk([42], max_hops=2)
        sql = captured[0][0]
        assert "relation_type" in sql
        assert "relationship_type" not in sql
        assert "kg_relationships" in sql

    def test_walk_edge_type_filter_forwards_to_sql(self):
        captured: list = []
        cur = FakeCursor(rows=[], capture=captured)
        with patch.object(graph_retrieval, "get_connection", lambda: fake_get_connection(cur)):
            graph_retrieval.walk([1], max_hops=2, edge_types=["LID_VAN", "DIENT_IN"])
        sql, params = captured[0]
        assert "r.relation_type = ANY(%s)" in sql
        assert ["LID_VAN", "DIENT_IN"] in params

    def test_walk_hydrates_rows_into_path_objects(self):
        rows = [
            # (node_ids, edge_types, edge_confs, depth)
            ([1, 2], ["DIENT_IN"], [0.9], 1),
            ([1, 2, 3], ["DIENT_IN", "LID_VAN"], [0.9, 0.8], 2),
        ]
        cur = FakeCursor(rows=rows)
        with patch.object(graph_retrieval, "get_connection", lambda: fake_get_connection(cur)):
            paths = graph_retrieval.walk([1], max_hops=2)
        assert len(paths) == 2
        assert paths[0].node_ids == [1, 2]
        assert paths[0].edge_types == ["DIENT_IN"]
        assert paths[0].edge_confidences == [0.9]
        assert paths[0].total_confidence == pytest.approx(0.9)
        assert paths[1].node_ids == [1, 2, 3]
        assert paths[1].total_confidence == pytest.approx(0.9 * 0.8)


class TestScorePaths:
    def test_empty_returns_empty(self):
        assert graph_retrieval.score_paths([]) == []

    def test_zero_hop_paths_dropped(self):
        p = graph_retrieval.Path(node_ids=[1], edge_types=[], edge_confidences=[])
        assert graph_retrieval.score_paths([p]) == []

    def test_sorted_descending_by_score(self):
        a = graph_retrieval.Path(
            node_ids=[1, 2], edge_types=["LID_VAN"],
            edge_confidences=[0.5], total_confidence=0.5,
        )
        b = graph_retrieval.Path(
            node_ids=[1, 2], edge_types=["LID_VAN"],
            edge_confidences=[0.9], total_confidence=0.9,
        )
        scored = graph_retrieval.score_paths([a, b])
        assert scored[0].path.total_confidence == 0.9
        assert scored[1].path.total_confidence == 0.5

    def test_hop_penalty_applied(self):
        """A 2-hop path with same total_confidence should score lower than 1-hop."""
        short = graph_retrieval.Path(
            node_ids=[1, 2], edge_types=["LID_VAN"],
            edge_confidences=[0.9], total_confidence=0.9,
        )
        long = graph_retrieval.Path(
            node_ids=[1, 2, 3], edge_types=["LID_VAN", "SPREEKT_OVER"],
            edge_confidences=[0.9, 1.0], total_confidence=0.9,
        )
        scored = graph_retrieval.score_paths([short, long])
        # Short path must come first (penalty kicks in only for hops > 1)
        assert scored[0].path is short

    def test_intent_boost_motie_trace(self):
        """DIENT_IN with motie_trace intent should beat DIENT_IN baseline."""
        p = graph_retrieval.Path(
            node_ids=[1, 2], edge_types=["DIENT_IN"],
            edge_confidences=[0.8], total_confidence=0.8,
        )
        no_boost = graph_retrieval.score_paths([p], query_intent="")
        boosted = graph_retrieval.score_paths([p], query_intent="motie_trace")
        assert boosted[0].score > no_boost[0].score


class TestHydrateChunks:
    def test_empty_entity_ids_short_circuits_without_db(self):
        captured: list = []
        cur = FakeCursor(rows=[], capture=captured)
        with patch.object(graph_retrieval, "get_connection", lambda: fake_get_connection(cur)):
            out = graph_retrieval.hydrate_chunks([])
        assert out == []
        assert captured == []

    def test_sql_joins_via_kg_mentions_not_chunk_entities(self):
        """Regression guard: drift used 'chunk_entities' (nonexistent); live schema has 'kg_mentions'."""
        captured: list = []
        cur = FakeCursor(rows=[], capture=captured)
        with patch.object(graph_retrieval, "get_connection", lambda: fake_get_connection(cur)):
            graph_retrieval.hydrate_chunks([1, 2, 3])
        sql = captured[0][0]
        assert "kg_mentions" in sql
        assert "chunk_entities" not in sql
        assert "document_chunks" in sql
        # Must NOT reference the legacy 'chunks' name either
        assert " FROM chunks " not in sql

    def test_gemeente_filter_shape(self):
        """Optional gemeente filter must target kg_entities metadata."""
        captured: list = []
        cur = FakeCursor(rows=[], capture=captured)
        with patch.object(graph_retrieval, "get_connection", lambda: fake_get_connection(cur)):
            graph_retrieval.hydrate_chunks([1], gemeente="rotterdam")
        sql, params = captured[0]
        assert "e.metadata->>'gemeente' = %s" in sql
        assert "rotterdam" in params

    def test_hydrated_row_shape(self):
        rows = [
            (
                101, "doc-1", "Titel", "Inhoud van de chunk.", 55,
                "2023-05-01", 2, [1, 2],
            ),
        ]
        cur = FakeCursor(rows=rows)
        with patch.object(graph_retrieval, "get_connection", lambda: fake_get_connection(cur)):
            out = graph_retrieval.hydrate_chunks([1, 2])
        assert len(out) == 1
        gc = out[0]
        assert gc.chunk_id == 101
        assert gc.document_id == "doc-1"
        assert gc.title == "Titel"
        assert gc.stream_type == "graph"
        assert gc.entity_ids == [1, 2]
        # similarity_score is (seed_hits / n_seeds) = 2/2 = 1.0
        assert gc.similarity_score == pytest.approx(1.0)


class TestReadiness:
    def setup_method(self):
        # Always start with fresh cache so we can observe the gate behaviour.
        graph_retrieval._readiness_cache = None

    def teardown_method(self):
        graph_retrieval._readiness_cache = None

    def test_disabled_env_returns_false_without_db_hit(self):
        with patch.object(graph_retrieval, "GRAPH_WALK_ENABLED", False):
            assert graph_retrieval.is_graph_walk_ready() is False

    def test_enabled_but_below_threshold_returns_false(self):
        cur = FakeCursor(rows=[(50,)])
        with patch.object(graph_retrieval, "GRAPH_WALK_ENABLED", True), \
             patch.object(graph_retrieval, "GRAPH_WALK_MIN_EDGES", 100), \
             patch.object(graph_retrieval, "get_connection", lambda: fake_get_connection(cur)):
            assert graph_retrieval.is_graph_walk_ready(force_recheck=True) is False

    def test_enabled_and_above_threshold_returns_true(self):
        cur = FakeCursor(rows=[(500_000,)])
        with patch.object(graph_retrieval, "GRAPH_WALK_ENABLED", True), \
             patch.object(graph_retrieval, "GRAPH_WALK_MIN_EDGES", 200_000), \
             patch.object(graph_retrieval, "get_connection", lambda: fake_get_connection(cur)):
            assert graph_retrieval.is_graph_walk_ready(force_recheck=True) is True


class TestExtractQueryEntities:
    def test_empty_query_returns_empty(self):
        assert graph_retrieval.extract_query_entities("") == []
        assert graph_retrieval.extract_query_entities("   ") == []

    def test_gazetteer_match(self, monkeypatch):
        # Bypass DB + JSON I/O by stubbing both cache loaders.
        monkeypatch.setattr(graph_retrieval, "_load_politician_registry", lambda: {})
        monkeypatch.setattr(
            graph_retrieval, "_load_gazetteer",
            lambda: {"warmtebedrijf": "Warmtebedrijf", "m4h": "M4H"},
        )
        monkeypatch.setattr(
            graph_retrieval, "_resolve_entity_id_by_name",
            lambda name, preferred_type=None: (42, "Organization"),
        )
        out = graph_retrieval.extract_query_entities("Wat was het debat over het Warmtebedrijf?")
        assert len(out) == 1
        assert out[0].name == "Warmtebedrijf"
        assert out[0].id == 42
        assert out[0].source == "gazetteer"

    def test_politician_surname_match(self, monkeypatch):
        monkeypatch.setattr(
            graph_retrieval, "_load_politician_registry",
            lambda: {
                "tak": {"canonical_name": "Dennis Tak", "surname": "Tak", "partij": "PvdA"},
            },
        )
        monkeypatch.setattr(graph_retrieval, "_load_gazetteer", lambda: {})
        monkeypatch.setattr(
            graph_retrieval, "_resolve_entity_id_by_name",
            lambda name, preferred_type=None: (77, "Person"),
        )
        out = graph_retrieval.extract_query_entities("Wat vond Tak van de motie?")
        assert len(out) == 1
        assert out[0].name == "Dennis Tak"
        assert out[0].source == "politician_registry"


class TestRetrieveViaGraphOrchestration:
    def test_returns_empty_when_not_ready(self, monkeypatch):
        monkeypatch.setattr(graph_retrieval, "is_graph_walk_ready", lambda: False)
        assert graph_retrieval.retrieve_via_graph("Heemraadssingel parkeren") == []

    def test_returns_empty_when_no_seeds_resolve(self, monkeypatch):
        monkeypatch.setattr(graph_retrieval, "is_graph_walk_ready", lambda: True)
        monkeypatch.setattr(graph_retrieval, "extract_query_entities", lambda q: [])
        assert graph_retrieval.retrieve_via_graph("random query") == []

    def test_returns_empty_when_walk_empty(self, monkeypatch):
        monkeypatch.setattr(graph_retrieval, "is_graph_walk_ready", lambda: True)
        monkeypatch.setattr(
            graph_retrieval, "extract_query_entities",
            lambda q: [graph_retrieval.Entity(id=1, type="Person", name="X")],
        )
        monkeypatch.setattr(graph_retrieval, "walk", lambda seeds, max_hops=2: [])
        assert graph_retrieval.retrieve_via_graph("x") == []
