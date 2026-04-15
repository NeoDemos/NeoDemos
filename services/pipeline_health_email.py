"""
WS5a Phase A — Daily pipeline health email composer + sender.

Reads the latest ``qa_digest`` row + last 24h of ``pipeline_runs`` /
``pipeline_failures`` / ``pipeline_runs WHERE job_name='00_smoke_test'`` and
produces an HTML + plaintext email. The actual SMTP path is delegated to
``services.email_service.EmailService`` — we never build new infrastructure.

Design rules
------------
* READ-ONLY against production tables. Every query is a small indexed SELECT,
  bounded by an explicit ``INTERVAL '24 hours'`` window.
* Uses ``services/db_pool.get_connection`` — never opens a raw psycopg2
  connection.
* Subject is ``NeoDemos pipeline — [GREEN|YELLOW|RED] YYYY-MM-DD`` so the
  inbox sort surfaces non-green days.
* Never throws on missing data — if ``qa_digest`` has no row yet, the email
  renders with explicit "No digest yet" placeholders rather than crashing
  the scheduled job.

Public API
----------
    compose_daily_digest(db_conn=None) -> (html: str, text: str, subject: str)
    send_daily_digest(recipient: str) -> bool
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from services.db_pool import get_connection
from services.email_service import EmailService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data loading — each function takes an optional conn so tests can mock
# ---------------------------------------------------------------------------

def _latest_qa_digest_row(cur) -> Optional[dict]:
    """Return the most recent ``qa_digest`` row + parsed error_message summary."""
    cur.execute(
        """
        SELECT id, started_at, finished_at, status,
               items_discovered, items_processed, items_failed, error_message,
               triggered_by
        FROM pipeline_runs
        WHERE job_name = 'qa_digest'
        ORDER BY started_at DESC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    if not row:
        return None
    return {
        "id": row[0],
        "started_at": row[1],
        "finished_at": row[2],
        "status": row[3],
        "items_discovered": row[4],
        "items_processed": row[5],
        "items_failed": row[6],
        "error_message": row[7],
        "triggered_by": row[8],
    }


def _runs_last_24h(cur) -> list[dict]:
    cur.execute(
        """
        SELECT job_name,
               COUNT(*)                                          AS total,
               SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END)  AS succeeded,
               SUM(CASE WHEN status = 'failure' THEN 1 ELSE 0 END)  AS failed,
               SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END)  AS running,
               SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END)  AS skipped
        FROM pipeline_runs
        WHERE started_at > NOW() - INTERVAL '24 hours'
        GROUP BY job_name
        ORDER BY job_name
        """
    )
    cols = ["job_name", "total", "succeeded", "failed", "running", "skipped"]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _failures_last_24h(cur, limit: int = 20) -> list[dict]:
    cur.execute(
        """
        SELECT job_name, item_id, item_type, failed_at, error_class,
               LEFT(COALESCE(error_message, ''), 300) AS error_message
        FROM pipeline_failures
        WHERE failed_at > NOW() - INTERVAL '24 hours'
        ORDER BY failed_at DESC
        LIMIT %s
        """,
        (limit,),
    )
    cols = ["job_name", "item_id", "item_type", "failed_at",
            "error_class", "error_message"]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _smoke_summary(cur) -> dict:
    cur.execute(
        """
        SELECT COUNT(*) FILTER (WHERE status = 'success')  AS succeeded,
               COUNT(*) FILTER (WHERE status = 'failure')  AS failed,
               COUNT(*)                                     AS total
        FROM pipeline_runs
        WHERE job_name = '00_smoke_test'
          AND started_at > NOW() - INTERVAL '24 hours'
        """
    )
    r = cur.fetchone()
    return {
        "succeeded": int(r[0]) if r and r[0] is not None else 0,
        "failed": int(r[1]) if r and r[1] is not None else 0,
        "total": int(r[2]) if r and r[2] is not None else 0,
    }


def _week_deltas(cur) -> dict:
    """Rough comparison: failures this week vs last week."""
    cur.execute(
        """
        SELECT
          COUNT(*) FILTER (WHERE failed_at > NOW() - INTERVAL '7 days') AS this_week,
          COUNT(*) FILTER (WHERE failed_at > NOW() - INTERVAL '14 days'
                           AND failed_at <= NOW() - INTERVAL '7 days')  AS last_week
        FROM pipeline_failures
        """
    )
    r = cur.fetchone()
    tw = int(r[0]) if r and r[0] is not None else 0
    lw = int(r[1]) if r and r[1] is not None else 0
    delta = tw - lw
    return {"this_week": tw, "last_week": lw, "delta": delta}


