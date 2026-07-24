#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ -f data/ui.pid ] && kill -0 "$(cat data/ui.pid)" 2>/dev/null; then
  echo "running (pid $(cat data/ui.pid)) — http://localhost:${UI_PORT:-8080}"
else
  echo "not running"
fi
