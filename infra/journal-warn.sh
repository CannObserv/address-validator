# shellcheck shell=bash
# Journal warnings for the shell timer scripts, attributed to their run (#246).
# Sourced, not executed: disk-hygiene.sh, docker-prune-check.sh.
#
# `logger` / `systemd-cat` run as short-lived children. journald looks up a
# sender's cgroup after the fact, so once the child has exited its line carries
# no _SYSTEMD_UNIT or _SYSTEMD_INVOCATION_ID, and the WARNING+ tail that
# notify_unit_failure.py sends off-host (#232) never finds it. A line on the
# unit's own stderr stream is attributed through the stream, and an sd-daemon
# `<N>` prefix sets its priority: the mechanism infra/journal_logging.py uses
# for the Python scripts. The unit's SyslogIdentifier= keeps `journalctl -t`.

# True when stderr is the stream systemd connected to journald. JOURNAL_STREAM
# is `<dev>:<inode>` of that stream; a child whose stderr was redirected
# elsewhere inherits the variable but not the stream. Without the check, a
# terminal run (`disk-hygiene.sh --dry-run`) would print a literal `<4>`.
stderr_is_journal() {
  [[ -n "${JOURNAL_STREAM:-}" ]] || return 1
  [[ "$(stat -L -c '%d:%i' "/proc/$$/fd/2" 2>/dev/null)" == "$JOURNAL_STREAM" ]]
}

JOURNAL_WARN_PREFIX=""
if stderr_is_journal; then
  JOURNAL_WARN_PREFIX="<4>"
fi

# journal_warn MESSAGE: one stderr line, at warning priority under systemd
journal_warn() {
  printf '%s%s\n' "$JOURNAL_WARN_PREFIX" "$*" >&2
}
