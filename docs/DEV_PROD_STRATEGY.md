# Dev / Prod / Backup Strategy

> NeoDemos environment separation, database management, versioning, and deployment plan.
> Created 2026-04-08 &mdash; aligns with v0.1.0 (alpha).

---

## 1. Problem Statement

| Concern | Detail |
|---------|--------|
| **Database size** | ~10 GB Postgres (1.6M chunks, KG tables) + ~33 GB Qdrant |
| **User data** | Users registering on production; sessions, preferences live only there |
| **Knowledge graph** | 881K entities, 57K edges today; Layers 2-3 will add ~500K+ edges via Flair NER + Gemini |
| **Sync cost** | Full `pg_dump` + Qdrant snapshot transfer takes 30-60 min and overwrites user data |
| **Risk** | Running untested migrations or pipeline scripts against production can corrupt data |

Full database syncs in either direction are **unsustainable**. We need to separate
**schema** (code, versioned) from **data** (content, environment-specific).

---

## 2. Architecture: Three Environments

```
┌─────────────┐      ┌──────────────────┐      ┌──────────────────┐
│  Local Dev   │      │  Staging (Hetzner)│      │ Production (Hetzner)│
│             │      │                  │      │                    │
│ Seed data    │ ──►  │ Prod-copy data   │      │ Real users + data  │
│ Unit tests   │      │ Migration dry-run│      │ Public traffic     │
│ Feature dev  │      │ KG pipeline runs │      │ neodemos.nl        │
│             │      │ staging.neodemos.nl│     │ mcp.neodemos.nl    │
└─────────────┘      └──────────────────┘      └──────────────────┘
                            ▲                          ▲
                            │  Schema migrations       │  Schema migrations
                            │  (Alembic, tested here   │  (Alembic, auto on
                            │   first)                 │   deploy)
                            │                          │
                     ┌──────┴──────────────────────────┴──────┐
                     │              Git (main branch)          │
                     │   Code + Alembic versions + Kamal cfg   │
                     └─────────────────────────────────────────┘
```

### 2.1 Local Development

**Purpose**: Write code, run unit tests, iterate quickly.

- Uses `docker-compose.yml` (existing).
- Database contains **seed data only** — never a full production copy.
- Qdrant holds a small test collection (~1-5K vectors).
- No real user accounts; test users created by seed script.

**What you do here**: Feature development, UI work, refactoring, writing tests.
**What you do NOT do here**: Run large pipelines, test migrations on real data.

### 2.2 Staging (on Hetzner, same server)

**Purpose**: Integration testing with real-scale data, migration dry-runs, KG pipeline execution.

- Runs alongside production on the CCX33 (32 GB RAM; ~6-8 GB headroom available).
- Separate Postgres instance on port **5433**, separate Qdrant on port **6335**.
- Accessible at `staging.neodemos.nl` behind **HTTP basic auth**.
- Staging database is a **periodic snapshot of production** (minus user passwords, which are hashed anyway).
- **All schema migrations run here first** before touching production.
- **KG Layer 2-3 pipelines run here first** — Flair NER and Gemini extraction are tested on staging before promoting results to production.

**What you do here**: Migration testing, KG pipeline runs, full-scale RAG eval, pre-deploy verification.

### 2.3 Production

**Purpose**: Serve real users, store authoritative data.

- Runs on `neodemos.nl` / `mcp.neodemos.nl` (existing Kamal deployment).
- **Source of truth** for all user data, document content, embeddings, and KG edges.
- Schema changes arrive **only** via tested Alembic migrations.
- KG pipeline results arrive **only** after staging validation.

---

## 3. Deployment Workflow

### 3.1 Current State

The project uses **Kamal 2** for zero-downtime deploys to a Hetzner CCX33 VPS.
The deploy is triggered **manually** from the developer's Mac — there is no CI/CD pipeline.

```
Developer's Mac                              Hetzner VPS (178.104.137.168)
─────────────────                            ────────────────────────────
1. git push origin main                      
2. kamal deploy ─────────────────────────►   3. Pull image from ghcr.io
   - docker build (local)                    4. Health check on /login
   - docker push to ghcr.io                  5. Zero-downtime container swap
                                             6. Alembic upgrade head (future)
```

