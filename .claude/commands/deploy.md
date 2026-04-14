# NeoDemos Deployment — Skill Reference

Use this skill when deploying, starting, stopping, or troubleshooting the NeoDemos stack. For security hardening, authentication, and secrets management, see `/secure`.

---

## ⚠️ Hard rules (read first, no exceptions)

1. **Deploys go through Kamal.** `git commit → git push → kamal deploy → live`. Never rsync files to the host. Never `ssh` to the host and run `docker` or `docker compose` commands for production services. If Kamal is not working, **stop and diagnose** — do not improvise a workaround.
2. **Docker runtime is Colima, NOT Docker Desktop.** If a Kamal build fails with `dial unix /Users/dennistak/.colima/default/docker.sock`, run `colima start` — do not tell the user to "start Docker Desktop". Dennis does not run Docker Desktop.
3. **Kamal binary is NOT in PATH.** It lives at `/opt/homebrew/lib/ruby/gems/4.0.0/bin/kamal`. `which kamal` returns empty; that does not mean it's missing.
4. **kamal-proxy is the sole public reverse proxy.** There is no Caddy, no Traefik, no nginx. Everything public goes through kamal-proxy on ports 80/443, including TLS termination via Let's Encrypt.
5. **Protect the postgres named volume** `neodemos_postgres_data` — it holds all council data (2865 meetings + chunks + embeddings). Never accept a config change that would switch it to an anonymous volume. Never run `docker compose` on the host — it can silently trigger a postgres recreate.
6. **Deployment window (SLO): downtime only 23:00–07:00 CET.** User traffic is growing. Zero-downtime operations are allowed any time; downtime-incurring operations are restricted to the maintenance window unless it is an emergency fix for an active outage. See the classification table below before proceeding.

### Deploy classification — can I ship this now?

| Operation | Downtime | Allowed window |
|---|---|---|
| `kamal deploy` (web service code change) | **Zero** — blue-green via kamal-proxy, drains old container after new one is healthy | Any time |
| `kamal rollback <sha>` (web service) | **Zero** — same blue-green path | Any time |
| `kamal accessory reboot mcp` | **~5–15s** — container stop/start, kamal-proxy returns 502 until `/up` healthy | 23:00–07:00 CET only (unless fixing active MCP outage) |
| `kamal accessory reboot postgres` / `qdrant` | **Seconds to minutes** — data services, web app errors until reconnect | 23:00–07:00 CET only; announce in advance |
| Postgres schema migration (Alembic) | **Varies** — zero for additive DDL; locking for NOT NULL / indexed cols | Any time for additive/CONCURRENTLY; else 23:00–07:00 CET |
| Kamal image build+push only (`kamal build push`) | **Zero** — nothing restarts | Any time |
| Any manual `docker` on host | **DO NOT** | Never |

Rule of thumb: if you're about to run `accessory reboot` and it's a business-hours day, ask yourself — is this fixing an active outage, or is it routine? If routine, schedule it for 23:00–07:00 CET. Write it into the plan, not into the moment.

### Emergency override

If MCP or web is already down during business hours, fix forward immediately — an already-broken service is not an SLO breach of the deployment window. Note the emergency in the incident log below so the exception is visible.

---

## Production topology (as of 2026-04-11 after Caddy → kamal-proxy migration)

```
Internet
  │
  ├── HTTPS (443) ──► kamal-proxy  ─┬─► neodemos-web-<sha>  :8000  (FastAPI)
  │                                 └─► neodemos-mcp         :8001  (MCP server)
  │
  └── (kamal-internal Docker network)
           ├── neodemos-postgres :5432
           └── neodemos-qdrant   :6333/6334
```

- **Host:** Hetzner VPS `178.104.137.168`
- **SSH:** `ssh -i ~/.ssh/neodemos_ed25519 deploy@178.104.137.168`
- **Registry:** `ghcr.io/neodemos/neodemos` (single image shared by web service and all accessories)
- **Kamal config:** [config/deploy.yml](config/deploy.yml) · **Secrets:** [.kamal/secrets](.kamal/secrets)
- **Docker network:** `kamal` (managed by Kamal, all containers join it automatically)

### Kamal service/accessory layout

| Name | Kamal type | Container | Role |
|---|---|---|---|
| `neodemos` | service (`web`) | `neodemos-web-<sha>` | FastAPI app, uvicorn on 8000 |
| `mcp` | accessory | `neodemos-mcp` | MCP server, uvicorn on 8001, OAuth 2.1 |
| `postgres` | accessory | `neodemos-postgres` | DB — named volume `neodemos_postgres_data` |
| `qdrant` | accessory | `neodemos-qdrant` | Vector store — bind mount `/home/deploy/neodemos-data/qdrant_storage` |

Note: there is **no `caddy` accessory anymore**. kamal-proxy handles 80/443 directly and is managed via `kamal proxy *` commands, not via the accessories block.

### Public hostnames (all issued by Let's Encrypt, auto-renewed by kamal-proxy)

