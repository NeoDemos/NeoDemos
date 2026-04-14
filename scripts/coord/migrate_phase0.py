#!/usr/bin/env python3
"""Phase 0 migration — archive `brain/` + `memory-bank/`, move FEEDBACK_LOG.

Pre-staged migration script. Run AFTER WS1/WS6/WS7 background jobs finish
(they don't read these docs, but Dennis prefers to wait for a quiet tree).

Actions in order:
    1. git mv brain/FEEDBACK_LOG.md → .coordination/FEEDBACK_LOG.md
    2. Rewrite all inbound refs to brain/FEEDBACK_LOG.md across the repo.
    3. git mv remaining brain/*.md → docs/archive/brain/
    4. git mv memory-bank/* → docs/archive/memory-bank/
    5. Insert a "Sources of truth" + "Do NOT read" block into
       .agent/rules/communication_guidelines.md (idempotent — skipped if
       already present via marker string).

Idempotent: re-running after completion reports "nothing to do" and exits 0.

Usage:
    python scripts/coord/migrate_phase0.py --dry-run     # preview
    python scripts/coord/migrate_phase0.py               # execute (requires clean tree)
    python scripts/coord/migrate_phase0.py --allow-dirty # execute over dirty tree
    python scripts/coord/migrate_phase0.py --skip-rule-update
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BRAIN_DIR = REPO_ROOT / "brain"
MEMORY_BANK_DIR = REPO_ROOT / "memory-bank"
COORD_DIR = REPO_ROOT / ".coordination"
ARCHIVE_BRAIN_DIR = REPO_ROOT / "docs" / "archive" / "brain"
ARCHIVE_MEMORY_BANK_DIR = REPO_ROOT / "docs" / "archive" / "memory-bank"
RULES_FILE = REPO_ROOT / ".agent" / "rules" / "communication_guidelines.md"

# Rule-block marker — idempotency check for step 5.
RULE_MARKER = "## Sources of truth\n\nRead these, in this order:"
RULE_BLOCK = """
## Sources of truth

Read these, in this order:
1. `docs/handoffs/README.md` — workstream index, status, parallelism map
2. `docs/handoffs/WS*.md` — task definitions, acceptance criteria
3. `docs/WAY_OF_WORKING.md` — workflow conventions
4. `.coordination/state.md` — auto-generated live status
5. `.coordination/FEEDBACK_LOG.md` — live triage inbox
6. `.agent/rules/` — behavioral rules
7. `.claude/commands/` — skill + coordination commands

## Do NOT read

