#!/usr/bin/env bash
# Self-checking sandbox tests for infra/disk-hygiene.sh, plus the journal
# warning shape it shares with infra/docker-prune-check.sh (infra/journal-warn.sh).
#
# Runs the hygiene script against a throwaway $HOME and a throwaway git repo,
# so nothing outside the sandbox is read or deleted (npm/uv resolve their
# caches from $HOME; the worktree sweep resolves the repo from the script's
# own location). Warnings go to stderr only; nothing reaches the journal.
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
# Run from a systemd unit, the rig would inherit its parent's journal stream
unset JOURNAL_STREAM

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
cp "$repo_root/infra/"{disk-hygiene.sh,journal-warn.sh,docker-prune-check.sh} "$sandbox/repo/infra/"
git -C "$sandbox/repo" init -q
git -C "$sandbox/repo" -c user.name=test -c user.email=test@test commit -q --no-verify --allow-empty -m init
SCRIPT="$sandbox/repo/infra/disk-hygiene.sh"
OUT="$sandbox/out.log"
ERR="$sandbox/err.log"

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

# Pinned SocratiCode trees (#322): npx names a single-package tree
# sha512(spec)[:16]. Two different specs prove each source is read: the
# repo's declared one and the VS Code machine setting that actually launches.
npx_hash() { printf '%s' "$1" | sha512sum | cut -c1-16; }
repo_spec="socraticode@9.9.9"
machine_spec="socraticode@8.8.8"
pinned_repo="$fake/.npm/_npx/$(npx_hash "$repo_spec")"
pinned_machine="$fake/.npm/_npx/$(npx_hash "$machine_spec")"
mkdir -p "$sandbox/repo/.claude" "$fake/.vscode-server/data/Machine"
machine_settings="$fake/.vscode-server/data/Machine/settings.json"
printf '{"env": {"SOCRATICODE_SPEC": "%s"}}\n' "$repo_spec" >"$sandbox/repo/.claude/settings.json"
printf '{"claudeCode.environmentVariables": [{"name": "SOCRATICODE_SPEC", "value": "%s"}]}\n' \
  "$machine_spec" >"$machine_settings"
# Install grace window: the tree's own mtime is old, but a file deep inside
# was written N minutes ago (npx reinstalls a moved spec into the same dir)
grace_old="$fake/.npm/_npx/grace-old"
grace_young="$fake/.npm/_npx/grace-young"
for d in "$pinned_repo" "$pinned_machine" "$grace_old" "$grace_young"; do
  mkdir -p "$d/node_modules/pkg"
  touch "$d/node_modules/pkg/f"
  find "$d" -exec touch -d "40 days ago" {} +
done
touch -d "61 minutes ago" "$grace_old/node_modules/pkg/f"
touch -d "10 minutes ago" "$grace_young/node_modules/pkg/f"

# Fake uv records its argv: a bare `uv cache prune` waits forever while any
# `uv run` holds the cache lock (#322), so the run must pass --force
mkdir -p "$sandbox/uvbin"
UV_ARGS="$sandbox/uv-args.log"
cat >"$sandbox/uvbin/uv" <<EOF
#!/bin/sh
echo "\$*" >>"$UV_ARGS"
EOF
chmod +x "$sandbox/uvbin/uv"

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
HOME=$fake PATH="$sandbox/uvbin:$PATH" "$SCRIPT" >"$OUT" 2>&1 || rc=$?

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
check "_npx tree pinned by repo SOCRATICODE_SPEC survives" test -d "$pinned_repo"
check "_npx tree pinned by VS Code machine setting survives" test -d "$pinned_machine"
check "pinned _npx tree logged as kept" /bin/grep -qF "keeping (pinned ${repo_spec})" "$OUT"
check "_npx tree last written 61min ago removed" test ! -d "$grace_old"
check "_npx tree written 10min ago survives (install grace)" test -d "$grace_young"
check "uv cache prune runs with --force" /bin/grep -qx "cache prune --force" "$UV_ARGS"
check "unit bounds its run (TimeoutStartSec=)" \
  /bin/grep -qE '^TimeoutStartSec=[1-9][0-9]*(s|min)?$' "$repo_root/infra/disk-hygiene.service"

