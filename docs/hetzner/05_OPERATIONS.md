# 05 — Operations: Monitoring, Backups, Scaling

Day-to-day operations for the NeoDemos Hetzner deployment.

---

## Monitoring

### Health checks

```bash
# Quick health (from your Mac)
curl -s https://neodemos.nl/health | python3 -m json.tool

# Detailed service status
kamal details

# Resource usage (CPU, memory, network)
ssh neodemos "docker stats --no-stream"
```

### Key metrics to watch

| Metric | Check command | Warning |
|--------|--------------|---------|
| Qdrant RAM | `docker stats neodemos-qdrant --no-stream` | > 10 GB (of 12 GB limit) |
| PostgreSQL connections | `kamal accessory exec postgres "psql -U postgres -c 'SELECT count(*) FROM pg_stat_activity;'"` | > 40 (of 50 max) |
| Disk usage | `ssh neodemos "df -h /"` | > 80% of 240 GB |
| FastAPI p95 latency | Check Cloudflare Analytics | > 500ms |
| Auto-ingest | `kamal app logs --since 1h \| grep -i "refresh\|ingest"` | > 3 consecutive failures |
| Container restarts | `ssh neodemos "docker ps --format 'table {{.Names}}\t{{.Status}}'"` | Any "Restarting" |

### Log monitoring

```bash
# All app logs
kamal app logs --since 1h

# Specific service
kamal accessory logs qdrant --since 1h
kamal accessory logs postgres --since 30m

# Search for errors
kamal app logs --since 24h | grep -i "error\|exception\|traceback"

# Auto-ingest cycle results
kamal app logs --since 4h | grep -i "refresh\|scheduled\|ingest\|embed"
```

### Uptime monitoring (external)

Set up a free uptime monitor to alert you when the site goes down:

- **UptimeRobot** (free, 5-min checks): https://uptimerobot.com
  - Monitor: HTTPS GET `https://neodemos.nl/health`
  - Alert: Email to your address
- **Cloudflare Health Checks** (free with Pro, or use Workers):
  - Already have Cloudflare — check if health checks are available on free tier

---

## Backups

### PostgreSQL daily backup (automated)

Set up a cron job on the server:

```bash
ssh neodemos

# Create backup script
cat > /home/deploy/backup-neodemos.sh << 'SCRIPT'
#!/bin/bash
set -euo pipefail
BACKUP_DIR="/home/deploy/backups/postgres"
mkdir -p "$BACKUP_DIR"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/neodemos_$DATE.sql.gz"

# Dump and compress
docker exec neodemos-postgres pg_dump -U postgres neodemos | gzip > "$BACKUP_FILE"

# Verify backup is not empty
if [ $(stat -c%s "$BACKUP_FILE") -lt 1000 ]; then
    echo "ERROR: Backup file suspiciously small: $BACKUP_FILE"
    exit 1
fi

# Keep only last 7 days
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +7 -delete

echo "Backup complete: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"
SCRIPT

chmod +x /home/deploy/backup-neodemos.sh

# Schedule daily at 02:00 UTC (03:00 Amsterdam)
(crontab -l 2>/dev/null; echo "0 2 * * * /home/deploy/backup-neodemos.sh >> /home/deploy/backups/backup.log 2>&1") | crontab -
```

### Qdrant snapshots (weekly)

```bash
ssh neodemos

cat > /home/deploy/backup-qdrant.sh << 'SCRIPT'
#!/bin/bash
set -euo pipefail

# Create snapshot
RESPONSE=$(curl -s -X POST http://localhost:6333/collections/notulen_chunks/snapshots)
echo "$(date): Qdrant snapshot created: $RESPONSE"

# Clean up old snapshots (keep last 3)
SNAPSHOT_DIR="/home/deploy/neodemos-data/qdrant_storage/collections/notulen_chunks/snapshots"
if [ -d "$SNAPSHOT_DIR" ]; then
    ls -t "$SNAPSHOT_DIR"/*.snapshot 2>/dev/null | tail -n +4 | xargs -r rm
fi
SCRIPT

chmod +x /home/deploy/backup-qdrant.sh

# Schedule weekly on Sunday at 03:00 UTC
(crontab -l 2>/dev/null; echo "0 3 * * 0 /home/deploy/backup-qdrant.sh >> /home/deploy/backups/backup.log 2>&1") | crontab -
```

