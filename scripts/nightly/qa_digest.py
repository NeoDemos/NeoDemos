#!/usr/bin/env python3
"""
WS5a Phase A — Unified QA digest runner (READ-ONLY)
====================================================

Runs every audit tool NeoDemos has in one shot, classifies each check as
green / yellow / red against documented thresholds, writes the result to
``reports/qa_digest/YYYY-MM-DD.json``, and logs one row into
``pipeline_runs`` so the ``/admin/pipeline`` dashboard picks it up.

The digest is designed to run in an APScheduler job daily at 07:00 CET and
produce the signal that drives the daily health email (see
``services/pipeline_health_email.py``).

Constraints
-----------
* READ-ONLY on production tables. Writes ONLY to ``pipeline_runs`` and the
  local JSON file under ``reports/qa_digest/``.
* No long transactions, no DDL, no row locks. Every SELECT is short or uses
  planner statistics so we can run safely while WS6 Phase 3 (Gemini summary
  writes) and WS11 Phase 6 (autovacuum on document_chunks) are in flight.
* Uses ``services/db_pool.get_connection`` for every DB touch.
* Never sends email directly — that is the caller's job
  (``services.pipeline_health_email.send_daily_digest``).

CLI
---
    python scripts/nightly/qa_digest.py
    python scripts/nightly/qa_digest.py --sample-size 2000
    python scripts/nightly/qa_digest.py --output-format json
    python scripts/nightly/qa_digest.py --email     # production trigger only

Exit codes:
  0  overall GREEN
  1  overall YELLOW
  2  overall RED
  3  operational error
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass

from services.db_pool import get_connection  # noqa: E402

logger = logging.getLogger("qa_digest")


# ---------------------------------------------------------------------------
# Status constants + classification helpers
# ---------------------------------------------------------------------------

STATUS_GREEN = "green"
STATUS_YELLOW = "yellow"
STATUS_RED = "red"
STATUS_UNKNOWN = "unknown"

_STATUS_RANK = {STATUS_GREEN: 0, STATUS_YELLOW: 1, STATUS_RED: 2, STATUS_UNKNOWN: 1}


def _overall(statuses: list[str]) -> str:
    """Overall status = worst individual status (unknown counts as yellow)."""
    if not statuses:
        return STATUS_UNKNOWN
    worst = max(statuses, key=lambda s: _STATUS_RANK.get(s, 0))
    return worst


def classify_threshold(value: float, green_max: float, yellow_max: float,
                       invert: bool = False) -> str:
    """
    Classify a numeric value.

    ``invert=False``: lower is better. value <= green_max  => green
                                      value <= yellow_max => yellow
                                      otherwise           => red
    ``invert=True``:  higher is better. value >= green_max  => green
                                        value >= yellow_max => yellow
                                        otherwise           => red
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return STATUS_UNKNOWN
    if invert:
        if v >= green_max:
            return STATUS_GREEN
        if v >= yellow_max:
            return STATUS_YELLOW
        return STATUS_RED
    if v <= green_max:
        return STATUS_GREEN
    if v <= yellow_max:
        return STATUS_YELLOW
    return STATUS_RED


# ---------------------------------------------------------------------------
# Individual checks — each returns (status, value, threshold_str, details_dict)
# ---------------------------------------------------------------------------

def _safe(fn: Callable[..., dict], *args, **kwargs) -> tuple[Optional[dict], Optional[str]]:
    """Call a check, swallow exceptions, return (result, error_message)."""
    try:
        return fn(*args, **kwargs), None
    except Exception as exc:
        logger.warning("check %s failed: %s", fn.__name__, exc)
        return None, f"{type(exc).__name__}: {exc}"


def check_chunk_attribution(sample_size: int) -> dict:
    """
    Run the chunk-attribution audit on a sample.
    Produces TWO checks: mismatch % and fuzzy %.
    """
    from scripts import audit_chunk_attribution as aca
    rows, summary = aca.run_audit(
        limit=sample_size,
        doc_id=None,
        check_qdrant=True,
    )
    total = max(summary.get("total", 0), 1)
    mismatch = summary.get("by_match", {}).get(aca.MATCH_MISMATCH, 0)
    fuzzy = summary.get("by_match", {}).get(aca.MATCH_FUZZY, 0)
    missing = summary.get("by_match", {}).get(aca.MATCH_MISSING_DOC, 0)

    return {
        "total_sampled": summary.get("total", 0),
        "mismatch_count": mismatch,
        "mismatch_pct": round(100.0 * mismatch / total, 2),
        "fuzzy_count": fuzzy,
        "fuzzy_pct": round(100.0 * fuzzy / total, 2),
        "missing_doc_count": missing,
    }


