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
# --check reports root-only installed units (address-validator.service) as SKIP;
# run it under sudo to cover them.
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

install_units() {
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
  -h | --help) sed -n '2,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
  *) install_units "$@" ;;
esac
