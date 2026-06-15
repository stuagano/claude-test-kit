#!/usr/bin/env bash
# Test runner. Exit code is non-zero if anything fails — wire this into your
# agent loop so "done" means "tests passed", not "the model said so".
#
#   ./run_tests.sh            # everything
#   ./run_tests.sh unit       # fast isolated tests only (the inner-loop gate)
#   ./run_tests.sh integration
#   ./run_tests.sh cov        # everything + coverage report
#
# Bootstraps a local .venv with python3 so it runs anywhere and never installs
# into a system (PEP 668 "externally-managed") Python. Override the base
# interpreter with PYTHON=/path/to/python ./run_tests.sh
set -euo pipefail
cd "$(dirname "$0")"

# Pick a base interpreter — macOS ships python3, not a bare `python`.
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if command -v python3 >/dev/null 2>&1; then PYTHON=python3
  elif command -v python  >/dev/null 2>&1; then PYTHON=python
  else echo "error: need python3 (or python) on PATH" >&2; exit 1; fi
fi

# Create the venv once; reuse it after.
VENV=".venv"
if [ ! -x "$VENV/bin/python" ]; then
  echo "Creating virtualenv in $VENV ..."
  "$PYTHON" -m venv "$VENV"
fi
VPY="$VENV/bin/python"

"$VPY" -m pip install -q --upgrade pip
"$VPY" -m pip install -q -r requirements.txt

case "${1:-all}" in
  unit)        "$VPY" -m pytest -m unit ;;
  integration) "$VPY" -m pytest -m integration ;;
  cov)         "$VPY" -m pip install -q pytest-cov
               "$VPY" -m pytest --cov=ctk --cov=examples --cov-report=term-missing ;;
  all|*)       "$VPY" -m pytest ;;
esac