- `docs/archive/` — historical artifacts, intentionally frozen
- `docs/archive/memory-bank/` — superseded by handoffs + auto-memory
- `docs/archive/brain/` — stale working notes (except FEEDBACK_LOG, now at `.coordination/`)
"""

SKIP_PATH_PARTS = {".git", "node_modules", ".venv", "venv", "__pycache__"}
SKIP_PREFIXES = ("docs/archive/", ".claude/worktrees/")
TEXT_EXTS = {".md", ".py", ".sh", ".yaml", ".yml", ".json", ".html", ".rst", ".txt"}

REWRITE_RULES = [
    (re.compile(r"(?<![\w/])brain/FEEDBACK_LOG\.md(?![\w])"),
     ".coordination/FEEDBACK_LOG.md",
     "brain/FEEDBACK_LOG.md → .coordination/FEEDBACK_LOG.md"),
    (re.compile(r"(?<![\w/])\.\./brain/FEEDBACK_LOG\.md(?![\w])"),
     "../.coordination/FEEDBACK_LOG.md",
     "../brain/FEEDBACK_LOG.md → ../.coordination/FEEDBACK_LOG.md"),
    (re.compile(r"(?<![\w/])\.\./\.\./brain/FEEDBACK_LOG\.md(?![\w])"),
     "../../.coordination/FEEDBACK_LOG.md",
     "../../brain/FEEDBACK_LOG.md → ../../.coordination/FEEDBACK_LOG.md"),
]


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, cwd=REPO_ROOT, capture_output=True, text=True)


def tracked_files() -> list[Path]:
    r = run(["git", "ls-files"])
    out: list[Path] = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line or any(line.startswith(p) for p in SKIP_PREFIXES):
            continue
        p = REPO_ROOT / line
        if any(part in SKIP_PATH_PARTS for part in p.parts):
            continue
        if p.suffix.lower() not in TEXT_EXTS:
            continue
        out.append(p)
    return out


def rewrite_file(path: Path, dry_run: bool) -> list[str]:
    try:
        text = path.read_text()
    except (UnicodeDecodeError, PermissionError):
        return []
    original = text
    hits: list[str] = []
    for pattern, replacement, label in REWRITE_RULES:
        new_text, count = pattern.subn(replacement, text)
        if count:
            hits.append(f"  [{count}] {label}")
            text = new_text
    if text != original and not dry_run:
        path.write_text(text)
    return hits


def git_mv(src: Path, dst: Path, dry_run: bool) -> None:
    rel_src = src.relative_to(REPO_ROOT).as_posix()
    rel_dst = dst.relative_to(REPO_ROOT).as_posix()
    if dry_run:
        print(f"  git mv {rel_src} → {rel_dst}")
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "mv", rel_src, rel_dst])


def git_status_clean_for(paths: list[Path]) -> bool:
    rels = [str(p.relative_to(REPO_ROOT)) for p in paths if p.exists()]
    if not rels:
        return True
    r = run(["git", "status", "--porcelain", "--"] + rels, check=False)
    return r.returncode == 0 and not r.stdout.strip()


def planned_moves() -> list[tuple[Path, Path]]:
    """Return list of (src, dst) for all moves. Skips files that don't exist
    (allowing re-running after partial migration)."""
    moves: list[tuple[Path, Path]] = []

    feedback_src = BRAIN_DIR / "FEEDBACK_LOG.md"
    if feedback_src.exists():
        moves.append((feedback_src, COORD_DIR / "FEEDBACK_LOG.md"))

    if BRAIN_DIR.exists():
        for p in sorted(BRAIN_DIR.iterdir()):
            if p.name == "FEEDBACK_LOG.md":
                continue  # handled separately
            if p.is_file():
                moves.append((p, ARCHIVE_BRAIN_DIR / p.name))

    if MEMORY_BANK_DIR.exists():
        for p in sorted(MEMORY_BANK_DIR.iterdir()):
            if p.is_file():
                moves.append((p, ARCHIVE_MEMORY_BANK_DIR / p.name))

    return moves


def apply_rule_update(dry_run: bool) -> str | None:
    if not RULES_FILE.exists():
        return "SKIP — .agent/rules/communication_guidelines.md not found"
    current = RULES_FILE.read_text()
    if RULE_MARKER in current:
        return "SKIP — Sources-of-Truth block already present"
    new_content = current.rstrip() + "\n" + RULE_BLOCK
    if not dry_run:
        RULES_FILE.write_text(new_content)
    return f"APPEND {len(RULE_BLOCK.splitlines())} lines to .agent/rules/communication_guidelines.md"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="Preview without touching the filesystem.")
    p.add_argument("--allow-dirty", action="store_true", help="Skip clean-tree check.")
    p.add_argument("--skip-rule-update", action="store_true", help="Don't modify .agent/rules/communication_guidelines.md")
    args = p.parse_args()

    if not COORD_DIR.exists():
        print("ABORT: .coordination/ does not exist. Run Phase 1 first.", file=sys.stderr)
        return 1

    moves = planned_moves()
    if not moves and RULES_FILE.exists() and RULE_MARKER in RULES_FILE.read_text():
        print("Nothing to do — already migrated.")
        return 0

    if not args.dry_run and not args.allow_dirty:
        # Check cleanliness of everything we'd touch
        check_paths = [src for src, _ in moves]
        check_paths.append(RULES_FILE)
        if not git_status_clean_for(check_paths):
            print("ABORT: one of the files being touched is dirty. "
                  "Commit or stash, or pass --allow-dirty.", file=sys.stderr)
            return 1

    prefix = "[DRY RUN] " if args.dry_run else ""

    print(f"\n{prefix}Planned moves ({len(moves)}):")
    if not moves:
        print("  (none — brain/ and memory-bank/ already empty)")
    for src, dst in moves:
        git_mv(src, dst, dry_run=args.dry_run)

    print(f"\n{prefix}Scanning tracked files for FEEDBACK_LOG refs...")
    total_files_changed = 0
    total_rewrites = 0
    for path in tracked_files():
        hits = rewrite_file(path, dry_run=args.dry_run)
        if hits:
            total_files_changed += 1
            total_rewrites += sum(int(h.strip().split("]")[0].strip("[")) for h in hits)
            print(f"\n  {path.relative_to(REPO_ROOT)}")
            for h in hits:
                print(h)

    print()
    if args.skip_rule_update:
        print(f"{prefix}Rule update: SKIPPED (--skip-rule-update)")
    else:
        result = apply_rule_update(dry_run=args.dry_run)
        print(f"{prefix}Rule update: {result}")

    print(f"\n{prefix}Summary: {len(moves)} file(s) moved, "
          f"{total_rewrites} ref(s) rewritten across {total_files_changed} file(s), "
          f"{'1 rule file updated' if not args.skip_rule_update else 'rule update skipped'}.")

    if args.dry_run:
        print("\n[DRY RUN] Nothing was written. Re-run without --dry-run to apply.")
    else:
        print("\nDone. `git status` shows renames + rewrites. Review with "
              "`git diff --stat`, then commit when ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
