# NeoDemos Deployment Guide

**Date**: April 7, 2026
**Target**: Hetzner CCX33 (8 vCPU, 32 GB RAM, 240 GB NVMe) -- single server
**Stack**: FastAPI + PostgreSQL + Qdrant + Caddy + MCP SSE
**Cost**: ~EUR 80-110/mo (incl. 21% BTW)

---

## Architecture

```
Internet
    |
    +-- HTTPS (443) --> Caddy (auto-TLS)
    |                      |
    |                      +-- neodemos.nl/* --> FastAPI (port 8000)
    |                      |                       |
    |                      |                    Auth Middleware
    |                      |                       |
    |                      |              +--------+--------+
    |                      |           Public           Protected
    |                      |          (/, /search)   (/profile, /api/*)
    |                      |
    |                      +-- neodemos.nl/mcp/* --> MCP SSE (port 8001)
    |                                                    |
    |                                               API Key Auth
    |                                               (user_api_keys table)
    |
    +-- (localhost only) --> PostgreSQL (5432)
                           > Qdrant (6333/6334)
```

| Service | Role | Port | Exposed? |
|---------|------|------|----------|
| FastAPI | Web + API + auth + auto-ingest scheduler | 8000 | Via Caddy |
| PostgreSQL | Documents, chunks, users, KG | 5432 | Never |
| Qdrant | Vector search (1.63M points) | 6333 | Never |
| Caddy | TLS, reverse proxy | 443 | Yes |
| MCP server | Claude Desktop / Co-Work tools | 8001 | Via Caddy (`/mcp/*`) |

---

## Prerequisites

- **Hetzner CCX33 VPS** -- 8 dedicated vCPU (AMD EPYC), 32 GB RAM, 240 GB NVMe
- **Domain**: `neodemos.nl` (registered at Transip or any SIDN-accredited registrar)
- **API keys**:
  - `GEMINI_API_KEY` -- Google Gemini (for MCP tool answers if web frontend is added later)
  - `NEBIUS_API_KEY` -- Nebius (server-side embedding via OpenAI-compatible API)
- **DNS**: A record for `neodemos.nl` and `www.neodemos.nl` pointing to VPS IP

---

## Server Setup

### 1. Provision VPS

Order a Hetzner CCX33 in Falkenstein or Nuremberg (Germany). Select Ubuntu 22.04 LTS.

### 2. Initial hardening

```bash
# SSH in as root, create deploy user
adduser deploy
usermod -aG sudo deploy

# Disable root SSH login
sed -i 's/^PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
systemctl restart sshd

# Firewall: only allow SSH, HTTP, HTTPS
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

### 3. Install Docker

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker deploy

# Install Docker Compose plugin
sudo apt install docker-compose-plugin

# Verify
docker --version
docker compose version
```

### 4. Clone repository

```bash
su - deploy
git clone https://github.com/yourusername/neodemos.git
cd neodemos
cp .env.example .env
nano .env  # Fill in production values (see Environment Configuration below)
```

---

## Docker Compose

The repository includes a `docker-compose.yml` with all five services. Key resource limits:

| Service | Memory Limit | Notes |
|---------|-------------|-------|
| Qdrant | 12 GB | Holds int8 quantized vectors (~7.8 GB) + HNSW graph (~0.6 GB) + headroom |
| PostgreSQL | 4 GB | shared_buffers=1GB, covers 10 GB database working set |
| FastAPI | 2 GB | 4 Uvicorn workers + APScheduler |
| Caddy | 128 MB | Reverse proxy + auto-TLS |
| OS + buffer | ~14 GB free | Room for file cache and spikes |

### Start services

```bash
cd /home/deploy/neodemos

# Build and start all services
docker compose up -d

# Verify everything is running
docker compose ps

# Follow logs
docker compose logs -f web
```

### Stop services

```bash
docker compose down

# Stop AND remove volumes (WARNING: deletes all data)
docker compose down -v
```

### PostgreSQL tuning

Add to a custom `postgresql.conf` mounted into the container, or set via environment variables:

```
shared_buffers = 1GB
effective_cache_size = 2GB
work_mem = 64MB
maintenance_work_mem = 256MB
max_connections = 50
```

---

## Environment Configuration

Create `.env` in the project root. Every variable used by the stack:

