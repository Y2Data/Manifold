#!/usr/bin/env bash
# Checks that claude/codex are installed and logged in (this project reuses
# their existing subscription auth — it never touches API keys), then syncs
# this project's own Python deps.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

for cli in claude codex; do
  if ! command -v "$cli" >/dev/null 2>&1; then
    echo "Missing: $cli — install it and log in (\`$cli\`) before continuing." >&2
    exit 1
  fi
done

uv sync

echo "claude/codex found on PATH. Run './manifold up' to start the dashboard."