**What travels through git (code)**:
- Python source (FastAPI, MCP server, pipeline scripts)
- Jinja2 templates (`templates/`)
- Static assets (`static/css/`, `static/js/`)
- Alembic migrations (future)
- Docker + Kamal configuration
- Documentation

**What does NOT travel through git (data)**:
- PostgreSQL data (stays in Docker volume on server)
- Qdrant vectors (stays in `/home/deploy/neodemos-data/qdrant_storage`)
- User uploads, logs, output files
- Secrets (`.kamal/secrets`, not committed)

**What travels via separate data migration (rare, one-time)**:
- Initial database load: `pg_dump | gzip | scp | psql` (done during setup)
- Initial Qdrant transfer: `rsync` of snapshot (done during setup)
- KG pipeline results: targeted `pg_dump -t` of specific tables (after staging validation)

### 3.2 Target: CI/CD with GitHub Actions

The manual `kamal deploy` workflow works for a solo developer but has no safety net:
no tests run, no linting, no migration check before code hits production.

**Recommended pipeline** (implement after staging is set up):

```yaml
# .github/workflows/deploy.yml
name: Deploy
on:
  push:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r requirements.txt
      - run: python -m pytest tests/ -x --tb=short

  deploy-staging:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: kamal deploy --destination=staging
    # Staging auto-deploys on every push to main

  deploy-production:
    needs: deploy-staging
    runs-on: ubuntu-latest
    environment: production          # requires manual approval in GitHub UI
    steps:
      - uses: actions/checkout@v4
      - run: kamal deploy
```

**Key principle**: Staging deploys automatically on push. Production requires a manual
approval click in the GitHub Actions UI — this is your "are you sure?" gate.

**When to implement this**: After Phase 3 (staging environment is live). Until then,
manual `kamal deploy` is acceptable for a solo developer.

### 3.3 What Gets Deployed Where

| Artifact | Mechanism | Destination | When |
|----------|-----------|-------------|------|
| **Application code** | `kamal deploy` (Docker image via ghcr.io) | Web + MCP containers | Every deploy |
| **Templates + static assets** | Inside Docker image (`COPY . .`) | Web container | Every deploy |
| **Schema migrations** | `alembic upgrade head` in container entrypoint | Postgres | Every deploy |
| **Caddyfile** | `scp` to server (manual, rare) | Caddy container | Only when routing changes |
| **Secrets** | `kamal env push` (reads `.kamal/secrets`) | All containers as env vars | Only when secrets change |
| **KG pipeline results** | `scripts/promote_kg_to_prod.sh` (targeted pg_dump) | Postgres | After staging validation |
| **Qdrant data** | Never re-deployed; lives on server permanently | Qdrant storage | Initial setup only |

### 3.4 Deploy Checklist

Before running `kamal deploy`:

1. **Code is committed and pushed** to `main`
2. **Pre-deploy backup** runs automatically (Kamal hook, see section 6.5)
3. **Alembic migrations** tested on staging first (if any schema changes)
4. **No background embedding/pipeline process** running on production

After deploy:

1. Health check passes (`/login` endpoint, Kamal handles this)
2. Spot-check: visit `neodemos.nl`, run a search query
3. Check MCP: `curl -s https://mcp.neodemos.nl/health`
4. Verify logs: `kamal app logs --since 5m`

---

## 4. Schema Migration Strategy (Alembic)

### 4.1 Why Alembic

Current state: manual `ALTER TABLE` scripts with `IF NOT EXISTS` guards in `scripts/`.
This works for a solo developer but breaks down when:
- You need to roll back a failed migration on production.
- You lose track of which scripts have been applied where.
- KG Layer 2-3 will add new columns/indexes to 1.6M-row tables.

Alembic gives you **versioned, reversible, ordered migrations** stored in git.

### 4.2 Setup

```bash
pip install alembic
alembic init migrations
```

Configure `migrations/env.py` to read `DATABASE_URL` from environment (same var as `db_pool.py`).

### 4.3 Workflow

