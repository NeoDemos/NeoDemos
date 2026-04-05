# NeoDemos Backup & Recovery — Skill Reference

Use this skill when backing up, restoring, or managing data retention. For service orchestration see `/deploy`; for secrets/encryption-at-rest see `/secure`.

## What to Back Up

| Layer | Tool | Destination | Frequency |
|-------|------|-------------|-----------|
| **Code + config** | `git push` | GitHub (private) | Every meaningful change |
| **PostgreSQL** | `pg_dump` | Google Drive (`gdrive:neodemos-backups/`) | Daily |
| **Qdrant vectors** | Snapshot API | Google Drive (`gdrive:neodemos-backups/`) | Weekly + before migrations |
| **Uploaded/generated files** | `rclone sync` | Google Drive (`gdrive:neodemos-backups/`) | Daily |
| **.env / secrets** | Manual export | Password manager (1Password/Bitwarden) | On change |

**Not backed up (reproducible):** `.venv/`, `__pycache__/`, `downloads/`, `logs/`, Docker images.

## 1. Git (Code Layer)

```bash
# Standard push (upstream already set)
cd "/Users/dennistak/Documents/Final Frontier/NeoDemos"
git add -A && git commit -m "description" && git push

# Verify remote is current
git log --oneline origin/main..HEAD   # Should be empty after push
```

**Rule:** Never commit `.env`, `*.xlsx`, PID files, or `tmp_*` — already in `.gitignore`.

## 2. PostgreSQL Backup

```bash
# Full dump (compressed)
pg_dump -U postgres neodemos | gzip > ~/backups/neodemos_pg_$(date +%Y%m%d_%H%M).sql.gz

# Schema only (lightweight, for version tracking)
pg_dump -U postgres --schema-only neodemos > ~/backups/neodemos_schema_$(date +%Y%m%d).sql

# Restore
gunzip -c ~/backups/neodemos_pg_YYYYMMDD_HHMM.sql.gz | psql -U postgres neodemos

# Restore to fresh database
createdb -U postgres neodemos_restored
gunzip -c ~/backups/neodemos_pg_YYYYMMDD_HHMM.sql.gz | psql -U postgres neodemos_restored
```

**Size estimate:** ~200-500 MB compressed (meetings + documents + chunks with tsvector).

## 3. Qdrant Backup

```bash
# Create snapshot (607K+ points, ~2-4 GB)
curl -X POST http://localhost:6333/collections/notulen_chunks/snapshots

# List snapshots
curl http://localhost:6333/collections/notulen_chunks/snapshots

# Download a specific snapshot
curl -o ~/backups/qdrant_notulen_$(date +%Y%m%d).snapshot \
  http://localhost:6333/collections/notulen_chunks/snapshots/<snapshot_name>

# Restore (CAUTION: overwrites existing collection)
curl -X PUT "http://localhost:6333/collections/notulen_chunks/snapshots/recover" \
  -H "Content-Type: application/json" \
  -d '{"location": "file:///path/to/snapshot.snapshot"}'
```

**Important:** Never create Qdrant snapshots while `migrate_embeddings.py` or any embedding script is running — can corrupt segments. Check first:
```bash
ps aux | grep -E "(migrate_embed|surgical_embed|chunk_unchunked)" | grep -v grep
```

## 4. rclone to Google Drive (Encrypted)

### Initial setup (one-time)

```bash
# 1. Configure Google Drive remote
rclone config
# → n (new) → name: gdrive → type: drive → follow OAuth flow

# 2. Add encryption layer on top
rclone config
# → n (new) → name: gdrive-crypt → type: crypt
# → remote: gdrive:neodemos-backups   (wraps the gdrive remote)
# → filename encryption: standard
# → directory name encryption: true
# → Set password (SAVE THIS — without it, backups are unrecoverable)
# → Set salt (SAVE THIS too)
```

**Store rclone passwords in a password manager, not in plaintext.** The rclone config file lives at `~/.config/rclone/rclone.conf` — back this up separately to your password manager.

### Sync commands

```bash
# Upload PostgreSQL + Qdrant backups
rclone sync ~/backups/ gdrive:neodemos-backups/ --progress

# Verify
rclone ls gdrive:neodemos-backups/

# Restore from backup
rclone copy gdrive:neodemos-backups/neodemos_pg_20260405_1100.sql.gz ~/restore/
```

### Optional: add encryption layer
To encrypt backups at rest on Google Drive, add a `gdrive-crypt` remote wrapping `gdrive:neodemos-backups/`:
```bash
rclone config  # → new → name: gdrive-crypt → type: crypt → remote: gdrive:neodemos-backups
```
Then replace `gdrive:neodemos-backups/` with `gdrive-crypt:` in all commands above. Store the encryption password in a password manager — without it, backups are unrecoverable.

## 5. Combined Backup Script

Save as `scripts/backup.sh`:

