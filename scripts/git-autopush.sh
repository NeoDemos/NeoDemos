#!/bin/bash
# NeoDemos daily git auto-backup — runs via LaunchAgent at 23:00 local time.
#
# Commits any staged/unstaged changes (respecting .gitignore) and pushes to
# origin/main. Safe to run even if there is nothing new — it exits quietly.
#
# Log: ~/Library/Logs/neodemos-git-autopush.log

set -euo pipefail

PROJECT="/Users/dennistak/Documents/Final Frontier/NeoDemos"
LOG="$HOME/Library/Logs/neodemos-git-autopush.log"
DATE=$(date +%Y%m%d_%H%M%S)

log() {
    echo "[$DATE] $*" >> "$LOG"
}

cd "$PROJECT"

log "=== git-autopush start ==="

# Stage everything .gitignore doesn't exclude
git add -A

# Check for staged changes
if git diff --cached --quiet; then
    log "Nothing to commit — checking for unpushed commits..."
else
    COMMIT_MSG="auto-backup $(date '+%Y-%m-%d %H:%M')"
    git commit -m "$COMMIT_MSG" >> "$LOG" 2>&1
    log "Committed: $COMMIT_MSG"
fi

# Push if we are ahead of origin
AHEAD=$(git rev-list --count origin/main..HEAD 2>/dev/null || echo "0")
if [ "$AHEAD" -gt 0 ]; then
    git push origin main >> "$LOG" 2>&1
    log "Pushed $AHEAD commit(s) to origin/main"
else
    log "Already up to date with origin/main"
fi

log "=== git-autopush done ==="
