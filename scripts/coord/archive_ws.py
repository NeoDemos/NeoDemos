#!/usr/bin/env python3
"""Archive one or more completed workstreams.

`git mv docs/handoffs/<WS>_*.md docs/handoffs/done/` for each WS,
then rewrite inbound markdown + docstring refs across the repo so nothing 404s.

Safety rails:
    - Refuses to archive a WS whose latest replayed status is not `done`
      (unless --force).
    - Stops if `git status` is not clean (unless --allow-dirty).
    - `--dry-run` prints every planned file move + every planned regex rewrite
      (with the before/after snippet) and exits without touching anything.
    - Only rewrites under files tracked by git; never touches binaries,
      `.git/`, `docs/archive/`, `docs/handoffs/done/`, or `.venv/`.

Usage:
    python scripts/coord/archive_ws.py --ws WS7 --dry-run
    python scripts/coord/archive_ws.py --ws WS7
    python scripts/coord/archive_ws.py --ws WS2 --ws WS4 --ws WS8 --ws WS9  # batch
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
HANDOFFS_DIR = REPO_ROOT / "docs" / "handoffs"
DONE_DIR = HANDOFFS_DIR / "done"
COORD_DIR = REPO_ROOT / ".coordination"
EVENTS_LOG = COORD_DIR / "events.jsonl"
DEPENDENCIES = COORD_DIR / "dependencies.yaml"

# Directories we never rewrite inside.
SKIP_PATH_PARTS = {".git", "node_modules", ".venv", "venv", "__pycache__"}
SKIP_PREFIXES = (
    "docs/archive/",
    "docs/handoffs/done/",
    ".claude/worktrees/",
)

# File extensions we scan for text-based ref rewrites.
TEXT_EXTS = {".md", ".py", ".sh", ".yaml", ".yml", ".json", ".html", ".rst", ".txt"}


def run(cmd: list[str], check: bool = True, cwd: Path = REPO_ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, cwd=cwd, capture_output=True, text=True)


def git_status_clean() -> bool:
    r = run(["git", "status", "--porcelain"], check=False)
    return r.returncode == 0 and not r.stdout.strip()


def tracked_files() -> list[Path]:
    r = run(["git", "ls-files"])
    paths = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if any(line.startswith(pref) for pref in SKIP_PREFIXES):
            continue
        p = REPO_ROOT / line
        if any(part in SKIP_PATH_PARTS for part in p.parts):
            continue
        if p.suffix.lower() not in TEXT_EXTS:
            continue
        paths.append(p)
    return paths


def load_ws_status() -> dict[str, str]:
    """Replay events.jsonl to determine current status of each WS."""
    states: dict[str, str] = {}
    if not DEPENDENCIES.exists():
        return states
    deps = yaml.safe_load(DEPENDENCIES.read_text())
    for ws in deps:
        states[ws] = "not_started"
    if not EVENTS_LOG.exists():
        return states
    events = []
    for line in EVENTS_LOG.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    events.sort(key=lambda e: e.get("ts", ""))
    transitions = {
        "claimed": "in_progress",
        "completed": "done",  # /ws-complete IS the QA-passed verdict
        "qa_passed": "done",  # legacy event, same outcome
        "qa_rejected": "in_progress",
        "rejected": "in_progress",
        "blocked": "blocked",
        "paused": "paused",
        "resumed": "in_progress",
        "released": "not_started",
        "escalation": "escalated",
    }
    for ev in events:
        ws = ev.get("ws")
        if ws and ws in states and ev.get("event") in transitions:
            states[ws] = transitions[ev["event"]]
    return states


def find_handoff_file(ws_id: str) -> Path | None:
    """Return the path of the handoff file for this WS, searching both
    active and already-archived locations."""
    for parent in (HANDOFFS_DIR, DONE_DIR):
        if not parent.exists():
            continue
        for p in parent.iterdir():
            if p.is_file() and p.suffix == ".md" and p.name.startswith(f"{ws_id}_"):
                return p
    return None


def rewrite_rules(moved_filenames: list[str]) -> list[tuple[re.Pattern, str, str]]:
    """Build (pattern, replacement, label) tuples.

    We generate rules per moved filename so that a batch archive of multiple
    workstreams rewrites all cross-refs in one pass.
    """
    rules: list[tuple[re.Pattern, str, str]] = []
    for fname in moved_filenames:
        esc = re.escape(fname)
        rules.append((
            re.compile(rf"(docs/handoffs/)(?!done/){esc}"),
            r"\1done/" + fname,
            f"docs/handoffs/{fname} → docs/handoffs/done/{fname}",
        ))
        rules.append((
            re.compile(rf"(\.\./handoffs/)(?!done/){esc}"),
            r"\1done/" + fname,
            f"../handoffs/{fname} → ../handoffs/done/{fname}",
        ))
        rules.append((
            re.compile(rf"(\./){esc}"),
            r"\1done/" + fname,
            f"./{fname} → ./done/{fname}",
        ))
        rules.append((
            re.compile(rf"(?<![\w/]){esc}(?![\w])"),
            "done/" + fname,
            f"{fname} → done/{fname}  (bare relative, scoped to docs/handoffs/ callers)",
        ))
    return rules


def rewrite_file(path: Path, rules: list[tuple[re.Pattern, str, str]], dry_run: bool) -> list[str]:
    """Apply rules to one file. The 'bare relative' rule (last per fname) is
    only applied when the file is under docs/handoffs/ (not docs/handoffs/done/),
    because a bare `WS7_X.md` elsewhere is ambiguous."""
    try:
        text = path.read_text()
    except (UnicodeDecodeError, PermissionError):
        return []
    original = text
    hits: list[str] = []
    rel = path.relative_to(REPO_ROOT).as_posix()
    in_handoffs = rel.startswith("docs/handoffs/") and not rel.startswith("docs/handoffs/done/")

    for pattern, replacement, label in rules:
        is_bare_rule = label.endswith("(bare relative, scoped to docs/handoffs/ callers)")
        if is_bare_rule and not in_handoffs:
            continue
        new_text, count = pattern.subn(replacement, text)
        if count:
            hits.append(f"  [{count}] {label}")
            text = new_text
    if text != original and not dry_run:
        path.write_text(text)
    return hits


def git_mv(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "mv", str(src.relative_to(REPO_ROOT)), str(dst.relative_to(REPO_ROOT))])


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ws", action="append", required=True,
                   help="Workstream ID to archive. Repeat for batch archival.")
    p.add_argument("--dry-run", action="store_true",
                   help="Show planned moves + rewrites without writing.")
    p.add_argument("--force", action="store_true",
                   help="Archive even if the WS is not in 'done' status.")
    p.add_argument("--allow-dirty", action="store_true",
                   help="Skip the clean-working-tree check.")
    args = p.parse_args()

    if not args.dry_run and not args.allow_dirty and not git_status_clean():
        print("ABORT: working tree not clean. Commit or stash, or pass --allow-dirty.",
              file=sys.stderr)
        return 1

    statuses = load_ws_status()
    moves: list[tuple[Path, Path]] = []
    moved_filenames: list[str] = []

    for ws_id in args.ws:
        src = find_handoff_file(ws_id)
        if src is None:
            print(f"ABORT: no handoff file found for {ws_id} "
                  f"(looked in {HANDOFFS_DIR} and {DONE_DIR}).", file=sys.stderr)
            return 2
        if src.parent == DONE_DIR:
            print(f"SKIP {ws_id}: already in done/ ({src.relative_to(REPO_ROOT)})")
            continue
        status = statuses.get(ws_id, "unknown")
        if status != "done" and not args.force:
            print(f"ABORT: {ws_id} status is '{status}', not 'done'. "
                  f"QA-pass it first, or pass --force.", file=sys.stderr)
            return 3
        dst = DONE_DIR / src.name
        moves.append((src, dst))
        moved_filenames.append(src.name)

    if not moves:
        print("Nothing to do.")
        return 0

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Planned moves:")
    for src, dst in moves:
        print(f"  git mv {src.relative_to(REPO_ROOT)} → {dst.relative_to(REPO_ROOT)}")

    rules = rewrite_rules(moved_filenames)
    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}Scanning tracked files for refs...")

    total_files_changed = 0
    total_rewrites = 0
    # Rewrite inside the files being moved too, so self-refs
    # (e.g. "docs/handoffs/WS9_X.md" inside WS9's own file) update to
    # the new done/ path before the git mv.
    for path in tracked_files():
        hits = rewrite_file(path, rules, dry_run=args.dry_run)
        if hits:
            total_files_changed += 1
            total_rewrites += sum(int(h.strip().split("]")[0].strip("[")) for h in hits)
            print(f"\n  {path.relative_to(REPO_ROOT)}")
            for h in hits:
                print(h)

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}"
          f"Summary: {len(moves)} file(s) moved, "
          f"{total_rewrites} ref(s) rewritten across {total_files_changed} file(s).")

    if args.dry_run:
        print("\n[DRY RUN] Nothing was written. Re-run without --dry-run to apply.")
        return 0

    print("\nExecuting git mv...")
    for src, dst in moves:
        git_mv(src, dst)

    print("\nDone. `git status` now shows the renames + rewrites.")
    print("Review with `git diff --stat`, then commit when ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
