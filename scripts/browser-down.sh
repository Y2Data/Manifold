#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ -f data/browser.pid ]; then
  pid="$(cat data/browser.pid)"
  kill "$pid" 2>/dev/null && echo "stopped (pid $pid)" || echo "not running"
  rm -f data/browser.pid
else
  echo "not running"
fi
