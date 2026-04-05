# NeoDemos Deployment — Skill Reference

Use this skill when deploying, starting, stopping, or troubleshooting the NeoDemos stack. For security hardening, authentication, and secrets management, see `/secure`.

## Full Service Map

```
Internet
  │
  ├── HTTPS (443) ──► Caddy (auto-TLS reverse proxy)
  │                      │
  │                      ├── /* ──────────► FastAPI web app (uvicorn, port 8000)
  │                      │                    ├── GET /            → Search UI
  │                      │                    ├── GET /meeting/*   → Meeting detail + analysis
  │                      │                    ├── GET /calendar    → Calendar view
  │                      │                    ├── GET /api/*       → JSON API + SSE streaming
  │                      │                    └── GET /health      → Health check endpoint
  │                      │
  │                      └── (not exposed) ──► MCP server (stdio or SSE, port 8001)
  │
  └── (localhost only) ──► PostgreSQL (5432)
                         ► Qdrant (6333/6334)
```

**5 services total:**

| Service | Role | Port | Exposed? |
|---------|------|------|----------|
| **FastAPI** (`main.py`) | Web frontend + API | 8000 | Via reverse proxy |
| **PostgreSQL** | Meetings, documents, chunks, BM25 search | 5432 | Never |
| **Qdrant** | Vector search (607K+ points) | 6333 | Never |
| **Caddy/nginx** | TLS termination, security headers | 443 | Yes |
| **MCP server** (`mcp_server.py`) | Claude Desktop / Co-Work tools | 8001 (SSE) or stdio | See below |

## Quick Start: Local User Testing

```bash
# 1. Configure environment (see /secure for secrets)
cp .env.example .env
# Edit .env with real values

# 2. Start core services
# PostgreSQL (already running locally, or via Docker):
docker compose up -d postgres

# Qdrant (standalone binary, NOT Docker — uses local data/qdrant_storage):
qdrant --config-path config/config.yaml &

# FastAPI:
cd /Users/dennistak/Documents/Final Frontier/NeoDemos
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000

# 3. Expose for testers (pick one):
# Option A: ngrok (simplest, HTTPS included)
ngrok http 8000 --basic-auth "tester:your_password"

# Option B: LAN-only (no internet exposure)
# Just share http://<your-ip>:8000 on local network

# Option C: Caddy on VPS (see "HTTPS with Caddy" below)
```

## Docker Compose Deployment (Full Stack)

### Production docker-compose with all services

The existing `docker-compose.yml` has `postgres` + `web` + `nginx`. For production, add Qdrant and use a prod override.

**Create `docker-compose.prod.yml`:**
```yaml
services:
  postgres:
    ports:
      - "127.0.0.1:5432:5432"      # Localhost only
    environment:
      POSTGRES_PASSWORD: ${DB_PASSWORD}  # No default fallback in prod
    command: >
      postgres
        -c log_connections=on
        -c log_disconnections=on
    deploy:
      resources:
        limits:
          memory: 2G

  qdrant:
    image: qdrant/qdrant:v1.13.2
    container_name: neodemos-qdrant
    ports:
      - "127.0.0.1:6333:6333"      # REST API, localhost only
      - "127.0.0.1:6334:6334"      # gRPC, localhost only
    volumes:
      - ./data/qdrant_storage:/qdrant/storage
      - ./config/config.yaml:/qdrant/config/production.yaml:ro
    environment:
      QDRANT__SERVICE__API_KEY: ${QDRANT_API_KEY:-}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:6333/healthz"]
      interval: 15s
      timeout: 5s
      retries: 3
    networks:
      - neodemos-network
    restart: unless-stopped
    deploy:
      resources:
        limits:
          memory: 4G

  web:
    environment:
      ENVIRONMENT: production
      DEBUG: "false"
      QDRANT_API_KEY: ${QDRANT_API_KEY:-}
      QDRANT_URL: http://qdrant:6333
    ports:
      - "127.0.0.1:8000:8000"      # Behind reverse proxy only
    depends_on:
      postgres:
        condition: service_healthy
      qdrant:
        condition: service_healthy
    deploy:
      resources:
        limits:
          memory: 4G

  # Remove nginx from prod — use Caddy externally instead
```

```bash
# Deploy
DB_PASSWORD="<generated>" GEMINI_API_KEY="<key>" QDRANT_API_KEY="<key>" \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d

# Verify
docker compose ps
curl -f http://localhost:8000/health
```

## MCP Server Deployment

The MCP server has two deployment modes:

### Mode 1: Claude Desktop (local, stdio) — Current Setup

This is how it works today. Claude Desktop launches `mcp_server.py` as a subprocess.

```json
// ~/Library/Application Support/Claude/claude_desktop_config.json
{
  "mcpServers": {
    "neodemos": {
      "command": "/Users/dennistak/Documents/Final Frontier/NeoDemos/.venv/bin/python",
      "args": ["/Users/dennistak/Documents/Final Frontier/NeoDemos/mcp_server.py"],
      "env": { "PYTHONPATH": "/Users/dennistak/Documents/Final Frontier/NeoDemos" }
    }
  }
}
```

**Requires:** Your Mac running, PostgreSQL + Qdrant + embedding model all local. No network exposure needed.

### Mode 2: Claude Co-Work / Remote (SSE transport) — For User Testing

Claude Co-Work and remote clients can't use stdio. They need an HTTP-based transport.

