# 03 — Kamal Deployment

**Tool**: Kamal 2 (by 37signals)
**What it does**: Deploys Docker containers to your Hetzner VPS via SSH. Zero-downtime deploys, no daemon on the server.
**Docs**: https://kamal-deploy.org

---

## Prerequisites

- Server provisioned and hardened (see [01_PROVISION.md](01_PROVISION.md))
- DNS pointing to server (see [02_DOMAIN_DNS.md](02_DOMAIN_DNS.md))
- Docker Hub account (or GitHub Container Registry) for pushing images

---

## Install Kamal

```bash
# macOS
brew install kamal

# Or via Ruby gem
gem install kamal

# Verify
kamal version
# Expected: Kamal 2.x
```

---

## Project Configuration

### config/deploy.yml

This is the single config file Kamal needs. Create it in your project root:

```yaml
# Kamal deployment configuration for NeoDemos
# Docs: https://kamal-deploy.org/docs/configuration/

service: neodemos

# Docker image — pushed to registry, pulled by server
image: dennistak/neodemos

# Server(s) to deploy to
servers:
  web:
    hosts:
      - <SERVER_IP>                    # Replace with: hcloud server ip neodemos
    labels:
      traefik.http.routers.neodemos.rule: Host(`neodemos.nl`) || Host(`www.neodemos.nl`) || Host(`neodemos.eu`) || Host(`www.neodemos.eu`)
      traefik.http.routers.neodemos.entrypoints: websecure
      traefik.http.routers.neodemos.tls.certresolver: letsencrypt
    options:
      memory: 2G

# Container registry (Docker Hub, or ghcr.io, or any OCI registry)
registry:
  server: ghcr.io                      # GitHub Container Registry (free for public repos)
  username: dennistak
  password:
    - KAMAL_REGISTRY_PASSWORD          # Set in .env — GitHub PAT with packages:write

# SSH configuration
ssh:
  user: deploy
  keys:
    - ~/.ssh/neodemos_ed25519

# Environment variables injected into the web container
env:
  clear:
    NEODEMOS_CITY: rotterdam
    DB_HOST: neodemos-postgres
    DB_PORT: "5432"
    DB_NAME: neodemos
    DB_USER: postgres
    DB_POOL_SIZE: "10"
    DB_MAX_OVERFLOW: "20"
    QDRANT_URL: http://neodemos-qdrant:6333
    HOST: 0.0.0.0
    PORT: "8000"
    ENVIRONMENT: production
    DEBUG: "false"
    LOG_LEVEL: INFO
    DOMAIN: neodemos.nl
    ALLOWED_HOSTS: neodemos.nl,www.neodemos.nl,neodemos.eu,www.neodemos.eu
  secret:
    - DB_PASSWORD
    - GEMINI_API_KEY
    - NEBIUS_API_KEY
    - QDRANT_API_KEY
    - SECRET_KEY
    - ORI_API_KEY

# Accessories — long-running services managed alongside the app
accessories:
  postgres:
    image: postgres:16
    host: <SERVER_IP>
    port: "127.0.0.1:5432:5432"
    env:
      clear:
        POSTGRES_DB: neodemos
        POSTGRES_USER: postgres
      secret:
        - DB_PASSWORD                  # Reuses same secret as POSTGRES_PASSWORD
    volumes:
      - neodemos-postgres-data:/var/lib/postgresql/data
    options:
      memory: 4G
      health-cmd: "pg_isready -U postgres"
      health-interval: 10s
      health-timeout: 5s
      health-retries: 5
      name: neodemos-postgres
      network: kamal

  qdrant:
    image: qdrant/qdrant:v1.13.2
    host: <SERVER_IP>
    port: "127.0.0.1:6333:6333"
    env:
      clear:
        QDRANT__SERVICE__API_KEY: ""   # Set via secret if needed
    volumes:
      - /home/deploy/neodemos-data/qdrant_storage:/qdrant/storage
    options:
      memory: 12G
      health-cmd: "curl -f http://localhost:6333/healthz"
      health-interval: 15s
      health-timeout: 5s
      health-retries: 3
      name: neodemos-qdrant
      network: kamal

  mcp:
    image: dennistak/neodemos          # Same image as web, different entrypoint
    host: <SERVER_IP>
    port: "127.0.0.1:8001:8001"
    cmd: "python mcp_server_v3.py --http --port 8001"
    env:
      clear:
        DB_HOST: neodemos-postgres
        DB_PORT: "5432"
        DB_NAME: neodemos
        DB_USER: postgres
        QDRANT_URL: http://neodemos-qdrant:6333
      secret:
        - DB_PASSWORD
        - GEMINI_API_KEY
        - NEBIUS_API_KEY
        - QDRANT_API_KEY
    options:
      memory: 1G
      name: neodemos-mcp
      network: kamal

# Kamal proxy (replaces Traefik in Kamal 2)
# Handles TLS termination and zero-downtime deploys
proxy:
  ssl: true
  host: neodemos.nl
  app_port: 8000

# Health check — Kamal waits for this before switching traffic
healthcheck:
  path: /health
  port: 8000
  max_attempts: 10
  interval: 3

# Asset bridging (optional — for static files)
# asset_path: /app/static

# Hooks — run on deploy events
# hooks:
#   pre-deploy:
#     - echo "Deploying NeoDemos..."
#   post-deploy:
#     - echo "Deploy complete!"
```

