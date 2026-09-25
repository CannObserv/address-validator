#!/usr/bin/env bash
# Self-checking sandbox tests for infra/install-units.sh.
#
# Points UNIT_DIR at a throwaway directory and SYSTEMCTL at a recorder, so no
# real unit is read, written, or reloaded.
#
# Usage: bash tests/shell/install-units-test.sh
#
# bash -c '...' "$0" "$1" bodies are single-quoted on purpose: args expand in the child.
# shellcheck disable=SC2016

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
# sandbox git calls at the outer repo
unset "${!GIT_@}" 2>/dev/null || true

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
sandbox="$(mktemp -d)"
trap 'chmod -R u+rwx "$sandbox" 2>/dev/null || true; rm -rf "$sandbox"' EXIT

# Sandbox infra/: the script resolves units from its own directory.
mkdir -p "$sandbox/infra" "$sandbox/units"
cp "$repo_root/infra/install-units.sh" "$sandbox/infra/"
printf '[Service]\nExecStart=/bin/true\n' >"$sandbox/infra/a.service"
printf '[Timer]\nOnCalendar=daily\n' >"$sandbox/infra/a.timer"
printf '[Service]\nExecStart=/bin/false\n' >"$sandbox/infra/b.service"

SCRIPT="$sandbox/infra/install-units.sh"
CALLS="$sandbox/systemctl.log"
printf '#!/bin/sh\necho "$*" >>"%s"\n' "$CALLS" >"$sandbox/systemctl"
chmod +x "$sandbox/systemctl"
export UNIT_DIR="$sandbox/units" SYSTEMCTL="$sandbox/systemctl"
OUT="$sandbox/out.log"

# --check on an empty unit dir: everything missing
check "--check fails when units are missing" \
  bash -c '! "$0" --check >"$1" 2>&1' "$SCRIPT" "$OUT"
check "--check names a missing unit" grep -q '^MISSING a.service' "$OUT"

# Install all
bash "$SCRIPT" >"$OUT" 2>&1
check "install copies every unit" \
  bash -c 'for u in a.service a.timer b.service; do cmp -s "$0/infra/$u" "$0/units/$u" || exit 1; done' "$sandbox"
check "install daemon-reloads" grep -qx 'daemon-reload' "$CALLS"
check "install reset-fails services" grep -qx 'reset-failed a.service' "$CALLS"
check "install does not reset-fail timers" bash -c '! grep -q "reset-failed a.timer" "$0"' "$CALLS"
check "--check passes after install" bash "$SCRIPT" --check

# Drift: installed copy edited out-of-band
printf '[Service]\nExecStart=/old/path\n' >"$sandbox/units/b.service"
check "--check fails on drift" bash -c '! "$0" --check >"$1" 2>&1' "$SCRIPT" "$OUT"
check "--check names the drifted unit" grep -q '^DRIFT   b.service' "$OUT"
check "--check shows the diff" grep -q '/old/path' "$OUT"
check "--check drift hint names the main checkout" grep -q 'from the main checkout' "$OUT"

# Install a named subset only
: >"$CALLS"
printf '[Timer]\nOnCalendar=weekly\n' >"$sandbox/units/a.timer"
bash "$SCRIPT" b.service >"$OUT" 2>&1
check "named install fixes that unit" cmp -s "$sandbox/infra/b.service" "$sandbox/units/b.service"
check "named install leaves others alone" bash -c '! cmp -s "$0/infra/a.timer" "$0/units/a.timer"' "$sandbox"
check "--help prints the whole header" bash -c '"$0" --help | grep -q "^UNIT_DIR and SYSTEMCTL"' "$SCRIPT"
check "unknown unit is rejected" bash -c '! "$0" nope.service >/dev/null 2>&1' "$SCRIPT"

# Unreadable installed unit (root-only address-validator.service): SKIP, not drift.
# Root reads through mode 000, so this case only holds for a non-root run.
if [ "$(id -u)" -ne 0 ]; then
  cp "$sandbox/infra/a.timer" "$sandbox/units/a.timer"
  chmod 000 "$sandbox/units/a.service"
  check "--check passes with only unreadable units" bash -c '"$0" --check >"$1" 2>&1' "$SCRIPT" "$OUT"
  check "--check reports unreadable as SKIP" grep -q '^SKIP    a.service' "$OUT"
fi

# Linked worktree: install refused, --check still allowed
wt_repo="$sandbox/wtrepo"
mkdir -p "$wt_repo/infra"
cp "$sandbox/infra/install-units.sh" "$sandbox/infra/b.service" "$wt_repo/infra/"
git -C "$wt_repo" init -q
git -C "$wt_repo" add -A
git -C "$wt_repo" -c user.name=test -c user.email=test@test commit -q --no-verify -m init
git -C "$wt_repo" worktree add -q "$sandbox/wt" -b wt-branch
wt_units="$sandbox/wt-units"
mkdir -p "$wt_units"
check "install from a linked worktree is refused" \
  bash -c '! UNIT_DIR="$1" "$0" >/dev/null 2>&1' "$sandbox/wt/infra/install-units.sh" "$wt_units"
check "refused install writes nothing" bash -c '[ -z "$(ls -A "$0")" ]' "$wt_units"
check "install from the main checkout still works" \
  bash -c 'UNIT_DIR="$1" "$0" >/dev/null 2>&1' "$wt_repo/infra/install-units.sh" "$wt_units"
# Root without SUDO_UID (su -, cron) gets "dubious ownership" from git on an
# exedev-owned repo; the guard must not treat that failure as "not a worktree".
mkdir -p "$sandbox/badgit"
printf '#!/bin/sh\necho "fatal: detected dubious ownership" >&2\nexit 128\n' >"$sandbox/badgit/git"
chmod +x "$sandbox/badgit/git"
check "install from a linked worktree is refused even when git fails" \
  bash -c '! PATH="$2:$PATH" UNIT_DIR="$1" "$0" >/dev/null 2>&1' \
  "$sandbox/wt/infra/install-units.sh" "$wt_units" "$sandbox/badgit"
check "--check from a linked worktree is allowed" \
  bash -c 'UNIT_DIR="$1" "$0" --check >/dev/null 2>&1' "$sandbox/wt/infra/install-units.sh" "$wt_units"

echo
if [ "$FAILS" -ne 0 ]; then
  echo "$FAILS check(s) failed"
  exit 1
fi
echo "All checks passed"
