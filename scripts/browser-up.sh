#!/usr/bin/env bash
# Idempotent: start the standalone browsing/research service in the background.
# Separate process/pidfile from the main router (data/ui.pid) — see
# app/routing/browser_client.py for how the main app calls this over HTTP.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

bash scripts/browser-bootstrap.sh

mkdir -p data

if [ -f data/browser.pid ] && kill -0 "$(cat data/browser.pid)" 2>/dev/null; then
  echo "already running (pid $(cat data/browser.pid))"
else
  nohup .venv/bin/uvicorn browser_service.main:app --port "${BROWSER_PORT:-8090}" > data/browser.log 2>&1 &
  echo $! > data/browser.pid
  echo "started (pid $(cat data/browser.pid)), logs: data/browser.log"
fi

echo "browser service: http://localhost:${BROWSER_PORT:-8090}"
