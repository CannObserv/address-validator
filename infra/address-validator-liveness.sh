#!/bin/bash
# address-validator liveness check-in (#255): co-status pages when the API is
# hung, degraded, or this VM is down.
#
# address-validator.service alerts on a crashloop (#248), but a process that is
# up and hung, or up with its database unreachable, never fails the unit, and
# nothing on this VM can report the VM's own absence. So every 5 minutes
# (address-validator-liveness.timer) this probes /api/v2/health locally and
# checks in to co-status's dead-man monitor `co-address-validator-live`, which
# runs on its own VM:
#
#   200                            -> `ok`
#   503                            -> `alert`, outcome `degraded`
#   anything else, after the retry -> `alert`, outcome `unreachable`
#   no check-in at all             -> co-status's deadline: "has stopped reporting"
#
# co-status reports a run of `alert` check-ins as one fault: once when it opens,
# then once per renotify_seconds, and the `ok` that ends it sends "has cleared"
# (CannObserv/status#28). metadata.fault carries the outcome, so a change of
# fault (degraded -> unreachable) reports at once.
#
# The probe retries every RETRY_INTERVAL s for RETRY_SECONDS, so a deploy
# restart (~4 s start to listening, 2026-10-19) or the boot's ~6 s 503
# (2026-09-29) is never an alarm. A hung API costs one attempt per
# PROBE_MAX_TIME + RETRY_INTERVAL, so a run is bounded near RETRY_SECONDS +
# PROBE_MAX_TIME, plus the two co-status calls.
#
# libpostal state never changes /api/v2/health's HTTP status; libpostal-probe
# (#256) owns it. The probe is a health read: it writes one audit_log row
# (endpoint /api/v2/health, outside the dashboard's API totals) and no address.
#
# variables go to notifier when a fault reports, so they are an allowlist, never
# the health body: each health field is checked against its models.py Literal.
#
# The check-in key is a credential (LoadCredential=, root-owned file), never in
# .env, never in argv: it reaches curl on stdin. The monitor id is not a secret
# (Environment= in the unit). Either missing: probe, log, and exit 0.
#
# Exit: always 0 once the probe has run. A co-status that is unreachable or
# refuses the check-in is a warning: its deadline is the backstop, and a failing
# unit would turn a co-status outage into an alert from every consumer.
# Read-only: it starts and restarts nothing, so the patching-hosts restarter
# scan reads it clean. Keep systemctl out of it.

set -uo pipefail

# shellcheck source=infra/journal-warn.sh
source "$(dirname "${BASH_SOURCE[0]}")/journal-warn.sh"

HEALTH_URL='http://localhost:8000/api/v2/health'
STATUS_URL='http://status:9000'
CREDENTIAL=status-checkin.key
PROBE_MAX_TIME=5
RETRY_SECONDS=20
RETRY_INTERVAL=2
MAX_ATTEMPTS=11
# co-status answers a check-in inside 8 s; consumers wait 10 (monitors.md)
CHECKIN_MAX_TIME=10
ME=address-validator-liveness

# reason RC MAX_TIME: a curl exit code, as a reason a reader needn't look up
reason() {
  case $1 in
    7) echo "connection refused" ;;
    28) echo "no answer within ${2}s" ;;
    52) echo "empty reply" ;;
    56) echo "connection dropped" ;;
    *) echo "curl failed" ;;
  esac
}

# allow VALUE ALLOWED...: VALUE if it is one of ALLOWED, else `unknown`
allow() {
  local value=$1 ok
  shift
  for ok in "$@"; do
    [[ "$value" == "$ok" ]] && { echo "$value"; return; }
  done
  echo unknown
}