```
1. Developer creates migration:
   alembic revision -m "add_flair_ner_columns"

2. Edit the generated file:
   def upgrade():
       op.add_column('kg_entities', sa.Column('flair_label', sa.Text))
       op.add_column('kg_entities', sa.Column('flair_confidence', sa.Float))

   def downgrade():
       op.drop_column('kg_entities', 'flair_confidence')
       op.drop_column('kg_entities', 'flair_label')

3. Test on staging:
   ENVIRONMENT=staging alembic upgrade head

4. Deploy to production (auto-runs on container start):
   alembic upgrade head
```

### 4.4 Existing Schema Baseline

Create an initial migration that captures the current schema as-is (all tables from
`migrate_3tier_schema.sql`, `create_raadslid_rollen_schema.py`, KG tables, etc.).
Mark it as already applied on both environments:

```bash
alembic stamp head  # "we are already at this version"
```

---

## 5. Versioning Strategy

### 5.1 Single Source of Truth

The `VERSION` file in the project root is the single source of truth (see `docs/VERSIONING.md`).
`neodemos_version.py` reads it and exports `__version__`, `VERSION_LABEL`, `DISPLAY_NAME`, and `STAGE`.

Currently, the version is consumed by:
- **MCP server**: Server name shown to Claude/ChatGPT/Perplexity clients
- **MCP instructions**: "Je bent verbonden met NeoDemos (alpha) v0.1.0..."

It is **not yet consumed by** the web frontend.

### 5.2 Frontend Versioning (New)

The web frontend (Jinja2 templates served by FastAPI) currently shows no version anywhere.
Add version to the template context so every page can display it.

**Implementation**:

1. Pass version to all templates via a global context:

```python
# main.py — add to the template response context
from neodemos_version import VERSION_LABEL, DISPLAY_NAME, STAGE

# Option A: Jinja2 global (available in all templates automatically)
templates.env.globals["version_label"] = VERSION_LABEL
templates.env.globals["display_name"] = DISPLAY_NAME
templates.env.globals["stage"] = STAGE
```

2. Display in `base.html` footer (inherited by all pages):

```html
<footer>
    <span class="version-badge">{{ display_name }} {{ version_label }}</span>
</footer>
```

3. Add version to API response headers (for debugging):

```python
@app.middleware("http")
async def add_version_header(request, call_next):
    response = await call_next(request)
    response.headers["X-NeoDemos-Version"] = VERSION_LABEL
    return response
```

### 5.3 MCP Server Versioning (Existing)

Already implemented in `mcp_server_v3.py`:
- FastMCP name = `DISPLAY_NAME` ("NeoDemos (alpha)")
- Server instructions include `VERSION_LABEL` ("v0.1.0")
- CLI output: `"NeoDemos (alpha) v0.1.0 — transport=http"`

No changes needed — this works correctly.

### 5.4 Static Asset Cache Busting (New)

Currently, `style.css` and `app.js` are served without version hashes. After a deploy,
users may see stale cached CSS/JS until their browser cache expires.

**Fix**: Append the version as a query parameter in `base.html`:

```html
<link rel="stylesheet" href="/static/css/style.css?v={{ version_label }}">
<script src="/static/js/app.js?v={{ version_label }}"></script>
```

When `VERSION` bumps, the URL changes, forcing browsers to fetch the new file.
This is a simple, zero-infrastructure approach — no webpack or build step needed.

### 5.5 Docker Image Tagging

Currently, Kamal pushes `ghcr.io/neodemos/neodemos:latest`. Add version tags:

```bash
# In deploy flow (Kamal or CI/CD)
docker build -t ghcr.io/neodemos/neodemos:latest \
             -t ghcr.io/neodemos/neodemos:v0.1.0 .
docker push ghcr.io/neodemos/neodemos:latest
docker push ghcr.io/neodemos/neodemos:v0.1.0
```

This lets you roll back to a specific version: `kamal rollback --version v0.1.0`.

### 5.6 Version Bump Checklist

```bash
# 1. Edit VERSION file
echo "0.2.0" > VERSION

# 2. Update STAGE in neodemos_version.py if changing stage (alpha → beta)

# 3. Commit
git add VERSION neodemos_version.py
git commit -m "Bump version to v0.2.0"
git tag -a v0.2.0 -m "v0.2.0: Flair NER + Gemini enrichment"

# 4. Push
git push origin main --tags

# 5. Deploy (picks up new version automatically)
kamal deploy
```

