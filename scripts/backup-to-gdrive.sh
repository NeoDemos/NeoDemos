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
#  2026-04-15 v4 — Hardening pass:
#                    * Date-first folder layout for Qdrant:
#                        03_Vector_Snapshots/<RUN_STAMP>/<COLLECTION>/<file>
#                      so each run is a self-contained restore bundle and
#                      Drive listing stays legible over time.
#                    * Pre-flight advisory-lock check (pg_advisory_lock 42)
#                      — refuses to snapshot Qdrant if an embedding or
#                      migration job is holding the lock, per CLAUDE.md
#                      "never write to Qdrant/PG while embeds may be live".
#                    * SKIP_COLLECTIONS env var (default: skip smoke test
#                      collection so it doesn't bloat Drive nightly).
#                    * curl --max-time on the snapshot stream so a hung
#                      download fails fast instead of holding the cron.
#                    * Optional HEALTHCHECK_URL (healthchecks.io-style
#                      dead-man's switch). Sends /start at begin and
#                      /success or /fail at end — surfaces silent failures
#                      even when cron's MAILTO isn't wired.
#                    * Automatic retention: deletes files older than
#                      PG_RETENTION_DAYS and QDRANT_RETENTION_DAYS from
#                      Drive (default 30). Keeps the vault lean.

set -euo pipefail

LOG="/home/deploy/backups/gdrive-backup.log"
DATE=$(date +%Y%m%d_%H%M%S)
RUN_STAMP="$DATE"
ENV_FILE="/home/deploy/neodemos/.env"
QDRANT_HOST="http://localhost:6333"
QDRANT_API_KEY=$(grep "^QDRANT_API_KEY=" "$ENV_FILE" | head -1 | cut -d= -f2)

PG_BACKUP_DIR="/home/deploy/backups/postgres"

# Tunables (override via environment / cron line)
SKIP_COLLECTIONS="${SKIP_COLLECTIONS:-smoke_test_notulen_chunks}"
CURL_MAX_TIME="${CURL_MAX_TIME:-14400}"          # 4h cap on snapshot stream
HEALTHCHECK_URL="${HEALTHCHECK_URL:-}"             # e.g. https://hc-ping.com/<uuid>
PG_RETENTION_DAYS="${PG_RETENTION_DAYS:-30}"
QDRANT_RETENTION_DAYS="${QDRANT_RETENTION_DAYS:-30}"

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

# healthcheck PATH — pings $HEALTHCHECK_URL$PATH if configured. Best-effort;
# never fails the backup. Used for start/success/fail markers.
healthcheck() {
    local suffix="$1"
    [ -z "$HEALTHCHECK_URL" ] && return 0
    curl -fsS -m 10 --retry 3 --retry-delay 5 \
        "${HEALTHCHECK_URL}${suffix}" >> "$LOG" 2>&1 || true
}

