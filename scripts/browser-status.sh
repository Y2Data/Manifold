#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ -f data/browser.pid ] && kill -0 "$(cat data/browser.pid)" 2>/dev/null; then
  echo "running (pid $(cat data/browser.pid)) — http://localhost:${BROWSER_PORT:-8090}"
else
  echo "not running"
fi
