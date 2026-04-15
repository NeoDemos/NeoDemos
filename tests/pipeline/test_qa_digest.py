"""
Regression tests for WS5a Phase A QA digest.

Why no real DB?
---------------
The digest is READ-ONLY against a production corpus (1.7M chunks) and must
coexist with WS6 Phase 3 + WS11 Phase 6 background writers. These tests:

* Validate threshold classification on known values.
* Exercise ``compose_daily_digest`` against a mocked cursor that simulates
  the three realistic states: empty DB, a successful digest row, a failed
  digest row with live failures.
* Never touch psycopg2, never hit Qdrant, never call SMTP.

Run:
    pytest tests/pipeline/test_qa_digest.py -v
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from scripts.nightly import qa_digest as qd
from services import pipeline_health_email as phe


# ---------------------------------------------------------------------------
# 1. Threshold classification — pure function
# ---------------------------------------------------------------------------

class TestClassifyThreshold:
    def test_lower_is_better_green(self):
        assert qd.classify_threshold(0, 0, 100) == qd.STATUS_GREEN

    def test_lower_is_better_yellow(self):
        assert qd.classify_threshold(50, 0, 100) == qd.STATUS_YELLOW

    def test_lower_is_better_red(self):
        assert qd.classify_threshold(250, 0, 100) == qd.STATUS_RED

    def test_higher_is_better_green(self):
        assert qd.classify_threshold(95.0, 80.0, 50.0, invert=True) == qd.STATUS_GREEN

    def test_higher_is_better_yellow(self):
        assert qd.classify_threshold(65.0, 80.0, 50.0, invert=True) == qd.STATUS_YELLOW

    def test_higher_is_better_red(self):
        assert qd.classify_threshold(30.0, 80.0, 50.0, invert=True) == qd.STATUS_RED

    def test_unknown_on_non_numeric(self):
        assert qd.classify_threshold(None, 0, 100) == qd.STATUS_UNKNOWN
        assert qd.classify_threshold("abc", 0, 100) == qd.STATUS_UNKNOWN

    def test_overall_picks_worst(self):
        assert qd._overall([qd.STATUS_GREEN, qd.STATUS_YELLOW]) == qd.STATUS_YELLOW
        assert qd._overall([qd.STATUS_GREEN, qd.STATUS_RED]) == qd.STATUS_RED
        assert qd._overall([qd.STATUS_GREEN, qd.STATUS_GREEN]) == qd.STATUS_GREEN
        assert qd._overall([]) == qd.STATUS_UNKNOWN


# ---------------------------------------------------------------------------
# 2. Digest fuzzy threshold scenarios — the parkeertarieven bug shape
# ---------------------------------------------------------------------------

class TestFuzzyThreshold:
    def test_fuzzy_50pct_is_red(self):
        """The handoff's load-bearing example: 49.5% fuzzy suggests chunker
        regression. Threshold is <10% / 10-40% / >40%, so 49.5% -> RED."""
        assert qd.classify_threshold(49.5, 10.0, 40.0) == qd.STATUS_RED

    def test_fuzzy_5pct_is_green(self):
        assert qd.classify_threshold(5.0, 10.0, 40.0) == qd.STATUS_GREEN

    def test_fuzzy_25pct_is_yellow(self):
        assert qd.classify_threshold(25.0, 10.0, 40.0) == qd.STATUS_YELLOW


# ---------------------------------------------------------------------------
# 3. Email rendering — mock the DB cursor
# ---------------------------------------------------------------------------

class FakeCursor:
    """Cheap psycopg2 cursor stub — each execute() sets the next fetch result
    based on the SQL text."""

    def __init__(self, scenario: str = "empty"):
        self.scenario = scenario
        self._next_fetchone = None
        self._next_fetchall: list = []

    def execute(self, sql: str, params=None):
        s = sql.lower()
        if self.scenario == "empty":
            # No rows for any query
            self._next_fetchone = None
            self._next_fetchall = []
            # Aggregates should return zeros when no rows
            if " count(" in s and "group by" not in s:
                # Smoke summary: 3 cols all zero
                if "00_smoke_test" in s:
                    self._next_fetchone = (0, 0, 0)
                elif "this_week" in s or "7 days" in s:
                    self._next_fetchone = (0, 0)
                else:
                    self._next_fetchone = (0,)
        elif self.scenario == "green":
            if "job_name = 'qa_digest'" in s and "order by started_at" in s:
                self._next_fetchone = (
                    42,
                    datetime.now(timezone.utc) - timedelta(minutes=5),
                    datetime.now(timezone.utc),
                    "success",
                    9, 9, 0, None, "cron",
                )
            elif "group by job_name" in s:
                self._next_fetchall = [
                    ("scheduled_refresh", 96, 96, 0, 0, 0),
                    ("document_processor", 72, 72, 0, 0, 0),
                    ("qa_digest", 1, 1, 0, 0, 0),
                ]
            elif "from pipeline_failures" in s and "order by failed_at" in s:
                self._next_fetchall = []
            elif "00_smoke_test" in s:
                self._next_fetchone = (24, 0, 24)
            elif "this_week" in s or "interval '14 days'" in s.replace('\n', ' '):
                self._next_fetchone = (0, 2)  # 0 this week, 2 last week
            else:
                self._next_fetchone = None
        elif self.scenario == "red":
            if "job_name = 'qa_digest'" in s and "order by started_at" in s:
                self._next_fetchone = (
                    43,
                    datetime.now(timezone.utc) - timedelta(minutes=5),
                    datetime.now(timezone.utc),
                    "failure",
                    9, 7, 2,
                    "overall=red; 9 checks; 2 red: chunk_attribution_fuzzy,vector_gaps",
                    "cron",
                )
            elif "group by job_name" in s:
                self._next_fetchall = [
                    ("scheduled_refresh", 96, 90, 6, 0, 0),
                    ("financial_sweep", 24, 23, 1, 0, 0),
                ]
            elif "from pipeline_failures" in s and "order by failed_at" in s:
                self._next_fetchall = [
                    ("scheduled_refresh", "doc-123", "document",
                     datetime.now(timezone.utc) - timedelta(hours=3),
                     "HTTPError",
                     "HTTP 500 on iBabs meeting payload"),
                    ("financial_sweep", "doc-999", "document",
                     datetime.now(timezone.utc) - timedelta(hours=1),
                     "ExtractionError",
                     "table_json had no recognizable header"),
                ]
            elif "00_smoke_test" in s:
                self._next_fetchone = (17, 7, 24)
            elif "this_week" in s or "interval '14 days'" in s.replace('\n', ' '):
                self._next_fetchone = (7, 2)
            else:
                self._next_fetchone = None

    def fetchone(self):
        return self._next_fetchone

    def fetchall(self):
        return self._next_fetchall

    def close(self):
        pass


class FakeConn:
    def __init__(self, scenario: str = "empty"):
        self.scenario = scenario

    def cursor(self, *args, **kwargs):
        return FakeCursor(self.scenario)

    def rollback(self):
        pass

    def commit(self):
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_compose_daily_digest_renders_on_empty_db(monkeypatch):
    """Hard requirement from the handoff: on an empty DB, the email must
    still render with explicit 'No runs yet' placeholders."""
    # Make sure no JSON snapshot gets picked up from disk during the test.
    monkeypatch.setattr(phe, "_find_todays_digest_json", lambda: None)

    html_body, text_body, subject = phe.compose_daily_digest(db_conn=FakeConn("empty"))

    assert "NeoDemos pipeline" in subject
    assert "UNKNOWN" in subject  # overall degrades to unknown on empty DB

    # Text body placeholders
    assert "No pipeline runs recorded" in text_body or "no pipeline runs recorded" in text_body.lower()
    assert "no smoke test runs" in text_body.lower()
    assert "no failures" in text_body.lower()

    # HTML has the "No runs yet" placeholder
    assert "No runs yet" in html_body or "No digest" in html_body or "No smoke test" in html_body

    # Both bodies must contain the admin link
    assert "/admin/pipeline" in html_body
    assert "/admin/pipeline" in text_body


def test_compose_daily_digest_green_scenario(monkeypatch):
    monkeypatch.setattr(
        phe, "_find_todays_digest_json",
        lambda: {
            "overall_status": "green",
            "summary": "9/9 green, 0 yellow, 0 red, 0 unknown",
            "checks": [
                {"name": "chunk_attribution_mismatch", "status": "green",
                 "value": 0, "threshold": "0% green"},
                {"name": "chunk_attribution_fuzzy", "status": "green",
                 "value": 4.2, "threshold": "<10% green"},
                {"name": "vector_gaps", "status": "green", "value": 0,
                 "threshold": "0 green"},
            ],
        },
    )

    html_body, text_body, subject = phe.compose_daily_digest(db_conn=FakeConn("green"))

    assert "GREEN" in subject
    assert "green" in text_body.lower()
    # Runs present
    assert "scheduled_refresh" in text_body
    assert "96/96 success" in text_body
    # Smoke shows 24/24
    assert "24/24" in text_body


def test_compose_daily_digest_red_scenario(monkeypatch):
    monkeypatch.setattr(
        phe, "_find_todays_digest_json",
        lambda: {
            "overall_status": "red",
            "summary": "6/9 green, 1 yellow, 2 red, 0 unknown",
            "checks": [
                {"name": "chunk_attribution_fuzzy", "status": "red",
                 "value": 49.5, "threshold": "<10% / 10-40% / >40%"},
                {"name": "vector_gaps", "status": "red", "value": 250,
                 "threshold": "0 / 1-100 / >100"},
            ],
        },
    )

    html_body, text_body, subject = phe.compose_daily_digest(db_conn=FakeConn("red"))

    assert "RED" in subject
    # Red fuzzy value surfaces in body
    assert "49.5" in text_body or "49.5" in html_body
    # Failure entries present
    assert "HTTPError" in text_body or "HTTPError" in html_body


# ---------------------------------------------------------------------------
# 4. build_checks with mocked audit functions
# ---------------------------------------------------------------------------

def test_build_checks_all_green(monkeypatch):
    """When every audit reports clean numbers, every check is green."""
    monkeypatch.setattr(qd, "check_chunk_attribution",
                        lambda n: {"total_sampled": 2000, "mismatch_count": 0,
                                   "mismatch_pct": 0.0, "fuzzy_count": 50,
                                   "fuzzy_pct": 2.5, "missing_doc_count": 0})
    monkeypatch.setattr(qd, "check_vector_gaps",
                        lambda n: {"missing_count": 0, "sample_missing_ids": [],
                                   "pg_chunks": 1_700_000, "qdrant_points": 1_700_000,
                                   "sampled": True})
    monkeypatch.setattr(qd, "check_raadslid_roles",
                        lambda: {"anomaly_count": 3, "errors": 0, "warnings": 3,
                                 "infos": 0, "sample_findings": [], "by_layer": {}})
    monkeypatch.setattr(qd, "check_financial_coverage",
                        lambda: {"coverage_pct": 92.0, "docs_promoted": 100,
                                 "docs_with_lines": 92, "gap_doc_types": [],
                                 "gemeente": "rotterdam"})
    monkeypatch.setattr(qd, "check_failures_queue_depth",
                        lambda: {"failure_count_24h": 0, "top_errors": []})
    monkeypatch.setattr(qd, "check_smoke_test_status",
                        lambda: {"runs_found": 24, "success_count": 24,
                                 "failure_count": 0, "running_count": 0})
    monkeypatch.setattr(qd, "check_active_writers",
                        lambda: {"long_running_count": 0, "writers": []})
    monkeypatch.setattr(qd, "check_lock_contention",
                        lambda: {"ungranted_locks": 0})

    checks = qd._build_checks(sample_size=2000)
    assert len(checks) == 9
    statuses = [c["status"] for c in checks]
    assert all(s == qd.STATUS_GREEN for s in statuses), \
        f"expected all green, got {statuses}"


def test_build_checks_fuzzy_regression_goes_red(monkeypatch):
    """The regression this whole workstream is designed to catch: chunker
    syllable-drop that shows up as fuzzy ratio >40%."""
    monkeypatch.setattr(qd, "check_chunk_attribution",
                        lambda n: {"total_sampled": 2000, "mismatch_count": 0,
                                   "mismatch_pct": 0.0, "fuzzy_count": 990,
                                   "fuzzy_pct": 49.5, "missing_doc_count": 0})
    monkeypatch.setattr(qd, "check_vector_gaps",
                        lambda n: {"missing_count": 12, "sample_missing_ids": [1, 2, 3],
                                   "pg_chunks": 1_700_000, "qdrant_points": 1_699_988,
                                   "sampled": True})
    monkeypatch.setattr(qd, "check_raadslid_roles",
                        lambda: {"anomaly_count": 0, "errors": 0, "warnings": 0,
                                 "infos": 0, "sample_findings": [], "by_layer": {}})
    monkeypatch.setattr(qd, "check_financial_coverage",
                        lambda: {"coverage_pct": 92.0, "docs_promoted": 100,
                                 "docs_with_lines": 92, "gap_doc_types": [],
                                 "gemeente": "rotterdam"})
    monkeypatch.setattr(qd, "check_failures_queue_depth",
                        lambda: {"failure_count_24h": 0, "top_errors": []})
    monkeypatch.setattr(qd, "check_smoke_test_status",
                        lambda: {"runs_found": 24, "success_count": 24,
                                 "failure_count": 0, "running_count": 0})
    monkeypatch.setattr(qd, "check_active_writers",
                        lambda: {"long_running_count": 0, "writers": []})
    monkeypatch.setattr(qd, "check_lock_contention",
                        lambda: {"ungranted_locks": 0})

    checks = qd._build_checks(sample_size=2000)
    by_name = {c["name"]: c for c in checks}
    assert by_name["chunk_attribution_fuzzy"]["status"] == qd.STATUS_RED
    assert by_name["chunk_attribution_fuzzy"]["value"] == 49.5
    # vector_gaps=12 is in the yellow band (1-100)
    assert by_name["vector_gaps"]["status"] == qd.STATUS_YELLOW
    # Overall must be red
    assert qd._overall([c["status"] for c in checks]) == qd.STATUS_RED


def test_build_checks_mismatch_thresholds(monkeypatch):
    """Mismatch thresholds calibrated 2026-04-15 after full 1.74M audit:
    <1% green / 1-2% yellow / >2% red. Baseline is 0.65% WS7 OCR drift,
    not corruption — so any realistic corpus should sit GREEN unless a
    new regression pushes it above 1%."""
    monkeypatch.setattr(qd, "check_chunk_attribution",
                        lambda n: {"total_sampled": 2000, "mismatch_count": 30,
                                   "mismatch_pct": 1.5, "fuzzy_count": 400,
                                   "fuzzy_pct": 20.0, "missing_doc_count": 0})
    # Fill the rest with greens so only the mismatch contributes a red.
    monkeypatch.setattr(qd, "check_vector_gaps",
                        lambda n: {"missing_count": 0, "sample_missing_ids": [],
                                   "pg_chunks": 0, "qdrant_points": 0, "sampled": True})
    monkeypatch.setattr(qd, "check_raadslid_roles",
                        lambda: {"anomaly_count": 0, "errors": 0, "warnings": 0,
                                 "infos": 0, "sample_findings": [], "by_layer": {}})
    monkeypatch.setattr(qd, "check_financial_coverage",
                        lambda: {"coverage_pct": 90.0, "docs_promoted": 10,
                                 "docs_with_lines": 9, "gap_doc_types": [],
                                 "gemeente": "rotterdam"})
    monkeypatch.setattr(qd, "check_failures_queue_depth",
                        lambda: {"failure_count_24h": 0, "top_errors": []})
    monkeypatch.setattr(qd, "check_smoke_test_status",
                        lambda: {"runs_found": 24, "success_count": 24,
                                 "failure_count": 0, "running_count": 0})
    monkeypatch.setattr(qd, "check_active_writers",
                        lambda: {"long_running_count": 0, "writers": []})
    monkeypatch.setattr(qd, "check_lock_contention",
                        lambda: {"ungranted_locks": 0})

    checks = qd._build_checks(sample_size=2000)
    by_name = {c["name"]: c for c in checks}
    # 1.5% lands in the yellow band (1-2%)
    assert by_name["chunk_attribution_mismatch"]["status"] == qd.STATUS_YELLOW
    # And 20% fuzzy is green (baseline is 20.8%)
    assert by_name["chunk_attribution_fuzzy"]["status"] == qd.STATUS_GREEN


def test_build_checks_survives_individual_failures(monkeypatch):
    """If one audit tool blows up, the digest must continue and report
    status=unknown for that single check without losing the rest."""

    def boom(*a, **kw):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(qd, "check_chunk_attribution", boom)
    monkeypatch.setattr(qd, "check_vector_gaps",
                        lambda n: {"missing_count": 0, "sample_missing_ids": [],
                                   "pg_chunks": 0, "qdrant_points": 0, "sampled": True})
    monkeypatch.setattr(qd, "check_raadslid_roles",
                        lambda: {"anomaly_count": 0, "errors": 0, "warnings": 0,
                                 "infos": 0, "sample_findings": [], "by_layer": {}})
    monkeypatch.setattr(qd, "check_financial_coverage",
                        lambda: {"coverage_pct": 90.0, "docs_promoted": 10,
                                 "docs_with_lines": 9, "gap_doc_types": [],
                                 "gemeente": "rotterdam"})
    monkeypatch.setattr(qd, "check_failures_queue_depth",
                        lambda: {"failure_count_24h": 0, "top_errors": []})
    monkeypatch.setattr(qd, "check_smoke_test_status",
                        lambda: {"runs_found": 24, "success_count": 24,
                                 "failure_count": 0, "running_count": 0})
    monkeypatch.setattr(qd, "check_active_writers",
                        lambda: {"long_running_count": 0, "writers": []})
    monkeypatch.setattr(qd, "check_lock_contention",
                        lambda: {"ungranted_locks": 0})

    checks = qd._build_checks(sample_size=2000)
    by_name = {c["name"]: c for c in checks}
    # The two attribution checks should be unknown
    assert by_name["chunk_attribution_mismatch"]["status"] == qd.STATUS_UNKNOWN
    assert by_name["chunk_attribution_fuzzy"]["status"] == qd.STATUS_UNKNOWN
    # Error message propagated
    assert "simulated failure" in str(by_name["chunk_attribution_mismatch"]["details"])
    # Every other check still present and green
    green_names = [n for n, c in by_name.items() if c["status"] == qd.STATUS_GREEN]
    assert "vector_gaps" in green_names
    assert "failures_queue_depth" in green_names