def _load_digest_json(report_path: Optional[str]) -> Optional[dict]:
    """The digest writes a full JSON snapshot under reports/qa_digest/. We
    prefer that over parsing error_message because it contains every check."""
    if not report_path:
        return None
    try:
        with open(report_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _find_todays_digest_json() -> Optional[dict]:
    """Fallback: find today's digest JSON by filename."""
    try:
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        today = datetime.now().strftime("%Y-%m-%d")
        path = root / "reports" / "qa_digest" / f"{today}.json"
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        # Try yesterday if today's isn't written yet
        yday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        yday_path = root / "reports" / "qa_digest" / f"{yday}.json"
        if yday_path.exists():
            with yday_path.open("r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_STATUS_BADGE = {
    "green": "GREEN",
    "yellow": "YELLOW",
    "red": "RED",
    "unknown": "UNKNOWN",
}
_STATUS_COLOR = {
    "green": "#2e7d32",
    "yellow": "#ed6c02",
    "red": "#d32f2f",
    "unknown": "#616161",
}
_RUN_MARK = {
    "success": "OK",
    "failure": "FAIL",
    "running": "...",
    "skipped": "skip",
}


def _fmt_run_line(r: dict) -> str:
    parts = [f"{r['job_name']:<30}"]
    if int(r["failed"]) == 0 and int(r["running"]) == 0:
        parts.append(f"OK {int(r['succeeded'])}/{int(r['total'])} success")
    else:
        parts.append(
            f"{int(r['succeeded'])}/{int(r['total'])} success "
            f"({int(r['failed'])} failed, {int(r['running'])} running)"
        )
    return " ".join(parts)


def _fmt_check_line(c: dict) -> str:
    val = c.get("value")
    val_s = f"{val}" if val is not None else "-"
    name = c["name"]
    status = c["status"]
    tag = f"({status})" if status != "green" else "(green)"
    return f"  {name:<32} {val_s:<12} {tag}"


def compose_daily_digest(
    db_conn=None,
) -> tuple[str, str, str]:
    """
    Build the daily digest email. Returns (html, text, subject).

    Safe against all the "first day of deployment" edge cases:
    - no qa_digest row       -> subject shows UNKNOWN, body explains
    - no pipeline_runs at all -> "No runs recorded in the last 24h"
    - no failures             -> omits the failures section cleanly
    """
    own_conn = db_conn is None
    ctx = get_connection() if own_conn else _BorrowedConn(db_conn)
    digest_row: Optional[dict] = None
    digest_json: Optional[dict] = None
    runs: list[dict] = []
    failures: list[dict] = []
    smoke: dict = {"succeeded": 0, "failed": 0, "total": 0}
    deltas: dict = {"this_week": 0, "last_week": 0, "delta": 0}

    with ctx as conn:
        cur = conn.cursor()
        try:
            digest_row = _latest_qa_digest_row(cur)
            runs = _runs_last_24h(cur)
            failures = _failures_last_24h(cur, limit=20)
            smoke = _smoke_summary(cur)
            deltas = _week_deltas(cur)
        except Exception as exc:
            logger.warning("digest email data load failed: %s", exc)
        finally:
            try:
                cur.close()
            except Exception:
                pass

    digest_json = _find_todays_digest_json()

    today = datetime.now().strftime("%Y-%m-%d")
    if digest_json:
        overall = digest_json.get("overall_status", "unknown")
        summary = digest_json.get("summary", "")
        checks = digest_json.get("checks", []) or []
    elif digest_row:
        # Fallback to parsing the pipeline_runs row when the JSON is missing.
        overall = (
            "green" if digest_row["status"] == "success" and not digest_row["error_message"]
            else "red" if digest_row["status"] == "failure"
            else "unknown"
        )
        summary = digest_row.get("error_message") or "No digest summary available"
        checks = []
    else:
        overall = "unknown"
        summary = "No digest has been recorded yet"
        checks = []

    badge = _STATUS_BADGE.get(overall, "UNKNOWN")
    color = _STATUS_COLOR.get(overall, "#616161")
    subject = f"NeoDemos pipeline — {badge} {today}"

    # ------------------------------------------------------------------ text
    tlines: list[str] = []
    tlines.append(f"Subject: {subject}")
    tlines.append("")
    tlines.append(f"Overall: {badge} ({summary})")
    tlines.append("")
    tlines.append("Pipeline runs (last 24h):")
    if runs:
        for r in runs:
            tlines.append(f"  {_fmt_run_line(r)}")
    else:
        tlines.append("  (no pipeline runs recorded in the last 24 hours)")
    tlines.append("")

    tlines.append("Smoke test (last 24h):")
    if smoke["total"] > 0:
        tlines.append(f"  {smoke['succeeded']}/{smoke['total']} success "
                      f"({smoke['failed']} failed)")
    else:
        tlines.append("  (no smoke test runs yet)")
    tlines.append("")

    tlines.append("Quality gates:")
    if checks:
        for c in checks:
            tlines.append(_fmt_check_line(c))
    else:
        tlines.append("  (no digest JSON found — run scripts/nightly/qa_digest.py)")
    tlines.append("")

    tlines.append("Failures (last 24h):")
    if failures:
        for f in failures[:10]:
            when = f["failed_at"].strftime("%H:%M") if hasattr(f["failed_at"], "strftime") else str(f["failed_at"])
            tlines.append(
                f"  [{when}] {f['job_name']} item={f['item_id'] or '-'} "
                f"{f['error_class'] or ''}: {(f['error_message'] or '').strip()[:140]}"
            )
        if len(failures) > 10:
            tlines.append(f"  ... and {len(failures) - 10} more")
    else:
        tlines.append("  (no failures)")
    tlines.append("")

    tlines.append(
        f"Week-over-week failures: {deltas['this_week']} this week vs "
        f"{deltas['last_week']} last week (delta {deltas['delta']:+d})"
    )
    tlines.append("")
    tlines.append("Links:")
    tlines.append("  https://neodemos.nl/admin/pipeline")
    tlines.append("")
    tlines.append("-- NeoDemos QA digest (WS5a Phase A)")
    text = "\n".join(tlines)

    # ------------------------------------------------------------------ html
    def esc(s: Any) -> str:
        return html.escape(str(s) if s is not None else "")

    hlines: list[str] = []
    hlines.append(f'<h2 style="color:{color};margin-bottom:4px;">'
                  f'NeoDemos pipeline — {esc(badge)} {esc(today)}</h2>')
    hlines.append(f'<p style="margin-top:0;color:#555;">Overall: <strong style="color:{color};">'
                  f'{esc(badge)}</strong> &mdash; {esc(summary)}</p>')

    # Pipeline runs table
    hlines.append("<h3>Pipeline runs (last 24h)</h3>")
    if runs:
        hlines.append('<table cellpadding="4" cellspacing="0" border="0" '
                      'style="border-collapse:collapse;font-family:monospace;">')
        hlines.append("<tr style='background:#f5f5f5;'>"
                      "<th align='left'>Job</th><th>Success</th><th>Failure</th>"
                      "<th>Running</th><th>Skipped</th><th>Total</th></tr>")
        for r in runs:
            ok = int(r["succeeded"])
            fail = int(r["failed"])
            row_color = "#d32f2f" if fail > 0 else "#2e7d32"
            hlines.append(
                f"<tr>"
                f"<td>{esc(r['job_name'])}</td>"
                f"<td align='right' style='color:{row_color};'>{ok}</td>"
                f"<td align='right' style='color:#d32f2f;'>{fail}</td>"
                f"<td align='right'>{int(r['running'])}</td>"
                f"<td align='right'>{int(r['skipped'])}</td>"
                f"<td align='right'><strong>{int(r['total'])}</strong></td>"
                f"</tr>"
            )
        hlines.append("</table>")
    else:
        hlines.append("<p><em>No runs yet — pipeline_runs is empty for the last 24h.</em></p>")

    # Smoke
    hlines.append("<h3>Smoke test (last 24h)</h3>")
    if smoke["total"] > 0:
        smoke_color = "#2e7d32" if smoke["failed"] == 0 else "#d32f2f"
        hlines.append(
            f"<p style='font-family:monospace;color:{smoke_color};'>"
            f"{smoke['succeeded']}/{smoke['total']} success ({smoke['failed']} failed)</p>"
        )
    else:
        hlines.append("<p><em>No smoke test runs yet.</em></p>")

    # Quality gates
    hlines.append("<h3>Quality gates</h3>")
    if checks:
        hlines.append('<table cellpadding="4" cellspacing="0" border="0" '
                      'style="border-collapse:collapse;font-family:monospace;">')
        hlines.append("<tr style='background:#f5f5f5;'>"
                      "<th align='left'>Check</th><th align='left'>Status</th>"
                      "<th align='right'>Value</th><th align='left'>Threshold</th></tr>")
        for c in checks:
            c_color = _STATUS_COLOR.get(c["status"], "#616161")
            val = c.get("value")
            val_s = f"{val}" if val is not None else "-"
            hlines.append(
                f"<tr>"
                f"<td>{esc(c['name'])}</td>"
                f"<td style='color:{c_color};'><strong>{esc(c['status'].upper())}</strong></td>"
                f"<td align='right'>{esc(val_s)}</td>"
                f"<td style='color:#777;'>{esc(c.get('threshold', ''))}</td>"
                f"</tr>"
            )
        hlines.append("</table>")
    else:
        hlines.append("<p><em>No digest JSON found — run <code>python scripts/nightly/qa_digest.py</code>.</em></p>")

    # Failures
    hlines.append("<h3>Failures (last 24h)</h3>")
    if failures:
        hlines.append('<table cellpadding="4" cellspacing="0" border="0" '
                      'style="border-collapse:collapse;font-family:monospace;font-size:12px;">')
        hlines.append("<tr style='background:#f5f5f5;'>"
                      "<th>When</th><th>Job</th><th>Item</th>"
                      "<th>Error class</th><th align='left'>Message</th></tr>")
        for f in failures[:20]:
            when = f["failed_at"].strftime("%H:%M") if hasattr(f["failed_at"], "strftime") else str(f["failed_at"])
            hlines.append(
                f"<tr>"
                f"<td>{esc(when)}</td>"
                f"<td>{esc(f['job_name'])}</td>"
                f"<td>{esc(f['item_id'] or '-')}</td>"
                f"<td>{esc(f['error_class'] or '')}</td>"
                f"<td>{esc((f['error_message'] or '').strip()[:180])}</td>"
                f"</tr>"
            )
        hlines.append("</table>")
    else:
        hlines.append("<p><em>No failures. Nice.</em></p>")

    # Deltas
    delta_sign = "+" if deltas["delta"] >= 0 else ""
    delta_color = "#d32f2f" if deltas["delta"] > 0 else "#2e7d32"
    hlines.append(
        f"<p>Week-over-week failures: {deltas['this_week']} this week vs "
        f"{deltas['last_week']} last week "
        f"(<strong style='color:{delta_color};'>{delta_sign}{deltas['delta']}</strong>)</p>"
    )

    hlines.append("<hr>")
    hlines.append("<p><a href='https://neodemos.nl/admin/pipeline'>Open /admin/pipeline</a></p>")
    hlines.append("<p style='color:#999;font-size:11px;'>NeoDemos QA digest (WS5a Phase A)</p>")

    html_body = (
        "<html><body style='font-family:Arial,sans-serif;color:#333;'>"
        + "".join(hlines)
        + "</body></html>"
    )

    return html_body, text, subject


# ---------------------------------------------------------------------------
# Sender
# ---------------------------------------------------------------------------

def send_daily_digest(recipient: str) -> bool:
    """
    Compose the digest and send it via EmailService.send_error_notification.

    We deliberately reuse the existing async SMTP path instead of introducing
    a new one; we just override the To: address at runtime by briefly setting
    EmailService.recipient_email for this send.

    Returns True on success, False otherwise. Errors are logged but never
    raised, so a broken SMTP server never kills the APScheduler job.
    """
    try:
        html_body, text_body, subject = compose_daily_digest()
    except Exception as exc:
        logger.error("compose_daily_digest failed: %s", exc)
        return False

    # Guard: if SMTP isn't configured we no-op silently so dev machines don't
    # crash the scheduler. EmailService already logs this condition.
    service = EmailService()
    if not service.sender_email or not service.sender_password:
        logger.warning("SMTP not configured — skipping digest send (compose ok)")
        return False

    service.recipient_email = recipient or service.recipient_email

    # Compose a message object directly — EmailService.send_error_notification
    # is shaped for error payloads, but we need to attach both HTML and text
    # versions of the digest. Use smtplib the same way the service does.
    try:
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[NeoDemos] {subject}"
        msg["From"] = service.sender_email
        msg["To"] = service.recipient_email
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP(service.smtp_server, service.smtp_port) as server:
            server.starttls()
            server.login(service.sender_email, service.sender_password)
            server.send_message(msg)
        logger.info("QA digest email sent to %s", service.recipient_email)
        return True
    except Exception as exc:
        logger.error("QA digest email send failed: %s", exc)
        # Last-ditch: try the existing async path as a plain-text fallback.
        try:
            asyncio.run(service.send_error_notification(
                subject=subject,
                error_message=text_body,
                timestamp=datetime.now(timezone.utc),
            ))
            return True
        except Exception as exc2:
            logger.error("fallback error-notification send also failed: %s", exc2)
            return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _BorrowedConn:
    """Context manager that yields an existing psycopg2 connection without
    closing it on exit (used when callers pass their own ``db_conn``)."""

    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            try:
                self._conn.rollback()
            except Exception:
                pass
        return False