Everything else (frontend footer, MCP server name, cache busting, API headers) updates
automatically because they all read from the same `VERSION` file.

---

## 6. Data Flow Rules

### 6.1 What Lives Where

| Data category | Local Dev | Staging | Production | Sync direction |
|---------------|-----------|---------|------------|----------------|
| **Schema** (DDL) | Alembic | Alembic | Alembic | Git → all envs |
| **User data** (users, sessions) | Seed script | Snapshot from prod | Authoritative | Prod → Staging (periodic) |
| **Documents** (meetings, agenda items) | Minimal seed | Snapshot from prod | Authoritative | Prod → Staging |
| **Chunks + embeddings** (document_chunks, Qdrant) | ~1K test chunks | Full copy from prod | Authoritative | Prod → Staging |
| **KG entities + edges** | Test fixtures | Pipeline output (tested here) | Promoted from staging | Staging → Prod (after validation) |
| **Enrichment columns** (section_topic, key_entities, etc.) | Test fixtures | Pipeline output | Promoted from staging | Staging → Prod |

### 6.2 Refresh Staging from Production

Run periodically (weekly, or before a major pipeline run):

```bash
#!/bin/bash
# scripts/refresh_staging.sh

# 1. Dump production Postgres (exclude sessions for privacy)
docker exec neodemos-postgres pg_dump -U postgres neodemos \
  --exclude-table-data=sessions \
  | gzip > /tmp/prod_snapshot.sql.gz

# 2. Restore into staging Postgres
gunzip -c /tmp/prod_snapshot.sql.gz \
  | docker exec -i neodemos-staging-postgres psql -U postgres neodemos

# 3. Qdrant: copy snapshot
docker exec neodemos-qdrant \
  curl -s -X POST 'http://localhost:6333/collections/neodemos/snapshots'
# rsync snapshot to staging Qdrant storage directory

rm /tmp/prod_snapshot.sql.gz
```

### 6.3 Promote KG Results from Staging to Production

After validating Layer 2-3 pipeline output on staging:

```bash
# Export only KG tables from staging
docker exec neodemos-staging-postgres pg_dump -U postgres neodemos \
  -t kg_entities -t kg_relationships -t kg_communities \
  --data-only | gzip > /tmp/kg_staging.sql.gz

# Import into production (after backup!)
gunzip -c /tmp/kg_staging.sql.gz \
  | docker exec -i neodemos-postgres psql -U postgres neodemos
```

For enrichment columns on `document_chunks`, use a targeted UPDATE script rather than
full table replacement — production chunks may have newer ingested documents.

---

## 7. Knowledge Graph: Hops Explained

### 7.1 What Is a Hop?

A "hop" is one step along a relationship edge in the knowledge graph. Each hop follows
one arrow between two entities. Think of it as: **entity → relationship → entity** = 1 hop.

### 7.2 Real Examples from Rotterdam Council Data

**1-hop query** — "Welke partij hoort raadslid Kasmi bij?"

```
Kasmi ──LID_VAN──► PvdA
       (1 hop)
```

SQL: one simple JOIN on `kg_relationships`.

---

**2-hop query** — "Welke partijen stemden tegen het Warmtebedrijf voorstel?"

```
Warmtebedrijf voorstel ◄──STEMT_TEGEN── SP
                        ◄──STEMT_TEGEN── Leefbaar Rotterdam
                                          │
                                          ├──LID_VAN──► Pastors (raadslid)
                                          └──LID_VAN──► De Jong
                        (hop 1: voorstel → partij)
                        (hop 2: partij → raadsleden)
```

SQL: two JOINs or a recursive CTE with `max_depth=2`. PostgreSQL handles this well.

---

**3-hop query** — "Welke raadsleden uit dezelfde partij als wethouder Kurvers hebben
moties ingediend over projecten in Feijenoord?"

```
Kurvers ──IS_WETHOUDER_VAN──► Bouw & Wonen (portfolio)
    │
    └──LID_VAN──► VVD (partij, hop 1)
                    │
                    ├──LID_VAN──► Raadslid A (hop 2)
                    │               │
                    │               └──DIENT_IN──► Motie X (hop 3)
                    │                               │
                    │                               └──BETREFT_WIJK──► Feijenoord (hop 4, filter)
                    │
                    └──LID_VAN──► Raadslid B (hop 2)
                                    │
                                    └──DIENT_IN──► Motie Y (hop 3)
```

