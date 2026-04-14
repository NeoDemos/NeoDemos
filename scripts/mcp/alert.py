#!/usr/bin/env python3
"""One-shot MCP health anomaly detector — press-moment safety net.

Runs every 5 minutes under launchd, queries mcp_audit_log through the local
SSH tunnel, and fires macOS-native notifications (osascript) on anomalies.
No external telemetry — local-only by Dennis's explicit request.

Rules (evaluated in order, each with its own cooldown in state file):
  1. error_rate    — >10% errors in last 5min, with >=5 total calls
  2. latency       — p95 latency_ms > 10000 in last 5min
  3. traffic_spike — last 5min calls >= 3x trailing 60min average
  4. silent        — 0 calls in last 15min when preceding 60min had >=10 calls

Usage:
    python scripts/mcp/alert.py                   # normal tick
    python scripts/mcp/alert.py --dry-run         # show what would fire
    python scripts/mcp/alert.py --test "hello"    # test notification path
    python scripts/mcp/alert.py --reset-cooldowns # clear state file

Exit codes:
    0 = ran cleanly (fired or not)
    1 = DB/tunnel unreachable (launchd will retry next tick)
    2 = unexpected error

launchd plist — copy to ~/Library/LaunchAgents/com.neodemos.mcp-alert.plist
then `launchctl load ~/Library/LaunchAgents/com.neodemos.mcp-alert.plist`:

    <?xml version="1.0" encoding="UTF-8"?>
    <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
        "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
    <plist version="1.0">
    <dict>
      <key>Label</key>
      <string>com.neodemos.mcp-alert</string>
      <key>ProgramArguments</key>
      <array>
        <string>/bin/bash</string>
        <string>-lc</string>
        <string>cd "/Users/dennistak/Documents/Final Frontier/NeoDemos" &amp;&amp; \
          source .env 2&gt;/dev/null; \
          /usr/bin/python3 scripts/mcp/alert.py</string>
      </array>
      <key>StartInterval</key>
      <integer>300</integer>
      <key>RunAtLoad</key>
      <true/>
      <key>StandardOutPath</key>
      <string>/tmp/neodemos-mcp-alert.out</string>
      <key>StandardErrorPath</key>
      <string>/tmp/neodemos-mcp-alert.err</string>
      <key>WorkingDirectory</key>
      <string>/Users/dennistak/Documents/Final Frontier/NeoDemos</string>
    </dict>
    </plist>
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make repo root importable when run directly.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

COORD_DIR = _REPO_ROOT / ".coordination"
STATE_PATH = COORD_DIR / ".mcp_alert_state.json"
EVENTS_LOG = COORD_DIR / "events.jsonl"

COOLDOWN_SECONDS = 30 * 60  # 30 minutes per rule
RULES = ("error_rate", "latency", "traffic_spike", "silent")


# ---------- macOS notification ----------

def notify(title: str, message: str) -> None:
    """Fire a native macOS notification via osascript."""
    safe_title = title.replace('"', '\\"')
    safe_msg = message.replace('"', '\\"')
    script = (
        f'display notification "{safe_msg}" with title "{safe_title}" '
        f'sound name "Ping"'
    )
    subprocess.run(["osascript", "-e", script], check=False)


# ---------- state (cooldowns) ----------

def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"last_fired": {r: None for r in RULES}}
    try:
        data = json.loads(STATE_PATH.read_text())
        data.setdefault("last_fired", {})
        for r in RULES:
            data["last_fired"].setdefault(r, None)
        return data
    except Exception:
        return {"last_fired": {r: None for r in RULES}}


def save_state(state: dict[str, Any]) -> None:
    COORD_DIR.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True))


def is_cooling_down(state: dict[str, Any], rule: str, now: datetime) -> bool:
    last = state["last_fired"].get(rule)
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (now - last_dt).total_seconds() < COOLDOWN_SECONDS


# ---------- events.jsonl audit trail ----------

def append_alert_event(rule: str, detail: str, metrics: dict[str, Any]) -> None:
    COORD_DIR.mkdir(exist_ok=True)
    event = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "agent": "mcp_alert",
        "event": "alert",
        "rule": rule,
        "detail": detail,
        "metrics": metrics,
    }
    line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
    fd = os.open(EVENTS_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


# ---------- DB queries ----------

def fetch_metrics(cur) -> dict[str, Any]:
    """Return a dict of all the numbers we need in two queries."""
    # 5-minute window
    cur.execute(
        """
        SELECT
            COUNT(*)::int AS total,
            COUNT(*) FILTER (
                WHERE status_code >= 400 OR error_class IS NOT NULL
            )::int AS errors,
            COALESCE(
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms), 0
            )::float AS p95_latency
        FROM mcp_audit_log
        WHERE ts >= NOW() - INTERVAL '5 minutes';
        """
    )
    row = cur.fetchone()
    total_5m, errors_5m, p95 = int(row[0]), int(row[1]), float(row[2] or 0)

    # 15-minute window (for silent rule)
    cur.execute(
        """
        SELECT COUNT(*)::int
        FROM mcp_audit_log
        WHERE ts >= NOW() - INTERVAL '15 minutes';
        """
    )
    total_15m = int(cur.fetchone()[0])

    # 60-minute trailing window EXCLUDING the last 5 minutes (baseline for
    # traffic_spike and context for silent rule).
    cur.execute(
        """
        SELECT COUNT(*)::int
        FROM mcp_audit_log
        WHERE ts >= NOW() - INTERVAL '65 minutes'
          AND ts <  NOW() - INTERVAL '5 minutes';
        """
    )
    total_60m_excl = int(cur.fetchone()[0])
    baseline_5m = total_60m_excl / 12.0  # normalize 60min to a 5min average

    # Preceding 60m (excluding last 15m) for silent rule's "was averaging" check.
    cur.execute(
        """
        SELECT COUNT(*)::int
        FROM mcp_audit_log
        WHERE ts >= NOW() - INTERVAL '75 minutes'
          AND ts <  NOW() - INTERVAL '15 minutes';
        """
    )
    total_60m_before_silent = int(cur.fetchone()[0])

    return {
        "total_5m": total_5m,
        "errors_5m": errors_5m,
        "p95_latency_ms": p95,
        "total_15m": total_15m,
        "total_60m_excl": total_60m_excl,
        "baseline_5m": baseline_5m,
        "total_60m_before_silent": total_60m_before_silent,
    }


# ---------- rule evaluation ----------

def evaluate(m: dict[str, Any]) -> list[dict[str, Any]]:
    """Return list of {rule, title, message, detail, metrics} for rules that fire."""
    fires: list[dict[str, Any]] = []

    # 1. error rate spike
    if m["total_5m"] >= 5:
        rate = m["errors_5m"] / m["total_5m"]
        if rate > 0.10:
            pct = int(round(rate * 100))
            detail = f"{pct}% ({m['errors_5m']}/{m['total_5m']}) last 5min"
            fires.append({
                "rule": "error_rate",
                "title": "NeoDemos MCP",
                "message": f"MCP error rate {detail}",
                "detail": detail,
                "metrics": {
                    "error_rate_pct": pct,
                    "errors": m["errors_5m"],
                    "total": m["total_5m"],
                },
            })

    # 2. latency spike (p95)
    if m["p95_latency_ms"] > 10000:
        p95_s = m["p95_latency_ms"] / 1000.0
        detail = f"p95 {p95_s:.1f}s last 5min"
        fires.append({
            "rule": "latency",
            "title": "NeoDemos MCP",
            "message": f"MCP p95 latency {p95_s:.1f}s last 5min \u00b7 check reranker/Jina",
            "detail": detail,
            "metrics": {"p95_latency_ms": round(m["p95_latency_ms"], 1)},
        })

    # 3. traffic spike (information)
    if m["baseline_5m"] > 0 and m["total_5m"] >= 3 * m["baseline_5m"] and m["total_5m"] >= 5:
        ratio = m["total_5m"] / m["baseline_5m"] if m["baseline_5m"] else 0.0
        detail = f"{m['total_5m']} calls ({ratio:.1f}x baseline)"
        fires.append({
            "rule": "traffic_spike",
            "title": "NeoDemos MCP",
            "message": f"MCP traffic spike: {m['total_5m']} calls ({ratio:.1f}x baseline) \u2014 press moment?",
            "detail": detail,
            "metrics": {
                "calls_5m": m["total_5m"],
                "baseline_5m": round(m["baseline_5m"], 2),
                "ratio": round(ratio, 2),
            },
        })

    # 4. silent
    if m["total_15m"] == 0 and m["total_60m_before_silent"] >= 10:
        hourly = m["total_60m_before_silent"]
        detail = f"0 calls in 15min (was averaging {hourly}/hr)"
        fires.append({
            "rule": "silent",
            "title": "NeoDemos MCP",
            "message": f"MCP silent \u2014 {detail}",
            "detail": detail,
            "metrics": {
                "calls_15m": 0,
                "prior_60m_calls": hourly,
            },
        })

    return fires


# ---------- main ----------

def run_once(dry_run: bool = False) -> int:
    try:
        from services.db_pool import get_connection  # type: ignore
    except Exception as e:
        print(f"ERROR: cannot import services.db_pool ({e}).", file=sys.stderr)
        return 2

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                metrics = fetch_metrics(cur)
    except Exception as e:
        # Tunnel/DB down — silent exit 1 (don't spam notifications).
        print(f"db unreachable: {e}", file=sys.stderr)
        return 1

    state = load_state()
    now = datetime.now(timezone.utc)
    fires = evaluate(metrics)

    if dry_run:
        print(f"metrics: {json.dumps(metrics, default=float)}")
        if not fires:
            print("no rules firing")
        for f in fires:
            cooling = is_cooling_down(state, f["rule"], now)
            print(f"would fire: [{f['rule']}] {f['message']}"
                  + ("  (SUPPRESSED: cooldown)" if cooling else ""))
        return 0

    any_fired = False
    for f in fires:
        if is_cooling_down(state, f["rule"], now):
            continue
        notify(f["title"], f["message"])
        append_alert_event(f["rule"], f["detail"], f["metrics"])
        state["last_fired"][f["rule"]] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        any_fired = True
        print(f"fired: [{f['rule']}] {f['message']}")

    if any_fired:
        save_state(state)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="MCP health anomaly detector.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print what would fire; send no notifications, don't write state.")
    p.add_argument("--test", metavar="MSG", nargs="?", const="test from alert.py",
                   help="Emit a test notification and exit.")
    p.add_argument("--reset-cooldowns", action="store_true",
                   help="Clear the state file and exit.")
    args = p.parse_args()

    if args.test is not None:
        notify("NeoDemos MCP (test)", args.test)
        return 0
    if args.reset_cooldowns:
        if STATE_PATH.exists():
            STATE_PATH.unlink()
        print("cooldown state cleared")
        return 0

    try:
        return run_once(dry_run=args.dry_run)
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