### Google Drive offsite backup (automated, daily at 00:00 CET)

All critical data is backed up daily to Google Drive (`NeoDemos/`) via rclone:

| Data | Google Drive path | Retention (local) |
|------|------------------|-------------------|
| PostgreSQL dump | `02_Database_Vault/` | 7 days |
| Qdrant snapshots (all collections) | `03_Vector_Snapshots/<collection>/` | 3 snapshots |
| Git bundle (full codebase) | `04_Source_Sync/` | Deleted after upload |

**Script**: `/home/deploy/backup-to-gdrive.sh`
**Cron**: `0 0 * * * /home/deploy/backup-to-gdrive.sh >> /home/deploy/backups/gdrive-backup.log 2>&1`
**Log**: `/home/deploy/backups/gdrive-backup.log`

```bash
# Check last backup status
ssh neodemos "tail -10 /home/deploy/backups/gdrive-backup.log"

# Manual run
ssh neodemos "/home/deploy/backup-to-gdrive.sh"

# Verify rclone remote
ssh neodemos "rclone lsd gdrive:NeoDemos/"

# Reconfigure rclone (if token expires)
ssh neodemos "rclone config"
```

> **Setup note**: rclone requires a one-time OAuth flow with Google Drive.
> Run `ssh neodemos "rclone config"` and follow the prompts to create a
> remote named `gdrive` (type: Google Drive). Since the server is headless,
> answer "No" to auto-config and paste the provided URL into a local browser
> to complete the OAuth dance.

### Hetzner server snapshots (before upgrades)

```bash
# From your Mac — before any risky operation
hcloud server create-image --type snapshot --description "pre-upgrade-$(date +%Y%m%d)" neodemos
```

### Restore procedures

```bash
# PostgreSQL restore
kamal accessory exec postgres "psql -U postgres -c 'DROP DATABASE neodemos;'"
kamal accessory exec postgres "psql -U postgres -c 'CREATE DATABASE neodemos;'"
ssh neodemos "zcat /home/deploy/backups/postgres/neodemos_LATEST.sql.gz | docker exec -i neodemos-postgres psql -U postgres -d neodemos"

# Qdrant restore
curl -X DELETE http://localhost:6333/collections/notulen_chunks
SNAPSHOT=$(ssh neodemos "ls -t /home/deploy/neodemos-data/qdrant_storage/collections/notulen_chunks/snapshots/*.snapshot | head -1")
curl -X PUT "http://localhost:6333/collections/notulen_chunks/snapshots/recover" \
  -H "Content-Type: application/json" \
  -d "{\"location\": \"file://$SNAPSHOT\"}"

# Full server restore (nuclear option)
hcloud server rebuild neodemos --image <snapshot-id>
```

---

## Scaling

### Current capacity (CCX33, 32 GB)

| Component | Allocation | Headroom |
|-----------|-----------|----------|
| Qdrant | 12 GB limit, ~8 GB used | ~4 GB |
| PostgreSQL | 4 GB limit, ~2-3 GB hot | ~1-2 GB |
| FastAPI (4 workers) | 2 GB limit | Sufficient |
| Caddy / kamal-proxy | 128 MB | Negligible |
| OS + buffers | ~14 GB free | Comfortable |

**1000 concurrent users**: Handled by 4 Uvicorn workers (~200-400 req/s throughput). The bottleneck is Qdrant search latency (~50-100ms per query), not CPU or connections.

### Upgrade triggers

| Trigger | Action | New cost |
|---------|--------|----------|
| Qdrant RAM > 10 GB (GraphRAG entities) | Upgrade to CCX43 (64 GB) | EUR 152/mo |
| > 400 req/s sustained | Add workers (8) or horizontal scale | Same box |
| > 10K users, > 1000 req/s | Multiple FastAPI replicas + PgBouncer | 2+ servers |

### Vertical upgrade (zero-downtime)