This is actually **3-4 hops**: wethouder → partij → raadslid → motie (→ wijk).
With PostgreSQL, this requires nested JOINs or a recursive CTE that starts to get slow
at scale (1M+ entities, 500K+ edges) because the search space fans out exponentially.

---

**4+ hop query** — "Vind alle moties over projecten in Zuid die zijn ingediend door
leden van coalitiepartijen waar de verantwoordelijke wethouder over begrotingsoverschrijdingen
heeft gesproken."

```
Coalitie ──bevat──► VVD ──LID_VAN──► wethouder ──SPREEKT_OVER──► begrotingspost
                     │                                              │
                     └──LID_VAN──► raadslid ──DIENT_IN──► motie ──BETREFT_WIJK──► Zuid
```

5 hops. This is where PostgreSQL becomes painful — the combinatorial explosion of
possible paths makes SQL queries slow and hard to optimize. A dedicated graph database
(Neo4j, Apache AGE) uses specialized graph traversal algorithms (BFS/DFS with pruning)
that handle this natively.

### 7.3 Where NeoDemos Sits Today

| Metric | Current (Layer 1) | After Layer 2-3 |
|--------|-------------------|-----------------|
| Entities | 881K | ~1.5M |
| Edges | 57K | ~500K-1M |
| Max useful hops | 2 | 2-3 |
| Relationship types | 7 (LID_VAN, DIENT_IN, etc.) | 11+ (adding HEEFT_BUDGET, BETREFT_WIJK, SPREEKT_OVER) |

**Today's query patterns** are predominantly 1-2 hops:
- "Who voted against X?" (1-2 hops)
- "What motions did party Y submit about topic Z?" (1-2 hops)
- "Compare budgets for culture vs sport" (2 hops via HEEFT_BUDGET)

**Future query patterns** (v0.5.0+, multi-municipality, agentic features) may need 3+ hops:
- Cross-municipality comparisons: "Do Rotterdam and Vlaardingen handle X differently?"
- Coalition analysis: "Which coalition partners consistently disagree on housing policy?"
- Temporal chains: "Track how policy position on X evolved across 3 coalition periods"

### 7.4 When to Move to a Graph Database

**Stay on PostgreSQL** (now through ~v0.4.0) when:
- Queries are 1-2 hops (current use cases)
- Edge count is under ~1M
- Graph is queried at read time only (no real-time graph algorithms)

**Consider Apache AGE** (Postgres extension, v0.5.0+) when:
- You need 3-hop queries but want to stay in the PostgreSQL ecosystem
- Lets you write Cypher queries alongside SQL — no new infrastructure
- Zero migration cost: runs inside your existing Postgres instance

**Consider Neo4j** (separate database, v0.7.0+ or v1.0) when:
- You need graph algorithms (PageRank for influential politicians, community detection)
- 3+ hop traversals are common and need to be fast (<100ms)
- Cross-municipality graphs create a combinatorial explosion
- Graph writes are frequent (real-time entity extraction during ingest)

**The `graph_retrieval.py` service abstracts the storage layer.** Switching from
PostgreSQL to AGE or Neo4j requires reimplementing the retriever only, not the
extraction pipelines or the rest of the RAG service.

---

## 8. Backup Strategy

### 8.1 Current Infrastructure

| Component | Script | Destination | Status |
|-----------|--------|-------------|--------|
| PostgreSQL dump | `scripts/daily_backup.sh` | `gdrive:02_Database_Vault/` | **Cron on Hetzner** (02:00 UTC) |
| Qdrant snapshot | `scripts/daily_backup.sh` | `gdrive:03_Vector_Snapshots/` | **Cron on Hetzner** (Sundays 03:00 UTC) |
| Post-embedding backup | `scripts/post_embedding_backup.sh` | `gdrive:02_Database_Vault/` | Manual, after pipeline runs |
| CSV export | `scripts/backup_db.py` | `data/db_backup_csv/` | Local only |
| Git push | `scripts/daily_backup.sh` | GitHub | Part of backup flow |
| Google Drive offsite | `rclone sync` to `gdrive-crypt` | Encrypted Google Drive | Daily via cron (00:00 UTC) |