```bash
# ==================== CITY CONFIGURATION ====================
NEODEMOS_CITY=rotterdam

# ==================== DATABASE ====================
DB_HOST=postgres                    # Docker service name (not localhost)
DB_PORT=5432
DB_NAME=neodemos
DB_USER=postgres
DB_PASSWORD=<strong-random-password>
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20

# ==================== QDRANT ====================
QDRANT_URL=http://qdrant:6333       # Docker service name
QDRANT_API_KEY=<optional-qdrant-key>

# ==================== API KEYS ====================
GEMINI_API_KEY=<your-gemini-key>    # Required if web frontend uses Gemini synthesis
NEBIUS_API_KEY=<your-nebius-key>    # Required for server-side embedding (auto-ingest)
ORI_API_KEY=                        # Optional, improves ORI API rate limits

# ==================== DOMAIN ====================
DOMAIN=neodemos.nl                  # Used by Caddy for auto-TLS

# ==================== SERVER ====================
HOST=0.0.0.0
PORT=8000
ENVIRONMENT=production
DEBUG=false
LOG_LEVEL=INFO

# ==================== SECURITY ====================
SECRET_KEY=<generate: python -c "import secrets; print(secrets.token_urlsafe(32))">
ALLOWED_HOSTS=neodemos.nl,www.neodemos.nl
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=10080            # 7 days

# ==================== EMAIL (for auth) ====================
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=noreply@neodemos.nl
SMTP_PASSWORD=<smtp-password>
ADMIN_EMAIL=admin@neodemos.nl
```

Generate a secure secret key and database password:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## Database Initialization

### Option A: Fresh database

```bash
# Run init script inside the container
docker compose exec web python scripts/init_db.py

# Verify tables
docker compose exec postgres psql -U postgres -d neodemos -c "\dt"
```

### Option B: Restore from backup

```bash
# Copy backup to server
scp neodemos_backup.sql.gz deploy@<VPS_IP>:/home/deploy/

# Restore
zcat neodemos_backup.sql.gz | docker compose exec -T postgres psql -U postgres -d neodemos

# Verify row counts
docker compose exec postgres psql -U postgres -d neodemos \
  -c "SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC LIMIT 10;"
```

### Qdrant data migration

Qdrant data lives in `./data/qdrant_storage/` (bind-mounted). To migrate from your Mac:

```bash
# On Mac: create Qdrant snapshot
curl -X POST http://localhost:6333/collections/notulen_chunks/snapshots

# Copy storage directory to server
rsync -avz --progress data/qdrant_storage/ deploy@<VPS_IP>:/home/deploy/neodemos/data/qdrant_storage/

# Restart Qdrant on server to pick up the data
docker compose restart qdrant
```

---

## Auto-Ingest (15-Minute Cycle)

APScheduler runs inside the FastAPI process with a 15-minute interval trigger. No external cron required.

**What it does each cycle:**

1. Queries the OpenRaadsinformatie (ORI) API for new documents since last check
2. Downloads new documents (notulen, moties, amendementen, etc.)
3. Chunks documents and stores them in PostgreSQL
4. Embeds chunks via the Nebius API (OpenAI-compatible, server-side)
5. Upserts vectors into Qdrant

**Configuration in `main.py`:**

```python
scheduler.add_job(
    scheduled_refresh,
    IntervalTrigger(minutes=15),
    id='interval_refresh',
    name='Check for new documents every 15 minutes',
    max_instances=1,        # Prevents overlapping runs
    misfire_grace_time=300,  # Skip if more than 5 min late
)
```

**Requirements:**

- `NEBIUS_API_KEY` must be set -- without it, the embedding step fails and new documents will not be searchable
- The embedder auto-detects: if `NEBIUS_API_KEY` is set, it uses the Nebius API; otherwise it tries local MLX (not available on the VPS)

**Limitations on server (acceptable):**

- Vision OCR (for scanned PDFs) requires a GPU -- not available on VPS
- Whisper audio transcription requires a GPU -- not available on VPS
- Both are covered by VTT subtitle fallback, which works on the server

---

## User Authentication

DIY auth built into FastAPI. Zero external cost, all user data stays in your PostgreSQL.

### Database schema

