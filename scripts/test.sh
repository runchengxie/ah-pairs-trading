#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash scripts/test.sh [all|unit|integration|coverage] [pytest args...]

Modes:
  all          Run the full pytest suite (default)
  unit         Run everything except tests marked as integration
  integration  Run only tests marked as integration
  coverage     Run the full suite with coverage output
EOF
}

mode="all"
if [[ $# -gt 0 ]]; then
  case "$1" in
    all|unit|integration|coverage)
      mode="$1"
      shift
      ;;
    -h|--help|help)
      usage
      exit 0
      ;;
  esac
fi

if [[ -x ".venv/bin/python" ]]; then
  export PYTHONPATH="${PYTHONPATH:+${PYTHONPATH}:}src"
  python_cmd=(.venv/bin/python)
  pytest_cmd=("${python_cmd[@]}" -m pytest)
elif command -v uv >/dev/null 2>&1; then
  export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
  python_cmd=()
  pytest_cmd=(uv run --extra dev pytest)
else
  export PYTHONPATH="${PYTHONPATH:+${PYTHONPATH}:}src"
  python_cmd=(python)
  pytest_cmd=("${python_cmd[@]}" -m pytest)
fi

case "$mode" in
  all)
    "${pytest_cmd[@]}" "$@"
    ;;
  unit)
    "${pytest_cmd[@]}" -m "not integration" "$@"
    ;;
  integration)
    "${pytest_cmd[@]}" -m "integration" "$@"
    ;;
  coverage)
    if [[ ${#python_cmd[@]} -gt 0 ]] && ! "${python_cmd[@]}" -c "import pytest_cov" >/dev/null 2>&1; then
      echo "coverage mode requires pytest-cov. Install dev dependencies with pip install -e \".[dev]\" or uv sync --extra dev." >&2
      exit 1
    fi
    mkdir -p artifacts/test
    "${pytest_cmd[@]}" --cov=ah_pairs_trading --cov-report=term-missing --cov-report=xml:artifacts/test/coverage.xml "$@"
    ;;
esac