# embedding_lock_held — returns 0 (true) if pg_advisory_lock(42) is held by
# any backend, which per CLAUDE.md means an embedding/migration job is live
# and we must NOT snapshot Qdrant. Falls back to "not held" on query error
# rather than blocking backups on an unrelated PG hiccup.
embedding_lock_held() {
    local holders
    holders=$(docker exec neodemos-postgres psql -U postgres -tAc \
        "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND objid=42;" \
        2>/dev/null || echo "0")
    [ "$holders" != "0" ]
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
log "  run_stamp:        $RUN_STAMP"
log "  skip_collections: $SKIP_COLLECTIONS"
log "  pg_retention:     $PG_RETENTION_DAYS days"
log "  qdrant_retention: $QDRANT_RETENTION_DAYS days"

healthcheck "/start"

# ---------------------------------------------------------------------------
# 1. PostgreSQL dump
# ---------------------------------------------------------------------------
log "[1/3] PostgreSQL dump"
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
#    Layout: gdrive:NeoDemos/03_Vector_Snapshots/<RUN_STAMP>/<COLLECTION>/<file>
#    One folder per nightly run; each collection gets its own subdir so a
#    single run is a self-contained restore bundle.
# ---------------------------------------------------------------------------
log "[2/3] Qdrant snapshots (run folder: $RUN_STAMP)"

# Preflight: refuse to snapshot if an embedding/migration job holds
# pg_advisory_lock(42). Per CLAUDE.md, writing to Qdrant while embeds are
# live can corrupt segments. Snapshotting is read-only but any concurrent
# write could produce an inconsistent snapshot.
if embedding_lock_held; then
    log "  SKIP: pg_advisory_lock(42) is held — embedding/migration job active"
    QDRANT_STATUS="skipped (embedding lock held)"
    COLLECTIONS=""
else
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
fi

if [ -z "$COLLECTIONS" ] && [ "$QDRANT_STATUS" = "skipped" ]; then
    log "  WARNING: no Qdrant collections found (API unreachable or empty)"
    QDRANT_STATUS="failed (no collections)"
elif [ -n "$COLLECTIONS" ]; then
    QDRANT_OK_COUNT=0
    QDRANT_FAIL_COUNT=0
    QDRANT_SKIP_COUNT=0

    for COLLECTION in $COLLECTIONS; do
        # Skip-list check (space-separated SKIP_COLLECTIONS)
        if echo " $SKIP_COLLECTIONS " | grep -q " $COLLECTION "; then
            log "  collection: $COLLECTION — SKIP (in SKIP_COLLECTIONS)"
            QDRANT_SKIP_COUNT=$((QDRANT_SKIP_COUNT + 1))
            continue
        fi

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

        # Destination path: date-first, then per-collection subdir.
        # Example: gdrive:NeoDemos/03_Vector_Snapshots/20260415_000000/notulen_chunks/<snapshot>
        DEST_DIR="gdrive:NeoDemos/03_Vector_Snapshots/$RUN_STAMP/$COLLECTION"
        DEST_PATH="$DEST_DIR/$SNAP_NAME"

        # Stream: curl fetches the snapshot from Qdrant and writes to stdout,
        # rclone rcat reads stdin and uploads as the destination filename.
        # No local file. No disk-full risk. No partial-file orphan risk.
        # curl --max-time bounds the full transfer — a hung download fails
        # fast (default 4h) instead of holding the cron indefinitely.
        # PIPE_RC captures both exit codes before set -e is restored to avoid
        # the "PIPESTATUS[1]: unbound variable" bug in bash with set -u.
        set +e
        curl -sS --max-time "$CURL_MAX_TIME" \
            -H "api-key: $QDRANT_API_KEY" \
            "$QDRANT_HOST/collections/$COLLECTION/snapshots/$SNAP_NAME" 2>> "$LOG" \
            | rclone rcat "$DEST_PATH" \
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
            REMOTE_SIZE=$(rclone size "$DEST_PATH" --json 2>> "$LOG" \
                | python3 -c 'import sys,json;
try: print(json.load(sys.stdin).get("bytes",0))
except Exception: print(0)' 2>> "$LOG" || echo "0")

            if [ "$SNAP_SIZE" -gt 0 ] && [ "$REMOTE_SIZE" != "$SNAP_SIZE" ]; then
                log "  ERROR: size mismatch — expected $SNAP_SIZE, remote has $REMOTE_SIZE"
                QDRANT_FAIL_COUNT=$((QDRANT_FAIL_COUNT + 1))
            else
                log "  uploaded to $DEST_PATH ($REMOTE_SIZE bytes)"
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
        QDRANT_STATUS="ok ($QDRANT_OK_COUNT collection(s), $QDRANT_SKIP_COUNT skipped)"
    else
        QDRANT_STATUS="partial ($QDRANT_OK_COUNT ok, $QDRANT_FAIL_COUNT failed, $QDRANT_SKIP_COUNT skipped)"
    fi
fi

# ---------------------------------------------------------------------------
# 3. Retention — prune Drive of backups older than N days.
#    Uses rclone --min-age (mod-time based). Snapshots are immutable after
#    upload, so mod-time == creation time. Errors here never fail the run;
#    retention is best-effort.
# ---------------------------------------------------------------------------
log "[3/3] Retention cleanup"

log "  pruning PostgreSQL dumps older than $PG_RETENTION_DAYS days"
rclone delete "gdrive:NeoDemos/02_Database_Vault/" \
    --min-age "${PG_RETENTION_DAYS}d" \
    --log-file="$LOG" --log-level INFO 2>> "$LOG" || \
    log "  WARNING: PG retention prune failed (non-fatal)"

log "  pruning Qdrant snapshots older than $QDRANT_RETENTION_DAYS days"
rclone delete "gdrive:NeoDemos/03_Vector_Snapshots/" \
    --min-age "${QDRANT_RETENTION_DAYS}d" \
    --log-file="$LOG" --log-level INFO 2>> "$LOG" || \
    log "  WARNING: Qdrant retention prune failed (non-fatal)"

# Remove now-empty date folders left behind by --min-age deletes
rclone rmdirs "gdrive:NeoDemos/03_Vector_Snapshots/" --leave-root \
    --log-file="$LOG" 2>> "$LOG" || \
    log "  WARNING: rmdirs on Qdrant vault failed (non-fatal)"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
log "==================== Backup summary ===================="
log "  PostgreSQL: $PG_STATUS"
log "  Qdrant:     $QDRANT_STATUS"
log "==================== Done ===================="

# Exit non-zero if anything failed — so cron's MAILTO (if set) surfaces it.
case "$PG_STATUS $QDRANT_STATUS" in
    *failed*|*partial*)
        healthcheck "/fail"
        exit 1
        ;;
    *)
        healthcheck ""
        exit 0
        ;;
esac
