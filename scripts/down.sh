#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if [ -f data/ui.pid ]; then
  pid="$(cat data/ui.pid)"
  kill "$pid" 2>/dev/null && echo "stopped (pid $pid)" || echo "not running"
  rm -f data/ui.pid
else
  echo "not running"
fi