| Host | Routes to | Notes |
|---|---|---|
| `neodemos.nl` | `neodemos-web:8000` | Primary |
| `www.neodemos.nl` | `neodemos-web:8000` | |
| `neodemos.eu` | `neodemos-web:8000` | 301 → `neodemos.nl` via `CanonicalHostRedirectMiddleware` in [main.py](main.py) |
| `www.neodemos.eu` | `neodemos-web:8000` | 301 → `neodemos.nl` |
| `mcp.neodemos.nl` | `neodemos-mcp:8001` | OAuth 2.1 at `/mcp` |
| `mcp.neodemos.eu` | `neodemos-mcp:8001` | |

---

## The commands you actually use

All commands run from the project root. Either add Kamal to your PATH or alias it:

```bash
export KAMAL=/opt/homebrew/lib/ruby/gems/4.0.0/bin/kamal
```

### Full deploy (web code changed)

```bash
colima status >/dev/null 2>&1 || colima start
git commit -am "fix foo"
git push
$KAMAL deploy
```

This builds + pushes + blue-green-deploys the `neodemos` web service. kamal-proxy waits for the new container to pass its `/up` healthcheck, then atomically swaps traffic from the old container to the new one. Old container is stopped after drain (default 30s). Does **not** touch accessories.

### Deploy an MCP code change

The MCP accessory shares the same Docker image as the web service. Steps:

```bash
colima status >/dev/null 2>&1 || colima start
git commit -am "fix mcp thing"
git push
$KAMAL build push                # builds + pushes new :latest
$KAMAL accessory reboot mcp      # recreates neodemos-mcp with new image
```

Or if you also changed web code, `$KAMAL deploy` handles the web side; then `$KAMAL accessory reboot mcp` for MCP.

### Restart a single accessory without rebuilding

```bash
$KAMAL accessory reboot <name>   # mcp | postgres | qdrant
```

### Inspect logs

```bash
$KAMAL app logs -f                       # web service
$KAMAL accessory logs mcp -f             # MCP
$KAMAL accessory logs postgres --lines 100
$KAMAL proxy logs -f                     # kamal-proxy (ACME issues, routing decisions)
```

### Rollback

```bash
$KAMAL rollback <previous_sha>           # web service to a previous image
```

For accessories, the rollback path is to pin the previous image SHA in `deploy.yml` and re-boot the accessory.

---

## Pre-flight checklist (before any deploy)

```bash
colima status                                # Docker runtime up?
ls /opt/homebrew/lib/ruby/gems/4.0.0/bin/kamal   # Kamal binary reachable?
git status                                   # Clean working tree? Kamal clones from git, uncommitted tracked changes are skipped.
curl -sI https://neodemos.nl/ | head -1       # Production currently healthy?
curl -sI https://mcp.neodemos.nl/mcp | head -1  # Expect HTTP/2 401 (OAuth challenge)
```

If any fail, fix them first — do not improvise a workaround.

---

## Verification after a deploy

```bash
# Public-facing
curl -sI https://neodemos.nl/login                # 200 via HTTP/2
curl -sI https://mcp.neodemos.nl/mcp              # 401 WWW-Authenticate: Bearer

# TLS cert freshness
echo | openssl s_client -servername neodemos.nl -connect neodemos.nl:443 2>/dev/null \
  | openssl x509 -noout -dates
# expect: Let's Encrypt E7 or E8 issuer, notAfter 90 days out

# Container state (read-only)
ssh -i ~/.ssh/neodemos_ed25519 deploy@178.104.137.168 \
  "sudo docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'"

# MCP query log — every tool call appends one JSONL line
ssh -i ~/.ssh/neodemos_ed25519 deploy@178.104.137.168 \
  "sudo docker exec neodemos-mcp tail -5 /app/logs/mcp_queries.jsonl"
```

All containers should be `Up ... (healthy)` and the image should be `ghcr.io/neodemos/neodemos:<sha>` or `:latest` — **not** a locally-built `neodemos-*:latest`. A local image name is a red flag that someone bypassed Kamal.

---

## Common failure modes & fixes

### `dial unix /Users/dennistak/.colima/default/docker.sock: no such file or directory`

Colima is stopped. Run `colima start`. Do **not** try to switch docker context, recreate the buildx builder, or "start Docker Desktop" — Dennis does not use Docker Desktop.

### `which kamal` → nothing

Kamal is not in PATH but IS installed at `/opt/homebrew/lib/ruby/gems/4.0.0/bin/kamal`. Use the absolute path.

### Kamal build says "Building from a local git clone, so ignoring these uncommitted changes: ..."

Informational — Kamal clones from the working tree's git, so uncommitted changes to tracked files are NOT baked into the image. Commit first if you want your changes deployed. Untracked files are also skipped.

### `kamal accessory reboot mcp` fails with name conflict

An old exited container is blocking the name. SSH in and remove it: `sudo docker rm neodemos-mcp`, then retry. This should only happen if someone bypassed Kamal previously.

