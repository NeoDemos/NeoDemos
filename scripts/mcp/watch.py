#!/usr/bin/env python3
"""Live terminal tail of the mcp_audit_log Postgres table.

A tiny tool for watching MCP traffic in real time during demos/press moments.
Connects through the local SSH tunnel (localhost:5432) — start the tunnel first
with ./scripts/dev_tunnel.sh --bg. See: python scripts/mcp/watch.py --help

Examples:
    python scripts/mcp/watch.py
    python scripts/mcp/watch.py --since 1h --errors-only
    python scripts/mcp/watch.py --tool zoek_moties --interval 5
"""
from __future__ import annotations

import argparse
import os
import re
import signal
import socket
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

# Make repo root importable so `services.db_pool` resolves when run directly.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

try:
    from services.db_pool import get_connection  # type: ignore
except Exception as e:  # pragma: no cover
    print(f"ERROR: cannot import services.db_pool ({e}).", file=sys.stderr)
    sys.exit(2)


# ---------- ANSI ----------
RESET = "\x1b[0m"
GREEN = "\x1b[32m"
RED = "\x1b[31m"
YELLOW = "\x1b[33m"
DIM = "\x1b[2m"


def _colorize(s: str, code: str, enabled: bool) -> str:
    return f"{code}{s}{RESET}" if enabled else s


# ---------- helpers ----------
def parse_since(s: str) -> timedelta:
    """Parse '1h', '30m', '45s', '2d' into a timedelta."""
    m = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", s)
    if not m:
        raise argparse.ArgumentTypeError(f"Invalid --since: {s!r} (use e.g. 30m, 1h, 2d)")
    n, unit = int(m.group(1)), m.group(2)
    return timedelta(seconds=n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit])


def check_tunnel(host: str = "127.0.0.1", port: int = 5432, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def mask_ip(ip: str | None) -> str:
    if not ip:
        return "—"
    # IPv4 → /16
    parts = ip.split(".")
    if len(parts) == 4:
        return f"{parts[0]}.{parts[1]}.x.x"
    # IPv6 or weird → show first block only
    return ip.split(":")[0] + ":…" if ":" in ip else ip[:12]


def mask_user(user_id: str | None) -> str:
    if not user_id:
        return "—"
    return user_id[:8]


def fmt_row(row: dict, color: bool) -> str:
    ts = row["ts"]
    if isinstance(ts, datetime):
        ts_str = ts.astimezone().strftime("%H:%M:%S")
    else:
        ts_str = str(ts)[:8]

    status = row.get("status_code")
    status_str = f"{status:<4}" if status is not None else "—   "
    status_block = f"[{status_str}]"
    if color:
        if status is None:
            status_block = _colorize(status_block, DIM, True)
        elif 200 <= status < 300:
            status_block = _colorize(status_block, GREEN, True)
        elif status >= 400:
            status_block = _colorize(status_block, RED, True)

    lat = row.get("latency_ms")
    lat_str = f"{lat}ms" if lat is not None else "—"
    if color and lat is not None and lat > 3000:
        lat_str = _colorize(lat_str, YELLOW, True)

    tool = (row.get("tool_name") or "—")[:24].ljust(24)
    user = mask_user(row.get("user_id")).ljust(8)
    ip = mask_ip(row.get("ip")).ljust(16)
    err = row.get("error_class") or "—"
    if color and row.get("error_class"):
        err = _colorize(err, RED, True)

    return f"{ts_str}  {status_block}  {tool}  {lat_str:<10}  {user}  {ip}  {err}"


# ---------- queries ----------
SELECT_COLS = (
    "id, ts, user_id, tool_name, latency_ms, status_code, ip, error_class"
)


def build_filters(args: argparse.Namespace) -> tuple[str, list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    if args.tool:
        where.append("tool_name = %s")
        params.append(args.tool)
    if args.user:
        where.append("user_id LIKE %s")
        params.append(args.user + "%")
    if args.errors_only:
        where.append("(status_code >= 400 OR error_class IS NOT NULL)")
    clause = (" AND " + " AND ".join(where)) if where else ""
    return clause, params


def fetch_initial(args: argparse.Namespace) -> list[dict]:
    filt, params = build_filters(args)
    if args.since:
        cutoff = datetime.now(timezone.utc) - args.since
        sql = (
            f"SELECT {SELECT_COLS} FROM mcp_audit_log "
            f"WHERE ts >= %s{filt} ORDER BY id ASC"
        )
        q_params: list[Any] = [cutoff] + params
    else:
        sql = (
            f"SELECT * FROM (SELECT {SELECT_COLS} FROM mcp_audit_log "
            f"WHERE TRUE{filt} ORDER BY id DESC LIMIT 20) t ORDER BY id ASC"
        )
        q_params = params
    return _run(sql, q_params)


def fetch_since_id(last_id: int, args: argparse.Namespace) -> list[dict]:
    filt, params = build_filters(args)
    sql = (
        f"SELECT {SELECT_COLS} FROM mcp_audit_log "
        f"WHERE id > %s{filt} ORDER BY id ASC LIMIT 200"
    )
    return _run(sql, [last_id] + params)


def _run(sql: str, params: Iterable[Any]) -> list[dict]:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, list(params))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]


