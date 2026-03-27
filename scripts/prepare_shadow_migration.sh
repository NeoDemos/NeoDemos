#!/bin/bash

# NeoDemos Shadow PC Migration Preparation Script
# This script dumps the database and zips the vector storage for transport.

# 1. Load Environment Variables
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
else
    echo "❌ .env file not found. Please run this from the project root."
    exit 1
fi

MIGRATION_DIR="migration_$(date +%Y%m%d)"
mkdir -p "$MIGRATION_DIR"

echo "🚀 Starting Migration Preparation..."

# 2. Dump Postgres Database
echo "📦 Dumping Postgres database: $DB_NAME..."
# Using pg_dump with credentials from .env
# Assuming pg_dump is available in the path
PGPASSWORD=$DB_PASSWORD /opt/homebrew/Cellar/postgresql@16/16.13/bin/pg_dump -h $DB_HOST -p $DB_PORT -U $DB_USER $DB_NAME > "$MIGRATION_DIR/neodemos_db_backup.sql"

if [ $? -eq 0 ]; then
    echo "✅ Database dump complete: $MIGRATION_DIR/neodemos_db_backup.sql"
else
    echo "❌ Database dump failed."
    exit 1
fi


# 3. Compress Qdrant Storage
echo "📂 Compressing Qdrant Storage (~17GB)... This may take a while."
# We use tar with gzip for compression
# We exclude qdrant_bin and other non-data files if any
tar -czf "$MIGRATION_DIR/qdrant_storage.tar.gz" -C data qdrant_storage

if [ $? -eq 0 ]; then
    echo "✅ Qdrant storage compressed: $MIGRATION_DIR/qdrant_storage.tar.gz"
else
    echo "❌ Compression failed."
    exit 1
fi

# 4. Zip Codebase (excluding large logs and venv)
echo "💻 Zipping codebase (excluding venv and large logs)..."
zip -r "$MIGRATION_DIR/neodemos_codebase.zip" . -x "*.venv*" "*node_modules*" "*.git*" "*.log*" "data/*" "$MIGRATION_DIR/*"

echo "✨ Migration Preparation Complete!"
echo "------------------------------------------------"
echo "Project data found in: $MIGRATION_DIR"
echo "Next step: Run 'rclone copy $MIGRATION_DIR gdrive:NeoDemos/$MIGRATION_DIR --progress'"
echo "------------------------------------------------"
