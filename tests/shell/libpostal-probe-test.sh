#!/usr/bin/env bash
# Self-checking sandbox tests for infra/libpostal-probe.sh (#256).
#
# Fake `curl` and `systemctl` on PATH stand in for the sidecar and its unit,
# and a scratch dir stands in for the unit's RuntimeDirectory=, so nothing
# here touches the real sidecar or the journal.
#
# Usage: bash tests/shell/libpostal-probe-test.sh

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
lacks() { ! /bin/grep -q "$1" "$2"; }

# Run from a systemd unit, the rig would inherit its parent's journal stream
unset JOURNAL_STREAM

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
sandbox="$(cd "$(mktemp -d)" && pwd -P)"
trap 'rm -rf "$sandbox"' EXIT

SCRIPT="$repo_root/infra/libpostal-probe.sh"
OUT="$sandbox/out.log"
ERR="$sandbox/err.log"
STATE="$sandbox/run"
mkdir -p "$sandbox/bin" "$STATE"

# The fakes read their behaviour from files, so one PATH serves every case.
# curl: exits with the code in curl.rc (0 = the sidecar answered) and logs
# its arguments; systemctl: prints and exits by the unit state in unit.state.
cat >"$sandbox/bin/curl" <<EOF
#!/bin/sh
printf '%s\n' "\$*" >>"$sandbox/curl.args"
exit "\$(cat "$sandbox/curl.rc")"
EOF
cat >"$sandbox/bin/systemctl" <<EOF
#!/bin/sh
state=\$(cat "$sandbox/unit.state")
# NONE: systemctl couldn't reach the manager, so it prints no state
[ "\$state" = NONE ] && exit 1
echo "\$state"
[ "\$state" = active ]
EOF
chmod +x "$sandbox/bin/curl" "$sandbox/bin/systemctl"

# probe <curl rc> [unit state]: one run; RC holds its exit status
probe() {
  echo "$1" >"$sandbox/curl.rc"
  echo "${2:-active}" >"$sandbox/unit.state"
  : >"$OUT"
  : >"$ERR"
  RC=0
  PATH="$sandbox/bin:$PATH" RUNTIME_DIRECTORY=$STATE JOURNAL_STREAM=$err_stream \
    "$SCRIPT" >"$OUT" 2>"$ERR" || RC=$?
}
misses() { cat "$STATE/misses"; }

touch "$OUT" "$ERR"
err_stream=$(stat -L -c '%d:%i' "$ERR")

# --- healthy ---
probe 0
check "answer: exits 0" test "$RC" -eq 0
check "answer: counter at 0" test "$(misses)" = 0
check "answer: logs nothing" test ! -s "$OUT" -a ! -s "$ERR"
check "probes the knob's synthetic URL, bounded" /bin/grep -qx -- \
  "-sf -o /dev/null --max-time 5 http://localhost:4400/parse?address=1+Main+St" "$sandbox/curl.args"

# --- an outage: one alert, on the transition ---
probe 28
check "miss 1: exits 0" test "$RC" -eq 0
check "miss 1: warning with the curl reason" /bin/grep -qx "<4>libpostal-probe: miss 1/3: no answer within 5s (curl exit 28).*" "$ERR"
probe 7
check "miss 2: exits 0" test "$RC" -eq 0
check "miss 2: names its reason" /bin/grep -q "miss 2/3: connection refused (curl exit 7)" "$ERR"
probe 28
check "miss 3: exits 1, so OnFailure= alerts" test "$RC" -eq 1
check "miss 3: warning names the threshold" /bin/grep -q "^<4>libpostal-probe: miss 3/3: .*threshold reached" "$ERR"
probe 28
check "miss 4: exits 0, no second alert" test "$RC" -eq 0
check "miss 4: info only, already alerted" /bin/grep -q "miss 4: .*alerted at miss 3" "$OUT"
check "miss 4: no warning" test ! -s "$ERR"
probe 52
check "miss 5: exits 0" test "$RC" -eq 0
probe 0
check "recovery: exits 0" test "$RC" -eq 0
check "recovery: info line" /bin/grep -qx "libpostal-probe: answering again after 5 consecutive misses (alerted at 3)" "$OUT"
check "recovery: no warning" test ! -s "$ERR"
check "recovery: counter reset" test "$(misses)" = 0

