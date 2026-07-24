#!/usr/bin/env bash
# Idempotent: start the dashboard/router in the background.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

mkdir -p data

if [ -f data/ui.pid ] && kill -0 "$(cat data/ui.pid)" 2>/dev/null; then
  echo "already running (pid $(cat data/ui.pid))"
else
  # .venv/bin/uvicorn directly, not `uv run` — this machine's uv (bundled
  # with miniconda) picks up the active conda base env's site-packages
  # instead of ./.venv when invoked from a conda-activated shell.
  nohup .venv/bin/uvicorn app.main:app --port "${UI_PORT:-8080}" > data/ui.log 2>&1 &
  echo $! > data/ui.pid
  echo "started (pid $(cat data/ui.pid)), logs: data/ui.log"
fi

echo "dashboard: http://localhost:${UI_PORT:-8080}"