### .kamal/secrets

Kamal reads secrets from this file. Create `.kamal/secrets`:

```bash
# .kamal/secrets — NOT committed to git (add to .gitignore)
# Kamal injects these as env vars into containers

KAMAL_REGISTRY_PASSWORD=ghp_your_github_pat_here
DB_PASSWORD=your_strong_db_password
GEMINI_API_KEY=your_gemini_key
NEBIUS_API_KEY=your_nebius_key
QDRANT_API_KEY=optional_qdrant_key
SECRET_KEY=your_secret_key_here
ORI_API_KEY=optional_ori_key
```

```bash
# Add to .gitignore
echo ".kamal/secrets" >> .gitignore
```

---

## Caddyfile (Alternative to Kamal Proxy)

If you prefer Caddy over kamal-proxy for TLS (to stay consistent with existing setup), you can run Caddy as an accessory instead and disable the built-in proxy:

```yaml
# Add to accessories in deploy.yml:
  caddy:
    image: caddy:2-alpine
    host: <SERVER_IP>
    port: "80:80"
    extra_ports:
      - "443:443"
    volumes:
      - /home/deploy/neodemos-config/Caddyfile:/etc/caddy/Caddyfile:ro
      - neodemos-caddy-data:/data
      - neodemos-caddy-config:/config
    options:
      memory: 128M
      name: neodemos-caddy
      network: kamal

# And set proxy: false in the top-level config
```

Upload the Caddyfile to the server before first deploy:

```bash
ssh neodemos "mkdir -p /home/deploy/neodemos-config"
scp Caddyfile neodemos:/home/deploy/neodemos-config/Caddyfile
```

---

## First Deploy

### 1. Build and push the Docker image

```bash
# Kamal handles this automatically, but you can also do it manually:
docker build -t ghcr.io/dennistak/neodemos:latest .
docker push ghcr.io/dennistak/neodemos:latest
```

### 2. Bootstrap the server

```bash
# First-time setup: installs Docker (if needed), starts accessories, deploys app
kamal setup
```

This command:
1. SSHs into the server as `deploy`
2. Installs Docker (if not already present)
3. Logs into the container registry
4. Starts all accessories (PostgreSQL, Qdrant, MCP)
5. Builds and pushes the web image
6. Pulls and starts the web container
7. Runs health checks
8. Switches traffic to the new container

### 3. Verify

```bash
# Check all containers
kamal details

# Check app logs
kamal app logs

# Check accessory logs
kamal accessory logs postgres
kamal accessory logs qdrant

# Hit the health endpoint
curl https://neodemos.nl/health
```

---

## Subsequent Deploys

```bash
# Deploy latest code (builds, pushes, zero-downtime swap)
kamal deploy

# Deploy without building (if image is already pushed)
kamal deploy --skip-push
```

---

## Common Kamal Commands

| Command | What it does |
|---------|-------------|
| `kamal setup` | First-time bootstrap (install Docker, deploy everything) |
| `kamal deploy` | Build, push, deploy with zero-downtime swap |
| `kamal redeploy` | Redeploy without building |
| `kamal app logs` | Tail web app logs |
| `kamal app logs --since 1h` | Logs from last hour |
| `kamal app exec "bash"` | Shell into running web container |
| `kamal app exec "python manage.py migrate"` | Run one-off commands |
| `kamal accessory logs qdrant` | Tail Qdrant logs |
| `kamal accessory reboot postgres` | Restart PostgreSQL |
| `kamal accessory exec postgres "psql -U postgres neodemos"` | PostgreSQL shell |
| `kamal rollback` | Roll back to previous version |
| `kamal details` | Show all running containers |
| `kamal audit` | Show deploy audit log |
| `kamal env push` | Push updated secrets to server |
| `kamal lock release` | Release stuck deploy lock |

---

## Claude Code Integration

Kamal is pure CLI — Claude Code can run any command directly:

```
You: "deploy to production"
Claude Code: kamal deploy

You: "check the logs"
Claude Code: kamal app logs --since 30m

You: "restart qdrant"
Claude Code: kamal accessory reboot qdrant

You: "roll back the last deploy"
Claude Code: kamal rollback
```

---

## Docker Network

All Kamal containers join the `kamal` network by default. Service discovery uses container names:

| Service | Container name | Reachable at |
|---------|---------------|--------------|
| PostgreSQL | `neodemos-postgres` | `neodemos-postgres:5432` |
| Qdrant | `neodemos-qdrant` | `neodemos-qdrant:6333` |
| Web | `neodemos-web-*` | `localhost:8000` (via proxy) |
| MCP | `neodemos-mcp` | `neodemos-mcp:8001` |
| Caddy / kamal-proxy | auto | `*:80`, `*:443` |

---

## Next Step

[04_DATA_MIGRATION.md](04_DATA_MIGRATION.md) — Transfer PostgreSQL and Qdrant data from Mac to VPS
