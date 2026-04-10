#!/bin/bash
# ──────────────────────────────────────────────────────────────
# Safe deployment script for NeoDemos.
#
# Runs pre-flight checks, creates a DB backup on the server,
# syncs code via rsync, and rebuilds containers with docker compose.
#
# Usage:
#   ./scripts/deploy.sh              # full deploy with checks
#   ./scripts/deploy.sh --skip-tests # skip syntax checks
#   ./scripts/deploy.sh --dry-run    # checks only, no deploy
# ──────────────────────────────────────────────────────────────

set -euo pipefail

REMOTE_HOST="178.104.137.168"
SSH_USER="deploy"
SSH_KEY="$HOME/.ssh/neodemos_ed25519"
REMOTE_DIR="/home/deploy/neodemos"
DOMAIN="neodemos.nl"
MCP_DOMAIN="mcp.neodemos.nl"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

SKIP_TESTS=false
DRY_RUN=false

for arg in "$@"; do
    case $arg in
        --skip-tests) SKIP_TESTS=true ;;
        --dry-run)    DRY_RUN=true ;;
    esac
done

step() { echo -e "\n${GREEN}[DEPLOY]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }

# ── 1. Pre-flight checks ──

step "1/8  Checking git status..."
if [ -n "$(git status --porcelain)" ]; then
    warn "Uncommitted changes detected:"
    git status --short
    echo ""
    read -p "Deploy with uncommitted changes? (y/N) " -n 1 -r
    echo
    [[ $REPLY =~ ^[Yy]$ ]] || fail "Aborted. Commit your changes first."
fi

BRANCH=$(git branch --show-current)
if [ "$BRANCH" != "main" ]; then
    warn "You are on branch '$BRANCH', not 'main'."
    read -p "Continue anyway? (y/N) " -n 1 -r
    echo
    [[ $REPLY =~ ^[Yy]$ ]] || fail "Aborted. Switch to main first."
fi

step "2/8  Reading version..."
VERSION=$(cat VERSION)
echo "  Version: v${VERSION}"

step "3/8  Checking for running pipelines on server..."
PIPELINE_PROCS=$(ssh -i "$SSH_KEY" "$SSH_USER@$REMOTE_HOST" \
    "docker exec neodemos-web ps aux 2>/dev/null | grep -E 'migrate_embeddings|build_knowledge|populate_kg|flair_ner' | grep -v grep || true")
if [ -n "$PIPELINE_PROCS" ]; then
    fail "Pipeline process running on production! Wait for it to finish:\n$PIPELINE_PROCS"
fi
echo "  No pipelines running."

step "4/8  Running local checks..."
if [ "$SKIP_TESTS" = false ]; then
    # Check if tests directory exists
    if [ -d "tests" ] && [ -n "$(ls tests/*.py 2>/dev/null)" ]; then
        echo "  Running test suite..."
        python -m pytest tests/ -x --tb=short -q 2>&1 | tail -5
    else
        echo "  No test suite found, skipping."
    fi
fi

# Syntax check critical files
echo "  Checking Python syntax..."
python -m py_compile main.py || fail "main.py has syntax errors"
python -m py_compile mcp_server_v3.py || fail "mcp_server_v3.py has syntax errors"
echo "  Syntax OK."

if [ "$DRY_RUN" = true ]; then
    step "DRY RUN complete. No deploy performed."
    exit 0
fi

# ── 2. Pre-deploy backup ──

step "5/8  Creating pre-deploy backup on server..."
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
ssh -i "$SSH_KEY" "$SSH_USER@$REMOTE_HOST" bash -s <<BACKUP_EOF
    set -e
    BACKUP_DIR=/home/deploy/backups
    mkdir -p \$BACKUP_DIR

    echo "  Dumping PostgreSQL..."
    docker exec neodemos-postgres pg_dump -U postgres neodemos \
        | gzip > "\${BACKUP_DIR}/pre_deploy_${TIMESTAMP}.sql.gz"

    SIZE=\$(du -h "\${BACKUP_DIR}/pre_deploy_${TIMESTAMP}.sql.gz" | cut -f1)
    echo "  Backup saved: pre_deploy_${TIMESTAMP}.sql.gz (\${SIZE})"

    # Keep only last 5 pre-deploy backups
    cd \$BACKUP_DIR
    ls -t pre_deploy_*.sql.gz 2>/dev/null | tail -n +6 | xargs rm -f 2>/dev/null || true
BACKUP_EOF

# ── 3. Deploy ──

step "6/8  Syncing code and rebuilding containers..."
echo "  Rsyncing to $REMOTE_HOST..."
rsync -az \
  -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=no" \
  --exclude='.git' \
  --exclude='.venv' \
  --exclude='.kamal' \
  --exclude='.claude' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.env' \
  --exclude='data/' \
  --exclude='output/' \
  --exclude='logs/' \
  --exclude='qdrant/' \
  --exclude='snapshots/' \
  --exclude='*.db' \
  --exclude='*.xlsx' \
  --exclude='tmp_*' \
  --exclude='brain/' \
  --exclude='memory-bank/' \
  --exclude='.DS_Store' \
  --exclude='eval_notulen/runs/' \
  --exclude='rag_evaluator/results/' \
  --exclude='node_modules/' \
  . "$SSH_USER@$REMOTE_HOST:$REMOTE_DIR/"

echo "  Building and restarting web + mcp..."
ssh -i "$SSH_KEY" -o StrictHostKeyChecking=no "$SSH_USER@$REMOTE_HOST" \
  "cd $REMOTE_DIR && docker compose -f docker-compose.prod.yml up -d --build web mcp 2>&1"

# ── 4. Post-deploy verification ──

step "7/8  Verifying deployment..."
sleep 5  # give containers a moment to stabilize

# Check web frontend
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "https://${DOMAIN}/login" || echo "000")
if [ "$HTTP_STATUS" = "200" ]; then
    echo "  Web frontend: OK (HTTP $HTTP_STATUS)"
else
    warn "Web frontend returned HTTP $HTTP_STATUS (expected 200)"
fi

# Check MCP health
MCP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "https://${MCP_DOMAIN}/health" 2>/dev/null || echo "000")
if [ "$MCP_STATUS" = "200" ]; then
    echo "  MCP server:   OK (HTTP $MCP_STATUS)"
else
    warn "MCP server returned HTTP $MCP_STATUS"
fi

# Check version header
DEPLOYED_VERSION=$(curl -s -I "https://${DOMAIN}/login" 2>/dev/null | grep -i "x-neodemos-version" | tr -d '\r' | awk '{print $2}')
if [ "$DEPLOYED_VERSION" = "v${VERSION}" ]; then
    echo "  Version:       $DEPLOYED_VERSION (matches VERSION file)"
else
    warn "Version mismatch: expected v${VERSION}, got '${DEPLOYED_VERSION}'"
fi

step "8/8  Deploy complete!"
echo ""
echo "  Version:  v${VERSION}"
echo "  Web:      https://${DOMAIN}"
echo "  MCP:      https://${MCP_DOMAIN}"
echo "  Backup:   pre_deploy_${TIMESTAMP}.sql.gz"
echo ""
echo "  Server logs:  ssh -i ~/.ssh/neodemos_ed25519 deploy@178.104.137.168 'docker logs neodemos-web --tail 50'"
