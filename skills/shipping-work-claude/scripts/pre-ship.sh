#!/usr/bin/env bash
# pre-ship.sh
# Runs lint and tests. Exits non-zero on any failure.
# Detects the git project root automatically; safe to invoke from any directory.
#
# Usage: bash skills/shipping-work-claude/scripts/pre-ship.sh [--help]
set -euo pipefail

if [[ "${1:-}" == "--help" ]]; then
  echo "Usage: bash skills/shipping-work-claude/scripts/pre-ship.sh"
  echo ""
  echo "Runs ruff lint and pytest. Exits non-zero on any failure."
  echo "Must pass before committing or pushing."
  echo ""
  echo "Exit codes:"
  echo "  0  All checks passed"
  echo "  1  Lint or test failure"
  exit 0
fi

PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$PROJECT_ROOT"

echo "=== Lint (ruff) ==="
uv run ruff check . --exclude vendor/

echo ""
echo "=== Tests ==="
uv run pytest --no-cov -x

echo ""
echo "Pre-ship checks passed."
