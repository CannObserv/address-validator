#!/usr/bin/env bash
# Weekly disk hygiene: prune stale VS Code server builds and extension
# versions, npm/uv/pre-commit caches, and orphaned worktree directories.
# Design: docs/plans/2026-07-09-disk-hygiene-design.md
#
# Usage: disk-hygiene.sh [--dry-run]

set -euo pipefail
shopt -s nullglob

if [[ $# -gt 1 || ($# -eq 1 && $1 != "--dry-run") ]]; then
  echo "usage: ${0##*/} [--dry-run]" >&2
  exit 2
fi
DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VSCODE_DIR="${HOME}/.vscode-server"
USAGE_WARN_PCT=75
VSIX_MAX_AGE_DAYS=14
NPX_MAX_AGE_DAYS=30
NPX_GRACE_MIN=60
SERVER_KEEP_COUNT=2
WORKTREE_MIN_AGE_MIN=30

# shellcheck source=infra/journal-warn.sh
source "${REPO}/infra/journal-warn.sh"

log() { echo "disk-hygiene: $*"; }

# Real journal warning priority, on the unit's own stream so the failure
# dispatch's per-run WARNING+ tail includes it (#246)
warn() { journal_warn "disk-hygiene: WARNING: $*"; }

remove_path() {
  local path=$1 size
  [[ -e "$path" ]] || return 0
  size=$(du -sh -- "$path" 2>/dev/null | cut -f1) || size="?"
  if ((DRY_RUN)); then
    log "would remove: $path (${size})"
  else
    log "removing: $path (${size})"
    # Fail-open: a permission error (e.g. root-owned strays) must not abort
    # the remaining hygiene sections under set -e
    rm -rf -- "$path" 2>/dev/null || warn "could not fully remove ${path} — check ownership"
  fi
}

log "start: $(df -h --output=used,avail,pcent / | tail -1 | xargs)"

# --- VS Code server builds: keep N most-recent per lru.json + any running ---
servers_dir="${VSCODE_DIR}/cli/servers"
if [[ -d "$servers_dir" && -f "${servers_dir}/lru.json" ]]; then
  keep=$(jq -r ".[0:${SERVER_KEEP_COUNT}][]" "${servers_dir}/lru.json" 2>/dev/null) || keep=""
  if [[ -z "$keep" ]]; then
    # An empty keep-list would delete every non-running build, including the
    # newest — treat unreadable/empty lru.json as "skip this section"
    warn "could not read keep-list from ${servers_dir}/lru.json — skipping server-build prune"
  else
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
fi

# --- VS Code extensions: keep highest version dir per extension id ---
ext_dir="${VSCODE_DIR}/extensions"
if [[ -d "$ext_dir" ]]; then
  while IFS= read -r old; do
    remove_path "$old"
  done < <(python3 - "$ext_dir" <<'PY'
import re
import sys
from pathlib import Path

groups: dict[str, list[tuple[tuple[int, ...], float, Path]]] = {}
for d in Path(sys.argv[1]).iterdir():
    # publisher.name-1.2.3 or publisher.name-1.2.3-linux-x64
    if d.is_dir() and (m := re.match(r"^(.+?)-(\d+\.\d+\.\d+)", d.name)):
        version = tuple(int(x) for x in m.group(2).split("."))
        groups.setdefault(m.group(1), []).append((version, d.stat().st_mtime, d))
for entries in groups.values():
    entries.sort()  # version first, mtime as tiebreak
    for _, _, old in entries[:-1]:
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
    npm cache clean --force 2>/dev/null || warn "npm cache clean failed (non-fatal)"
  fi
fi

# Pinned SocratiCode trees (#322): npx names a single-package tree
# sha512(spec)[:16]. Pin every spec either source declares (the repo's
# settings, and the VS Code machine setting that launches the session), so a
# re-pin that reaches only one still keeps the tree in use. Computed per run,
# never hard-coded. Losing the pinned tree makes the next session reinstall
# it outside the memory cap (docs/HOST-MEMORY.md).
npx_specs=()
npx_unreadable=""
read_specs() {
  local file=$1 out
  [[ -f "$file" ]] || return 0
  if out=$(jq -r "$2" "$file" 2>/dev/null); then
    if [[ -n "$out" ]]; then
      mapfile -t -O "${#npx_specs[@]}" npx_specs <<<"$out"
    fi
  else
    npx_unreadable+=" ${file}"
  fi
}
read_specs "${REPO}/.claude/settings.json" '.env.SOCRATICODE_SPEC // empty'
read_specs "${VSCODE_DIR}/data/Machine/settings.json" \
  '.["claudeCode.environmentVariables"][]? | select(.name == "SOCRATICODE_SPEC") | .value // empty'

if [[ -d "${HOME}/.npm/_npx" ]]; then
  if [[ -n "$npx_unreadable" ]]; then
    # An unreadable pin must never widen the prune (same rule as lru.json)
    warn "could not read SOCRATICODE_SPEC from${npx_unreadable} — skipping _npx prune"
  else
    declare -A npx_pinned=()
    for spec in "${npx_specs[@]}"; do
      npx_pinned[$(printf '%s' "$spec" | sha512sum | cut -c1-16)]=$spec
    done
    for dir in "${HOME}/.npm/_npx"/*/; do
      dir="${dir%/}"
      name=$(basename "$dir")
      if [[ -n "${npx_pinned[$name]:-}" ]]; then
        log "keeping (pinned ${npx_pinned[$name]}): $dir"
        continue
      fi
      [[ -n "$(find "$dir" -maxdepth 0 -mtime "+${NPX_MAX_AGE_DAYS}")" ]] || continue
      # An install in progress is named by no process, and npx reinstalls a
      # moved spec into its existing tree: judge by the newest entry anywhere
      # under it, not the tree's own mtime. A walk that errors keeps the tree.
      if ! recent=$(find "$dir" -mmin "-${NPX_GRACE_MIN}" -print -quit 2>/dev/null); then
        log "keeping (unreadable, cannot rule out an install): $dir"
        continue
      fi
      if [[ -n "$recent" ]]; then
        log "keeping (written <${NPX_GRACE_MIN}min): $dir"
        continue
      fi
      remove_path "$dir"
    done
  fi
fi

# --- uv cache: drop wheels not referenced by current environments ---
# --force: a bare prune waits forever while any `uv run` (a dev server, a test
# run) holds the cache lock, and this oneshot would never finish (#322)
if command -v uv >/dev/null 2>&1; then
  if ((DRY_RUN)); then
    log "would run: uv cache prune --force"
  else
    uv cache prune --force 2>/dev/null || warn "uv cache prune failed (non-fatal)"
  fi
fi

# --- pre-commit: drop hook repos unreferenced by any config ---
if [[ -x "${REPO}/.venv/bin/pre-commit" ]]; then
  if ((DRY_RUN)); then
    log "would run: pre-commit gc"
  else
    "${REPO}/.venv/bin/pre-commit" gc 2>/dev/null || warn "pre-commit gc failed (non-fatal)"
  fi
fi

# --- orphaned worktree dirs: on disk but not registered with git ---
if ((!DRY_RUN)); then
  git -C "$REPO" worktree prune
fi
registered=$(git -C "$REPO" worktree list --porcelain | sed -n 's/^worktree //p')
for base in "${REPO}/.claude/worktrees" "${REPO}/.worktrees"; do
  [[ -d "$base" ]] || continue
  for dir in "$base"/*/; do
    dir="${dir%/}"
    [[ -d "$dir" ]] || continue
    if grep -qxF "$dir" <<<"$registered"; then
      continue
    fi
    # In-flight creation window: the dir can exist moments before git
    # registers it — never sweep anything this young
    if [[ -n "$(find "$dir" -maxdepth 0 -mmin "-${WORKTREE_MIN_AGE_MIN}")" ]]; then
      log "keeping (recent, <${WORKTREE_MIN_AGE_MIN}min): $dir"
      continue
    fi
    remove_path "$dir"
  done
done

# --- usage threshold warning ---
pct=$(df --output=pcent / | tail -1 | tr -dc '0-9')
if ((pct >= USAGE_WARN_PCT)); then
  warn "root filesystem at ${pct}% (threshold ${USAGE_WARN_PCT}%)"
fi

log "end: $(df -h --output=used,avail,pcent / | tail -1 | xargs)"
