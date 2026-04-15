#!/bin/bash
# NeoDemos local-machine backup — runs on the Mac via launchd, daily.
#
# CANONICAL LOCATION: this file, in the repo.
# Launchd plist:      ~/Library/LaunchAgents/com.neodemos.backup-local.plist
# Log:                ~/Library/Logs/neodemos-backup.log
#
# WHY THIS EXISTS:
#   Some data only lives on the Mac — Whisper transcripts, LLM-processed
#   staging cache, WS1 knowledge-graph NER outputs, hand-curated financial
#   configs. The Hetzner cron backs up PG + Qdrant but not these. If the
#   Mac dies without this script, re-generating them costs days and real
#   money in LLM/GPU spend.
#
# WHAT IS SYNCED (→ gdrive:NeoDemos/04_Source_Sync/...):
#   output/transcripts/                     → transcripts/
#   output/transcripts/staging_cache/       → staging_cache/
#   data/knowledge_graph/                   → knowledge_graph/
#   data/financial/                         → financial_configs/
#   data/lexicons/                          → lexicons/
#   config/                                 → config/
#   data/ibabs_2026_mapping.json +
#   data/municipalities_index.json +
#   data/audio_recovery/recovery_checkpoint.sqlite  → misc/
#
# WHAT IS NOT SYNCED (intentionally — reproducible or too large):
#   .venv/, node_modules/, __pycache__/
#   snapshots/ (35GB local Qdrant snapshots; prod backs up Hetzner's)
#   data/qdrant_storage/ (5.1GB local dev Qdrant)
#   data/db_backup_csv/ (already covered by prod PG dump)
#   data/financial_pdfs/ (8GB, re-downloadable from watdoetdegemeente)
#   data/pipeline_state/, data/debug_outputs/, data/corrupted_segments_backup/
#   data/demo_cache.json
#
# DELETION SAFETY:
#   rclone sync is used with --backup-dir, so anything deleted locally is
#   moved to gdrive:NeoDemos/04_Source_Sync/_deleted/<YYYYMMDD>/<label>/
#   and kept for DELETED_RETENTION_DAYS (30) before being pruned. An
#   accidental `rm -rf` on the Mac will NOT nuke the Drive copy the same
#   night — you have a 30-day window to recover.
#
# FIRST RUN:
#   Do a dry-run first to see what will happen:
#     DRY_RUN=1 ./scripts/backup-local-to-gdrive.sh
#     tail -100 ~/Library/Logs/neodemos-backup.log
#   Then a live run; then install the launchd plist.

set -euo pipefail

PROJECT="/Users/dennistak/Documents/Final Frontier/NeoDemos"
LOG_DIR="$HOME/Library/Logs"
LOG="$LOG_DIR/neodemos-backup.log"
DATE=$(date +%Y%m%d_%H%M%S)
DATE_DAY=$(date +%Y%m%d)

# Tunables (override via environment / launchd plist EnvironmentVariables)
DRY_RUN="${DRY_RUN:-0}"
HEALTHCHECK_URL="${HEALTHCHECK_URL:-}"
DELETED_RETENTION_DAYS="${DELETED_RETENTION_DAYS:-30}"

REMOTE_BASE="gdrive:NeoDemos/04_Source_Sync"
DELETED_BASE="$REMOTE_BASE/_deleted/$DATE_DAY"

RCLONE_DRY=""
if [ "$DRY_RUN" = "1" ]; then
    RCLONE_DRY="--dry-run"
fi

mkdir -p "$LOG_DIR"

OK_COUNT=0
FAIL_COUNT=0

log() {
    echo "[$DATE] $*" >> "$LOG"
}

healthcheck() {
    local suffix="$1"
    [ -z "$HEALTHCHECK_URL" ] && return 0
    curl -fsS -m 10 --retry 3 --retry-delay 5 \
        "${HEALTHCHECK_URL}${suffix}" >> "$LOG" 2>&1 || true
}

# sync_dir LOCAL REMOTE LABEL
# rclone sync with deletion preservation. Captures exit code safely under set -e.
sync_dir() {
    local local_path="$1" remote_path="$2" label="$3"
    local rc=0

    if [ ! -d "$local_path" ]; then
        log "  SKIP: $label — $local_path does not exist"
        return 0
    fi

    log "  syncing $label: $local_path → $remote_path"

    # Extra per-dir args passed as $4+. Using ${a[@]+"${a[@]}"} so empty
    # arrays don't trip set -u (bash 3.2 on macOS errors on "${a[@]}" when
    # the array is empty).
    shift 3

    set +e
    rclone sync "$local_path" "$remote_path" \
        $RCLONE_DRY \
        --backup-dir "$DELETED_BASE/$label" \
        --exclude ".DS_Store" \
        --exclude "__pycache__/**" \
        --exclude "*.pyc" \
        --exclude "*.tmp" \
        "$@" \
        --retries 3 --retries-sleep 30s \
        --log-file "$LOG" --log-level INFO 2>> "$LOG"
    rc=$?
    set -e

    if [ $rc -eq 0 ]; then
        log "  ok: $label"
        OK_COUNT=$((OK_COUNT + 1))
    else
        log "  ERROR: $label sync failed (rc=$rc)"
        FAIL_COUNT=$((FAIL_COUNT + 1))
    fi
}

