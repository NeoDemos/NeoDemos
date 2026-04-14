#!/usr/bin/env python3
"""Append one event to .coordination/events.jsonl.

Writes with O_APPEND so concurrent invocations from parallel agents don't
overwrite each other — the OS serializes the appends.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COORD_DIR = REPO_ROOT / ".coordination"
EVENTS_LOG = COORD_DIR / "events.jsonl"
DEPENDENCIES = COORD_DIR / "dependencies.yaml"

VALID_EVENTS = {
    "claimed", "started", "completed", "blocked", "unblocked",
    "note", "paused", "resumed", "qa_passed", "qa_rejected",
    "escalation", "released",
}


def load_workstreams() -> set[str]:
    with DEPENDENCIES.open() as f:
        return set(yaml.safe_load(f).keys())


def append_event(event: dict) -> None:
    COORD_DIR.mkdir(exist_ok=True)
    line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
    fd = os.open(EVENTS_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def main() -> int:
    p = argparse.ArgumentParser(description="Append an event to events.jsonl")
    p.add_argument("--event", required=True, choices=sorted(VALID_EVENTS))
    p.add_argument("--ws", help="Workstream ID (e.g. WS7). Omit for system events.")
    p.add_argument("--agent", default=os.environ.get("COORD_AGENT", "unknown"))
    p.add_argument("--detail", default="")
    p.add_argument("--commit", default="")
    p.add_argument("--blocker", action="append", default=[],
                   help="For 'blocked' events: the WS(es) blocking this one. Repeatable.")
    p.add_argument("--reason", default="", help="For qa_rejected or escalation.")
    p.add_argument("--skip-validation", action="store_true",
                   help="Don't validate the WS exists in dependencies.yaml.")
    args = p.parse_args()

    if args.ws and not args.skip_validation:
        known = load_workstreams()
        if args.ws not in known:
            print(f"ERROR: unknown workstream '{args.ws}'. Known: {sorted(known)}",
                  file=sys.stderr)
            return 2

    event = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "agent": args.agent,
        "event": args.event,
    }
    if args.ws:
        event["ws"] = args.ws
    if args.detail:
        event["detail"] = args.detail
    if args.commit:
        event["commit"] = args.commit
    if args.blocker:
        event["blocker"] = args.blocker
    if args.reason:
        event["reason"] = args.reason

    append_event(event)
    print(json.dumps(event, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
