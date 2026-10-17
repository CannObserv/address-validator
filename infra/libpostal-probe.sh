#!/bin/bash
# libpostal liveness probe (#256): alert when the sidecar runs but doesn't answer.
#
# libpostal.service alerts on a crashloop (#248), but a container that is up
# and wedged, or a Docker port proxy that accepts and then drops (#239), never
# fails the unit, and libpostal never affects /api/v2/health's status. Run by
# libpostal-probe.timer every 2 minutes as libpostal-probe.service.
#
# One alert per outage: the run that reaches MISSES_TO_ALERT consecutive
# misses exits 1, so OnFailure=unit-failure@ fires once. Later misses exit 0.
# A deliberate restart answers again within seconds (4-7 s measured,
# 2026-09-29/30), so it costs one miss at most and never alerts.
#
# Probes the sidecar directly, not /api/v2/health: it works while the API is
# down, and the health route is audited, so every probe would write an
# audit_log row (#311). The URL is the patching knob's libpostal health line,
# so the probe and the patch health check agree. Its address is synthetic; the
# sidecar logs every address it parses (docs/LOGGING.md PII policy).
#
# Stands down while libpostal.service isn't active: a failed unit has alerted
# already, an activating one is restarting, and a stopped one was stopped on
# purpose. The count starts afresh after a stand-down.
#
# The count lives in RuntimeDirectory= (/run/libpostal-probe), so a reboot
# clears it: misses before a reboot and the boot's warmup aren't consecutive.
#
# Exit: 0 answered, below the threshold, past it, or stood down; 1 the
# threshold reached on this run; 2 run outside the unit (no RUNTIME_DIRECTORY).
# Read-only: it starts and restarts nothing, so the patching-hosts restarter
# scan reads it clean. Keep systemctl verbs other than is-active out of it.

set -uo pipefail

# shellcheck source=infra/journal-warn.sh
source "$(dirname "${BASH_SOURCE[0]}")/journal-warn.sh"

URL='http://localhost:4400/parse?address=1+Main+St'
UNIT=libpostal.service
MAX_TIME=5
MISSES_TO_ALERT=3
ME=libpostal-probe

if [[ -z "${RUNTIME_DIRECTORY:-}" ]]; then
  journal_warn "$ME: RUNTIME_DIRECTORY unset; run it as libpostal-probe.service"
  exit 2
fi
COUNTER="$RUNTIME_DIRECTORY/misses"

misses=$(cat "$COUNTER" 2>/dev/null) || misses=0
[[ "$misses" =~ ^[0-9]+$ ]] || misses=0

save() {
  printf '%s\n' "$1" >"$COUNTER.tmp" && mv -f "$COUNTER.tmp" "$COUNTER"
}

# A curl exit code, as a reason a reader needn't look up
reason() {
  case $1 in
    7) echo "connection refused" ;;
    22) echo "HTTP error status" ;;
    28) echo "no answer within ${MAX_TIME}s" ;;
    52) echo "empty reply" ;;
    56) echo "connection dropped" ;;
    *) echo "curl failed" ;;
  esac
}

state=$(systemctl is-active "$UNIT" 2>/dev/null) || {
  echo "$ME: $UNIT is ${state:-unknown}; standing down"
  save 0
  exit 0
}

rc=0
curl -sf -o /dev/null --max-time "$MAX_TIME" "$URL" || rc=$?

if [[ "$rc" -eq 0 ]]; then
  if [[ "$misses" -ge "$MISSES_TO_ALERT" ]]; then
    echo "$ME: answering again after $misses consecutive misses (alerted at $MISSES_TO_ALERT)"
  elif [[ "$misses" -eq 1 ]]; then
    echo "$ME: answering again after 1 consecutive miss"
  elif [[ "$misses" -gt 1 ]]; then
    echo "$ME: answering again after $misses consecutive misses"
  fi
  save 0
  exit 0
fi

misses=$((misses + 1))
save "$misses"
why="$(reason "$rc") (curl exit $rc)"
if [[ "$misses" -lt "$MISSES_TO_ALERT" ]]; then
  journal_warn "$ME: miss $misses/$MISSES_TO_ALERT: $why"
  exit 0
fi
if [[ "$misses" -eq "$MISSES_TO_ALERT" ]]; then
  journal_warn "$ME: miss $misses/$MISSES_TO_ALERT: $why; threshold reached, $UNIT is active but not answering"
  exit 1
fi
echo "$ME: miss $misses: $why; alerted at miss $MISSES_TO_ALERT"
exit 0
