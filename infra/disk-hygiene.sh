#!/usr/bin/env bash
# Weekly disk hygiene: prune stale VS Code server builds and extension
# versions, npm/uv/pre-commit caches, and orphaned worktree directories.
# Design: docs/plans/2026-07-09-disk-hygiene-design.md
#
# Usage: disk-hygiene.sh [--dry-run]

set -euo pipefail

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VSCODE_DIR="${HOME}/.vscode-server"
USAGE_WARN_PCT=75
VSIX_MAX_AGE_DAYS=14
NPX_MAX_AGE_DAYS=30
SERVER_KEEP_COUNT=2

log() { echo "disk-hygiene: $*"; }

remove_path() {
  local path=$1 size
  [[ -e "$path" ]] || return 0
  size=$(du -sh "$path" 2>/dev/null | cut -f1)
  if ((DRY_RUN)); then
    log "would remove: $path (${size})"
  else
    log "removing: $path (${size})"
    # Fail-open: a permission error (e.g. root-owned strays) must not abort
    # the remaining hygiene sections under set -e
    rm -rf -- "$path" 2>/dev/null || log "WARNING: could not fully remove ${path} — check ownership"
  fi
}

log "start: $(df -h --output=used,avail,pcent / | tail -1 | xargs)"

# --- VS Code server builds: keep N most-recent per lru.json + any running ---
servers_dir="${VSCODE_DIR}/cli/servers"
if [[ -d "$servers_dir" && -f "${servers_dir}/lru.json" ]]; then
  keep=$(jq -r ".[0:${SERVER_KEEP_COUNT}][]" "${servers_dir}/lru.json")
  for dir in "$servers_dir"/*/; do
    dir="${dir%/}"
    name=$(basename "$dir")
    if grep -qxF "$name" <<<"$keep"; then
      continue
    fi
    # Never delete a build with a live server process, regardless of LRU age
    if pgrep -f "cli/servers/${name}/" >/dev/null 2>&1; then
      log "keeping (running): $name"
      continue
    fi
    remove_path "$dir"
  done
fi

# --- VS Code extensions: keep newest version dir per extension id ---
ext_dir="${VSCODE_DIR}/extensions"
if [[ -d "$ext_dir" ]]; then
  while IFS= read -r old; do
    remove_path "$old"
  done < <(python3 - "$ext_dir" <<'PY'
import re
import sys
from pathlib import Path

groups: dict[str, list[Path]] = {}
for d in Path(sys.argv[1]).iterdir():
    # publisher.name-1.2.3 or publisher.name-1.2.3-linux-x64
    if d.is_dir() and (m := re.match(r"^(.+?)-(\d+\.\d+\.\d+.*)$", d.name)):
        groups.setdefault(m.group(1), []).append(d)
for dirs in groups.values():
    dirs.sort(key=lambda p: p.stat().st_mtime)
    for old in dirs[:-1]:
        print(old)
PY
  )
fi

# --- VS Code cached VSIX downloads older than N days ---
vsix_dir="${VSCODE_DIR}/data/CachedExtensionVSIXs"
if [[ -d "$vsix_dir" ]]; then
  while IFS= read -r f; do
    remove_path "$f"
  done < <(find "$vsix_dir" -mindepth 1 -mtime "+${VSIX_MAX_AGE_DAYS}")
fi

# --- npm: wipe registry cache; expire stale npx sandboxes ---
if command -v npm >/dev/null 2>&1; then
  if ((DRY_RUN)); then
    log "would run: npm cache clean --force"
  else
    npm cache clean --force 2>/dev/null || log "npm cache clean failed (non-fatal)"
  fi
fi
if [[ -d "${HOME}/.npm/_npx" ]]; then
  while IFS= read -r d; do
    remove_path "$d"
  done < <(find "${HOME}/.npm/_npx" -mindepth 1 -maxdepth 1 -mtime "+${NPX_MAX_AGE_DAYS}")
fi

# --- uv cache: drop wheels not referenced by current environments ---
if command -v uv >/dev/null 2>&1; then
  if ((DRY_RUN)); then
    log "would run: uv cache prune"
  else
    uv cache prune 2>/dev/null || log "uv cache prune failed (non-fatal)"
  fi
fi

# --- pre-commit: drop hook repos unreferenced by any config ---
if [[ -x "${REPO}/.venv/bin/pre-commit" ]]; then
  if ((DRY_RUN)); then
    log "would run: pre-commit gc"
  else
    "${REPO}/.venv/bin/pre-commit" gc 2>/dev/null || log "pre-commit gc failed (non-fatal)"
  fi
fi

# --- orphaned worktree dirs: on disk but not registered with git ---
if ((!DRY_RUN)); then
  git -C "$REPO" worktree prune
fi
registered=$(git -C "$REPO" worktree list --porcelain | awk '/^worktree /{print $2}')
for base in "${REPO}/.claude/worktrees" "${REPO}/.worktrees"; do
  [[ -d "$base" ]] || continue
  for dir in "$base"/*/; do
    dir="${dir%/}"
    [[ -d "$dir" ]] || continue
    if ! grep -qxF "$dir" <<<"$registered"; then
      remove_path "$dir"
    fi
  done
done

# --- usage threshold warning ---
pct=$(df --output=pcent / | tail -1 | tr -dc '0-9')
if ((pct >= USAGE_WARN_PCT)); then
  log "WARNING: root filesystem at ${pct}% (threshold ${USAGE_WARN_PCT}%)"
fi

log "end: $(df -h --output=used,avail,pcent / | tail -1 | xargs)"