# ---------- main loop ----------
def main() -> int:
    ap = argparse.ArgumentParser(description="Live tail of mcp_audit_log.")
    ap.add_argument("--since", type=parse_since, default=None,
                    help="Show rows since N ago (e.g. 30m, 1h, 2d)")
    ap.add_argument("--tool", default=None, help="Filter to one tool_name")
    ap.add_argument("--user", default=None, help="Filter by user_id prefix")
    ap.add_argument("--errors-only", action="store_true",
                    help="Only status_code >= 400 or error_class IS NOT NULL")
    ap.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    ap.add_argument("--interval", type=float, default=10.0, help="Poll interval (s)")
    args = ap.parse_args()

    color = (not args.no_color) and sys.stdout.isatty()

    if not check_tunnel():
        print(
            "ERROR: Postgres not reachable on localhost:5432.\n"
            "  Start the tunnel: ./scripts/dev_tunnel.sh --bg\n"
            "  (See memory: feedback_tunnel_workflow.md)",
            file=sys.stderr,
        )
        return 1

    started = datetime.now()
    count = 0
    errors = 0
    latencies: list[int] = []

    def _summary_and_exit(*_: Any) -> None:
        elapsed = datetime.now() - started
        p95 = "—"
        if latencies:
            s = sorted(latencies)
            p95 = f"{s[min(len(s) - 1, int(len(s) * 0.95))]}ms"
        # strip microseconds for display
        elapsed_str = str(elapsed).split(".")[0]
        print(
            f"\nWatched {count} calls in {elapsed_str} · "
            f"{errors} error{'s' if errors != 1 else ''} · p95 latency {p95}"
        )
        sys.exit(0)

    signal.signal(signal.SIGINT, _summary_and_exit)
    signal.signal(signal.SIGTERM, _summary_and_exit)

    header = f"{'time':<8}  {'status':<8}  {'tool':<24}  {'latency':<10}  {'user':<8}  {'ip':<16}  error"
    print(_colorize(header, DIM, color))
    print(_colorize("-" * len(header), DIM, color))

    last_id = 0
    try:
        initial = fetch_initial(args)
    except Exception as e:
        print(f"ERROR: initial query failed: {e}", file=sys.stderr)
        return 1

    for row in initial:
        print(fmt_row(row, color))
        last_id = max(last_id, row["id"])
        count += 1
        if row.get("status_code") and row["status_code"] >= 400:
            errors += 1
        elif row.get("error_class"):
            errors += 1
        if row.get("latency_ms") is not None:
            latencies.append(row["latency_ms"])

    while True:
        time.sleep(args.interval)
        try:
            rows = fetch_since_id(last_id, args)
        except Exception as e:
            print(_colorize(f"[poll error] {e}", RED, color), file=sys.stderr)
            continue
        for row in rows:
            print(fmt_row(row, color))
            last_id = max(last_id, row["id"])
            count += 1
            if row.get("status_code") and row["status_code"] >= 400:
                errors += 1
            elif row.get("error_class"):
                errors += 1
            if row.get("latency_ms") is not None:
                latencies.append(row["latency_ms"])


if __name__ == "__main__":
    sys.exit(main())