```sql
CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT UNIQUE NOT NULL,
    name          TEXT,
    password_hash TEXT NOT NULL,
    is_active     BOOLEAN DEFAULT TRUE,
    is_verified   BOOLEAN DEFAULT FALSE,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login    TIMESTAMP
);

CREATE TABLE user_api_keys (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID REFERENCES users(id) ON DELETE CASCADE,
    key_hash    TEXT NOT NULL,
    label       TEXT,
    last_used   TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE sessions (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL,
    expires_at  TIMESTAMP NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### Routes

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| POST | `/auth/register` | Public | Create account (email + password) |
| POST | `/auth/login` | Public | Returns JWT, sets session cookie |
| POST | `/auth/logout` | Authenticated | Invalidates session |
| GET | `/auth/verify` | Public | Email verification link |
| POST | `/auth/reset` | Public | Password reset request |
| GET | `/profile` | Authenticated | User dashboard, API keys |
| POST | `/profile/api-keys` | Authenticated | Generate MCP API key |
| DELETE | `/profile/api-keys/{id}` | Authenticated | Revoke API key |

### Libraries

Add to `requirements.txt`:

```
python-multipart>=0.0.9
passlib[bcrypt]>=1.7.4
python-jose[cryptography]>=3.3.0
```

### Access control

- **Public**: homepage, search, static assets
- **Authenticated**: profile, API endpoints, MCP tools
- **Admin**: user management (future)

---

## MCP SSE Deployment

The MCP server runs as a separate process on port 8001, proxied by Caddy at `/mcp/*`.

### Caddyfile

```
neodemos.nl, www.neodemos.nl {
    reverse_proxy localhost:8000

    handle /mcp/* {
        reverse_proxy localhost:8001
    }

    log {
        output file /var/log/caddy/neodemos.log
        format json
    }
}
```

### Run MCP in SSE mode

```bash
# Inside Docker or as a separate service
python mcp_server.py --http --port 8001
```

### API key authentication

Each registered user generates a personal API key from their profile page. The MCP SSE endpoint validates the key against the `user_api_keys` table.

### Claude Desktop configuration (end-user)

Users paste this into their Claude Desktop config after generating an API key:

```json
{
  "mcpServers": {
    "neodemos": {
      "command": "uvx",
      "args": ["mcp-proxy", "https://neodemos.nl/mcp/sse"],
      "env": { "API_KEY": "nd_user_abc123..." }
    }
  }
}
```

---

## Monitoring and Health

### Health endpoint

```bash
curl https://neodemos.nl/health
# Returns: {"status": "ok", "database": "connected"}
```

### Service status

```bash
docker compose ps
docker stats neodemos-web neodemos-postgres neodemos-qdrant neodemos-caddy
```

### Logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f web --tail=200

# Caddy access logs
docker compose exec caddy cat /var/log/caddy/neodemos.log | tail -50
```

### Key metrics to watch

| Metric | Warning threshold |
|--------|------------------|
| Qdrant RAM usage | > 10 GB (of 12 GB limit) |
| PostgreSQL connections | > 40 (of 50 max) |
| Disk usage | > 80% of 240 GB |
| FastAPI response time (p95) | > 500ms |
| Auto-ingest failures | > 3 consecutive |
| CPU usage | > 80% sustained |

---

## Backup and Recovery

### PostgreSQL daily backup (cron)

```bash
# Create backup script
cat > /home/deploy/backup-neodemos.sh << 'SCRIPT'
#!/bin/bash
BACKUP_DIR="/home/deploy/backups/postgres"
mkdir -p "$BACKUP_DIR"
DATE=$(date +%Y%m%d_%H%M%S)
docker compose -f /home/deploy/neodemos/docker-compose.yml \
  exec -T postgres pg_dump -U postgres neodemos | gzip > "$BACKUP_DIR/neodemos_$DATE.sql.gz"
# Keep only last 7 days
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +7 -delete
SCRIPT
chmod +x /home/deploy/backup-neodemos.sh

# Add to crontab (runs daily at 02:00 UTC)
(crontab -l 2>/dev/null; echo "0 2 * * * /home/deploy/backup-neodemos.sh") | crontab -
```

### Qdrant snapshots

```bash
# Create snapshot
curl -X POST http://localhost:6333/collections/notulen_chunks/snapshots

# List snapshots
curl http://localhost:6333/collections/notulen_chunks/snapshots

# Snapshots are stored in ./data/qdrant_storage/snapshots/
```

### Restore PostgreSQL

```bash
docker compose down web   # Stop app to prevent writes
zcat /home/deploy/backups/postgres/neodemos_YYYYMMDD_HHMMSS.sql.gz | \
  docker compose exec -T postgres psql -U postgres -d neodemos
docker compose up -d web
```

### Restore Qdrant

```bash
# Copy snapshot back into storage directory
docker compose restart qdrant
# Or use the Qdrant snapshot recovery API
```

---

## Scaling Path

### Current: Hetzner CCX33 (32 GB RAM)

Sufficient for the current RAG workload:

- Qdrant: ~7.8 GB RAM (1.63M points, int8 quantized)
- PostgreSQL: ~10.2 GB on disk, ~2-3 GB hot working set
- ~14 GB headroom for OS, FastAPI, and growth

### Upgrade trigger: GraphRAG entity embeddings

When GraphRAG goes live, entity embeddings add ~4.7 GB to Qdrant RAM, pushing the total to ~12.5 GB. Combined with PostgreSQL growth (+3-6 GB from `kg_relationships`), 32 GB becomes tight.

**Upgrade to Hetzner CCX43:**

| Spec | CCX33 (current) | CCX43 (upgrade) |
|------|-----------------|-----------------|
| vCPU | 8 dedicated | 16 dedicated |
| RAM | 32 GB | 64 GB |
| Disk | 240 GB NVMe | 360 GB NVMe |
| Monthly | EUR 76.22 | EUR 151.84 |

**What GraphRAG adds:**

| Component | Size |
|-----------|------|
| `kg_relationships` (PostgreSQL) | +3-6 GB (8-16M rows) |
| Entity embeddings (Qdrant) | +4.7 GB RAM, +19 GB disk |
| Community embeddings (Qdrant) | +20 MB RAM, ~78 MB disk |
| Total Qdrant RAM | ~12.5 GB (up from 7.8 GB) |
| Total Qdrant disk | ~52 GB (up from 33 GB) |

**Estimated timeline**: upgrade when GraphRAG goes live (entity extraction and embedding complete).

**Build steps stay on Mac**: entity extraction (Gemini API), community detection (NetworkX, 6-10 GB peak RAM), and entity embedding (MLX + Qwen3, Apple Silicon GPU) all run locally. Upload snapshots and pg_dump to VPS.

### At 10K+ users

Horizontal scaling: multiple FastAPI replicas behind a load balancer, PgBouncer for connection pooling, PostgreSQL read replicas.

---

## Cost Breakdown

| Component | Monthly |
|-----------|---------|
| Hetzner CCX33 | EUR 76.22 (incl. 21% BTW) |
| Domain (neodemos.nl) | ~EUR 0.50 |
| Nebius API (embeddings) | ~EUR 2-5 |
| Jina API (reranking) | ~EUR 0-20 |
| Transactional email (Resend/Mailgun) | ~EUR 0-5 |
| **Total** | **~EUR 80-107/mo** |

**Notes:**

- Gemini API costs are zero for the MCP-only MVP since Claude handles synthesis. If a web frontend with Gemini synthesis is added later, costs increase.
- Hetzner price is EUR 63.00 ex-VAT, EUR 76.22 incl. 21% BTW.
- Nebius pricing based on Qwen3-Embedding at 4096 dims; only consumed during auto-ingest cycles.
- Jina reranking costs depend on query volume; free tier covers light usage.

---

## Troubleshooting

**1. Database connection failed**

```
Error: could not connect to server
```

```bash
# Check PostgreSQL is running
docker compose ps postgres

# Test connection from inside the network
docker compose exec web python -c "
from services.storage import StorageService
s = StorageService()
print('Connected:', s.test_connection())
"

# Check credentials
grep DB_ .env
```

**2. Qdrant out of memory**

```
Error: Killed (OOM) or Qdrant container restarting
```

```bash
# Check memory usage
docker stats neodemos-qdrant

# Increase memory limit in docker-compose.yml
# Change: memory: 12G -> memory: 14G
docker compose up -d qdrant
```

**3. Auto-ingest not running**

```bash
# Check scheduler is active in logs
docker compose logs web | grep -i "scheduler\|refresh\|interval"

# Verify NEBIUS_API_KEY is set
docker compose exec web env | grep NEBIUS

# If key is missing, embedding will fail silently
```

**4. Caddy cannot get TLS certificate**

```
Error: obtaining certificate... ACME challenge failed
```

```bash
# Verify DNS is pointing to VPS IP
dig neodemos.nl +short

# Check ports 80 and 443 are open
sudo ufw status

# Check Caddy logs
docker compose logs caddy
```

**5. Disk full**

```bash
# Check disk usage
df -h

# Docker-specific cleanup
docker system prune -f
docker volume ls

# Check Qdrant storage size
du -sh data/qdrant_storage/

# Check PostgreSQL data
docker compose exec postgres du -sh /var/lib/postgresql/data
```

**6. FastAPI workers crash / OOM**

```bash
# Check container restarts
docker compose ps

# View crash logs
docker compose logs web --tail=500 | grep -i "error\|killed\|oom"

# Increase memory limit if needed
# Change in docker-compose.yml: memory: 2G -> memory: 4G
docker compose up -d web
```

**7. MCP SSE connection refused**

```bash
# Check MCP server is running on port 8001
docker compose exec web curl -f http://localhost:8001/health

# Check Caddy is routing /mcp/* correctly
curl -v https://neodemos.nl/mcp/sse

# Verify Caddyfile has the /mcp/* handle block
cat Caddyfile
```

**8. Slow search queries (> 2 seconds)**

```bash
# Check Qdrant search latency
curl -X POST http://localhost:6333/collections/notulen_chunks/points/search \
  -H 'Content-Type: application/json' \
  -d '{"vector": [0.1, ...], "limit": 5}'

# Check PostgreSQL slow queries
docker compose exec postgres psql -U postgres -d neodemos \
  -c "SELECT query, calls, mean_exec_time FROM pg_stat_statements ORDER BY mean_exec_time DESC LIMIT 5;"

# If Qdrant is slow, check if quantized vectors are in RAM
docker compose exec web curl http://qdrant:6333/collections/notulen_chunks | python -m json.tool | grep -A5 quantization
```