```bash
#!/bin/bash
set -euo pipefail

BACKUP_DIR="$HOME/backups"
DATE=$(date +%Y%m%d_%H%M)
PROJECT="/Users/dennistak/Documents/Final Frontier/NeoDemos"

mkdir -p "$BACKUP_DIR"

echo "=== NeoDemos Backup — $DATE ==="

# 1. Git push
echo "[1/4] Git push..."
cd "$PROJECT"
if [ -n "$(git status --porcelain)" ]; then
    echo "  WARNING: Uncommitted changes exist. Skipping git push."
    echo "  Run: git add -A && git commit -m 'description' && git push"
else
    git push 2>/dev/null && echo "  Pushed to origin." || echo "  Already up to date."
fi

# 2. PostgreSQL
echo "[2/4] PostgreSQL dump..."
/opt/homebrew/Cellar/postgresql@16/16.13/bin/pg_dump -U postgres neodemos | gzip > "$BACKUP_DIR/neodemos_pg_${DATE}.sql.gz"
echo "  Saved: neodemos_pg_${DATE}.sql.gz ($(du -h "$BACKUP_DIR/neodemos_pg_${DATE}.sql.gz" | cut -f1))"

# 3. Qdrant snapshot
echo "[3/4] Qdrant snapshot..."
if ps aux | grep -E "(migrate_embed|surgical_embed)" | grep -v grep > /dev/null; then
    echo "  SKIPPED: Embedding process running. Qdrant snapshot unsafe."
else
    SNAP=$(curl -s -X POST http://localhost:6333/collections/notulen_chunks/snapshots | python3 -c "import sys,json; print(json.load(sys.stdin)['result']['name'])" 2>/dev/null)
    if [ -n "$SNAP" ]; then
        curl -s -o "$BACKUP_DIR/qdrant_notulen_${DATE}.snapshot" \
          "http://localhost:6333/collections/notulen_chunks/snapshots/$SNAP"
        echo "  Saved: qdrant_notulen_${DATE}.snapshot ($(du -h "$BACKUP_DIR/qdrant_notulen_${DATE}.snapshot" | cut -f1))"
    else
        echo "  FAILED: Could not create Qdrant snapshot. Is Qdrant running?"
    fi
fi

# 4. rclone upload
echo "[4/4] Uploading to Google Drive..."
if rclone listremotes | grep -q "gdrive:"; then
    rclone sync "$BACKUP_DIR/" gdrive:neodemos-backups/ --progress --transfers=4
    echo "  Uploaded to gdrive:neodemos-backups/"
else
    echo "  SKIPPED: gdrive remote not configured. Run: rclone config"
fi

echo "=== Backup complete ==="
```

```bash
chmod +x scripts/backup.sh
```

## 6. Retention Policy

Keep backups lean — storage is cheap, but clutter makes restores slow.

| Type | Keep | Cleanup command |
|------|------|-----------------|
| PostgreSQL daily | 7 days | `find ~/backups -name "neodemos_pg_*.sql.gz" -mtime +7 -delete` |
| PostgreSQL weekly | 4 weeks | Keep every Monday's backup manually |
| Qdrant snapshots | 3 most recent | `ls -t ~/backups/qdrant_* \| tail -n +4 \| xargs rm -f` |
| Google Drive | 30 days | Configure via rclone `--max-age 30d` on cleanup runs |

Add cleanup to the backup script or run separately:
```bash
# Prune old local backups (keep 7 days)
find ~/backups -name "neodemos_pg_*.sql.gz" -mtime +7 -delete
find ~/backups -name "qdrant_*.snapshot" -mtime +21 -delete
```

## 7. Backup Verification

A backup you've never tested is not a backup.

```bash
# Verify PostgreSQL backup integrity (dry-run restore)
gunzip -t ~/backups/neodemos_pg_YYYYMMDD_HHMM.sql.gz && echo "OK" || echo "CORRUPT"

# Verify rclone encrypted backup (check file count matches)
LOCAL=$(ls ~/backups/ | wc -l)
REMOTE=$(rclone ls gdrive:neodemos-backups/ | wc -l)
echo "Local: $LOCAL files, Remote: $REMOTE files"

# Full restore test (to separate database, quarterly)
createdb -U postgres neodemos_test_restore
gunzip -c ~/backups/neodemos_pg_LATEST.sql.gz | psql -U postgres neodemos_test_restore
psql -U postgres neodemos_test_restore -c "SELECT count(*) FROM meetings;"
dropdb -U postgres neodemos_test_restore
```

## 8. Disaster Recovery Quick Reference

| Scenario | Steps |
|----------|-------|
| **Code lost** | `git clone https://github.com/NeoDemos/NeoDemos.git` |
| **PostgreSQL corrupted** | Restore latest `pg_dump`: `gunzip -c backup.sql.gz \| psql -U postgres neodemos` |
| **Qdrant corrupted** | Restore snapshot via PUT `/snapshots/recover` endpoint |
| **Mac stolen/dead** | New Mac → clone repo → restore `.env` from password manager → restore DB + Qdrant from Drive → `rclone copy gdrive:neodemos-backups/ ~/backups/` |
| **Google Drive compromised** | Backups are encrypted — attacker gets ciphertext only. Rotate rclone password, re-encrypt. |
| **rclone password lost** | **Unrecoverable.** This is why it must be in a password manager. |