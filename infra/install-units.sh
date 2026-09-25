#!/usr/bin/env bash
# Install infra/*.service + infra/*.timer into systemd, or check for drift (#228).
#
# #109 moved helper scripts from scripts/ to infra/, but the units referencing
# them were never re-installed; audit-archive + docker-prune then failed every
# night for months with nobody reading journalctl. This is the single install
# path, so moving a path a unit references means re-running it.
#
# Usage:
#   sudo infra/install-units.sh [UNIT ...]  # copy (default: all), daemon-reload, reset-failed
#   infra/install-units.sh --check          # exit 1 if any installed unit differs from infra/
#
# Install does not enable units; a new timer still needs `systemctl enable --now`.
# Units are installed mode 0644. That normalizes address-validator.service,
# historically 0600 on this host; its text is public in the repo, so 0600
# protected nothing, and once re-installed --check reads it without sudo.
# Until then --check reports it as SKIP; run under sudo to cover it.
#
# UNIT_DIR and SYSTEMCTL are overridable for the sandbox test rig.

set -euo pipefail

INFRA="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="${UNIT_DIR:-/etc/systemd/system}"
SYSTEMCTL="${SYSTEMCTL:-systemctl}"

all_units() {
  local f
  for f in "$INFRA"/*.service "$INFRA"/*.timer; do
    [ -e "$f" ] && basename "$f"
  done
}

check() {
  local unit drift=0 skipped=0
  while read -r unit; do
    if [ ! -e "$UNIT_DIR/$unit" ]; then
      echo "MISSING $unit"
      drift=1
    elif [ ! -r "$UNIT_DIR/$unit" ]; then
      echo "SKIP    $unit (unreadable; re-run with sudo)"
      skipped=1
    elif cmp -s "$INFRA/$unit" "$UNIT_DIR/$unit"; then
      echo "OK      $unit"
    else
      echo "DRIFT   $unit"
      diff -u "$UNIT_DIR/$unit" "$INFRA/$unit" | sed 's/^/        /' || true
      drift=1
    fi
  done < <(all_units)
  if [ "$drift" -ne 0 ]; then
    echo "Installed units drifted from infra/; fix: sudo infra/install-units.sh" >&2
    return 1
  fi
  [ "$skipped" -eq 0 ] || echo "Some units were not compared (need sudo)." >&2
  return 0
}

# Installing from a linked worktree would put a branch's units into production
# ahead of its merge (docs/DEPLOYMENT.md: unit file and code must move together).
refuse_linked_worktree() {
  local git_dir common_dir
  git_dir="$(git -C "$INFRA" rev-parse --absolute-git-dir 2>/dev/null)" || return 0
  common_dir="$(git -C "$INFRA" rev-parse --path-format=absolute --git-common-dir)"
  if [ "$git_dir" != "$common_dir" ]; then
    echo "Refusing to install from a linked worktree ($INFRA); run the main checkout's copy." >&2
    return 1
  fi
}

install_units() {
  refuse_linked_worktree
  local unit units=("$@")
  [ "${#units[@]}" -gt 0 ] || mapfile -t units < <(all_units)
  for unit in "${units[@]}"; do
    if [ ! -f "$INFRA/$unit" ]; then
      echo "No such unit in infra/: $unit" >&2
      return 1
    fi
  done
  for unit in "${units[@]}"; do
    install -m 0644 "$INFRA/$unit" "$UNIT_DIR/$unit"
    echo "installed $unit"
  done
  "$SYSTEMCTL" daemon-reload
  for unit in "${units[@]}"; do
    # Clears a stale `failed` state so `systemctl --failed` reflects the new unit.
    if [[ "$unit" == *.service ]]; then
      "$SYSTEMCTL" reset-failed "$unit" 2>/dev/null || true
    fi
  done
}

case "${1:-}" in
  --check) check ;;
  -h | --help) awk 'NR > 1 && !/^#/ { exit } NR > 1 { sub(/^# ?/, ""); print }' "${BASH_SOURCE[0]}" ;;
  *) install_units "$@" ;;
esac
