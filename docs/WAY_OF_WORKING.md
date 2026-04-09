# Way of Working

> How to develop, test, and deploy NeoDemos. Cloud-first setup.

---

## The Golden Rule

**All data lives in the cloud.** Your local machine runs code only — it connects
to the Hetzner server's PostgreSQL and Qdrant via an SSH tunnel. You never run
local database containers for development anymore.

---

## Daily Workflow

### 1. Start your session

```bash
# Open the tunnel (runs in background)
./scripts/dev_tunnel.sh --bg

# Verify
./scripts/dev_tunnel.sh --status
```

This forwards:
- `localhost:5432` → Hetzner PostgreSQL (1.6M chunks, KG tables, user data)
- `localhost:6333` → Hetzner Qdrant (notulen_chunks + committee_transcripts_staging)

Your `.env` already points at `localhost` — no config changes needed.

### 2. Run the app

```bash
uvicorn main:app --reload --port 8000
```

Open `http://localhost:8000`. You're working with **real production data** now.

### 3. Write code, test, iterate

Edit Python files, templates, CSS — the `--reload` flag picks up changes instantly.
Every query hits the live cloud database through the tunnel.

### 4. End your session

```bash
./scripts/dev_tunnel.sh --stop
```

---

## What Lives Where

### Local (your Mac)

| What | Where | Purpose |
|------|-------|---------|
| Python source code | `*.py`, `services/`, `pipeline/` | All application logic |
| Jinja2 templates | `templates/` | HTML pages |
| Static assets | `static/css/`, `static/js/` | Styles, scripts |
| Configuration | `.env`, `config/deploy.yml` | Local env vars, Kamal config |
| Scripts | `scripts/` | Dev tunnel, deploy, pipeline tools |
| Documentation | `docs/` | Architecture plans, guides |
| Git history | `.git/` | Version control |
| Secrets | `.env`, `.kamal/secrets` | API keys, DB password (gitignored) |
| VERSION file | `VERSION` | Single source of truth for all versioning |

**Not on your Mac**: No database data, no Qdrant vectors, no user records.

### Cloud (Hetzner VPS — 178.104.137.168)

| What | Where | Purpose |
|------|-------|---------|
| PostgreSQL 16 | Docker volume `neodemos-postgres-data` | All tables: documents, chunks, users, KG |
| Qdrant v1.13.2 | `/home/deploy/neodemos-data/qdrant_storage` | Vector embeddings (1.6M+) |
| Running containers | Docker via Kamal | Web app, MCP server, Caddy, Postgres, Qdrant |
| Backups | `/home/deploy/backups/` | Pre-deploy dumps (last 5) |
| Caddy config | `/home/deploy/neodemos-config/Caddyfile` | TLS + reverse proxy |
| Logs | Container stdout (via `kamal app logs`) | Application logs |

### Offsite (Google Drive — encrypted via rclone)

| What | Folder | Frequency |
|------|--------|-----------|
| PostgreSQL dumps | `gdrive:02_Database_Vault/` | Daily (03:00 UTC) |
| KG table exports | `gdrive:02_Database_Vault/kg/` | After each pipeline layer |
| Qdrant snapshots | `gdrive:03_Vector_Snapshots/` | Weekly (Sundays) |

---

## Deploying Changes

**Never push directly to the server.** Always use the deploy script:

```bash
./scripts/deploy.sh
```

What this does (8 steps):
1. Checks git status (warns on uncommitted changes or wrong branch)
2. Reads version from `VERSION`
3. Checks no pipeline is running on production
4. Runs syntax checks + tests
5. Creates a pre-deploy Postgres backup on the server
6. Builds Docker image, pushes to ghcr.io, deploys via Kamal
7. Verifies: web returns 200, MCP health OK, version header matches
8. Prints summary with rollback command

**Dry run** (checks only, no deploy):
```bash
./scripts/deploy.sh --dry-run
```

**Rollback** (if something goes wrong):
```bash
kamal rollback
```

---

## Versioning

Single source of truth: the `VERSION` file in the project root.

**What updates automatically when you bump VERSION**:
- Footer on every web page: "NeoDemos (alpha) v0.1.0"
- `X-NeoDemos-Version` HTTP response header
- MCP server name shown to Claude/ChatGPT/Perplexity
- Static asset cache busting (`style.css?v=v0.1.0`)

**How to release a new version**:
```bash
echo "0.2.0" > VERSION
# Edit STAGE in neodemos_version.py if changing stage (alpha → beta)
git add VERSION neodemos_version.py
git commit -m "Bump version to v0.2.0"
git tag -a v0.2.0 -m "v0.2.0: Flair NER + Gemini enrichment"
git push origin main --tags
./scripts/deploy.sh
```

---

## Important Safety Rules

### When using the tunnel

- You are connected to **production data**. Every write is real.
- Don't run destructive scripts (`DROP TABLE`, bulk `DELETE`) without a backup.
- Don't run KG pipeline scripts (Flair NER, Gemini extraction) directly — use staging first.
- The tunnel auto-reconnects via `ServerAliveInterval=60`. If it drops, re-run `./scripts/dev_tunnel.sh --bg`.

### Before deploying

- Commit your changes to `main` first.
- If you changed the database schema, create an Alembic migration and test it on staging.
- The deploy script creates an automatic backup, but for schema changes, also run:
  ```bash
  ssh deploy@178.104.137.168 "docker exec neodemos-postgres pg_dump -U postgres neodemos | gzip > /home/deploy/backups/manual_$(date +%Y%m%d).sql.gz"
  ```

### For KG pipeline runs

- Always run on **staging** first (see `docs/DEV_PROD_STRATEGY.md` section 9).
- Create a pre-pipeline backup: `./scripts/pre_pipeline_backup.sh layer2_flair`
- Promote to production only after validation: `./scripts/promote_kg_to_prod.sh`

---

## Quick Reference

| Task | Command |
|------|---------|
| Start tunnel | `./scripts/dev_tunnel.sh --bg` |
| Stop tunnel | `./scripts/dev_tunnel.sh --stop` |
| Tunnel status | `./scripts/dev_tunnel.sh --status` |
| Run app locally | `uvicorn main:app --reload` |
| Deploy to production | `./scripts/deploy.sh` |
| Deploy dry-run | `./scripts/deploy.sh --dry-run` |
| Rollback last deploy | `kamal rollback` |
| View server logs | `kamal app logs --since 5m` |
| Bump version | Edit `VERSION`, commit, tag, deploy |
| Server shell | `ssh deploy@178.104.137.168` |
| Postgres shell (via tunnel) | `PGPASSWORD=... psql -h localhost -U postgres -d neodemos` |
| Check running pipelines | `kamal app exec "ps aux \| grep migrate"` |