def check_vector_gaps(sample_size: int) -> dict:
    from scripts import audit_vector_gaps as avg
    return avg.run_audit(limit=sample_size)


def check_raadslid_roles() -> dict:
    from scripts import audit_raadslid_rollen as arr
    return arr.run_audit()


def check_financial_coverage() -> dict:
    from scripts import financial_coverage_report as fcr
    return fcr.run_audit(gemeente="rotterdam")


def check_failures_queue_depth() -> dict:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*)
            FROM pipeline_failures
            WHERE failed_at > NOW() - INTERVAL '24 hours'
            """
        )
        count = int(cur.fetchone()[0])

        cur.execute(
            """
            SELECT job_name, error_class, COUNT(*) AS n
            FROM pipeline_failures
            WHERE failed_at > NOW() - INTERVAL '24 hours'
            GROUP BY job_name, error_class
            ORDER BY n DESC
            LIMIT 5
            """
        )
        top_errors = [
            {"job_name": r[0], "error_class": r[1], "count": int(r[2])}
            for r in cur.fetchall()
        ]
        cur.close()
    return {"failure_count_24h": count, "top_errors": top_errors}


def check_smoke_test_status() -> dict:
    """Smoke test is a separate hourly job; we read the last 24 runs."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT status
            FROM pipeline_runs
            WHERE job_name = '00_smoke_test'
            ORDER BY started_at DESC
            LIMIT 24
            """
        )
        statuses = [r[0] for r in cur.fetchall()]
        cur.close()
    success = sum(1 for s in statuses if s == "success")
    return {
        "runs_found": len(statuses),
        "success_count": success,
        "failure_count": sum(1 for s in statuses if s == "failure"),
        "running_count": sum(1 for s in statuses if s == "running"),
    }


def check_active_writers() -> dict:
    """Any statement running more than 5 minutes that isn't idle."""
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT pid, usename, state,
                   EXTRACT(EPOCH FROM (NOW() - query_start))::int AS seconds,
                   LEFT(COALESCE(query, ''), 160) AS query_preview
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND state <> 'idle'
              AND query_start IS NOT NULL
              AND NOW() - query_start > INTERVAL '5 minutes'
              AND pid <> pg_backend_pid()
            ORDER BY seconds DESC
            LIMIT 10
            """
        )
        rows = cur.fetchall()
        cur.close()
    writers = [
        {
            "pid": r[0], "user": r[1], "state": r[2],
            "seconds": int(r[3]) if r[3] is not None else None,
            "query_preview": r[4],
        }
        for r in rows
    ]
    return {"long_running_count": len(writers), "writers": writers}


def check_lock_contention() -> dict:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT COUNT(*)
            FROM pg_locks
            WHERE NOT granted
            """
        )
        ungranted = int(cur.fetchone()[0])
        cur.close()
    return {"ungranted_locks": ungranted}


# ---------------------------------------------------------------------------
# Check definitions — the full catalogue
# ---------------------------------------------------------------------------

