#!/usr/bin/env bash
# Self-checking sandbox tests for infra/address-validator-liveness.sh (#255).
#
# Fake `curl`, `sleep` and `hostname` on PATH stand in for the API, co-status
# and the host, and a scratch dir stands in for the unit's credentials
# directory, so nothing here reaches the real API, co-status or the journal.
#
# Usage: bash tests/shell/address-validator-liveness-test.sh

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
lacks() { ! /bin/grep -q -- "$1" "$2"; }
has() { /bin/grep -q -- "$1" "$2"; }
# jqis FILTER EXPECTED: the recorded check-in body's FILTER is EXPECTED
jqis() { [[ "$(jq -r "$1" "$sandbox/checkin.body")" == "$2" ]]; }
# jqmatch FILTER REGEX: the same, matched against an anchored regex. Elapsed
# time is real seconds even with sleep faked: a slow run can cross a second.
jqmatch() { [[ "$(jq -r "$1" "$sandbox/checkin.body")" =~ ^$2$ ]]; }

# Run from a systemd unit, the rig would inherit its parent's journal stream
unset JOURNAL_STREAM

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
sandbox="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$sandbox"' EXIT

SCRIPT="$repo_root/infra/address-validator-liveness.sh"
OUT="$sandbox/out.log"
ERR="$sandbox/err.log"
CREDS="$sandbox/creds"
KEY=sk_test_liveness_key
MONITOR=01TESTMONITOR0000000000000
mkdir -p "$sandbox/bin" "$CREDS"

# The fakes read their behaviour from files, so one PATH serves every case.
# curl, by the URL (its last argument):
#   /api/v2/health  one line of health.seq per attempt, the last repeating:
#                   `<curl rc> [<http code> <body>]`
#   /health         co-status's: prints status.health, exits status.rc
#   /checkin        stdin to checkin.stdin, --data-binary to checkin.body;
#                   exits checkin.rc, else prints checkin.code
cat >"$sandbox/bin/curl" <<EOF
#!/bin/bash
sb="$sandbox"
printf '%s\n' "\$*" >>"\$sb/curl.args"
url="\${*: -1}"
case \$url in
  */api/v2/health)
    n=\$((\$(cat "\$sb/health.n") + 1))
    echo "\$n" >"\$sb/health.n"
    line=\$(sed -n "\${n}p" "\$sb/health.seq")
    [ -n "\$line" ] || line=\$(tail -n 1 "\$sb/health.seq")
    read -r rc code body <<<"\$line"
    if [ "\$rc" != 0 ]; then printf '\n000'; exit "\$rc"; fi
    printf '%s\n%s' "\$body" "\$code"
    ;;
  */health)
    cat "\$sb/status.health"
    exit "\$(cat "\$sb/status.rc")"
    ;;
  */checkin)
    cat >"\$sb/checkin.stdin"
    prev=""
    for a in "\$@"; do
      [ "\$prev" = --data-binary ] && printf '%s\n' "\$a" >"\$sb/checkin.body"
      prev=\$a
    done
    rc=\$(cat "\$sb/checkin.rc")
    [ "\$rc" = 0 ] || exit "\$rc"
    cat "\$sb/checkin.code"
    ;;
esac
EOF
cat >"$sandbox/bin/sleep" <<EOF
#!/bin/sh
echo "\$1" >>"$sandbox/sleeps"
EOF
cat >"$sandbox/bin/hostname" <<'EOF'
#!/bin/sh
echo testhost
EOF
chmod +x "$sandbox/bin/curl" "$sandbox/bin/sleep" "$sandbox/bin/hostname"

OK='{"status":"ok","api_version":"2","database":"ok","libpostal":"ok"}'
DEGRADED='{"status":"degraded","api_version":"2","database":"error","libpostal":"unavailable"}'

# health LINE...: the API's answer to each attempt in turn
health() { printf '%s\n' "$@" >"$sandbox/health.seq"; }

# run: one run, configured unless MONITOR_ID/key are changed; RC holds its exit
run() {
  echo 0 >"$sandbox/health.n"
  : >"$OUT"
  : >"$ERR"
  rm -f "$sandbox/curl.args" "$sandbox/sleeps" "$sandbox/checkin.stdin" "$sandbox/checkin.body"
  RC=0
  PATH="$sandbox/bin:$PATH" JOURNAL_STREAM=$err_stream CREDENTIALS_DIRECTORY=$CREDS \
    LIVENESS_MONITOR_ID=$MONITOR_ID "$SCRIPT" >"$OUT" 2>"$ERR" || RC=$?
}
attempts() { cat "$sandbox/health.n"; }
sleeps() { [ -f "$sandbox/sleeps" ] && wc -l <"$sandbox/sleeps" || echo 0; }

