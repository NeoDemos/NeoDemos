#!/usr/bin/env bash
#
# bump_version.sh — bump patch (v0.Y.Z → v0.Y.Z+1) before every deploy.
#
# Scheme (Dennis 2026-04-15):
#   - Every Kamal deploy increments the patch digit by 1.
#   - When we cut a milestone release, bump the minor (v0.Y → v0.Y+1.0) manually.
#   - Both VERSION and package.json are updated in sync.
#
# Usage:
#   ./scripts/bump_version.sh           # patch bump
#   ./scripts/bump_version.sh --minor   # minor bump, reset patch to 0
#   ./scripts/bump_version.sh --show    # print current, no change
#
set -euo pipefail
cd "$(dirname "$0")/.."

CUR=$(cat VERSION | tr -d ' \n')

case "${1:-}" in
  --show)
    echo "$CUR"
    exit 0
    ;;
  --minor)
    IFS='.' read -r MAJ MIN _ <<< "$CUR"
    NEXT="${MAJ}.$((MIN + 1)).0"
    ;;
  *)
    IFS='.' read -r MAJ MIN PATCH <<< "$CUR"
    # strip any -alpha.N suffix from patch
    PATCH=${PATCH%%-*}
    NEXT="${MAJ}.${MIN}.$((PATCH + 1))"
    ;;
esac

echo "$NEXT" > VERSION
# Keep package.json in sync
python3 - <<PY
import json, pathlib
p = pathlib.Path("package.json")
data = json.loads(p.read_text())
data["version"] = "$NEXT"
p.write_text(json.dumps(data, indent=2) + "\n")
PY

echo "Version: $CUR → $NEXT"