To run the MCP server over SSE (Server-Sent Events):

```python
# At the bottom of mcp_server.py, change:
if __name__ == "__main__":
    import sys
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    if transport == "sse":
        mcp.run(transport="sse", host="127.0.0.1", port=8001)
    else:
        mcp.run(transport="stdio")
```

```bash
# Start MCP server in SSE mode (behind reverse proxy)
.venv/bin/python mcp_server.py sse
```

Then expose via Caddy alongside the main app:
```
yourdomain.com {
    # Main web app
    reverse_proxy /api/* localhost:8000
    reverse_proxy /* localhost:8000

    # MCP SSE endpoint (for Claude Co-Work)
    handle /mcp/* {
        reverse_proxy localhost:8001
    }
}
```

**Important:** The MCP SSE endpoint should be authenticated. See `/secure` for how to add API key auth.

## Embedding Strategy

Embeddings run **on your Mac only** (Apple Silicon + MLX + Qwen3-8B). This is intentional:
- The bulk 607K+ points are already embedded
- Daily ingest is small volume (a few documents per day)
- No need to deploy GPU infrastructure for this

**How daily ingest works:**
1. `RefreshService` runs at 8 AM via scheduler (in `main.py`) — downloads new docs to PostgreSQL
2. You run embedding manually when needed, or (future) via the auto-ingest pipeline
3. The deployed server reads from Qdrant, never writes to it

**If the Mac is off:** The deployed web app still works — it queries existing vectors in Qdrant. New documents just won't have embeddings until you run the ingest again.

## HTTPS with Caddy

```bash
# Install
sudo apt install -y caddy    # Debian/Ubuntu
# or: brew install caddy      # macOS
```

Create `Caddyfile`:
```
yourdomain.com {
    reverse_proxy localhost:8000

    log {
        output file /var/log/caddy/neodemos.log
        format json
    }
}
```

Caddy auto-provisions TLS certificates via Let's Encrypt. No manual cert management.

```bash
caddy run --config Caddyfile
```

Security headers are handled by `/secure` — add them to the Caddyfile as described there.

## Process Management (systemd)

For VPS deployment without Docker:

**`/etc/systemd/system/neodemos.service`:**
```ini
[Unit]
Description=NeoDemos FastAPI Application
After=network.target postgresql.service

[Service]
Type=simple
User=neodemos
Group=neodemos
WorkingDirectory=/opt/neodemos
EnvironmentFile=/opt/neodemos/.env
ExecStart=/opt/neodemos/.venv/bin/uvicorn main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --workers 4 \
    --limit-max-requests 1000 \
    --timeout-keep-alive 65 \
    --access-log
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

**`/etc/systemd/system/neodemos-mcp.service`** (optional, for SSE mode):
```ini
[Unit]
Description=NeoDemos MCP Server (SSE)
After=neodemos.service

[Service]
Type=simple
User=neodemos
WorkingDirectory=/opt/neodemos
EnvironmentFile=/opt/neodemos/.env
ExecStart=/opt/neodemos/.venv/bin/python mcp_server.py sse
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable neodemos neodemos-mcp
sudo systemctl start neodemos neodemos-mcp
```

## Health Checks & Monitoring

### Health Endpoint (add to main.py)

```python
@app.get("/health")
async def health_check():
    checks = {"status": "ok", "services": {}}
    # PostgreSQL
    try:
        with storage._get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        checks["services"]["postgres"] = "ok"
    except:
        checks["services"]["postgres"] = "error"
        checks["status"] = "degraded"
    # Qdrant
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(url="http://localhost:6333")
        info = client.get_collection("notulen_chunks")
        checks["services"]["qdrant"] = f"ok ({info.points_count} points)"
    except:
        checks["services"]["qdrant"] = "error"
        checks["status"] = "degraded"
    return checks
```

### Monitoring Commands

```bash
# All services
curl -s http://localhost:8000/health | python -m json.tool

# Qdrant
curl -s http://localhost:6333/collections/notulen_chunks | python -m json.tool

# PostgreSQL connections
psql -U postgres -d neodemos -c "SELECT count(*) FROM pg_stat_activity;"

# Logs
docker compose logs -f --tail=100 web        # Docker
journalctl -u neodemos -f --no-pager         # systemd
```

## Deployment Topology Options

| Scenario | Stack | Notes |
|----------|-------|-------|
| **User testing (local)** | uvicorn + ngrok | Simplest. `ngrok http 8000 --basic-auth` |
| **User testing (VPS)** | Caddy + uvicorn + systemd | Auto-TLS, proper security |
| **Production (self-hosted)** | Docker Compose + Caddy | Full isolation, all 5 services |
| **Production (cloud)** | Docker + managed Postgres + Qdrant Cloud | Replace local services with managed ones |

## Rollback Plan

```bash
# Docker: roll back to previous image
docker compose down
docker compose up -d --no-build    # Uses cached image

# Git: roll back code
git log --oneline -5               # Find last good commit
git checkout <commit-hash> -- main.py services/
```

For database and Qdrant backup/restore procedures, see `/backup`.

## What NOT to Deploy to Production

- **MLX models** — Apple Silicon only. Embedding stays on your Mac.
- **Sandbox scripts** (`sandbox/`) — debug tools, not production code.
- **Migration scripts** (`scripts/migrate_*.py`) — run manually, never exposed.
- **`data/` directory** — mount as volume, never bake into Docker image.
