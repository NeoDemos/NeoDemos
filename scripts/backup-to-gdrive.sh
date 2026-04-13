#!/bin/bash
# NeoDemos nightly backup — runs on Hetzner via user crontab at 00:00 CET.
#
# CANONICAL LOCATION: /home/deploy/backup-to-gdrive.sh on the server.
# This repo copy exists for version tracking — after editing here, scp to the
# server. The server-side file is what the crontab actually executes.
#
# Backs up to Google Drive via rclone:
#   PostgreSQL → gdrive:NeoDemos/02_Database_Vault/
#   Qdrant     → gdrive:NeoDemos/03_Vector_Snapshots/<collection>/
#
# Git is backed up via the GitHub remote (origin) — no server-side bundle.
# The /home/deploy/neodemos directory is a deploy target, not a git checkout.
#
# Zero-downtime: pg_dump takes a consistent snapshot against a live DB via
# a transaction; Qdrant's snapshot API is consistent without blocking reads
# or writes. This script never stops any service.
#
# History:
#  2026-04-11 v1 — rewrote Qdrant branch to use HTTP download instead of
#                  reading snapshots from the host filesystem (the previous
#                  path never matched because Qdrant writes snapshots to
#                  /qdrant/snapshots/ inside the container, and only
#                  /qdrant/storage is bind-mounted).
#  2026-04-11 v2 — Qdrant snapshots now stream directly from curl to rclone
#                  (no local disk buffer). The v1 approach wrote a 38GB
#                  local file and failed with curl error 23 at ~90% for
#                  unclear reasons. Streaming via `rclone rcat` eliminates
#                  the local disk pressure, removes any risk of a partial
#                  file being left on disk, and is faster end-to-end.
#                  Also dropped the git bundle branch — the server has no
#                  .git directory; git is already versioned on GitHub.
#  2026-04-13 v3 — Retry logic throughout: wait_for_postgres() handles the
#                  "recovery mode" failure that broke the April 13 run after
#                  the Docker volume migration. retry_cmd() wraps pg_dump
#                  and Qdrant snapshot creation. rclone gets --retries 5.
#                  Fixed PIPESTATUS[1]: unbound variable bash bug.

set -euo pipefail

LOG="/home/deploy/backups/gdrive-backup.log"
DATE=$(date +%Y%m%d_%H%M%S)
ENV_FILE="/home/deploy/neodemos/.env"
QDRANT_HOST="http://localhost:6333"
QDRANT_API_KEY=$(grep "^QDRANT_API_KEY=" "$ENV_FILE" | head -1 | cut -d= -f2)

PG_BACKUP_DIR="/home/deploy/backups/postgres"

mkdir -p "$PG_BACKUP_DIR"

PG_STATUS="skipped"
QDRANT_STATUS="skipped"

log() {
    echo "[$DATE] $*" >> "$LOG"
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# retry_cmd MAX_ATTEMPTS SLEEP_SECS CMD [ARGS...]
# Runs CMD up to MAX_ATTEMPTS times, sleeping SLEEP_SECS between failures.
# Returns 0 on first success, last non-zero exit code if all attempts fail.
retry_cmd() {
    local max=$1 sleep_s=$2; shift 2
    local attempt=1 rc=0
    while [ $attempt -le $max ]; do
        set +e
        "$@"
        rc=$?
        set -e
        [ $rc -eq 0 ] && return 0
        if [ $attempt -lt $max ]; then
            log "  RETRY: attempt $attempt/$max failed (rc=$rc), retrying in ${sleep_s}s..."
            sleep $sleep_s
        fi
        attempt=$((attempt + 1))
    done
    log "  RETRY: all $max attempts failed (last rc=$rc)"
    return $rc
}

# wait_for_postgres MAX_WAIT_SECS
# Blocks until Postgres is a writable primary (not in recovery mode).
# Handles the window after a Docker volume migration where the DB briefly
# reports "recovery mode" before becoming healthy.
wait_for_postgres() {
    local max_wait=${1:-300} waited=0
    while [ $waited -lt $max_wait ]; do
        local state
        state=$(docker exec neodemos-postgres psql -U postgres -tAc \
            "SELECT pg_is_in_recovery();" 2>/dev/null || echo "error")
        case "$state" in
            f)   return 0 ;;  # primary, ready
            t)   log "  Postgres in recovery mode, waiting (${waited}s / ${max_wait}s)..." ;;
            *)   log "  Postgres not reachable yet (${waited}s / ${max_wait}s)..." ;;
        esac
        sleep 30
        waited=$((waited + 30))
    done
    log "  ERROR: Postgres did not become primary after ${max_wait}s"
    return 1
}

