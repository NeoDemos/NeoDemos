# 04 — Data Migration (Mac to VPS)

Transfer your local PostgreSQL database and Qdrant vector storage to the Hetzner server.

---

## Overview

| Data | Local size | Transfer method |
|------|-----------|----------------|
| PostgreSQL (neodemos) | ~10 GB | `pg_dump` → gzip → scp → `psql` restore |
| Qdrant (notulen_chunks) | ~33 GB on disk, 1.63M points | Qdrant snapshot → rsync |

**Estimated transfer time**: ~30-60 min (depends on upload speed)

---

## Step 1: PostgreSQL Migration

### 1a. Dump on your Mac

```bash
# Full database dump, compressed
pg_dump -U postgres -d neodemos --no-owner --no-acl | gzip > neodemos_dump.sql.gz

# Check size
ls -lh neodemos_dump.sql.gz
# Expected: ~1-2 GB compressed
```

### 1b. Transfer to server

```bash
scp neodemos_dump.sql.gz neodemos:/home/deploy/
```

### 1c. Restore on server

```bash
ssh neodemos

# Ensure PostgreSQL accessory is running
# (If using Kamal, it's already up after kamal setup)
docker ps | grep postgres

# Restore into the running container
zcat /home/deploy/neodemos_dump.sql.gz | \
  docker exec -i neodemos-postgres psql -U postgres -d neodemos

# Verify row counts
docker exec neodemos-postgres psql -U postgres -d neodemos \
  -c "SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC LIMIT 15;"

# Clean up
rm /home/deploy/neodemos_dump.sql.gz
```

### Expected table counts (approximate)

| Table | Rows |
|-------|------|
| `chunks` | ~600K+ |
| `documents` | ~5K+ |
| `meetings` | ~2K+ |
| `agenda_items` | ~10K+ |
| `kg_relationships` (if GraphRAG) | 0 (future) |

---

## Step 2: Qdrant Migration

### Option A: Qdrant Snapshot (recommended)

```bash
# On your Mac — create a snapshot of the collection
curl -X POST http://localhost:6333/collections/notulen_chunks/snapshots

# List snapshots to get the filename
curl -s http://localhost:6333/collections/notulen_chunks/snapshots | python3 -m json.tool
# Note the snapshot filename, e.g.: notulen_chunks-1234567890.snapshot
```

The snapshot is stored in `data/qdrant_storage/collections/notulen_chunks/snapshots/`.

```bash
# Transfer to server
rsync -avz --progress \
  data/qdrant_storage/collections/notulen_chunks/snapshots/ \
  neodemos:/home/deploy/neodemos-data/qdrant_snapshots/
```

Restore on server:

```bash
ssh neodemos

# Get the snapshot filename
SNAPSHOT=$(ls /home/deploy/neodemos-data/qdrant_snapshots/*.snapshot | head -1)

# Restore via Qdrant API (container must be running)
curl -X PUT "http://localhost:6333/collections/notulen_chunks/snapshots/recover" \
  -H "Content-Type: application/json" \
  -d "{\"location\": \"file://$SNAPSHOT\"}"

# Verify
curl -s http://localhost:6333/collections/notulen_chunks | python3 -m json.tool | grep points_count
# Expected: 1630000+ points
```

### Option B: Full storage rsync (simpler, larger transfer)

If snapshots cause issues, copy the entire Qdrant storage directory:

```bash
# Stop Qdrant on Mac first (to ensure consistency)
# Then rsync the full storage
rsync -avz --progress \
  data/qdrant_storage/ \
  neodemos:/home/deploy/neodemos-data/qdrant_storage/

# Restart Qdrant on server to pick up the data
ssh neodemos "docker restart neodemos-qdrant"
```

This transfers ~33 GB — slower but guaranteed to work.

---

## Step 3: Verify Everything

```bash
ssh neodemos

# PostgreSQL
docker exec neodemos-postgres psql -U postgres -d neodemos \
  -c "SELECT count(*) AS total_chunks FROM chunks;"

# Qdrant
curl -s http://localhost:6333/collections/notulen_chunks | python3 -m json.tool | grep -E "points_count|status"

# FastAPI health
curl -s http://localhost:8000/health | python3 -m json.tool

# End-to-end: run a search query
curl -s "http://localhost:8000/api/search?q=begroting+rotterdam" | python3 -m json.tool | head -20
```

---

## Step 4: Post-Migration Cleanup

### On your Mac

Keep your local data as a backup. No cleanup needed.

### On the server

```bash
# Remove the SQL dump
rm -f /home/deploy/neodemos_dump.sql.gz

# Remove snapshot files (already loaded into Qdrant)
rm -rf /home/deploy/neodemos-data/qdrant_snapshots/

# Verify disk usage
df -h
du -sh /home/deploy/neodemos-data/qdrant_storage/
```

---

## Incremental Updates

After initial migration, new data flows in via the auto-ingest pipeline (15-min cycle):

1. FastAPI scheduler queries ORI API for new documents
2. Documents are chunked and stored in PostgreSQL
3. Chunks are embedded via Nebius API (server-side)
4. Vectors are upserted into Qdrant

No manual data transfer needed after the initial migration — the server is self-sustaining.

### Manual sync (if needed)

If you embed new data locally on your Mac and want to push it to the server:

```bash
# Dump only new/changed rows (using timestamp)
pg_dump -U postgres -d neodemos --data-only \
  --table=chunks --table=documents \
  -c "WHERE created_at > '2026-04-08'" | gzip > incremental_dump.sql.gz

scp incremental_dump.sql.gz neodemos:/home/deploy/
ssh neodemos "zcat /home/deploy/incremental_dump.sql.gz | docker exec -i neodemos-postgres psql -U postgres -d neodemos"

# For Qdrant: create and transfer a new snapshot
```

---

## Rollback

If the migration goes wrong:

```bash
# PostgreSQL: drop and re-import
ssh neodemos
docker exec neodemos-postgres psql -U postgres -c "DROP DATABASE neodemos;"
docker exec neodemos-postgres psql -U postgres -c "CREATE DATABASE neodemos;"
# Then re-run the dump import

# Qdrant: delete collection and re-import snapshot
curl -X DELETE http://localhost:6333/collections/notulen_chunks
# Then re-run the snapshot recovery
```

---

## Next Step

[05_OPERATIONS.md](05_OPERATIONS.md) — Monitoring, backups, scaling, and troubleshooting
