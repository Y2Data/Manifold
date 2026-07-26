#!/usr/bin/env bash
# Checks the browser feature's (heavier, opt-in) deps are actually installed —
# never silently installs the ~150-300MB Chromium binary itself. Mirrors
# bootstrap.sh's "check, don't surprise-install" convention for claude/codex.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

uv sync --extra browser

if ! .venv/bin/python -c "import playwright, trafilatura" 2>/dev/null; then
  echo "Missing browser deps — run: uv sync --extra browser" >&2
  exit 1
fi

if ! .venv/bin/python -c "
from pathlib import Path
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    exit(0 if Path(p.chromium.executable_path).exists() else 1)
" 2>/dev/null; then
  echo "Chromium browser binary not installed — run: .venv/bin/python -m playwright install chromium" >&2
  exit 1
fi

echo "browser deps + chromium found. Run './manifold browser-up' to start the research service."