def _build_checks(sample_size: int) -> list[dict]:
    """
    Return an ordered list of check definitions with status classification
    applied. Every check is attempted; failures degrade to status=unknown
    with the exception message in ``details.error``.
    """
    checks: list[dict] = []

    # 1 + 2: chunk attribution (one run, two checks)
    #
    # Thresholds calibrated 2026-04-15 against the full 1.74M-chunk audit:
    #   reports/chunk_attribution/full_audit_20260415.csv
    # Measured corpus state: mismatch 0.65%, fuzzy 20.8%, missing_doc 0,
    # Qdrant drift 0. The 0.65% mismatch is not corruption — it's WS7 OCR-
    # recovery rewrites + bilingual/structured docs drifting against the
    # current documents.content (avg token overlap on "fuzzy" chunks is
    # 99.4%; the chunks remain semantically correct). Thresholds here
    # track that permanent post-WS7 baseline, not the ideal 0%.
    ca_result, ca_err = _safe(check_chunk_attribution, sample_size)
    if ca_result:
        mismatch_pct = ca_result["mismatch_pct"]
        mm_status = classify_threshold(mismatch_pct, 1.0, 2.0)
        checks.append({
            "name": "chunk_attribution_mismatch",
            "status": mm_status,
            "value": mismatch_pct,
            "threshold": "<1% green; 1-2% yellow; >2% red (baseline 0.65% — WS7 drift)",
            "details": ca_result,
        })
        # Fuzzy: 20-25% is the expected post-WS7 OCR-recovery baseline.
        # Anything beyond 30% would signal a new regression (chunker
        # syllable-drop, unresolved OCR batch, etc.).
        fuzzy_status = classify_threshold(ca_result["fuzzy_pct"], 25.0, 30.0)
        checks.append({
            "name": "chunk_attribution_fuzzy",
            "status": fuzzy_status,
            "value": ca_result["fuzzy_pct"],
            "threshold": "<25% green; 25-30% yellow; >30% red (baseline 20.8% — WS7 drift)",
            "details": {
                "fuzzy_count": ca_result["fuzzy_count"],
                "fuzzy_pct": ca_result["fuzzy_pct"],
                "total_sampled": ca_result["total_sampled"],
                "note": "Fuzzy > 30% historically correlated with chunker syllable-drop regression",
            },
        })
    else:
        for name in ("chunk_attribution_mismatch", "chunk_attribution_fuzzy"):
            checks.append({
                "name": name, "status": STATUS_UNKNOWN, "value": None,
                "threshold": "0% / <10% / <40%",
                "details": {"error": ca_err},
            })

    # 3: vector gaps
    vg_result, vg_err = _safe(check_vector_gaps, sample_size)
    if vg_result:
        status = classify_threshold(vg_result["missing_count"], 0, 100)
        checks.append({
            "name": "vector_gaps",
            "status": status,
            "value": vg_result["missing_count"],
            "threshold": "0 green; 1-100 yellow; >100 red",
            "details": vg_result,
        })
    else:
        checks.append({
            "name": "vector_gaps", "status": STATUS_UNKNOWN, "value": None,
            "threshold": "0 / 1-100 / >100", "details": {"error": vg_err},
        })

    # 4: raadslid roles
    rr_result, rr_err = _safe(check_raadslid_roles)
    if rr_result:
        status = classify_threshold(rr_result["errors"], 0, 10)
        checks.append({
            "name": "raadslid_roles",
            "status": status,
            "value": rr_result["errors"],
            "threshold": "0 errors green; 1-10 yellow; >10 red",
            "details": rr_result,
        })
    else:
        checks.append({
            "name": "raadslid_roles", "status": STATUS_UNKNOWN, "value": None,
            "threshold": "0 / 1-10 / >10", "details": {"error": rr_err},
        })

    # 5: financial coverage (higher is better)
    fc_result, fc_err = _safe(check_financial_coverage)
    if fc_result:
        status = classify_threshold(
            fc_result["coverage_pct"], 80.0, 50.0, invert=True,
        )
        checks.append({
            "name": "financial_coverage",
            "status": status,
            "value": fc_result["coverage_pct"],
            "threshold": ">=80% green; 50-80% yellow; <50% red",
            "details": fc_result,
        })
    else:
        checks.append({
            "name": "financial_coverage", "status": STATUS_UNKNOWN, "value": None,
            "threshold": ">=80 / >=50 / <50", "details": {"error": fc_err},
        })

    # 6: failures queue depth
    fq_result, fq_err = _safe(check_failures_queue_depth)
    if fq_result:
        status = classify_threshold(fq_result["failure_count_24h"], 0, 5)
        checks.append({
            "name": "failures_queue_depth",
            "status": status,
            "value": fq_result["failure_count_24h"],
            "threshold": "0 green; 1-5 yellow; >5 red",
            "details": fq_result,
        })
    else:
        checks.append({
            "name": "failures_queue_depth", "status": STATUS_UNKNOWN, "value": None,
            "threshold": "0 / 1-5 / >5", "details": {"error": fq_err},
        })

    # 7: smoke test
    st_result, st_err = _safe(check_smoke_test_status)
    if st_result:
        success = st_result["success_count"]
        if success >= 22:
            status = STATUS_GREEN
        elif success >= 18:
            status = STATUS_YELLOW
        else:
            status = STATUS_RED
        # If there are no runs at all, the hourly job hasn't been deployed yet.
        if st_result["runs_found"] == 0:
            status = STATUS_UNKNOWN
        checks.append({
            "name": "smoke_test_status",
            "status": status,
            "value": success,
            "threshold": ">=22/24 green; 18-21 yellow; <18 red",
            "details": st_result,
        })
    else:
        checks.append({
            "name": "smoke_test_status", "status": STATUS_UNKNOWN, "value": None,
            "threshold": ">=22 / >=18 / <18", "details": {"error": st_err},
        })

    # 8: active writers
    aw_result, aw_err = _safe(check_active_writers)
    if aw_result:
        status = classify_threshold(aw_result["long_running_count"], 0, 1)
        checks.append({
            "name": "active_writers",
            "status": status,
            "value": aw_result["long_running_count"],
            "threshold": "0 green; 1 yellow; >1 red",
            "details": aw_result,
        })
    else:
        checks.append({
            "name": "active_writers", "status": STATUS_UNKNOWN, "value": None,
            "threshold": "0 / 1 / >1", "details": {"error": aw_err},
        })

    # 9: lock contention
    lc_result, lc_err = _safe(check_lock_contention)
    if lc_result:
        status = classify_threshold(lc_result["ungranted_locks"], 0, 3)
        checks.append({
            "name": "lock_contention",
            "status": status,
            "value": lc_result["ungranted_locks"],
            "threshold": "0 green; 1-3 yellow; >3 red",
            "details": lc_result,
        })
    else:
        checks.append({
            "name": "lock_contention", "status": STATUS_UNKNOWN, "value": None,
            "threshold": "0 / 1-3 / >3", "details": {"error": lc_err},
        })

    return checks


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _default_report_path(now: datetime) -> Path:
    dir_ = PROJECT_ROOT / "reports" / "qa_digest"
    dir_.mkdir(parents=True, exist_ok=True)
    return dir_ / f"{now.strftime('%Y-%m-%d')}.json"


