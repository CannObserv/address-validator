#!/usr/bin/env bash
# Pre-commit hook: rebuild Tailwind CSS and stage the output if changed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT="src/address_validator/static/admin/css/tailwind.css"

"$SCRIPT_DIR/build-css.sh"

if ! git diff --quiet -- "$OUTPUT" 2>/dev/null; then
    git add "$OUTPUT"
fi