# copy_files TARGET_REMOTE LABEL FILE1 [FILE2 ...]
# Copies individual files (not whole dirs). Missing files are silently skipped.
copy_files() {
    local remote="$1" label="$2"; shift 2
    local attempted=0 succeeded=0 rc=0

    for f in "$@"; do
        if [ ! -f "$f" ]; then
            log "  SKIP: $label — $f does not exist"
            continue
        fi
        attempted=$((attempted + 1))

        set +e
        rclone copy "$f" "$remote" \
            $RCLONE_DRY \
            --retries 3 --retries-sleep 30s \
            --log-file "$LOG" --log-level INFO 2>> "$LOG"
        rc=$?
        set -e

        if [ $rc -eq 0 ]; then
            succeeded=$((succeeded + 1))
        else
            log "  ERROR: copy of $f failed (rc=$rc)"
            FAIL_COUNT=$((FAIL_COUNT + 1))
        fi
    done

    if [ $attempted -gt 0 ]; then
        log "  $label: $succeeded/$attempted copied"
        [ $succeeded -eq $attempted ] && OK_COUNT=$((OK_COUNT + 1))
    fi
}

log "==================== Starting local backup ===================="
log "  run_stamp:         $DATE"
log "  dry_run:           $DRY_RUN"
log "  deleted_retention: $DELETED_RETENTION_DAYS days"

healthcheck "/start"

cd "$PROJECT"

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
if ! command -v rclone > /dev/null 2>&1; then
    log "  ERROR: rclone not installed (brew install rclone)"
    healthcheck "/fail"
    exit 2
fi

if ! rclone listremotes | grep -q "^gdrive:"; then
    log "  ERROR: rclone 'gdrive' remote not configured (run: rclone config)"
    healthcheck "/fail"
    exit 2
fi

# ---------------------------------------------------------------------------
# 1. Directory syncs (the bulk of the work)
# ---------------------------------------------------------------------------
log "[1/3] Directory syncs"
# transcripts: exclude staging_cache/ because it's synced separately below to
# its own top-level folder, avoiding duplicate storage on Drive.
sync_dir "$PROJECT/output/transcripts"               "$REMOTE_BASE/transcripts"        "transcripts" --exclude "staging_cache/**"
sync_dir "$PROJECT/output/transcripts/staging_cache" "$REMOTE_BASE/staging_cache"      "staging_cache"
sync_dir "$PROJECT/data/knowledge_graph"             "$REMOTE_BASE/knowledge_graph"    "knowledge_graph"
sync_dir "$PROJECT/data/financial"                   "$REMOTE_BASE/financial_configs"  "financial_configs"
sync_dir "$PROJECT/data/lexicons"                    "$REMOTE_BASE/lexicons"           "lexicons"
sync_dir "$PROJECT/config"                           "$REMOTE_BASE/config"             "config"

# ---------------------------------------------------------------------------
# 2. Individual files that don't fit the whole-dir pattern
# ---------------------------------------------------------------------------
log "[2/3] Individual files → misc/"
copy_files "$REMOTE_BASE/misc" "misc" \
    "$PROJECT/data/ibabs_2026_mapping.json" \
    "$PROJECT/data/municipalities_index.json" \
    "$PROJECT/data/audio_recovery/recovery_checkpoint.sqlite"

# ---------------------------------------------------------------------------
# 3. Retention — prune the deleted-files vault older than N days
# ---------------------------------------------------------------------------
log "[3/3] Retention (pruning _deleted/ older than $DELETED_RETENTION_DAYS days)"

if [ "$DRY_RUN" = "1" ]; then
    log "  (dry-run: retention prune skipped)"
else
    rclone delete "$REMOTE_BASE/_deleted/" \
        --min-age "${DELETED_RETENTION_DAYS}d" \
        --log-file "$LOG" --log-level INFO 2>> "$LOG" || \
        log "  WARNING: _deleted retention prune failed (non-fatal)"
    rclone rmdirs "$REMOTE_BASE/_deleted/" --leave-root \
        --log-file "$LOG" 2>> "$LOG" || \
        log "  WARNING: rmdirs on _deleted failed (non-fatal)"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
log "==================== Local backup summary ===================="
log "  ok_steps:     $OK_COUNT"
log "  failed_steps: $FAIL_COUNT"
log "==================== Done ===================="

if [ "$FAIL_COUNT" -gt 0 ]; then
    healthcheck "/fail"
    exit 1
fi

healthcheck ""
exit 0