### 8.2 Google Drive Layout (via rclone + gdrive-crypt)

```
gdrive:/
  01_Engines_Legacy/
  02_Database_Vault/          <-- PostgreSQL dumps (daily)
    └── kg/                   <-- KG table exports (after each pipeline layer)
  03_Vector_Snapshots/        <-- Qdrant snapshots (weekly)
  04_Source_Sync/             <-- Project files
  05_Project_Admin/
```

**Encryption**: Two-layer rclone config:
- `gdrive` — base remote (OAuth to Google Drive)
- `gdrive-crypt` — encryption overlay (filename + directory encryption, password + salt)

**Critical**: Encryption passwords **must** be stored in a password manager (1Password/Bitwarden).
Loss of the rclone encryption password = permanent loss of all Google Drive backups.

**Storage budget**: Google Drive free tier is 15 GB. At current backup sizes (~1-2 GB
Postgres + 2-4 GB Qdrant snapshots per week), expect to need Google One 100 GB
(EUR 1.99/mo) within a few months.

### 8.3 Target: Automated Backup Schedule

| What | Frequency | Retention | Where | Trigger |
|------|-----------|-----------|-------|---------|
| **PostgreSQL full dump** | Daily at 03:00 UTC | 7 local, 30 on Google Drive | `gdrive:02_Database_Vault/` | Cron on Hetzner |
| **PostgreSQL pre-deploy dump** | Before every Kamal deploy | 5 most recent | `/home/deploy/backups/` | Kamal hook |
| **Qdrant snapshot** | Weekly (Sunday 04:00 UTC) | 3 local, 6 on Google Drive | `gdrive:03_Vector_Snapshots/` | Cron on Hetzner |
| **Pre-pipeline snapshot** | Before KG Layer 2-3 runs | Until pipeline validated | `/home/deploy/backups/` | Manual |
| **KG tables export** | After each pipeline layer completes | 3 versions | `gdrive:02_Database_Vault/kg/` | Script |
| **Google Drive offsite sync** | Daily at 00:00 UTC | Mirrors local retention | Encrypted gdrive-crypt | Cron on Hetzner |

### 8.4 Implementation: Cron on Hetzner

```bash
# /etc/cron.d/neodemos-backup (on Hetzner server)

# Daily offsite sync to Google Drive at 00:00 UTC
0 0 * * * deploy rclone sync /home/deploy/backups gdrive-crypt:02_Database_Vault/ --log-file=/home/deploy/logs/rclone.log

# Daily Postgres backup at 03:00 UTC
0 3 * * * deploy /home/deploy/neodemos/scripts/daily_backup.sh >> /home/deploy/logs/backup.log 2>&1

# Weekly Qdrant snapshot on Sunday at 04:00 UTC
0 4 * * 0 deploy /home/deploy/neodemos/scripts/qdrant_weekly_snapshot.sh >> /home/deploy/logs/backup.log 2>&1
```

### 8.5 Pre-Deploy Backup (Kamal Hook)

Add to `config/deploy.yml`:

```yaml
hooks:
  pre-deploy:
    - scripts/pre_deploy_backup.sh
```

```bash
#!/bin/bash
# scripts/pre_deploy_backup.sh
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR=/home/deploy/backups

docker exec neodemos-postgres pg_dump -U postgres neodemos \
  | gzip > "${BACKUP_DIR}/pre_deploy_${TIMESTAMP}.sql.gz"

# Keep only last 5 pre-deploy backups
ls -t ${BACKUP_DIR}/pre_deploy_*.sql.gz | tail -n +6 | xargs rm -f 2>/dev/null

echo "Pre-deploy backup saved: pre_deploy_${TIMESTAMP}.sql.gz"
```

### 8.6 KG Pipeline Backup Protocol

Before running **any** KG pipeline layer (Flair NER, Gemini extraction):