# --- probe ---
start=$SECONDS
attempts=0
outcome=unreachable
cause=""
health_status="" database="" libpostal=""
while :; do
  attempts=$((attempts + 1))
  rc=0
  out=$(curl -s -w '\n%{http_code}' --max-time "$PROBE_MAX_TIME" "$HEALTH_URL") || rc=$?
  code=${out##*$'\n'}
  body=${out%$'\n'*}
  if [[ "$rc" -eq 0 && "$code" == 200 ]]; then
    outcome=ok
    cause=""
  elif [[ "$rc" -eq 0 && "$code" == 503 ]]; then
    outcome=degraded
    cause="503"
  elif [[ "$rc" -eq 0 ]]; then
    outcome=unreachable
    cause="HTTP $code"
  else
    outcome=unreachable
    cause="$(reason "$rc" "$PROBE_MAX_TIME") (curl exit $rc)"
  fi
  if [[ "$rc" -eq 0 && ( "$code" == 200 || "$code" == 503 ) ]]; then
    fields=$(jq -r '[.status, .database, .libpostal] | map(tostring) | join(" ")' <<<"$body" 2>/dev/null) || fields=""
    read -r health_status database libpostal <<<"$fields"
    health_status=$(allow "${health_status:-}" ok degraded)
    database=$(allow "${database:-}" ok error unconfigured)
    libpostal=$(allow "${libpostal:-}" ok unavailable)
  else
    health_status="" database="" libpostal=""
  fi
  [[ "$outcome" == ok ]] && break
  ((attempts >= MAX_ATTEMPTS || SECONDS - start + RETRY_INTERVAL > RETRY_SECONDS)) && break
  sleep "$RETRY_INTERVAL"
done

elapsed=$((SECONDS - start))
error=""
case $outcome in
  degraded)
    error="/api/v2/health answered 503 (database $database) after $attempts attempts over ${elapsed}s"
    ;;
  unreachable)
    error="/api/v2/health: no healthy answer after $attempts attempts over ${elapsed}s: $cause"
    ;;
esac
[[ -n "$error" ]] && journal_warn "$ME: $outcome: $error"

# --- check in ---
monitor=${LIVENESS_MONITOR_ID:-}
key=""
if [[ -n "${CREDENTIALS_DIRECTORY:-}" && -r "$CREDENTIALS_DIRECTORY/$CREDENTIAL" ]]; then
  IFS= read -r key <"$CREDENTIALS_DIRECTORY/$CREDENTIAL" || true
  key=${key%$'\r'}
fi
if [[ -z "$monitor" || -z "$key" ]]; then
  echo "$ME: $outcome; liveness check-in unconfigured"
  exit 0
fi

# A check-in landing on dev's co-status would let production's deadline page a
# healthy API (monitors.md "Check the endpoint before the timer").
environment=$(curl -s --max-time "$PROBE_MAX_TIME" "$STATUS_URL/health" | jq -r '.environment' 2>/dev/null) || true
if [[ "$environment" != production ]]; then
  journal_warn "$ME: $STATUS_URL/health says environment=${environment:-<no answer>}, not production; not checking in"
  exit 0
fi

checkin_status=alert
[[ "$outcome" == ok ]] && checkin_status=ok
payload=$(jq -nc \
  --arg status "$checkin_status" \
  --arg source_host "$(hostname)" \
  --arg outcome "$outcome" \
  --arg error "$error" \
  --arg attempts "$attempts" \
  --arg health_status "$health_status" \
  --arg database "$database" \
  --arg libpostal "$libpostal" \
  '{status: $status,
    variables: {source_host: $source_host, outcome: $outcome, error: $error,
                attempts: $attempts, health_status: $health_status,
                database: $database, libpostal: $libpostal}}
   + (if $status == "alert" then {metadata: {fault: $outcome}} else {} end)')

rc=0
code=$(printf 'X-API-Key: %s\n' "$key" | curl -s -o /dev/null -w '%{http_code}' \
  --max-time "$CHECKIN_MAX_TIME" -X POST -H @- -H 'Content-Type: application/json' \
  --data-binary "$payload" "$STATUS_URL/api/v1/monitors/$monitor/checkin") || rc=$?
if [[ "$rc" -ne 0 ]]; then
  journal_warn "$ME: check-in failed: $(reason "$rc" "$CHECKIN_MAX_TIME") (curl exit $rc); co-status's deadline is the backstop"
elif [[ "$code" != 202 ]]; then
  journal_warn "$ME: check-in refused: HTTP $code; co-status's deadline is the backstop"
fi
exit 0
