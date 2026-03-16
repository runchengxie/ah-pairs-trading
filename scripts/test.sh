#!/usr/bin/env bash
set -euo pipefail

if command -v uv >/dev/null 2>&1; then
  export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
  uv run pytest "$@"
  exit 0
fi

export PYTHONPATH="${PYTHONPATH:+${PYTHONPATH}:}src"
python -m pytest "$@"
