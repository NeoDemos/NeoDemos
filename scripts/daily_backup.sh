#!/bin/bash
set -euo pipefail
# Backup script for NeoDemos Project
# Targets: Postgres (SQL), Qdrant (Vectors), Code (Git)

TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_DIR="/tmp/neodemos_backup_$TIMESTAMP"
REMOTE_NAME="gdrive" # Ensure this matches rclone config
REMOTE_PATH="NeoDemos_Backups/$TIMESTAMP"

echo "🚀 Starting Daily Backup: $TIMESTAMP"
mkdir -p "$BACKUP_DIR"

# 1. Postgres Backup
echo "🐘 Dumping Postgres DB..."
/opt/homebrew/Cellar/postgresql@16/16.13/bin/pg_dump postgresql://postgres:postgres@localhost:5432/neodemos > "$BACKUP_DIR/neodemos_db.sql"

# 2. Qdrant Backup (Metadata and Snapshots)
echo "📦 Creating Qdrant Snapshot..."
# Trigger snapshot and capture name
SNAPSHOT_NAME=$(curl -s -X POST "http://localhost:6333/collections/notulen_chunks/snapshots" | python3 -c "import sys, json; print(json.load(sys.stdin)['result']['name'])")
if [ ! -z "$SNAPSHOT_NAME" ]; then
    echo "⬇️ Downloading snapshot: $SNAPSHOT_NAME"
    curl -o "$BACKUP_DIR/$SNAPSHOT_NAME" "http://localhost:6333/collections/notulen_chunks/snapshots/$SNAPSHOT_NAME"
fi

# 3. Code Backup (Git & Tar)
echo "💻 Packaging Code..."
tar -czf "$BACKUP_DIR/code_backup.tar.gz" --exclude=".venv" --exclude="__pycache__" --exclude="data" --exclude=".git" .

echo "🐙 Pushing to GitHub..."
git add -u
git commit -m "System restoration, Qdrant migration, and search stability fixes - $TIMESTAMP"
git push origin main

# 4. Upload to Google Drive via rclone
echo "☁️ Uploading to Google Drive..."
rclone copy "$BACKUP_DIR" "$REMOTE_NAME:$REMOTE_PATH"

echo "✅ Backup Complete and Uploaded to $REMOTE_PATH"
rm -rf "$BACKUP_DIR"
