#!/bin/bash
# ORI → iBabs URL alignment cron wrapper
#
# Schedules:
#   - Daily 06:00:  sync rolling 30-day window (today ± 14 days)
#   - Weekly Sun 03:00: sync full current quarter
#   - Monthly 1st 02:00: full year audit
#
# Add to crontab:
#   0 6 * * *   /path/to/sync_ibabs_urls_cron.sh daily
#   0 3 * * 0   /path/to/sync_ibabs_urls_cron.sh weekly
#   0 2 1 * *   /path/to/sync_ibabs_urls_cron.sh monthly

set -e
cd "$(dirname "$0")/.."

MODE="${1:-daily}"
LOG_DIR="logs/sync_ibabs_urls"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/$(date +%Y%m%d_%H%M%S)_$MODE.log"

# Ensure SSH tunnel is up before running
if ! ./scripts/dev_tunnel.sh --status 2>&1 | grep -q "RUNNING"; then
    echo "[$(date)] SSH tunnel not running — attempting to start" >> "$LOG_FILE"
    ./scripts/dev_tunnel.sh --bg >> "$LOG_FILE" 2>&1
    sleep 3
fi

case "$MODE" in
  daily)
    FROM=$(date -v-14d +%Y-%m-%d 2>/dev/null || date -d "-14 days" +%Y-%m-%d)
    TO=$(date -v+14d +%Y-%m-%d 2>/dev/null || date -d "+14 days" +%Y-%m-%d)
    python3 scripts/sync_ibabs_urls.py --from "$FROM" --to "$TO" --apply --source cron_daily >> "$LOG_FILE" 2>&1
    ;;
  weekly)
    YEAR=$(date +%Y)
    MONTH=$(date +%m)
    QUARTER=$(( (10#$MONTH - 1) / 3 ))
    Q_START_MONTH=$(( QUARTER * 3 + 1 ))
    Q_END_MONTH=$(( Q_START_MONTH + 2 ))
    FROM="$YEAR-$(printf '%02d' $Q_START_MONTH)-01"
    TO="$YEAR-$(printf '%02d' $Q_END_MONTH)-28"
    python3 scripts/sync_ibabs_urls.py --from "$FROM" --to "$TO" --apply --source cron_weekly >> "$LOG_FILE" 2>&1
    ;;
  monthly)
    YEAR=$(date +%Y)
    python3 scripts/sync_ibabs_urls.py --year "$YEAR" --apply --source cron_monthly >> "$LOG_FILE" 2>&1
    ;;
  *)
    echo "Usage: $0 {daily|weekly|monthly}"
    exit 1
    ;;
esac

# Compress old logs (>30 days)
find "$LOG_DIR" -name "*.log" -mtime +30 -exec gzip {} \; 2>/dev/null || true
