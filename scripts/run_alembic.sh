#!/usr/bin/env bash
set -euo pipefail
# ──────────────────────────────────────────────────────────────
# Wrapper for running Alembic migrations against the NeoDemos DB.
#
# Requires DATABASE_URL or DB_* env vars to be set (typically via
# the dev_tunnel + .env).
#
# Usage:
#   ./scripts/dev_tunnel.sh --bg && source .env && ./scripts/run_alembic.sh upgrade head
#   ./scripts/run_alembic.sh current
#   ./scripts/run_alembic.sh history
# ──────────────────────────────────────────────────────────────

if [ -z "${DATABASE_URL:-}" ] && [ -z "${DB_HOST:-}" ]; then
  echo "ERROR: Neither DATABASE_URL nor DB_HOST is set."
  echo "Make sure the dev tunnel is running and .env is sourced."
  echo ""
  echo "Usage:"
  echo "  ./scripts/dev_tunnel.sh --bg && source .env && ./scripts/run_alembic.sh upgrade head"
  exit 1
fi

cd "$(dirname "$0")/.."
python -m alembic "$@"