```bash
# 1. Take a snapshot first
hcloud server create-image --type snapshot --description "pre-resize" neodemos

# 2. Resize (requires server stop — plan for ~5 min downtime)
hcloud server poweroff neodemos
hcloud server change-type neodemos --type ccx43
hcloud server poweron neodemos

# 3. Wait for boot, verify
sleep 30
curl https://neodemos.nl/health

# 4. Update Qdrant memory limit in deploy.yml (12G → 24G)
# Then: kamal deploy
```

### Horizontal scaling (future)

When a single box isn't enough:

1. **PgBouncer** in front of PostgreSQL (connection pooling)
2. **Multiple FastAPI replicas** behind Cloudflare load balancing
3. **PostgreSQL read replicas** for search queries
4. **Qdrant cluster mode** (3-node, built-in sharding)

This is overkill until you hit 10K+ active users.

---

## Troubleshooting

### Container won't start

```bash
# Check logs
kamal app logs --since 10m

# Check if port is in use
ssh neodemos "ss -tlnp | grep -E '8000|8001|5432|6333'"

# Restart specific service
kamal accessory reboot postgres
kamal redeploy
```

### Out of memory (OOM)

```bash
# Check which container was killed
ssh neodemos "dmesg | grep -i oom | tail -5"
ssh neodemos "docker stats --no-stream"

# Increase limit in deploy.yml, then:
kamal deploy
```

### Disk full

```bash
ssh neodemos "df -h"

# Docker cleanup (safe — only removes unused images/containers)
ssh neodemos "docker system prune -f"

# Check largest directories
ssh neodemos "du -sh /home/deploy/neodemos-data/* | sort -rh | head"

# Check old backups
ssh neodemos "du -sh /home/deploy/backups/*"
```

### Deploy stuck / lock held

```bash
# Release the Kamal deploy lock
kamal lock release

# If container is stuck
kamal app stop
kamal app boot
```

### Database connection errors

```bash
# Check PostgreSQL is healthy
kamal accessory exec postgres "pg_isready -U postgres"

# Check connection count
kamal accessory exec postgres "psql -U postgres -c 'SELECT count(*) FROM pg_stat_activity;'"

# Restart PostgreSQL
kamal accessory reboot postgres
```

### Qdrant slow or unresponsive

```bash
# Check memory pressure
ssh neodemos "docker stats neodemos-qdrant --no-stream"

# Check collection status
curl -s http://localhost:6333/collections/notulen_chunks | python3 -m json.tool | grep -E "status|points_count"

# If status is "yellow" (recovering), wait — it's rebuilding HNSW index
# If status is "red", check logs:
kamal accessory logs qdrant --since 1h
```

### SSL/TLS certificate issues

```bash
# If using kamal-proxy: it handles ACME automatically
kamal proxy logs

# If using Caddy: check Caddy logs
kamal accessory logs caddy

# Verify DNS is correct
dig neodemos.nl +short
```

---

## Maintenance Runbook

### Weekly (5 min)

```bash
# Check everything is healthy
curl -s https://neodemos.nl/health
ssh neodemos "docker stats --no-stream"
ssh neodemos "df -h /"
```

### Monthly (15 min)

```bash
# Update Docker images (security patches)
kamal accessory reboot postgres   # pulls latest postgres:16
kamal accessory reboot qdrant     # pulls latest qdrant:v1.13.2

# Review backup logs
ssh neodemos "tail -20 /home/deploy/backups/backup.log"

# Check for OS security updates
ssh neodemos "sudo apt update && sudo apt list --upgradable"
ssh neodemos "sudo apt upgrade -y"
```

### Quarterly

- Review Hetzner costs and usage
- Consider Qdrant / PostgreSQL version upgrades
- Test backup restore procedure (restore to a test database)
- Rotate secrets (API keys, DB password)

---

## Cost Summary

| Item | Monthly |
|------|---------|
| Hetzner CCX33 | EUR 76.23 (incl. 21% BTW) |
| neodemos.nl domain | ~EUR 0.65 |
| Cloudflare (free tier) | EUR 0 |
| Nebius API (embeddings) | EUR 2-5 |
| UptimeRobot (free) | EUR 0 |
| **Total** | **~EUR 79-82/mo** |
