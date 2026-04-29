#!/usr/bin/env bash
# Build the minified Tailwind CSS output from the input file.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Ensure binary is available
"$SCRIPT_DIR/download-tailwind.sh"

"$SCRIPT_DIR/bin/tailwindcss" \
    -c "$PROJECT_ROOT/tailwind.config.js" \
    -i "$PROJECT_ROOT/src/address_validator/static/admin/css/input.css" \
    -o "$PROJECT_ROOT/src/address_validator/static/admin/css/tailwind.css" \
    --minify
