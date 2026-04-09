#!/bin/bash
# ──────────────────────────────────────────────────────────────
# SSH tunnel to Hetzner cloud databases for local development.
#
# Forwards:
#   localhost:5432 → Hetzner:5432 (PostgreSQL)
#   localhost:6333 → Hetzner:6333 (Qdrant HTTP)
#
# Usage:
#   ./scripts/dev_tunnel.sh          # start tunnel (foreground)
#   ./scripts/dev_tunnel.sh --bg     # start tunnel (background)
#   ./scripts/dev_tunnel.sh --stop   # stop background tunnel
#   ./scripts/dev_tunnel.sh --status # check if tunnel is running
#
# Prerequisites:
#   - SSH key at ~/.ssh/neodemos_ed25519
#   - No local Postgres or Qdrant running on those ports
# ──────────────────────────────────────────────────────────────

set -euo pipefail

REMOTE_HOST="178.104.137.168"
SSH_USER="deploy"
SSH_KEY="$HOME/.ssh/neodemos_ed25519"
PID_FILE="/tmp/neodemos-tunnel.pid"

# Port mappings: local:remote
POSTGRES_LOCAL=5432
POSTGRES_REMOTE=5432
QDRANT_LOCAL=6333
QDRANT_REMOTE=6333

check_port() {
    local port=$1
    if lsof -i :"$port" -sTCP:LISTEN >/dev/null 2>&1; then
        return 0  # port in use
    fi
    return 1  # port free
}

check_local_services() {
    local blocked=false

    if check_port $POSTGRES_LOCAL; then
        echo "ERROR: Port $POSTGRES_LOCAL is already in use (local Postgres running?)"
        echo "  Stop it with: docker stop neodemos-postgres  OR  brew services stop postgresql@16"
        blocked=true
    fi

    if check_port $QDRANT_LOCAL; then
        echo "ERROR: Port $QDRANT_LOCAL is already in use (local Qdrant running?)"
        echo "  Stop it with: docker stop neodemos-qdrant"
        blocked=true
    fi

    if [ "$blocked" = true ]; then
        echo ""
        echo "Stop local database containers first, then re-run this script."
        echo "Quick fix:  docker compose down"
        exit 1
    fi
}

start_tunnel() {
    local mode=${1:-foreground}

    check_local_services

    echo "Opening SSH tunnel to Hetzner ($REMOTE_HOST)..."
    echo "  localhost:$POSTGRES_LOCAL → PostgreSQL"
    echo "  localhost:$QDRANT_LOCAL   → Qdrant"

    if [ "$mode" = "background" ]; then
        ssh -f -N \
            -i "$SSH_KEY" \
            -L "$POSTGRES_LOCAL:127.0.0.1:$POSTGRES_REMOTE" \
            -L "$QDRANT_LOCAL:127.0.0.1:$QDRANT_REMOTE" \
            -o ServerAliveInterval=60 \
            -o ServerAliveCountMax=3 \
            -o ExitOnForwardFailure=yes \
            "$SSH_USER@$REMOTE_HOST"

        # Find the PID of the SSH tunnel we just started
        pgrep -f "ssh.*-L.*$POSTGRES_LOCAL:127.0.0.1:$POSTGRES_REMOTE.*$REMOTE_HOST" > "$PID_FILE"
        echo ""
        echo "Tunnel running in background (PID: $(cat "$PID_FILE"))"
        echo "Stop with:  ./scripts/dev_tunnel.sh --stop"
    else
        echo ""
        echo "Tunnel active. Press Ctrl+C to stop."
        echo ""
        ssh -N \
            -i "$SSH_KEY" \
            -L "$POSTGRES_LOCAL:127.0.0.1:$POSTGRES_REMOTE" \
            -L "$QDRANT_LOCAL:127.0.0.1:$QDRANT_REMOTE" \
            -o ServerAliveInterval=60 \
            -o ServerAliveCountMax=3 \
            -o ExitOnForwardFailure=yes \
            "$SSH_USER@$REMOTE_HOST"
    fi
}

stop_tunnel() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid"
            rm -f "$PID_FILE"
            echo "Tunnel stopped (PID $pid)"
        else
            rm -f "$PID_FILE"
            echo "Tunnel was not running (stale PID file cleaned up)"
        fi
    else
        # Try to find it by pattern
        local pid
        pid=$(pgrep -f "ssh.*-L.*$POSTGRES_LOCAL:127.0.0.1:$POSTGRES_REMOTE.*$REMOTE_HOST" || true)
        if [ -n "$pid" ]; then
            kill "$pid"
            echo "Tunnel stopped (PID $pid)"
        else
            echo "No tunnel running"
        fi
    fi
}

status_tunnel() {
    local pid
    pid=$(pgrep -f "ssh.*-L.*$POSTGRES_LOCAL:127.0.0.1:$POSTGRES_REMOTE.*$REMOTE_HOST" || true)
    if [ -n "$pid" ]; then
        echo "Tunnel RUNNING (PID $pid)"
        echo "  localhost:$POSTGRES_LOCAL → PostgreSQL"
        echo "  localhost:$QDRANT_LOCAL   → Qdrant"
    else
        echo "Tunnel NOT running"
    fi
}

case "${1:-}" in
    --bg|--background)
        start_tunnel background
        ;;
    --stop)
        stop_tunnel
        ;;
    --status)
        status_tunnel
        ;;
    --help|-h)
        head -16 "$0" | tail -12
        ;;
    *)
        start_tunnel foreground
        ;;
esac