# --- an unreadable spec source skips the _npx prune: never prune more ---
mkdir -p "$fake/.npm/_npx/stale"
touch -d "40 days ago" "$fake/.npm/_npx/stale"
cp "$machine_settings" "$sandbox/machine-settings.good"
echo '{ // a JSONC comment jq cannot read' >"$machine_settings"
rc=0
HOME=$fake PATH="$sandbox/uvbin:$PATH" "$SCRIPT" >"$OUT" 2>&1 || rc=$?
check "unreadable machine setting: run exits 0" test "$rc" -eq 0
check "unreadable machine setting: warns and skips" /bin/grep -q "skipping _npx prune" "$OUT"
check "unreadable machine setting: stale _npx untouched" test -d "$fake/.npm/_npx/stale"
check "unreadable machine setting: pinned trees untouched" test -d "$pinned_machine"
cp "$sandbox/machine-settings.good" "$machine_settings"

# --- an absent spec source pins nothing from it; the rest still apply ---
rm "$machine_settings"
HOME=$fake PATH="$sandbox/uvbin:$PATH" "$SCRIPT" >"$OUT" 2>&1 || true
check "absent machine setting: stale _npx removed" test ! -d "$fake/.npm/_npx/stale"
check "absent machine setting: its tree is no longer pinned" test ! -d "$pinned_machine"
check "absent machine setting: repo-pinned tree survives" test -d "$pinned_repo"

# --- dry-run names the forced prune ---
HOME=$fake PATH="$sandbox/uvbin:$PATH" "$SCRIPT" --dry-run >"$OUT" 2>&1 || true
check "dry-run names uv cache prune --force" /bin/grep -qx "disk-hygiene: would run: uv cache prune --force" "$OUT"
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

# --- journal shape (#246): warnings on the unit's stderr stream, <4> only there ---
# Under systemd, JOURNAL_STREAM names stderr's <dev>:<inode>; a file stands in
lacks() { ! /bin/grep -q "$1" "$2"; }
: >"$OUT"
: >"$ERR"
out_stream=$(stat -L -c '%d:%i' "$OUT")
err_stream=$(stat -L -c '%d:%i' "$ERR")
HOME=$fake JOURNAL_STREAM=$err_stream "$SCRIPT" >"$OUT" 2>"$ERR" || true
check "journal: warning carries <4> priority prefix" /bin/grep -qx "<4>disk-hygiene: WARNING: could not read keep-list.*" "$ERR"
check "journal: warning is not duplicated on stdout" lacks WARNING "$OUT"
check "journal: info lines carry no prefix" lacks "^<" "$OUT"

HOME=$fake JOURNAL_STREAM=$out_stream "$SCRIPT" >"$OUT" 2>"$ERR" || true
check "redirected stderr: inherited JOURNAL_STREAM adds no prefix" /bin/grep -qx "disk-hygiene: WARNING: could not read keep-list.*" "$ERR"

HOME=$fake "$SCRIPT" --dry-run >"$OUT" 2>"$ERR" || true
check "no JOURNAL_STREAM: warning has no prefix" /bin/grep -qx "disk-hygiene: WARNING: could not read keep-list.*" "$ERR"

# docker-prune-check.sh: a fake df reports the root filesystem at 90%
mkdir -p "$sandbox/bin"
cat >"$sandbox/bin/df" <<'EOF'
#!/bin/sh
echo 'Use%'
echo ' 90%'
EOF
chmod +x "$sandbox/bin/df"
rc=0
PATH="$sandbox/bin:$PATH" JOURNAL_STREAM=$err_stream \
  bash "$sandbox/repo/infra/docker-prune-check.sh" >"$OUT" 2>"$ERR" || rc=$?
check "docker-prune-check exits 0" test "$rc" -eq 0
check "docker-prune-check: warning carries <4> priority prefix" /bin/grep -qx "<4>DISK WARNING: / at 90% after Docker prune" "$ERR"
PATH="$sandbox/bin:$PATH" bash "$sandbox/repo/infra/docker-prune-check.sh" >"$OUT" 2>"$ERR" || true
check "docker-prune-check: no JOURNAL_STREAM, no prefix" /bin/grep -qx "DISK WARNING: / at 90% after Docker prune" "$ERR"

echo
if ((FAILS > 0)); then
  echo "$FAILS assertion(s) failed"
  exit 1
fi
echo "all assertions passed"
