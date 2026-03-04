#!/usr/bin/env bash
# pre-ship.sh
# Runs lint and tests. Exits non-zero on any failure.
# Run from the project root.
set -euo pipefail

echo "=== Lint (ruff) ==="
uv run ruff check .

echo ""
echo "=== Tests ==="
uv run pytest --no-cov -x

echo ""
echo "Pre-ship checks passed."
