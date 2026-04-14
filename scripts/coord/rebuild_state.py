#!/usr/bin/env python3
"""Reads dependencies.yaml + events.jsonl, writes .coordination/state.md.

READ-ONLY: never writes to events.jsonl. Only output is state.md.

Replay semantics for each workstream:
    claimed      → status=in_progress, claimed_by=<agent>, claimed_ts=<ts>
    completed    → status=review, completed_ts=<ts>
    qa_passed    → status=done
    qa_rejected  → status=in_progress (back to worker)
    blocked      → status=blocked
    unblocked    → status=available (if no claim) or in_progress (if claimed)
    paused       → status=paused
    resumed      → status=in_progress
    released     → status=not_started, claimed_by=None
    escalation   → status=escalated
    note         → no state change

After replay, dependency-derived blocking is recomputed: any WS whose
`depends_on` includes a non-done WS is surfaced as blocked in the dashboard,
regardless of its last event status. This catches the "all blockers shipped,
WS is now available" case without writing anything.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COORD_DIR = REPO_ROOT / ".coordination"
EVENTS_LOG = COORD_DIR / "events.jsonl"
DEPENDENCIES = COORD_DIR / "dependencies.yaml"
STATE_MD = COORD_DIR / "state.md"


def load_events() -> list[dict]:
    if not EVENTS_LOG.exists():
        return []
    events = []
    with EVENTS_LOG.open() as f:
        for i, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"WARN: {EVENTS_LOG}:{i}: {e}", file=sys.stderr)
    events.sort(key=lambda e: e.get("ts", ""))
    return events


def initial_state(ws_id: str, spec: dict) -> dict:
    return {
        "ws": ws_id,
        "title": spec.get("title", ""),
        "priority": spec.get("priority", 99),
        "depends_on": list(spec.get("depends_on", [])),
        "status": "not_started",
        "claimed_by": None,
        "claimed_ts": None,
        "completed_ts": None,
        "last_event_ts": None,
        "last_detail": spec.get("notes", ""),
        "blocker_ws": [],
    }


def replay(states: dict, event: dict) -> None:
    ws_id = event.get("ws")
    if not ws_id or ws_id not in states:
        return
    s = states[ws_id]
    typ = event.get("event")
    s["last_event_ts"] = event.get("ts")
    if event.get("detail"):
        s["last_detail"] = event["detail"]
    if typ == "claimed":
        s["status"] = "in_progress"
        s["claimed_by"] = event.get("agent") or s["claimed_by"]
        s["claimed_ts"] = event.get("ts")
    elif typ == "completed":
        s["status"] = "done"
        s["completed_ts"] = event.get("ts")
    elif typ == "qa_passed":
        # Legacy event kept for backwards compatibility with older logs.
        s["status"] = "done"
    elif typ in ("qa_rejected", "rejected"):
        s["status"] = "in_progress"
    elif typ == "blocked":
        s["status"] = "blocked"
        if event.get("blocker"):
            s["blocker_ws"] = list(event["blocker"])
    elif typ == "unblocked":
        s["status"] = "in_progress" if s["claimed_by"] else "available"
    elif typ == "paused":
        s["status"] = "paused"
    elif typ == "resumed":
        s["status"] = "in_progress"
    elif typ == "released":
        s["status"] = "not_started"
        s["claimed_by"] = None
    elif typ == "escalation":
        s["status"] = "escalated"
    # note: no state change


def recompute_deps(states: dict) -> None:
    """Layer dependency-derived blocking on top of event-driven status."""
    for ws_id, s in states.items():
        if s["status"] == "done":
            continue
        unsatisfied = [d for d in s["depends_on"] if d in states and states[d]["status"] != "done"]
        if unsatisfied:
            # Keep explicit blocked status if set, else mark as derived blocked
            if s["status"] in ("not_started", "available"):
                s["status"] = "blocked"
                s["blocker_ws"] = unsatisfied
            elif s["status"] == "blocked":
                s["blocker_ws"] = unsatisfied
        else:
            # All deps satisfied
            if s["status"] == "blocked":
                s["status"] = "available"
                s["blocker_ws"] = []
            elif s["status"] == "not_started":
                s["status"] = "available"


def render_table(rows: list[list[str]], headers: list[str]) -> str:
    if not rows:
        return "| " + " | ".join(headers) + " |\n|" + "|".join(["---"] * len(headers)) + "|\n| *(none)* |" + " |" * (len(headers) - 1) + "\n"
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))
    out = ["| " + " | ".join(h.ljust(w) for h, w in zip(headers, widths)) + " |"]
    out.append("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    for row in rows:
        out.append("| " + " | ".join(str(c).ljust(w) for c, w in zip(row, widths)) + " |")
    return "\n".join(out) + "\n"


def format_ts(ts: str | None) -> str:
    if not ts:
        return "—"
    try:
        dt = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return ts


def section(title: str, rows: list[list[str]], headers: list[str]) -> str:
    return f"## {title}\n\n{render_table(rows, headers)}\n"


def render_state_md(states: dict, events: list[dict]) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    header = f"# NeoDemos Project State\n\n> Auto-generated from `.coordination/events.jsonl` — do not edit manually.\n> Last rebuilt: {now}\n\n"

    sorted_ws = sorted(states.values(), key=lambda s: (s["priority"], s["ws"]))

    active = [
        [s["ws"], s["title"], s["claimed_by"] or "—", format_ts(s["claimed_ts"]), s["last_detail"][:60]]
        for s in sorted_ws if s["status"] == "in_progress"
    ]
    blocked = [
        [s["ws"], s["title"], ", ".join(s["blocker_ws"]) or "—", "when all blockers complete"]
        for s in sorted_ws if s["status"] == "blocked"
    ]
    available = [
        [s["ws"], s["title"], ", ".join(s["depends_on"]) or "none"]
        for s in sorted_ws if s["status"] == "available"
    ]
    paused = [
        [s["ws"], s["title"], s["last_detail"][:80]]
        for s in sorted_ws if s["status"] == "paused"
    ]
    escalated = [
        [s["ws"], s["title"], s["last_detail"][:80]]
        for s in sorted_ws if s["status"] == "escalated"
    ]

    cutoff = (datetime.now(timezone.utc) - timedelta(days=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    done = sorted(
        [s for s in states.values() if s["status"] == "done"],
        key=lambda s: s.get("completed_ts") or "",
        reverse=True,
    )
    done_rows = [
        [s["ws"], s["title"], format_ts(s["completed_ts"]), s["claimed_by"] or "—"]
        for s in done
        if (s.get("completed_ts") or "0") >= cutoff
    ]

    parts = [
        header,
        section("Active Now", active, ["WS", "Title", "Claimed by", "Since", "Detail"]),
        section("Blocked", blocked, ["WS", "Title", "Waiting on", "Unblocks when"]),
        section("Available (unclaimed, no blockers)", available, ["WS", "Title", "Dependencies"]),
        section("Paused", paused, ["WS", "Title", "Detail"]),
        section("Escalated", escalated, ["WS", "Title", "Detail"]),
        section("Recently Completed (last 14 days)", done_rows, ["WS", "Title", "Completed", "Worker"]),
    ]

    parts.append("## Recent events (last 15)\n\n")
    parts.append("```jsonl\n")
    for ev in events[-15:]:
        parts.append(json.dumps(ev, ensure_ascii=False, sort_keys=True) + "\n")
    parts.append("```\n")

    return "".join(parts)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="Print to stdout, don't write state.md.")
    args = p.parse_args()

    with DEPENDENCIES.open() as f:
        deps = yaml.safe_load(f)

    states = {ws: initial_state(ws, spec) for ws, spec in deps.items()}
    events = load_events()
    for ev in events:
        replay(states, ev)
    recompute_deps(states)

    out = render_state_md(states, events)
    if args.dry_run:
        sys.stdout.write(out)
    else:
        COORD_DIR.mkdir(exist_ok=True)
        STATE_MD.write_text(out)
        print(f"Wrote {STATE_MD} ({len(out)} bytes, {len(events)} events replayed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
