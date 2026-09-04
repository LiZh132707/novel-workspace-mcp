#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

if command -v uv >/dev/null 2>&1; then
  uv sync
  exec uv run novel-workspace serve
else
  python3 -m pip install -e .
  exec novel-workspace serve
fi