reset() {
  MONITOR_ID=$MONITOR
  printf '%s\n' "$KEY" >"$CREDS/status-checkin.key"
  printf '%s' '{"status":"ok","environment":"production"}' >"$sandbox/status.health"
  echo 0 >"$sandbox/status.rc"
  echo 0 >"$sandbox/checkin.rc"
  printf 202 >"$sandbox/checkin.code"
}

touch "$OUT" "$ERR"
err_stream=$(stat -L -c '%d:%i' "$ERR")

# --- healthy ---
reset
health "0 200 $OK"
run
check "ok: exits 0" test "$RC" -eq 0
check "ok: one attempt, no retry" test "$(attempts)" = 1 -a "$(sleeps)" = 0
check "ok: logs nothing" test ! -s "$OUT" -a ! -s "$ERR"
check "ok: probes the knob's health URL, bounded" has \
  "^-s -w .* --max-time 5 http://localhost:8000/api/v2/health$" "$sandbox/curl.args"
check "ok: asks co-status's environment first" has "^-s --max-time 5 http://status:9000/health$" "$sandbox/curl.args"
check "ok: posts to the monitor's check-in, bounded at 10s" has \
  "--max-time 10 -X POST -H @- .*http://status:9000/api/v1/monitors/$MONITOR/checkin$" "$sandbox/curl.args"
check "ok: the key reaches curl on stdin" test "$(cat "$sandbox/checkin.stdin")" = "X-API-Key: $KEY"
check "ok: the key is never in curl's argv" lacks "$KEY" "$sandbox/curl.args"
check "ok: status ok" jqis .status ok
check "ok: no metadata" jqis '.metadata // "none"' none
check "ok: variables are the allowlist, all strings" jqis \
  '.variables | to_entries | map("\(.key)=\(.value)|\(.value|type)") | join(",")' \
  "source_host=testhost|string,outcome=ok|string,error=|string,attempts=1|string,health_status=ok|string,database=ok|string,libpostal=ok|string"

# --- a restart: refused twice, then healthy, inside the retry ---
reset
health 7 7 "0 200 $OK"
run
check "restart: exits 0" test "$RC" -eq 0
check "restart: three attempts, 2 s apart" test "$(attempts)" = 3 -a "$(sleeps)" = 2
check "restart: no warning" test ! -s "$ERR"
check "restart: checks in ok" jqis .status ok
check "restart: counts its attempts" jqis .variables.attempts 3

# The boot's 503 window, inside the retry: not degraded
health "0 503 $DEGRADED" "0 503 $DEGRADED" "0 200 $OK"
run
check "boot 503 then ok: checks in ok" jqis .status ok

# --- stopped: refused for the whole retry ---
reset
health 7
run
check "stopped: exits 0 (co-status pages, not OnFailure=)" test "$RC" -eq 0
check "stopped: 11 attempts, 10 waits of 2 s" test "$(attempts)" = 11 -a "$(sleeps)" = 10
check "stopped: waits are 2 s" test "$(sort -u "$sandbox/sleeps")" = 2
check "stopped: alert" jqis .status alert
check "stopped: outcome unreachable" jqis .variables.outcome unreachable
check "stopped: metadata.fault is the outcome" jqis .metadata.fault unreachable
check "stopped: error names the cause" jqmatch .variables.error \
  "/api/v2/health: no healthy answer after 11 attempts over [0-9]+s: connection refused \(curl exit 7\)"
check "stopped: no health fields" jqis '[.variables.health_status, .variables.database, .variables.libpostal] | join("")' ""
check "stopped: journal warning" has "^<4>address-validator-liveness: unreachable: /api/v2/health: no healthy answer" "$ERR"

# --- hung: each attempt times out ---
health 28
run
check "hung: unreachable, names the timeout" jqmatch .variables.error \
  "/api/v2/health: no healthy answer after 11 attempts over [0-9]+s: no answer within 5s \(curl exit 28\)"

# --- an unexpected status: unreachable, with the status ---
health "0 500 Internal"
run
check "HTTP 500: unreachable" jqis .variables.outcome unreachable
check "HTTP 500: error names it" jqmatch .variables.error \
  "/api/v2/health: no healthy answer after 11 attempts over [0-9]+s: HTTP 500"

