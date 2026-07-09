#!/usr/bin/env bash
# Self-checking sandbox tests for infra/disk-hygiene.sh.
#
# Runs the hygiene script against a throwaway $HOME and a throwaway git repo,
# so nothing outside the sandbox is read or deleted (npm/uv resolve their
# caches from $HOME; the worktree sweep resolves the repo from the script's
# own location). warn() calls do emit real journal lines tagged disk-hygiene.
#
# Usage: bash tests/shell/disk-hygiene-test.sh

set -euo pipefail

FAILS=0
check() {
  local desc=$1
  shift
  if "$@"; then
    echo "PASS: $desc"
  else
    echo "FAIL: $desc"
    FAILS=$((FAILS + 1))
  fi
}

# Hook-safe: under a git hook (pre-commit), exported GIT_* vars would point
# sandbox git calls at the outer repo and recursively fire its hooks
unset "${!GIT_@}" 2>/dev/null || true

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
sandbox="$(cd "$(mktemp -d)" && pwd -P)"
cleanup() {
  chmod -R u+rwx "$sandbox" 2>/dev/null || true
  rm -rf "$sandbox"
}
trap cleanup EXIT

# Sandbox repo: the script derives REPO from its own location, so a copy
# inside a scratch repo keeps `git worktree prune` and the orphan sweep
# away from the real repo
mkdir -p "$sandbox/repo/infra"
cp "$repo_root/infra/disk-hygiene.sh" "$sandbox/repo/infra/"
git -C "$sandbox/repo" init -q
git -C "$sandbox/repo" -c user.name=test -c user.email=test@test commit -q --no-verify --allow-empty -m init
SCRIPT="$sandbox/repo/infra/disk-hygiene.sh"
OUT="$sandbox/out.log"

# Per-run-unique server names: the hygiene script pgrep-matches
# "cli/servers/<name>/" against ALL process cmdlines, so a concurrent
# run using identical fixture names would suppress removals here
uniq="dhtest$$"
fake="$sandbox/home"
servers="$fake/.vscode-server/cli/servers"
ext="$fake/.vscode-server/extensions"
vsix="$fake/.vscode-server/data/CachedExtensionVSIXs"
mkdir -p "$servers"/Stable-${uniq}-{keep1,keep2,old} "$ext"/dhtest.ext-1.0.{9,10} "$vsix" "$fake/.npm/_npx"/{fresh,stale}
echo "[\"Stable-${uniq}-keep1\",\"Stable-${uniq}-keep2\"]" >"$servers/lru.json"
touch -d "2 days ago" "$ext/dhtest.ext-1.0.10"      # newer version, older mtime
touch "$vsix/fresh.vsix"
touch -d "20 days ago" "$vsix/stale.vsix"
touch -d "40 days ago" "$fake/.npm/_npx/stale"

# Worktree fixtures: one registered, one young orphan, one old orphan
git -C "$sandbox/repo" worktree add -q .worktrees/registered-wt -b wt-branch
mkdir -p "$sandbox/repo/.worktrees/orphan-young" "$sandbox/repo/.worktrees/orphan-old"
touch -d "2 hours ago" "$sandbox/repo/.worktrees/orphan-old"

# du/rm fail-open fixture: unreadable subdir inside a doomed server build
mkdir -p "$servers/Stable-${uniq}-old/locked"
touch "$servers/Stable-${uniq}-old/locked/f"
chmod 000 "$servers/Stable-${uniq}-old/locked"

# --- argument rejection ---
rc=0
HOME=$fake "$SCRIPT" --bogus >/dev/null 2>&1 || rc=$?
check "unknown argument rejected with exit 2" test "$rc" -eq 2

# --- real run against the sandbox ---
rc=0
HOME=$fake "$SCRIPT" >"$OUT" 2>&1 || rc=$?

check "run completes despite unremovable path" test "$rc" -eq 0
check "unremovable path warned, not fatal" /bin/grep -q "WARNING: could not fully remove" "$OUT"
check "kept lru server 1 survives" test -d "$servers/Stable-${uniq}-keep1"
check "kept lru server 2 survives" test -d "$servers/Stable-${uniq}-keep2"
check "higher extension version survives despite older mtime" test -d "$ext/dhtest.ext-1.0.10"
check "lower extension version removed" test ! -d "$ext/dhtest.ext-1.0.9"
check "fresh VSIX survives" test -e "$vsix/fresh.vsix"
check "stale VSIX removed" test ! -e "$vsix/stale.vsix"
check "fresh _npx sandbox survives" test -d "$fake/.npm/_npx/fresh"
check "stale _npx sandbox removed" test ! -d "$fake/.npm/_npx/stale"
check "registered worktree survives" test -d "$sandbox/repo/.worktrees/registered-wt"
check "young orphan worktree survives (in-flight window)" test -d "$sandbox/repo/.worktrees/orphan-young"
check "old orphan worktree removed" test ! -d "$sandbox/repo/.worktrees/orphan-old"

# --- corrupt lru.json skips server prune instead of deleting ---
echo 'not json' >"$servers/lru.json"
rc=0
HOME=$fake "$SCRIPT" >"$OUT" 2>&1 || rc=$?
check "corrupt lru.json run exits 0" test "$rc" -eq 0
check "corrupt lru.json warns and skips" /bin/grep -q "skipping server-build prune" "$OUT"
check "servers untouched under corrupt lru.json" test -d "$servers/Stable-${uniq}-keep1"

# --- empty lru.json also skips ---
echo '[]' >"$servers/lru.json"
HOME=$fake "$SCRIPT" >"$OUT" 2>&1 || true
check "empty lru.json warns and skips" /bin/grep -q "skipping server-build prune" "$OUT"

echo
if ((FAILS > 0)); then
  echo "$FAILS assertion(s) failed"
  exit 1
fi
echo "all assertions passed"
