#!/bin/bash
set -euo pipefail

PROJECT="/Users/dennistak/Documents/Final Frontier/NeoDemos"
BACKUP_DIR="$HOME/backups"
DATE=$(date +%Y%m%d_%H%M)
PG_DUMP="/opt/homebrew/Cellar/postgresql@16/16.13/bin/pg_dump"

mkdir -p "$BACKUP_DIR"

echo "=== Waiting for embedding to finish ==="
while ps aux | grep migrate_embeddings | grep -v grep > /dev/null 2>&1; do
    PROGRESS=$(tail -1 "$PROJECT/logs/"embedding_recovery_*.log 2>/dev/null | grep -oE '[0-9]+/[0-9]+' | tail -1)
    echo "  $(date '+%H:%M:%S') — Embedding running... ${PROGRESS:-unknown}"
    sleep 300
done
echo "  Embedding process finished at $(date '+%H:%M:%S')"

echo ""
echo "=== [1/3] Git commit & push ==="
cd "$PROJECT"
git add -A
if [ -n "$(git status --porcelain)" ]; then
    git commit -m "Post-embedding backup: 874 UUID docs recovered, chunked, and embedded

- OCR'd 868 previously empty UUID iBabs stubs via portal URL pattern
- Chunked 873 docs (1,629,768 total chunks)
- Embedded 27,758 new chunks to Qdrant
- Updated RefreshService with UUID URL fallback
- Updated /ingest skill with recovery playbook

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
    git push && echo "  Pushed to origin." || echo "  Push failed — check remote."
else
    echo "  No changes to commit."
    git push 2>/dev/null && echo "  Pushed to origin." || echo "  Already up to date."
fi

echo ""
echo "=== [2/3] PostgreSQL backup ==="
"$PG_DUMP" -U postgres neodemos | gzip > "$BACKUP_DIR/neodemos_pg_${DATE}.sql.gz"
SIZE=$(du -h "$BACKUP_DIR/neodemos_pg_${DATE}.sql.gz" | cut -f1)
echo "  Saved: neodemos_pg_${DATE}.sql.gz ($SIZE)"

echo ""
echo "=== [3/3] Upload to Google Drive ==="
if rclone listremotes | grep -q "gdrive:"; then
    rclone copy "$BACKUP_DIR/neodemos_pg_${DATE}.sql.gz" gdrive:02_Database_Vault/ --progress
    echo "  Uploaded pg dump to gdrive:02_Database_Vault/"
else
    echo "  SKIPPED: gdrive remote not configured."
fi

echo ""
echo "=== Backup complete at $(date '+%H:%M:%S') ==="

# Prune old local backups (keep 7 days)
find "$BACKUP_DIR" -name "neodemos_pg_*.sql.gz" -mtime +7 -delete 2>/dev/null || true