log "==================== Starting backup ===================="

# ---------------------------------------------------------------------------
# 1. PostgreSQL dump
# ---------------------------------------------------------------------------
log "[1/2] PostgreSQL dump"
PGDUMP="$PG_BACKUP_DIR/neodemos_$DATE.sql.gz"

# Wait up to 5 min for Postgres to be writable (handles post-migration recovery)
set +e
wait_for_postgres 300
PG_WAIT_RC=$?
set -e

if [ $PG_WAIT_RC -ne 0 ]; then
    PG_STATUS="failed (postgres not ready)"
else
    # pg_dump with up to 3 attempts — transient connection errors are retried
    _do_pgdump() {
        docker exec neodemos-postgres pg_dump -U postgres neodemos 2>> "$LOG" \
            | gzip > "$PGDUMP"
        # Capture the pg_dump exit code, not gzip's
        local PIPE_RC=("${PIPESTATUS[@]}")
        return ${PIPE_RC[0]}
    }

    set +e
    retry_cmd 3 60 _do_pgdump
    PG_RC=$?
    set -e

    if [ $PG_RC -ne 0 ]; then
        log "  ERROR: pg_dump failed after retries (rc=$PG_RC)"
        rm -f "$PGDUMP"
        PG_STATUS="failed (pg_dump rc=$PG_RC)"
    else
        FILESIZE=$(stat -c%s "$PGDUMP" 2>/dev/null || echo "0")
        if [ "$FILESIZE" -lt 1000 ]; then
            log "  ERROR: dump suspiciously small ($FILESIZE bytes)"
            rm -f "$PGDUMP"
            PG_STATUS="failed (empty dump: $FILESIZE bytes)"
        else
            log "  dumped: $PGDUMP ($FILESIZE bytes)"
            if rclone copy "$PGDUMP" gdrive:NeoDemos/02_Database_Vault/ \
                --retries 5 --retries-sleep 30s \
                --log-file="$LOG" --log-level INFO 2>> "$LOG"; then
                log "  uploaded to gdrive:NeoDemos/02_Database_Vault/"
                PG_STATUS="ok ($FILESIZE bytes)"
                # Keep last 7 local dumps
                find "$PG_BACKUP_DIR" -name "neodemos_*.sql.gz" -mtime +7 -delete 2>> "$LOG" || true
            else
                log "  ERROR: rclone upload failed"
                PG_STATUS="failed (rclone upload)"
            fi
        fi
    fi
fi

# ---------------------------------------------------------------------------
# 2. Qdrant snapshots — stream curl → rclone rcat (no local disk buffer)
# ---------------------------------------------------------------------------
log "[2/2] Qdrant snapshots"

