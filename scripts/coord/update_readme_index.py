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


# ──────────────────────────────────────────────────────────────────────────────
# Fix 3 — Parallelism map (Mermaid, auto-rendered between PARALLELISM-AUTO markers)
# ──────────────────────────────────────────────────────────────────────────────

PARALLELISM_SENTINEL_START = "<!-- PARALLELISM-AUTO-START -->"
PARALLELISM_SENTINEL_END   = "<!-- PARALLELISM-AUTO-END -->"

# Maps Mermaid node_id → WS ID for status lookup.
# WS1 appears as two phase nodes but shares one status in events.jsonl.
MERMAID_NODE_WS: list[tuple[str, str]] = [
    ("WS7",   "WS7"),
    ("WS11",  "WS11"),
    ("WS1A",  "WS1"),
    ("WS1B",  "WS1"),
    ("WS3",   "WS3"),
    ("WS2",   "WS2"),
    ("WS4",   "WS4"),
    ("WS6",   "WS6"),
    ("WS5a",  "WS5a"),
    ("WS8ae", "WS8"),
    ("WS9",   "WS9"),
    ("WS8f",  "WS8f"),
    ("WS14",  "WS14"),
]

STATUS_TO_MERMAID_CLASS: dict[str, str] = {
    "done":        "done",
    "in_progress": "running",
    "blocked":     "blocked",
    "available":   "available",
    "paused":      "paused",
    "escalated":   "escalated",
    "deferred":    "deferred",
    "not_started": "notstarted",
}


def get_last_event_type(ws_id: str, events: list[dict]) -> str | None:
    """Return the event type of the most recent event for a given WS."""
    ws_events = [e for e in events if e.get("ws") == ws_id]
    return ws_events[-1].get("event") if ws_events else None


def render_mermaid_map(states: dict, events: list[dict]) -> str:
    """Generate a Mermaid flowchart block with node styles from current WS statuses."""
    class_groups: dict[str, list[str]] = {}
    for node_id, ws_id in MERMAID_NODE_WS:
        last_event = get_last_event_type(ws_id, events)
        if last_event in ("qa_rejected", "rejected"):
            cls = "rejected"
        else:
            status = states.get(ws_id, {}).get("status", "not_started")
            cls = STATUS_TO_MERMAID_CLASS.get(status, "notstarted")
        class_groups.setdefault(cls, []).append(node_id)

    class_lines = "\n".join(
        f"  class {','.join(nodes)} {cls}"
        for cls, nodes in sorted(class_groups.items())
    )

    return (
        "```mermaid\n"
        "flowchart TD\n"
        "\n"
        '  subgraph A["TRACK A — Corpus quality (critical path)"]\n'
        '    WS7["WS7 · OCR recovery"]\n'
        '    WS11["WS11 · Corpus gaps"]\n'
        '    WS1A["WS1 Phase A · Flair NER + Gemini"]\n'
        '    WS1B["WS1 Phase B · graph svc + MCP tools"]\n'
        '    WS3["WS3 · Journey timelines"]\n'
        "    WS7  --> WS1A\n"
        "    WS11 --> WS1A\n"
        "    WS1A --> WS1B\n"
        "    WS1B --> WS3\n"
        "  end\n"
        "\n"
        '  subgraph B["TRACK B — Independent workstreams"]\n'
        '    WS2["WS2 · Financial analysis"]\n'
        '    WS4["WS4 · MCP discipline"]\n'
        '    WS6["WS6 · Summarization"]\n'
        '    WS5a["WS5a · Nightly pipeline"]\n'
        "  end\n"
        "\n"
        '  subgraph C["TRACK C — Public launch"]\n'
        '    WS8ae["WS8a-e · Design system"]\n'
        '    WS9["WS9 · Web intelligence"]\n'
        '    WS8f["WS8f · Admin CMS"]\n'
        '    WS14["WS14 · Calendar quality"]\n'
        "    WS8ae --> WS8f\n"
        "    WS8f  --> WS14\n"
        "  end\n"
        "\n"
        "  classDef done      fill:#2da44e,color:#fff,stroke:#2da44e\n"
        "  classDef running   fill:#d29922,color:#fff,stroke:#d29922\n"
        "  classDef blocked   fill:#6e7781,color:#fff,stroke:#6e7781\n"
        "  classDef available fill:#0969da,color:#fff,stroke:#0969da\n"
        "  classDef rejected  fill:#cf222e,color:#fff,stroke:#cf222e\n"
        "  classDef paused    fill:#8250df,color:#fff,stroke:#8250df\n"
        "  classDef deferred  fill:#656d76,color:#fff,stroke:#656d76\n"
        "  classDef notstarted fill:#f6f8fa,color:#333,stroke:#d0d7de\n"
        "\n"
        + class_lines + "\n"
        "```\n"
    )


def update_parallelism_map(text: str, states: dict, events: list[dict]) -> str:
    """Replace the PARALLELISM-AUTO block with a freshly rendered Mermaid diagram."""
    if PARALLELISM_SENTINEL_START not in text or PARALLELISM_SENTINEL_END not in text:
        return text  # Markers absent — skip silently.
    new_block = render_mermaid_map(states, events)
    pattern = re.compile(
        re.escape(PARALLELISM_SENTINEL_START) + r".*?" + re.escape(PARALLELISM_SENTINEL_END),
        re.DOTALL,
    )
    wrapped = f"{PARALLELISM_SENTINEL_START}\n{new_block}{PARALLELISM_SENTINEL_END}"
    return pattern.sub(wrapped, text, count=1)


# ──────────────────────────────────────────────────────────────────────────────
# Fix 4 — Eval gate (WS-derived status cells, auto-updated via <!-- EVAL:WS_ID -->)
# ──────────────────────────────────────────────────────────────────────────────

_EVAL_MARKER_RE = re.compile(r"<!-- EVAL:(\w+) -->")


def update_eval_gate(text: str, states: dict) -> str:
    """Auto-tick eval gate cells marked with <!-- EVAL:WS_ID -->.

    Rule: if the WS is done and the cell content does NOT already start with
    '✅', replace the content with '✅ done YYYY-MM-DD'. Cells that already
    start with '✅' are left untouched (preserves manually added measurement
    detail). Rows whose WS is not done are never modified.
    """
    lines = text.split("\n")
    result = []
    for line in lines:
        m = _EVAL_MARKER_RE.search(line)
        if m:
            ws_id = m.group(1)
            state = states.get(ws_id, {})
            if state.get("status") == "done":
                completed_ts = (state.get("completed_ts") or "")[:10] or "done"
                cell_pat = re.compile(
                    r"(\|\s*)([^|]*?)(<!-- EVAL:" + re.escape(ws_id) + r" -->)(\s*\|)"
                )

                def _replace(cm: re.Match, _ts: str = completed_ts) -> str:
                    content = cm.group(2).strip()
                    if content.startswith("\u2705"):  # ✅
                        return cm.group(0)  # Already ticked — preserve detail.
                    return f"{cm.group(1)}\u2705 done {_ts} {cm.group(3)}{cm.group(4)}"

                line = cell_pat.sub(_replace, line)
        result.append(line)
    return "\n".join(result)


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
    events = load_events()
    for ev in events:
        replay(states, ev)
    recompute_deps(states)

    rows, counts, warnings = build_rows(states, deps, verbose=args.verbose)
    for w in warnings:
        print(w, file=sys.stderr)

    table_md = render_table(rows)
    original = args.readme.read_text()
    new_text, action = splice_sentinels(original, table_md)
    new_text = update_parallelism_map(new_text, states, events)
    new_text = update_eval_gate(new_text, states)

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
