#!/usr/bin/env bash
# Download the Tailwind CSS standalone CLI binary for the current platform.
set -euo pipefail

TAILWIND_VERSION="v3.4.17"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$SCRIPT_DIR/bin"
BINARY="$BIN_DIR/tailwindcss"

if [ -f "$BINARY" ]; then
    exit 0
fi

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"
case "$ARCH" in
    x86_64) ARCH="x64" ;;
    aarch64|arm64) ARCH="arm64" ;;
    *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac

URL="https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-${OS}-${ARCH}"

mkdir -p "$BIN_DIR"
echo "Downloading tailwindcss ${TAILWIND_VERSION} (${OS}-${ARCH})..."
curl -sL "$URL" -o "$BINARY"
chmod +x "$BINARY"
echo "Installed: $BINARY"
