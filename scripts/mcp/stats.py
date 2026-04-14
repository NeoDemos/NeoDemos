#!/usr/bin/env python3
"""
MCP audit log — one-shot terminal summary.

Reads mcp_audit_log from production Postgres (via SSH tunnel on localhost:5432)
and prints top queries, error rate, latency, hourly histogram, top users.

Usage:
    # Last 24h (default)
    python3 scripts/mcp/stats.py

    # Last 2 hours, JSON output
    python3 scripts/mcp/stats.py --window 2h --json

    # Scope to one tool / user
    python3 scripts/mcp/stats.py --tool zoek_moties --window 7d
    python3 scripts/mcp/stats.py --user dennis --window 1d

    # No ANSI colors
    python3 scripts/mcp/stats.py --no-color

Prereq: ./scripts/dev_tunnel.sh --bg
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Ensure repo root is on sys.path so `from services.db_pool import ...` works
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.db_pool import get_connection  # noqa: E402

SPARK_CHARS = "▁▂▃▄▅▆▇█"
_DURATION_RE = re.compile(r"^\s*(\d+)\s*(m|h|d)\s*$", re.IGNORECASE)


def parse_window(s: str) -> str:
    """Parse '30m' / '2h' / '7d' into a Postgres INTERVAL literal string."""
    m = _DURATION_RE.match(s)
    if not m:
        raise argparse.ArgumentTypeError(
            f"bad window {s!r}; expected e.g. 30m, 2h, 7d"
        )
    n, unit = int(m.group(1)), m.group(2).lower()
    unit_name = {"m": "minutes", "h": "hours", "d": "days"}[unit]
    return f"{n} {unit_name}"


def check_tunnel(host: str = "127.0.0.1", port: int = 5432) -> None:
    """Probe Postgres port; exit with a helpful error if unreachable."""
    try:
        with socket.create_connection((host, port), timeout=2):
            return
    except (OSError, socket.timeout) as e:
        print(
            f"ERROR: cannot reach Postgres at {host}:{port} ({e}).\n"
            f"Start the SSH tunnel first:\n"
            f"    ./scripts/dev_tunnel.sh --bg",
            file=sys.stderr,
        )
        sys.exit(2)


def _tool_filter(tool: str | None) -> tuple[str, list[Any]]:
    return (" AND tool_name = %s", [tool]) if tool else ("", [])


def _user_filter(user_prefix: str | None) -> tuple[str, list[Any]]:
    return (
        (" AND user_id LIKE %s", [user_prefix + "%"]) if user_prefix else ("", [])
    )


def fetch_headline(
    cur: Any, window: str, tool: str | None, user: str | None
) -> dict[str, Any]:
    tf, tv = _tool_filter(tool)
    uf, uv = _user_filter(user)
    cur.execute(
        f"""
        SELECT
          COUNT(*) AS calls,
          COUNT(DISTINCT user_id) AS users,
          COUNT(*) FILTER (WHERE status_code >= 400 OR error_class IS NOT NULL) AS errors,
          PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95
        FROM mcp_audit_log
        WHERE ts > NOW() - INTERVAL %s
        {tf}{uf}
        """,
        [window, *tv, *uv],
    )
    row = cur.fetchone()
    calls = int(row[0] or 0)
    errors = int(row[2] or 0)
    success = ((calls - errors) / calls * 100.0) if calls else 0.0
    err_pct = (errors / calls * 100.0) if calls else 0.0
    return {
        "calls": calls,
        "unique_users": int(row[1] or 0),
        "errors": errors,
        "success_pct": round(success, 1),
        "error_pct": round(err_pct, 1),
        "p95_ms": int(row[3]) if row[3] is not None else None,
    }


def fetch_tool_usage(
    cur: Any, window: str, tool: str | None, user: str | None
) -> list[dict[str, Any]]:
    tf, tv = _tool_filter(tool)
    uf, uv = _user_filter(user)
    cur.execute(
        f"""
        SELECT
          tool_name,
          COUNT(*) AS calls,
          PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY latency_ms) AS p50,
          PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95,
          COUNT(*) FILTER (WHERE status_code >= 400 OR error_class IS NOT NULL) AS errors
        FROM mcp_audit_log
        WHERE ts > NOW() - INTERVAL %s
        {tf}{uf}
        GROUP BY tool_name
        ORDER BY calls DESC
        LIMIT 10
        """,
        [window, *tv, *uv],
    )
    return [
        {
            "tool": r[0],
            "calls": int(r[1]),
            "p50_ms": int(r[2]) if r[2] is not None else None,
            "p95_ms": int(r[3]) if r[3] is not None else None,
            "errors": int(r[4]),
        }
        for r in cur.fetchall()
    ]


def fetch_errors(
    cur: Any, window: str, tool: str | None, user: str | None
) -> list[dict[str, Any]]:
    tf, tv = _tool_filter(tool)
    uf, uv = _user_filter(user)
    cur.execute(
        f"""
        SELECT
          COALESCE(error_class, 'http_' || status_code::text) AS ec,
          COUNT(*) AS n,
          MAX(ts) AS last_seen,
          (ARRAY_AGG(tool_name ORDER BY ts DESC))[1] AS sample_tool
        FROM mcp_audit_log
        WHERE ts > NOW() - INTERVAL %s
          AND (status_code >= 400 OR error_class IS NOT NULL)
          {tf}{uf}
        GROUP BY ec
        ORDER BY n DESC
        LIMIT 10
        """,
        [window, *tv, *uv],
    )
    return [
        {
            "error_class": r[0],
            "count": int(r[1]),
            "last_seen": r[2].astimezone(timezone.utc).isoformat() if r[2] else None,
            "sample_tool": r[3],
        }
        for r in cur.fetchall()
    ]


def fetch_hourly(
    cur: Any, window: str, tool: str | None, user: str | None
) -> list[dict[str, Any]]:
    tf, tv = _tool_filter(tool)
    uf, uv = _user_filter(user)
    cur.execute(
        f"""
        SELECT date_trunc('hour', ts) AS bucket, COUNT(*)
        FROM mcp_audit_log
        WHERE ts > NOW() - INTERVAL %s
        {tf}{uf}
        GROUP BY bucket
        ORDER BY bucket ASC
        """,
        [window, *tv, *uv],
    )
    return [
        {"bucket": r[0].astimezone(timezone.utc).isoformat(), "count": int(r[1])}
        for r in cur.fetchall()
    ]


def fetch_top_users(
    cur: Any, window: str, tool: str | None, user: str | None
) -> list[dict[str, Any]]:
    tf, tv = _tool_filter(tool)
    uf, uv = _user_filter(user)
    cur.execute(
        f"""
        SELECT
          COALESCE(user_id, '(anonymous)') AS uid,
          COUNT(*) AS calls,
          COUNT(DISTINCT tool_name) AS distinct_tools,
          COUNT(*) FILTER (WHERE status_code >= 400 OR error_class IS NOT NULL) AS errors
        FROM mcp_audit_log
        WHERE ts > NOW() - INTERVAL %s
        {tf}{uf}
        GROUP BY uid
        ORDER BY calls DESC
        LIMIT 5
        """,
        [window, *tv, *uv],
    )
    out = []
    for r in cur.fetchall():
        calls = int(r[1])
        errors = int(r[3])
        out.append(
            {
                "user_id": r[0],
                "calls": calls,
                "distinct_tools": int(r[2]),
                "errors": errors,
                "error_pct": round((errors / calls * 100.0) if calls else 0.0, 1),
            }
        )
    return out


# ---- formatting ----------------------------------------------------------


def mask_user(uid: str) -> str:
    if uid in ("(anonymous)", "dennis") or uid.startswith("dennis"):
        return uid
    if len(uid) <= 8:
        return uid
    return uid[:8] + "\u2026"


def sparkline(counts: Iterable[int]) -> str:
    counts = list(counts)
    if not counts:
        return ""
    mx = max(counts)
    if mx == 0:
        return "_" * len(counts)
    step = mx / (len(SPARK_CHARS) - 1)
    return "".join(
        "_" if c == 0 else SPARK_CHARS[min(len(SPARK_CHARS) - 1, int(c / step))]
        for c in counts
    )


def fmt_ms(v: int | None) -> str:
    return "-" if v is None else f"{v}ms"


def print_text(
    headline: dict[str, Any],
    tools: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    hourly: list[dict[str, Any]],
    users: list[dict[str, Any]],
    window_label: str,
    use_color: bool,
) -> None:
    bold = "\033[1m" if use_color else ""
    dim = "\033[2m" if use_color else ""
    red = "\033[31m" if use_color else ""
    reset = "\033[0m" if use_color else ""

    # A. Headline
    p95 = fmt_ms(headline["p95_ms"])
    err_clr = red if headline["errors"] else ""
    print(
        f"{bold}Last {window_label}:{reset} "
        f"{headline['calls']} calls \u00b7 "
        f"{headline['unique_users']} unique users \u00b7 "
        f"{headline['success_pct']}% success \u00b7 "
        f"p95 latency {p95} \u00b7 "
        f"{err_clr}{headline['errors']} errors ({headline['error_pct']}%){reset}"
    )
    print()

    # B. Tool usage
    if tools:
        print(f"{bold}Tool usage (top 10){reset}")
        print(
            f"{'Tool':<30}{'Calls':>8}  {'p50':>8}  {'p95':>8}  {'errors':>8}"
        )
        for t in tools:
            print(
                f"{t['tool']:<30}{t['calls']:>8}  "
                f"{fmt_ms(t['p50_ms']):>8}  {fmt_ms(t['p95_ms']):>8}  "
                f"{t['errors']:>8}"
            )
        print()

    # C. Error breakdown
    if errors:
        print(f"{bold}Errors{reset}")
        print(f"{'Error class':<22}{'Count':>7}  {'Last seen':<20}{'Sample tool'}")
        for e in errors:
            ts = (e["last_seen"] or "")[:19].replace("T", " ")
            print(
                f"{e['error_class']:<22}{e['count']:>7}  "
                f"{ts:<20}{e['sample_tool'] or '-'}"
            )
        print()

    # D. Hourly histogram
    if hourly:
        print(f"{bold}Hourly activity{reset}")
        counts = [h["count"] for h in hourly]
        # Print label per bucket with sparkline char
        mx = max(counts) if counts else 0
        step = (mx / (len(SPARK_CHARS) - 1)) if mx else 0
        cells = []
        for h in hourly:
            dt = datetime.fromisoformat(h["bucket"]).astimezone()
            lbl = dt.strftime("%H")
            nxt = (dt.hour + 1) % 24
            c = h["count"]
            if c == 0:
                ch = "_"
            else:
                ch = SPARK_CHARS[min(len(SPARK_CHARS) - 1, int(c / step))] if step else SPARK_CHARS[-1]
            cells.append(f"{lbl}-{nxt:02d} {ch}")
        # wrap at 6 per line
        for i in range(0, len(cells), 6):
            print("   ".join(cells[i : i + 6]))
        print(f"{dim}(max bucket: {mx}){reset}")
        print()

    # E. Top users
    if users:
        print(f"{bold}Top users (top 5){reset}")
        print(f"{'User':<18}{'Calls':>7}  {'Tools used':<18}{'Error %':>8}")
        for u in users:
            print(
                f"{mask_user(u['user_id']):<18}{u['calls']:>7}  "
                f"{str(u['distinct_tools']) + ' distinct':<18}"
                f"{u['error_pct']:>7}%"
            )


def main() -> int:
    ap = argparse.ArgumentParser(description="MCP audit-log summary")
    ap.add_argument("--window", type=parse_window, default=parse_window("24h"),
                    help="Time window (e.g. 30m, 2h, 7d). Default 24h.")
    ap.add_argument("--tool", help="Filter to a single tool_name")
    ap.add_argument("--user", help="Filter to user_id prefix")
    ap.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    ap.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    args = ap.parse_args()

    check_tunnel()

    use_color = (not args.no_color) and sys.stdout.isatty() and os.getenv("NO_COLOR") is None
    window_label = args.window

    with get_connection() as conn:
        with conn.cursor() as cur:
            headline = fetch_headline(cur, args.window, args.tool, args.user)
            tools = fetch_tool_usage(cur, args.window, args.tool, args.user)
            errors = fetch_errors(cur, args.window, args.tool, args.user)
            hourly = fetch_hourly(cur, args.window, args.tool, args.user)
            users = fetch_top_users(cur, args.window, args.tool, args.user)

    if args.json:
        print(json.dumps({
            "window": window_label,
            "tool_filter": args.tool,
            "user_filter": args.user,
            "headline": headline,
            "tool_usage": tools,
            "errors": errors,
            "hourly": hourly,
            "top_users": [{**u, "user_id_masked": mask_user(u["user_id"])} for u in users],
        }, indent=2, default=str))
        return 0

    if headline["calls"] == 0:
        print(f"No MCP activity in last {window_label}.")
        return 0

    print_text(headline, tools, errors, hourly, users, window_label, use_color)
    return 0


if __name__ == "__main__":
    sys.exit(main())