# Discover collections
COLLECTIONS_JSON=$(curl -s -H "api-key: $QDRANT_API_KEY" "$QDRANT_HOST/collections" 2>> "$LOG" || echo "")
COLLECTIONS=$(echo "$COLLECTIONS_JSON" | python3 -c 'import sys, json;
try:
    data = json.load(sys.stdin)
    for c in data.get("result", {}).get("collections", []):
        print(c["name"])
except Exception as e:
    sys.stderr.write(f"parse error: {e}\n")
    sys.exit(1)
' 2>> "$LOG" || echo "")

if [ -z "$COLLECTIONS" ]; then
    log "  WARNING: no Qdrant collections found (API unreachable or empty)"
    QDRANT_STATUS="failed (no collections)"
else
    QDRANT_OK_COUNT=0
    QDRANT_FAIL_COUNT=0

    for COLLECTION in $COLLECTIONS; do
        log "  collection: $COLLECTION — creating snapshot..."

        # Create snapshot with up to 3 attempts (Qdrant can be temporarily busy
        # or return an internal error while rebuilding segments)
        SNAP_RESPONSE=""
        _do_snap_create() {
            SNAP_RESPONSE=$(curl -sf -X POST -H "api-key: $QDRANT_API_KEY" \
                "$QDRANT_HOST/collections/$COLLECTION/snapshots" 2>> "$LOG") || return 1
        }

        set +e
        retry_cmd 3 60 _do_snap_create
        SNAP_RC=$?
        set -e

        SNAP_NAME=$(echo "$SNAP_RESPONSE" | python3 -c 'import sys, json;
try:
    print(json.load(sys.stdin).get("result", {}).get("name", ""))
except Exception:
    pass
' 2>> "$LOG" || echo "")
        SNAP_SIZE=$(echo "$SNAP_RESPONSE" | python3 -c 'import sys, json;
try:
    print(json.load(sys.stdin).get("result", {}).get("size", 0))
except Exception:
    print(0)
' 2>> "$LOG" || echo "0")

        if [ $SNAP_RC -ne 0 ] || [ -z "$SNAP_NAME" ]; then
            log "  ERROR: snapshot creation failed for $COLLECTION after retries (response: $SNAP_RESPONSE)"
            QDRANT_FAIL_COUNT=$((QDRANT_FAIL_COUNT + 1))
            continue
        fi

        log "  created: $SNAP_NAME ($SNAP_SIZE bytes)"

        # Stream: curl fetches the snapshot from Qdrant and writes to stdout,
        # rclone rcat reads stdin and uploads as the destination filename.
        # No local file. No disk-full risk. No partial-file orphan risk.
        # PIPE_RC captures both exit codes before set -e is restored to avoid
        # the "PIPESTATUS[1]: unbound variable" bug in bash with set -u.
        set +e
        curl -sS -H "api-key: $QDRANT_API_KEY" \
            "$QDRANT_HOST/collections/$COLLECTION/snapshots/$SNAP_NAME" 2>> "$LOG" \
            | rclone rcat \
                "gdrive:NeoDemos/03_Vector_Snapshots/$SNAP_NAME" \
                --retries 5 --retries-sleep 30s \
                --log-file="$LOG" --log-level INFO 2>> "$LOG"
        PIPE_RC=("${PIPESTATUS[@]}")
        set -e

        CURL_RC=${PIPE_RC[0]:-0}
        RCLONE_RC=${PIPE_RC[1]:-0}

        if [ "$CURL_RC" -ne 0 ]; then
            log "  ERROR: curl download of $SNAP_NAME failed (rc=$CURL_RC)"
            QDRANT_FAIL_COUNT=$((QDRANT_FAIL_COUNT + 1))
        elif [ "$RCLONE_RC" -ne 0 ]; then
            log "  ERROR: rclone upload of $SNAP_NAME failed (rc=$RCLONE_RC)"
            QDRANT_FAIL_COUNT=$((QDRANT_FAIL_COUNT + 1))
        else
            # Verify the upload actually landed — rclone rcat can return 0
            # on a truncated input without error. Size check vs expected.
            REMOTE_SIZE=$(rclone size \
                "gdrive:NeoDemos/03_Vector_Snapshots/$SNAP_NAME" \
                --json 2>> "$LOG" | python3 -c 'import sys,json;
try: print(json.load(sys.stdin).get("bytes",0))
except Exception: print(0)' 2>> "$LOG" || echo "0")

            if [ "$SNAP_SIZE" -gt 0 ] && [ "$REMOTE_SIZE" != "$SNAP_SIZE" ]; then
                log "  ERROR: size mismatch — expected $SNAP_SIZE, remote has $REMOTE_SIZE"
                QDRANT_FAIL_COUNT=$((QDRANT_FAIL_COUNT + 1))
            else
                log "  uploaded to gdrive:NeoDemos/03_Vector_Snapshots/ ($REMOTE_SIZE bytes)"
                QDRANT_OK_COUNT=$((QDRANT_OK_COUNT + 1))
            fi
        fi

        # Always delete the server-side snapshot after the attempt to avoid
        # Qdrant container disk bloat. Runs even on failure so a bad run
        # doesn't pile up snapshots.
        curl -s -X DELETE -H "api-key: $QDRANT_API_KEY" \
            "$QDRANT_HOST/collections/$COLLECTION/snapshots/$SNAP_NAME" \
            >> "$LOG" 2>&1 || log "  WARNING: snapshot cleanup failed for $SNAP_NAME"
    done

    if [ "$QDRANT_FAIL_COUNT" -eq 0 ]; then
        QDRANT_STATUS="ok ($QDRANT_OK_COUNT collection(s))"
    else
        QDRANT_STATUS="partial ($QDRANT_OK_COUNT ok, $QDRANT_FAIL_COUNT failed)"
    fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
log "==================== Backup summary ===================="
log "  PostgreSQL: $PG_STATUS"
log "  Qdrant:     $QDRANT_STATUS"
log "==================== Done ===================="

# Exit non-zero if anything failed — so cron's MAILTO (if set) surfaces it.
case "$PG_STATUS $QDRANT_STATUS" in
    *failed*|*partial*) exit 1 ;;
    *) exit 0 ;;
esac
