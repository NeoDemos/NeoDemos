#!/usr/bin/env python3
"""Auto-regenerate the workstream index table in docs/handoffs/README.md.

Reads .coordination/dependencies.yaml + .coordination/events.jsonl, replays
them through the same state machine as rebuild_state.py, and rewrites the
markdown table between <!-- STATE-AUTO-START --> / <!-- STATE-AUTO-END -->
sentinels in the README.

On first run: the script locates the existing workstream-index table under
the '## Workstream index' heading and wraps it with sentinels in place.
On subsequent runs: the block between sentinels is replaced wholesale.

Usage:
    python3 scripts/coord/update_readme_index.py                  # write
    python3 scripts/coord/update_readme_index.py --dry-run        # diff only
    python3 scripts/coord/update_readme_index.py --verbose        # per-WS log
    python3 scripts/coord/update_readme_index.py --readme PATH    # alt target

READ-ONLY re: events.jsonl and dependencies.yaml. Only output is README.md.
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from collections import Counter
from pathlib import Path

# Reuse state-machine helpers from rebuild_state.py to keep logic in one place.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rebuild_state import (  # type: ignore  # noqa: E402
    DEPENDENCIES,
    EVENTS_LOG,
    initial_state,
    load_events,
    recompute_deps,
    replay,
)

import yaml  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_README = REPO_ROOT / "docs" / "handoffs" / "README.md"
HANDOFFS_DIR = REPO_ROOT / "docs" / "handoffs"
DONE_DIR = HANDOFFS_DIR / "done"

SENTINEL_START = "<!-- STATE-AUTO-START -->"
SENTINEL_END = "<!-- STATE-AUTO-END -->"

# Status → bold label used in the Status column.
STATUS_LABELS = {
    "in_progress": "in progress",
    "not_started": "not started",
    "review": "review",
    "done": "done",
    "blocked": "blocked",
    "paused": "paused",
    "deferred": "deferred",
    "available": "available",
    "escalated": "escalated",
}


def ws_sort_key(ws_id: str) -> tuple:
    """Sort WS ids like WS1, WS2, WS2b, WS8f, WS10 sensibly."""
    m = re.match(r"WS(\d+)([a-z]*)$", ws_id)
    if not m:
        return (999, ws_id, "")
    return (int(m.group(1)), m.group(2), ws_id)


def find_handoff_file(ws_id: str) -> tuple[str, bool]:
    """Return (relative_path_from_handoffs_dir, exists).

    Looks in docs/handoffs/WS<N>_*.md then docs/handoffs/done/WS<N>_*.md.
    Relative path uses 'WS<N>_*.md' or 'done/WS<N>_*.md' form.
    """
    for p in sorted(HANDOFFS_DIR.glob(f"{ws_id}_*.md")):
        return (p.name, True)
    for p in sorted(DONE_DIR.glob(f"{ws_id}_*.md")):
        return (f"done/{p.name}", True)
    return (f"{ws_id}.md", False)


def truncate(s: str, n: int = 60) -> str:
    s = (s or "").strip().replace("\n", " ").replace("|", "\\|")
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


def status_cell(state: dict, spec: dict) -> str:
    status = state["status"]
    # Prefer dependency-specific status for blocked.
    label = STATUS_LABELS.get(status, status)
    detail = ""
    if status == "blocked":
        blockers = state.get("blocker_ws") or []
        if blockers:
            detail = "waiting on " + ", ".join(blockers)
    elif status == "done":
        shipped = spec.get("shipped")
        if shipped:
            detail = f"shipped {shipped}"
        elif state.get("completed_ts"):
            detail = f"shipped {state['completed_ts'][:10]}"
    else:
        detail = truncate(state.get("last_detail", ""))
    if detail:
        return f"**{label}** — {truncate(detail, 60)}"
    return f"**{label}**"


def build_rows(states: dict, deps: dict, verbose: bool = False) -> tuple[list[list[str]], Counter, list[str]]:
    rows: list[list[str]] = []
    counts: Counter = Counter()
    warnings: list[str] = []
    ordered = sorted(
        states.items(),
        key=lambda kv: (kv[1]["priority"], ws_sort_key(kv[0])),
    )
    for ws_id, s in ordered:
        spec = deps.get(ws_id, {})
        filename, exists = find_handoff_file(ws_id)
        if not exists:
            warnings.append(f"WARN: {ws_id} has no handoff file in docs/handoffs/ or docs/handoffs/done/")
        depends_on = s.get("depends_on") or []
        deps_cell = ", ".join(depends_on) if depends_on else "—"
        file_cell = f"[`{filename}`]({filename})"
        status_md = status_cell(s, spec)
        counts[s["status"]] += 1
        if verbose:
            print(f"  {ws_id:<6} priority={s['priority']:<4} status={s['status']:<12} file={filename}", file=sys.stderr)
        rows.append([ws_id, file_cell, s["title"], str(spec.get("priority", s["priority"])), status_md, deps_cell])
    return rows, counts, warnings


def render_table(rows: list[list[str]]) -> str:
    headers = ["#", "File", "Title", "Priority", "Status", "Depends on"]
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join(["---"] * len(headers)) + "|",
    ]
    for r in rows:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines) + "\n"


def find_existing_table_bounds(text: str) -> tuple[int, int] | None:
    """Locate the Workstream index table. Returns (start_line, end_line) inclusive."""
    lines = text.splitlines()
    # Find the header row matching '| # | File | Title |'.
    header_idx = None
    for i, line in enumerate(lines):
        if re.match(r"^\|\s*#\s*\|\s*File\s*\|\s*Title\s*\|", line):
            header_idx = i
            break
    if header_idx is None:
        return None
    # Walk forward: header, separator, then rows until first non-table line.
    end = header_idx
    for j in range(header_idx, len(lines)):
        if lines[j].lstrip().startswith("|"):
            end = j
        else:
            break
    return (header_idx, end)


def splice_sentinels(text: str, new_block: str) -> tuple[str, str]:
    """Insert or update the auto block. Returns (new_text, action)."""
    if SENTINEL_START in text and SENTINEL_END in text:
        pattern = re.compile(
            re.escape(SENTINEL_START) + r".*?" + re.escape(SENTINEL_END),
            re.DOTALL,
        )
        wrapped = f"{SENTINEL_START}\n{new_block}{SENTINEL_END}"
        return (pattern.sub(wrapped, text, count=1), "updated")

    bounds = find_existing_table_bounds(text)
    if bounds is None:
        raise SystemExit(
            "ERROR: README has neither STATE-AUTO sentinels nor a recognizable\n"
            "workstream-index table (expected header row '| # | File | Title |').\n"
            "Insert the sentinels manually around your table first:\n"
            f"    {SENTINEL_START}\n    ...table...\n    {SENTINEL_END}"
        )
    lines = text.splitlines(keepends=True)
    start, end = bounds
    prefix = "".join(lines[:start])
    suffix = "".join(lines[end + 1:])
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    wrapped = f"{SENTINEL_START}\n{new_block}{SENTINEL_END}\n"
    return (prefix + wrapped + suffix, "inserted")


def summary_line(path: Path, counts: Counter) -> str:
    total = sum(counts.values())
    parts = []
    for key in ("in_progress", "done", "blocked", "available", "paused", "not_started", "review", "deferred", "escalated"):
        if counts.get(key):
            parts.append(f"{counts[key]} {key.replace('_', ' ')}")
    rel = path.relative_to(REPO_ROOT) if path.is_relative_to(REPO_ROOT) else path
    return f"Wrote {rel}: {total} WSs in index ({', '.join(parts)})."


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="Print generated table + diff, don't write.")
    p.add_argument("--readme", type=Path, default=DEFAULT_README, help="Override README path.")
    p.add_argument("--verbose", action="store_true", help="Print per-WS derived state to stderr.")
    args = p.parse_args()

    # Preflight.
    missing = [p for p in (DEPENDENCIES, EVENTS_LOG, args.readme) if not p.exists()]
    if missing:
        for m in missing:
            print(f"ERROR: required file missing: {m}", file=sys.stderr)
        return 2

    with DEPENDENCIES.open() as f:
        deps = yaml.safe_load(f) or {}

    states = {ws: initial_state(ws, spec) for ws, spec in deps.items()}
    for ev in load_events():
        replay(states, ev)
    recompute_deps(states)

    rows, counts, warnings = build_rows(states, deps, verbose=args.verbose)
    for w in warnings:
        print(w, file=sys.stderr)

    table_md = render_table(rows)
    original = args.readme.read_text()
    new_text, action = splice_sentinels(original, table_md)

    if args.dry_run:
        sys.stdout.write("--- generated table ---\n")
        sys.stdout.write(table_md)
        sys.stdout.write("\n--- unified diff vs current README ---\n")
        diff = difflib.unified_diff(
            original.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=str(args.readme) + " (current)",
            tofile=str(args.readme) + " (proposed)",
            n=3,
        )
        sys.stdout.writelines(diff)
        sys.stdout.write(f"\n--- would {action}: {summary_line(args.readme, counts)}\n")
        return 0

    if new_text == original:
        print(f"No changes: {args.readme} already up to date ({sum(counts.values())} WSs).")
        return 0

    args.readme.write_text(new_text)
    print(summary_line(args.readme, counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