```bash
#!/bin/bash
# scripts/pre_pipeline_backup.sh
LAYER=$1  # e.g. "layer2_flair" or "layer3_gemini"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# 1. Full KG tables backup
docker exec neodemos-postgres pg_dump -U postgres neodemos \
  -t kg_entities -t kg_relationships -t kg_communities \
  | gzip > "/home/deploy/backups/kg_${LAYER}_pre_${TIMESTAMP}.sql.gz"

# 2. Enrichment columns backup (document_chunks metadata only)
docker exec neodemos-postgres psql -U postgres neodemos -c \
  "COPY (SELECT id, section_topic, key_entities, vote_outcome, vote_counts, indieners, motion_number FROM document_chunks) TO STDOUT WITH CSV HEADER" \
  | gzip > "/home/deploy/backups/enrichment_${LAYER}_pre_${TIMESTAMP}.csv.gz"

echo "KG backup complete for ${LAYER}: kg + enrichment snapshots saved"
```

After validation, upload to Google Drive:

```bash
rclone copy "/home/deploy/backups/kg_${LAYER}_pre_${TIMESTAMP}.sql.gz" \
  gdrive-crypt:02_Database_Vault/kg/
```

### 8.7 Disaster Recovery

| Scenario | Recovery steps | RTO |
|----------|---------------|-----|
| **Bad migration** | `alembic downgrade -1` | 2 min |
| **Bad KG pipeline run** | Restore pre-pipeline KG backup (targeted tables only) | 10 min |
| **Corrupted Qdrant** | Restore latest weekly snapshot from Google Drive | 15-30 min |
| **Server loss** | Provision new Hetzner, restore Postgres + Qdrant from Google Drive | 1-2 hours |
| **Accidental data deletion** | Restore from daily backup on Google Drive | 15 min |
| **rclone token expired** | Re-authenticate: `rclone config reconnect gdrive:` | 5 min |

---

## 9. Staging Environment Setup

### 9.1 Docker Compose (Staging)

Create `docker-compose.staging.yml` on the Hetzner server:

```yaml
services:
  staging-postgres:
    image: postgres:16
    container_name: neodemos-staging-postgres
    environment:
      POSTGRES_DB: neodemos
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: ${STAGING_DB_PASSWORD}
    ports:
      - "127.0.0.1:5433:5432"
    volumes:
      - staging_postgres_data:/var/lib/postgresql/data
    mem_limit: 2g
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 10s

  staging-qdrant:
    image: qdrant/qdrant:v1.13.2
    container_name: neodemos-staging-qdrant
    ports:
      - "127.0.0.1:6335:6333"
    volumes:
      - /home/deploy/neodemos-data/staging_qdrant:/qdrant/storage
    mem_limit: 4g

  staging-web:
    build: .
    container_name: neodemos-staging-web
    environment:
      ENVIRONMENT: staging
      DB_HOST: staging-postgres
      DB_PORT: 5432
      DB_PASSWORD: ${STAGING_DB_PASSWORD}
      QDRANT_HOST: staging-qdrant
      QDRANT_PORT: 6333
    ports:
      - "127.0.0.1:8002:8000"
    depends_on:
      staging-postgres:
        condition: service_healthy
    mem_limit: 2g

volumes:
  staging_postgres_data:

networks:
  default:
    name: neodemos-staging-network
```

### 9.2 Caddy Config

Add to `Caddyfile.prod`:

```
staging.neodemos.nl {
    basicauth {
        dennis <bcrypt-hashed-password>
    }
    reverse_proxy staging-web:8002
}
```

### 9.3 Resource Budget (Hetzner CCX33, 32 GB RAM)

| Service | Production | Staging | Total |
|---------|-----------|---------|-------|
| Postgres | 4 GB | 2 GB | 6 GB |
| Qdrant | 12 GB | 4 GB | 16 GB |
| Web app | 2 GB | 2 GB | 4 GB |
| Caddy | 128 MB | (shared) | 128 MB |
| MCP | ~1 GB | — | 1 GB |
| OS + overhead | ~3 GB | — | 3 GB |
| **Total** | | | **~30 GB** |

Leaves ~2 GB headroom. Sufficient for current workload. If KG Layer 3 (Gemini) needs
more memory during pipeline execution, temporarily stop staging-qdrant.

---

## 10. Implementation Roadmap

### Phase 1: Safety Net (this week)

