#!/usr/bin/env bash
# gather-context.sh
# Prints a structured summary of repo state and runs lint for use during code review.
# Run from the project root.
set -euo pipefail

echo "=== Git status ==="
git status --short

echo ""
echo "=== Staged diff ==="
git diff --staged --stat 2>/dev/null || true

echo ""
echo "=== Unstaged diff ==="
git diff --stat 2>/dev/null || true

echo ""
echo "=== Recent commits ==="
git log --oneline -10 2>/dev/null || true

echo ""
echo "=== Changed files (working tree vs HEAD) ==="
git diff --name-only HEAD 2>/dev/null || true
git diff --name-only --staged HEAD 2>/dev/null || true

echo ""
echo "=== Lint (ruff) ==="
uv run ruff check . 2>&1 || true