### kamal-proxy won't boot: "Bind for 0.0.0.0:80 failed: port is already allocated"

Something else is holding 80/443. Check `sudo netstat -tlnp | grep -E ':80 |:443 '` on the host. There should only be kamal-proxy's `docker-proxy` processes holding those ports. If you find a rogue Caddy/nginx/etc., that's the problem — take it down, not kamal-proxy.

### kamal-proxy logs show `Healthcheck failed ... dial tcp: lookup <container> on ... network is unreachable`

kamal-proxy cannot resolve the target container over Docker DNS. This means the new container is on a different network than the `kamal` network that kamal-proxy lives on. Check `config/deploy.yml` for any stray `options.network:` overrides — they must NOT override to a non-`kamal` network.

### ACME cert issuance failed for one hostname

1. Check DNS: `dig +short A <hostname>` — must resolve to `178.104.137.168`
2. Check CAA records: `dig +short CAA <hostname>` — must allow `letsencrypt.org` (or be empty)
3. Check kamal-proxy logs: `$KAMAL proxy logs --lines 100 | grep -i acme`
4. Let's Encrypt rate limits: 5 failed validations per hostname per hour, 5 duplicate cert requests per week. If you're hitting these, wait an hour and retry, or debug with staging first.
5. **Staging ACME first** (safer for debugging, no rate-limit exposure): add `run.env.clear.ACME_DIRECTORY: https://acme-staging-v02.api.letsencrypt.org/directory` under the proxy block and redeploy. Staging certs are untrusted but prove the flow works.

### Postgres container in "Created" state, won't start

Someone ran `docker compose up` or `docker network connect` on the host and put postgres into a broken state. The named volume `neodemos_postgres_data` should still be intact — run `sudo docker start neodemos-postgres` on the host. Then verify: `sudo docker exec neodemos-postgres psql -U postgres -d neodemos -c 'SELECT COUNT(*) FROM meetings;'` (should return ~2865 as of 2026-04).

### MCP endpoint returns connection failure, neodemos.nl is up

The `neodemos-mcp` accessory has crashed or is stopped. Check `$KAMAL accessory logs mcp --lines 100`. Reboot with `$KAMAL accessory reboot mcp`.

---

## Incident log (keep this section honest — newest on top)

### 2026-04-11 — Caddy → kamal-proxy migration (completed)

**What happened:** The previous commit `392213b "Fix Kamal deploy: remove kamal-proxy, use Caddy as sole reverse proxy"` left the stack in a broken hybrid state. Caddy held 80/443, kamal-proxy ran in a degraded no-port state, and Kamal's deploy flow still tried to register with kamal-proxy over Docker DNS that couldn't resolve across networks. Result: **every web service deploy silently failed for 11+ hours** — the running `neodemos-web` container was from the pre-Kamal era with image ID `7c186fdad945` (no git-SHA tag).

**What was done:** Migrated to kamal-proxy as the sole public reverse proxy, matching Kamal v2's canonical architecture.

- `main.py`: added `/up` liveness endpoint + `CanonicalHostRedirectMiddleware` for `.eu → .nl` redirects (kamal-proxy has no native cross-TLD redirect feature).
- `mcp_server_v3.py`: added `/up` liveness endpoint via FastMCP's `@mcp.custom_route` decorator.
- `config/deploy.yml`: added `proxy:` blocks to web service and MCP accessory; removed `options.network` overrides so containers join the `kamal` network where kamal-proxy can resolve them by container name.
- Removed Caddy accessory, `Caddyfile`, `Caddyfile.prod`, and the broken post-deploy hook.
- 6 Let's Encrypt certs issued in one burst (neodemos.nl, www.neodemos.nl, neodemos.eu, www.neodemos.eu, mcp.neodemos.nl, mcp.neodemos.eu).

**Verification path used:** Phase 1 (HTTP-only, alternate ports 4444/4445, Caddy still running on 80/443) confirmed routing + healthchecks worked. Phase 3 cut over to 80/443 with TLS; cert issuance completed in under a minute. Total public traffic interruption: ~1 minute.

**Lessons baked into the hard rules above:**
- No rsync/SSH fallbacks. Ever.
- Colima, not Docker Desktop.
- Kamal binary lives at `/opt/homebrew/lib/ruby/gems/4.0.0/bin/kamal`.
- Never `docker compose` on the host.
- kamal-proxy is the sole public proxy — do not stack another reverse proxy in front of it.
- Protect the `neodemos_postgres_data` named volume.

---

## What NOT to deploy

- **MLX models** — Apple Silicon only. Embedding stays on Dennis's Mac.
- **Sandbox scripts** (`sandbox/`) — debug tools, not production code.
- **Migration scripts** (`scripts/migrate_*.py`) — run manually, never exposed.
- **`data/` directory** — mounted as a volume, never baked into the image.
- **`.env` file** — secrets flow through Kamal's `.kamal/secrets`, not the image.
