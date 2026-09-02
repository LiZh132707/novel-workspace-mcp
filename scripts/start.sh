#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if command -v uv >/dev/null 2>&1; then
  uv sync
  exec uv run python ui/app.py
else
  python3 -m pip install -e .
  exec python3 ui/app.py
fi
