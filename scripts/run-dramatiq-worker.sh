#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

export REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"
exec dramatiq app.tasks --processes "${DRAMATIQ_PROCESSES:-1}" --threads "${DRAMATIQ_THREADS:-8}"