# --- a restart: a miss or two, never an alert ---
probe 56
probe 0
check "restart: one miss then an answer, no alert" test "$RC" -eq 0
check "restart: recovery names no alert" /bin/grep -qx "libpostal-probe: answering again after 1 consecutive miss" "$OUT"
probe 7
probe 7
probe 0
check "two misses then an answer: no alert" test "$RC" -eq 0

# --- the unit isn't active: stand down, and start counting afresh ---
probe 7
probe 7
: >"$sandbox/curl.args"
probe 7 activating
check "unit activating: exits 0" test "$RC" -eq 0
check "unit activating: curl not run" test ! -s "$sandbox/curl.args"
check "unit activating: counter reset" test "$(misses)" = 0
check "unit activating: info line names the state and the count" /bin/grep -qx "libpostal-probe: libpostal.service is activating; standing down (count was 2)" "$OUT"
probe 7
check "after a stand-down: the count restarts at 1" test "$(misses)" = 1
probe 0 failed
check "unit failed (crashloop alerted already): exits 0" test "$RC" -eq 0
probe 7
probe 7
probe 7
probe 7 inactive
check "stand-down after an alert: says so" /bin/grep -qx "libpostal-probe: libpostal.service is inactive; standing down (count was 3, alerted at 3)" "$OUT"
probe 0 inactive
check "stand-down with no count: bare line" /bin/grep -qx "libpostal-probe: libpostal.service is inactive; standing down" "$OUT"

# --- no state at all: not a stand-down, or the probe goes blind ---
probe 0
: >"$sandbox/curl.args"
probe 7 NONE
check "no unit state: still probes" test -s "$sandbox/curl.args"
check "no unit state: warns that it can't read it" /bin/grep -q "^<4>libpostal-probe: can't read libpostal.service's state; probing anyway" "$ERR"
check "no unit state: the miss counts" test "$(misses)" = 1
probe 0

# --- state the script must not trust ---
echo garbage >"$STATE/misses"
probe 7
check "unreadable counter: read as 0" test "$(misses)" = 1
rm -f "$STATE/misses"
probe 0
check "no counter yet: exits 0" test "$RC" -eq 0

# An unwritable count would never reach the threshold: fail loudly instead.
# Root writes through a read-only mode, so the case can't be staged as root.
if [[ "$EUID" -ne 0 ]]; then
  chmod a-w "$STATE"
  probe 7
  chmod u+w "$STATE"
  check "unwritable counter: exits 2" test "$RC" -eq 2
  check "unwritable counter: warning names it" /bin/grep -q "^<4>libpostal-probe: can't write .*/misses" "$ERR"
  probe 0 >/dev/null
  check "unwritable counter: answer path exits 0 once writable" test "$RC" -eq 0
else
  echo "SKIP: unwritable counter (running as root)"
fi

RC=0
PATH="$sandbox/bin:$PATH" env -u RUNTIME_DIRECTORY "$SCRIPT" >"$OUT" 2>"$ERR" || RC=$?
check "no RUNTIME_DIRECTORY: exits 2 (config error, alerts)" test "$RC" -eq 2
check "no RUNTIME_DIRECTORY: says to run the unit" /bin/grep -q "libpostal-probe.service" "$ERR"
check "no JOURNAL_STREAM: no priority prefix" lacks "^<" "$ERR"

# --- PII (docs/LOGGING.md): the address never reaches a log line ---
probe 7
probe 7
probe 7
check "alert run: address not logged" lacks "Main" "$ERR"

echo
if ((FAILS > 0)); then
  echo "$FAILS assertion(s) failed"
  exit 1
fi
echo "all assertions passed"