# --- degraded: a 503 for the whole retry ---
health "0 503 $DEGRADED"
run
check "degraded: exits 0" test "$RC" -eq 0
check "degraded: alert" jqis .status alert
check "degraded: outcome degraded" jqis .variables.outcome degraded
check "degraded: metadata.fault degraded" jqis .metadata.fault degraded
check "degraded: error names the failing component" jqmatch .variables.error \
  "/api/v2/health answered 503 \(database error\) after 11 attempts over [0-9]+s"
check "degraded: health fields from the body" jqis \
  '[.variables.health_status, .variables.database, .variables.libpostal] | join(",")' "degraded,error,unavailable"

# degraded, then unreachable: the last answer is the outcome
health "0 503 $DEGRADED" 7
run
check "degraded then refused: unreachable" jqis .metadata.fault unreachable

# A body outside the models.py Literals never reaches notifier
health '0 503 {"status":"degraded","database":"1 Main St","libpostal":["x"],"extra":"secret"}'
run
check "odd body: database unknown" jqis .variables.database unknown
check "odd body: libpostal unknown" jqis .variables.libpostal unknown
check "odd body: no other field forwarded" lacks secret "$sandbox/checkin.body"
check "odd body: no address forwarded" lacks Main "$sandbox/checkin.body"
health '0 503 not json'
run
check "non-JSON 503: still degraded" jqis .variables.outcome degraded
check "non-JSON 503: fields unknown" jqis .variables.health_status unknown

# --- unconfigured: probe and log, no check-in ---
reset
health "0 200 $OK"
MONITOR_ID=""
run
check "no monitor id: exits 0" test "$RC" -eq 0
check "no monitor id: says unconfigured" test "$(cat "$OUT")" = "address-validator-liveness: ok; liveness check-in unconfigured"
check "no monitor id: co-status not called" lacks status:9000 "$sandbox/curl.args"
reset
: >"$CREDS/status-checkin.key"
health 7
run
check "empty key (SetCredential= fallback): unconfigured, with the outcome" \
  has "^address-validator-liveness: unreachable; liveness check-in unconfigured$" "$OUT"
check "empty key: the fault still warns" has "^<4>address-validator-liveness: unreachable:" "$ERR"
check "empty key: co-status not called" lacks status:9000 "$sandbox/curl.args"
reset
RC=0
PATH="$sandbox/bin:$PATH" LIVENESS_MONITOR_ID=$MONITOR env -u CREDENTIALS_DIRECTORY \
  "$SCRIPT" >"$OUT" 2>"$ERR" || RC=$?
check "no credentials directory: unconfigured, exits 0" test "$RC" -eq 0
check "no credentials directory: says so" has "liveness check-in unconfigured" "$OUT"
check "no JOURNAL_STREAM: no priority prefix" lacks "^<" "$ERR"

# A key file with a CRLF ending: the header carries the key alone
reset
printf '%s\r\n' "$KEY" >"$CREDS/status-checkin.key"
health "0 200 $OK"
run
check "CRLF key: header is clean" test "$(cat "$sandbox/checkin.stdin")" = "X-API-Key: $KEY"
printf ' \t%s \r\n\n' "$KEY" >"$CREDS/status-checkin.key"
run
check "padded key: header is clean" test "$(cat "$sandbox/checkin.stdin")" = "X-API-Key: $KEY"

# --- co-status: wrong environment, unreachable, refusing ---
reset
health "0 200 $OK"
printf '%s' '{"status":"ok","environment":"development"}' >"$sandbox/status.health"
run
check "dev co-status: exits 0" test "$RC" -eq 0
check "dev co-status: no check-in" test ! -e "$sandbox/checkin.body"
check "dev co-status: warns" has "^<4>address-validator-liveness: http://status:9000/health says environment=development, not production; not checking in$" "$ERR"
: >"$sandbox/status.health"
echo 6 >"$sandbox/status.rc"
run
check "co-status unresolvable: exits 0" test "$RC" -eq 0
check "co-status unresolvable: warns" has "environment=<no answer>, not production" "$ERR"
reset
echo 28 >"$sandbox/checkin.rc"
run
check "check-in timeout: exits 0" test "$RC" -eq 0
check "check-in timeout: warns with its own limit" has \
  "^<4>address-validator-liveness: check-in failed: no answer within 10s (curl exit 28); co-status's deadline is the backstop$" "$ERR"
reset
printf 422 >"$sandbox/checkin.code"
run
check "check-in 422: exits 0" test "$RC" -eq 0
check "check-in 422: warns" has "check-in refused: HTTP 422" "$ERR"
check "no line anywhere carries the key" lacks "$KEY" "$ERR"

echo
if ((FAILS > 0)); then
  echo "$FAILS assertion(s) failed"
  exit 1
fi
echo "all assertions passed"