| # | Task | Effort |
|---|------|--------|
| 1 | Verify rclone is configured on Hetzner server (one-time) | 30 min |
| 2 | Schedule `daily_backup.sh` + rclone offsite sync as cron jobs | 15 min |
| 3 | Create `scripts/pre_deploy_backup.sh` and wire into Kamal hooks | 30 min |
| 4 | Test restore from Google Drive backup to local dev | 30 min |

### Phase 2: Schema Migrations + Versioning (this week)

| # | Task | Effort |
|---|------|--------|
| 5 | Install Alembic, create baseline migration from current schema | 2-3 hours |
| 6 | Add `alembic upgrade head` to Dockerfile entrypoint | 30 min |
| 7 | Migrate existing `scripts/migrate_*.sql` into Alembic versions | 1-2 hours |
| 8 | Add version to frontend: Jinja2 globals, footer badge, cache busting | 1 hour |
| 9 | Add `X-NeoDemos-Version` response header | 15 min |
| 10 | Add version tag to Docker image build | 15 min |

### Phase 3: Staging Environment (next week, target 2026-04-14)

| # | Task | Effort |
|---|------|--------|
| 11 | Create `docker-compose.staging.yml` | 1 hour |
| 12 | Add `staging.neodemos.nl` to Caddy + DNS (TransIP) | 30 min |
| 13 | Create `scripts/refresh_staging.sh` | 1 hour |
| 14 | Test full deploy cycle: local → staging → production | 1 hour |

### Phase 4: KG Pipeline Safety (before Layer 2-3, target 2026-04-14)

| # | Task | Effort |
|---|------|--------|
| 15 | Create `scripts/pre_pipeline_backup.sh` | 30 min |
| 16 | Create Alembic migration for Layer 2 columns (`source`, `flair_confidence`) | 30 min |
| 17 | Create `scripts/promote_kg_to_prod.sh` for staging → prod promotion | 1 hour |
| 18 | Run Layer 2 (Flair NER) on staging, validate, promote | 3-4 hours |
| 19 | Run Layer 3 (Gemini) on staging, validate, promote | 3-5 hours |

### Phase 5: CI/CD + Seed Data (target 2026-04-21)

| # | Task | Effort |
|---|------|--------|
| 20 | Create `.github/workflows/deploy.yml` (test → staging → prod) | 2-3 hours |
| 21 | Create `scripts/seed_dev_db.py` (test users + ~1K sample chunks) | 2 hours |
| 22 | Create matching small Qdrant collection for dev | 1 hour |

---

## 11. Decision Log

| Decision | Rationale | Revisit when |
|----------|-----------|-------------|
| **Staging on same server as production** | Budget-efficient (EUR 0 extra); 32 GB RAM has headroom | If staging pipeline runs cause production latency |
| **PostgreSQL for KG (not Neo4j)** | 1-2 hop queries work fine with SQL JOINs; graph_retrieval abstracts storage | After Layer 2-3 when query patterns are clearer; consider Apache AGE at v0.5.0 |
| **Apache AGE before Neo4j** | Zero-infrastructure cost (Postgres extension), Cypher support, no new database to manage | If AGE performance insufficient for 3+ hops at 1M+ edges |
| **Google Drive for offsite backups** | Already configured with encryption; 15 GB free, 100 GB for EUR 1.99/mo | If backup size exceeds Google Drive quota |
| **Alembic over raw SQL scripts** | Versioned, reversible, ordered; industry standard for Python projects | N/A (permanent choice) |
| **No full DB sync between environments** | Unsustainable at 10 GB+; user data must stay in production | N/A (permanent principle) |
| **KG pipelines run on staging first** | Layers 2-3 cost $100-140 in API calls; mistakes are expensive to undo | If pipelines become routine and low-risk |
| **Manual Kamal deploy (no CI/CD yet)** | Solo developer; automation overhead not justified until staging exists | Phase 5 (target 2026-04-21) |
| **Single VERSION file for all components** | MCP + frontend + Docker tags all read from one file; no drift | N/A (permanent) |
| **Server-rendered templates (no SPA)** | Low complexity, fast iteration, no JS build step; appropriate for current feature set | If frontend needs complex client-side state (real-time dashboards, drag-and-drop) |