def _log_pipeline_run(result: dict) -> Optional[int]:
    """
    Append one row to ``pipeline_runs``. Returns the row id, or None on error.

    Uses a single short INSERT — no long transactions. Tolerates DB errors
    silently so a broken observability path never kills the digest.
    """
    status_map = {
        STATUS_GREEN: "success",
        STATUS_YELLOW: "success",
        STATUS_RED: "failure",
        STATUS_UNKNOWN: "failure",
    }
    run_status = status_map.get(result["overall_status"], "failure")
    n_green = sum(1 for c in result["checks"] if c["status"] == STATUS_GREEN)
    n_red = sum(1 for c in result["checks"] if c["status"] == STATUS_RED)
    red_names = [c["name"] for c in result["checks"] if c["status"] == STATUS_RED]
    short_summary = (
        f"overall={result['overall_status']}; {len(result['checks'])} checks; "
        f"{n_red} red: {','.join(red_names)[:200]}"
    )

    try:
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO pipeline_runs
                  (job_name, started_at, finished_at, status,
                   items_discovered, items_processed, items_failed,
                   error_message, triggered_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    "qa_digest",
                    result["started_at"],
                    result["finished_at"],
                    run_status,
                    len(result["checks"]),
                    n_green,
                    n_red,
                    short_summary if n_red else None,
                    result.get("triggered_by", "manual"),
                ),
            )
            row_id = cur.fetchone()[0]
            cur.close()
        return int(row_id)
    except Exception as exc:
        logger.warning("failed to log pipeline_runs row: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def run_full_digest(sample_size: int = 2000,
                    triggered_by: str = "manual") -> dict:
    """
    Run every check, classify, persist (pipeline_runs + reports JSON),
    and return the structured result.

    This is the function APScheduler calls.
    """
    start = datetime.now(timezone.utc)
    checks = _build_checks(sample_size)
    end = datetime.now(timezone.utc)

    statuses = [c["status"] for c in checks]
    overall = _overall(statuses)
    n_green = sum(1 for s in statuses if s == STATUS_GREEN)
    n_yellow = sum(1 for s in statuses if s == STATUS_YELLOW)
    n_red = sum(1 for s in statuses if s == STATUS_RED)
    n_unknown = sum(1 for s in statuses if s == STATUS_UNKNOWN)

    result = {
        "version": 1,
        "timestamp": start.isoformat(),
        "started_at": start.isoformat(),
        "finished_at": end.isoformat(),
        "duration_seconds": round((end - start).total_seconds(), 1),
        "sample_size": sample_size,
        "triggered_by": triggered_by,
        "overall_status": overall,
        "summary": (
            f"{n_green}/{len(checks)} green, "
            f"{n_yellow} yellow, {n_red} red, {n_unknown} unknown"
        ),
        "counts": {
            "green": n_green, "yellow": n_yellow,
            "red": n_red, "unknown": n_unknown, "total": len(checks),
        },
        "checks": checks,
    }

    # Persist JSON snapshot (overwrites the day's latest).
    try:
        out = _default_report_path(start.astimezone())
        out.write_text(
            json.dumps(result, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        result["report_path"] = str(out)
    except Exception as exc:
        logger.warning("failed to write report JSON: %s", exc)

    # Log to pipeline_runs.
    row_id = _log_pipeline_run(result)
    if row_id:
        result["pipeline_run_id"] = row_id

    return result


# ---------------------------------------------------------------------------
# CLI rendering
# ---------------------------------------------------------------------------

_EMOJI = {
    STATUS_GREEN: "[OK]",
    STATUS_YELLOW: "[WARN]",
    STATUS_RED: "[FAIL]",
    STATUS_UNKNOWN: "[??]",
}


def render_table(result: dict) -> str:
    lines = [
        f"QA Digest @ {result['timestamp']}",
        f"Overall: {result['overall_status'].upper()}  ({result['summary']})",
        "",
        f"{'Check':<32}{'Status':<10}{'Value':<14}Threshold",
        "-" * 88,
    ]
    for c in result["checks"]:
        val = c.get("value")
        val_s = f"{val}" if val is not None else "-"
        lines.append(
            f"{c['name']:<32}{_EMOJI.get(c['status'], '?') + ' ' + c['status']:<10}"
            f"{val_s:<14}{c.get('threshold', '')}"
        )
    report_path = result.get("report_path")
    if report_path:
        lines.extend(["", f"Report: {report_path}"])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _exit_code(overall: str) -> int:
    return {STATUS_GREEN: 0, STATUS_YELLOW: 1, STATUS_RED: 2,
            STATUS_UNKNOWN: 3}.get(overall, 3)


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Unified NeoDemos QA digest runner (read-only).",
    )
    p.add_argument("--sample-size", type=int, default=2000,
                   help="Sample size for chunk-attribution + vector-gap checks (default 2000).")
    p.add_argument("--output-format", choices=["json", "table"], default="table",
                   help="Stdout format (default: table).")
    p.add_argument("--email", action="store_true",
                   help="Also send the daily digest email to PIPELINE_ALERT_EMAIL.")
    p.add_argument("--triggered-by", choices=["cron", "manual", "smoke_test"],
                   default="manual",
                   help="pipeline_runs.triggered_by value (default: manual).")
    p.add_argument("--verbose", action="store_true")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_argparser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        result = run_full_digest(
            sample_size=args.sample_size,
            triggered_by=args.triggered_by,
        )
    except Exception as exc:
        logger.error("digest failed: %s\n%s", exc, traceback.format_exc())
        return 3

    if args.output_format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        print(render_table(result))

    if args.email:
        try:
            from services.pipeline_health_email import send_daily_digest
            recipient = os.getenv("PIPELINE_ALERT_EMAIL", "dennis@neodemos.nl")
            sent = send_daily_digest(recipient)
            logger.info("email send -> recipient=%s sent=%s", recipient, sent)
        except Exception as exc:
            logger.error("email send failed: %s", exc)
            # Don't flip exit code on email failure — the digest itself succeeded.

    return _exit_code(result["overall_status"])


if __name__ == "__main__":
    sys.exit(main())
