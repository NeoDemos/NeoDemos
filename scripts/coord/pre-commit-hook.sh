#!/usr/bin/env bash
# NeoDemos coordination pre-commit hook.
# Keeps .coordination/state.md and docs/handoffs/README.md in sync whenever
# events.jsonl or dependencies.yaml is staged.
#
# Install (one-time, per machine):
#   ln -sf ../../scripts/coord/pre-commit-hook.sh .git/hooks/pre-commit
#   chmod +x .git/hooks/pre-commit

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"

# Only run when coordination files are part of this commit.
if ! git diff --cached --name-only | grep -qE '\.coordination/(events\.jsonl|dependencies\.yaml)'; then
  exit 0
fi

echo "[coord] events.jsonl or dependencies.yaml staged — rebuilding state + README..."
cd "$REPO_ROOT"

python scripts/coord/rebuild_state.py
python scripts/coord/update_readme_index.py

git add .coordination/state.md docs/handoffs/README.md

echo "[coord] Done. State + README updated and staged."
