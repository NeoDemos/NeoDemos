#!/usr/bin/env python3
"""One-shot: seed .coordination/events.jsonl from dependencies.yaml initial_status.

Idempotent: if events.jsonl already contains any `claimed`/`completed`/etc
events, refuses to run without --force so manual events are never clobbered.

For each workstream, emits the minimum event sequence to produce its
initial_status:

    not_started → (no events; absence of events = not_started)
    in_progress → claimed
    done        → claimed + completed  (completed IS the QA-passed verdict)
    paused      → claimed + paused
    blocked     → blocked (lists blockers from depends_on)
    deferred    → note (status=deferred)

Note: 'review' is no longer a distinct state. A WS where the agent finished
but Dennis hasn't run /ws-complete yet is simply still in_progress — the
handoff's Outcome section signals agent-done; Dennis's /ws-complete signals
human-approved-done.

Timestamps: `shipped` field if present in dependencies.yaml, else today.
Agent: "seed".
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COORD_DIR = REPO_ROOT / ".coordination"
EVENTS_LOG = COORD_DIR / "events.jsonl"
DEPENDENCIES = COORD_DIR / "dependencies.yaml"

STATE_CHANGING_EVENTS = {
    "claimed", "completed", "qa_passed", "qa_rejected",
    "blocked", "unblocked", "paused", "resumed", "released",
}


def existing_state_events() -> int:
    if not EVENTS_LOG.exists():
        return 0
    count = 0
    with EVENTS_LOG.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("agent") == "seed":
                continue
            if ev.get("event") in STATE_CHANGING_EVENTS:
                count += 1
    return count


def iso_date(s: str | None) -> str:
    if s:
        try:
            return datetime.fromisoformat(str(s)).replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def events_for(ws_id: str, spec: dict) -> list[dict]:
    status = spec.get("initial_status", "not_started")
    shipped_ts = iso_date(spec.get("shipped"))
    today_ts = iso_date(None)
    notes = spec.get("notes", "")

    def base(event: str, ts: str) -> dict:
        d = {"ts": ts, "agent": "seed", "event": event, "ws": ws_id}
        if notes:
            d["detail"] = notes
        return d

    if status == "not_started":
        return []
    if status == "in_progress":
        return [base("claimed", today_ts)]
    if status == "review":
        # Legacy status: treat as still in_progress — Dennis will /ws-complete
        # when he signs off.
        return [base("claimed", today_ts)]
    if status == "done":
        return [base("claimed", shipped_ts), base("completed", shipped_ts)]
    if status == "paused":
        return [base("claimed", today_ts), base("paused", today_ts)]
    if status == "blocked":
        ev = base("blocked", today_ts)
        ev["blocker"] = list(spec.get("depends_on", []))
        return [ev]
    if status == "deferred":
        ev = base("note", today_ts)
        ev["detail"] = f"status=deferred. {notes}".strip(". ")
        return [ev]
    print(f"WARN: unknown initial_status '{status}' for {ws_id}", file=sys.stderr)
    return []


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--force", action="store_true",
                   help="Overwrite existing seed events. Will NOT clobber non-seed events unless --force-all.")
    p.add_argument("--force-all", action="store_true",
                   help="Truncate events.jsonl entirely before seeding. Destructive.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print events without writing.")
    args = p.parse_args()

    non_seed_count = existing_state_events()
    if non_seed_count > 0 and not args.force_all:
        print(f"ABORT: events.jsonl already contains {non_seed_count} "
              f"non-seed state events. Use --force-all to wipe and reseed "
              f"(destructive).", file=sys.stderr)
        return 1

    with DEPENDENCIES.open() as f:
        deps = yaml.safe_load(f)

    all_events: list[dict] = []
    for ws_id in deps:
        all_events.extend(events_for(ws_id, deps[ws_id]))

    all_events.sort(key=lambda e: (e["ts"], e.get("ws", ""), e["event"]))

    if args.dry_run:
        for ev in all_events:
            print(json.dumps(ev, ensure_ascii=False, sort_keys=True))
        print(f"\n[dry-run] would write {len(all_events)} events to {EVENTS_LOG}",
              file=sys.stderr)
        return 0

    COORD_DIR.mkdir(exist_ok=True)
    if args.force_all and EVENTS_LOG.exists():
        EVENTS_LOG.unlink()

    fd = os.open(EVENTS_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        for ev in all_events:
            os.write(fd, (json.dumps(ev, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(fd)

    print(f"Seeded {len(all_events)} events into {EVENTS_LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
