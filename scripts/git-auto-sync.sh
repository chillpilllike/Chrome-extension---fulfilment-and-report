#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/Users/amitsoni/Documents/Chrome extension - fulfilment and report"
LOG_FILE="$HOME/Library/Logs/nutricity-github-auto-sync.log"
LOCK_DIR="/tmp/nutricity-github-auto-sync.lock"
SECRET_PATTERN='(9d37dcd4|3d60b21c|96ed5b|gctuoC3|99480662f2d523232050a76cf200bd95|postgres://postgres:|trycloudflare|typesense-o6v8xou|sk_live|AKIA[0-9A-Z]{16}|-----BEGIN PRIVATE KEY-----)'

mkdir -p "$(dirname "$LOG_FILE")"

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE"
}

if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  exit 0
fi
trap 'rmdir "$LOCK_DIR"' EXIT

cd "$REPO_DIR"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  log "Not a git repository: $REPO_DIR"
  exit 1
fi

if [ -z "$(git status --porcelain)" ]; then
  exit 0
fi

if command -v rg >/dev/null 2>&1; then
  if rg -n --hidden \
    --glob '!**/.git/**' \
    --glob '!**/.venv/**' \
    --glob '!frontend/node_modules/**' \
    --glob '!frontend/dist/**' \
    --glob '!**/__pycache__/**' \
    --glob '!data/**' \
    --glob '!reports/**' \
    --glob '!scripts/git-auto-sync.sh' \
    "$SECRET_PATTERN" . >> "$LOG_FILE" 2>&1; then
    log "Secret-looking value found. Auto-sync skipped."
    exit 1
  fi
fi

git add -A

if git diff --cached --quiet; then
  exit 0
fi

commit_message="Auto sync $(date '+%Y-%m-%d %H:%M:%S')"
git commit -m "$commit_message" >> "$LOG_FILE" 2>&1

if ! git pull --rebase origin main >> "$LOG_FILE" 2>&1; then
  log "Pull --rebase failed. Resolve manually."
  exit 1
fi

if git push origin main >> "$LOG_FILE" 2>&1; then
  log "Pushed: $commit_message"
else
  log "Push failed."
  exit 1
fi
