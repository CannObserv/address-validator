"""The patching-hosts knob reads clean and keeps the decisions it records (#311).

``.skills/patching-hosts`` tells the vendored ``patching-hosts`` skill what only
this repo's owner knows. **A malformed line makes the host report-only**, which
reads as "nothing to do" at the next monthly run rather than as an error, so the
skill's own reader is run here. It ships in a submodule CI doesn't initialise:
off this host that is a skip, on it a failure. The rest are pure parses.

Shape from CannObserv/broker's ``tests/deploy/test_patching_knob.py``.
"""

from __future__ import annotations

import json
import re
import shlex
import socket
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
KNOB = REPO_ROOT / ".skills" / "patching-hosts"
READ_KNOB = REPO_ROOT / "skills" / "patching-hosts" / "scripts" / "read-knob.sh"
INFRA = REPO_ROOT / "infra"
APT_PREFS = INFRA / "apt-preferences.d" / "address-validator"
APT_PREFS_INSTALLED = Path("/etc/apt/preferences.d/address-validator")
HOST = "address-validator"
DEPLOY_ROOT = "/home/exedev/address-validator"

PUBLIC_CALLERS = {"CannObserv/power-map", "CannObserv/wslcb-licensing-tracker"}
FOLLOWED = {
    "deb.nodesource.com": {"nodejs"},
    "pkgs.tailscale.com": {"tailscale", "tailscale-archive-keyring"},
}


def _directives() -> list[list[str]]:
    """Each directive line's words. ``#`` opens a comment at a line's start or
    after whitespace (knob.md), so a command keeps a bare ``#``."""
    lines = []
    for raw in KNOB.read_text().splitlines():
        words = []
        for word in raw.split():
            if word.startswith("#"):
                break
            words.append(word)
        if words:
            lines.append(words)
    return lines


def _named(directive: str) -> list[list[str]]:
    return [words[1:] for words in _directives() if words[0] == directive]


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _span(hhmm_range: str) -> tuple[int, int]:
    start, end = hhmm_range.split("-")
    return _minutes(start), _minutes(end)


def test_the_knob_reads_clean() -> None:
    if not READ_KNOB.is_file():
        if socket.gethostname() == HOST:
            pytest.fail(f"{READ_KNOB} missing on the host: run bash .skills/doctor.sh")
        pytest.skip("patching-hosts submodule not initialised")
    out = subprocess.run(
        ["bash", str(READ_KNOB), "--config", str(KNOB), "--host", HOST],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    knob = json.loads(out)
    assert knob["findings"] == []
    assert knob["report_only"] is False, knob["report_only_reasons"]
    assert knob["class"]["value"] == "production"
    assert knob["posture"]["value"] == "scheduled"


def test_the_window_is_the_owners_and_in_utc() -> None:
    """2nd Sunday 17:00-19:00 UTC (#272, 2026-10-16). The grammar has no week of
    the month, so the line admits every Sunday; UTC so DST never moves it."""
    assert _named("window") == [["Sun", "17:00-19:00"]]


def test_no_image_owner_line() -> None:
    """The image repo is private; this one is public (gregoryfoster/skills#350)."""
    assert _named("image-owner") == []


# Frequent probes: a timer that fires every few minutes can't sit inside a quiet
# range, and a liveness probe shouldn't stand down for the window, where the
# service it watches is restarted on purpose. So each is declared here with its
# reason, and held to a class contract instead (the tests below): monotonic,
# never overlapping itself, restarting nothing, and its miss threshold sized so
# a deliberate restart never alerts. Every other timer is a scheduled job, still
# bound by the quiet-range rule.
FREQUENT_PROBES = {
    "libpostal-probe.timer": (
        "#256: libpostal answering, every 2 min. Reads one synthetic /parse; "
        "a quiet range or a window stand-down would blind it exactly when the "
        "patch run restarts the sidecar"
    ),
}
# The patching-hosts restarter scan's own pattern (probe.sh RESTART_RE): a
# script line matching it makes its unit a restarter the knob must declare.
SKILL_RESTART_RE = re.compile(r"systemctl[^#]*(start|restart|try-restart|reload-or-restart)")
# The longest deliberate libpostal restart a patch run may cause. Measured
# 4-7 s accept-to-answer (2026-09-29 boot, two 2026-09-30 restarts); the margin
# is for a docker package upgrade restarting the daemon, which is unmeasured.
LIBPOSTAL_RESTART_SECONDS = 120
LIBPOSTAL_PROBE = INFRA / "libpostal-probe.sh"


def _seconds(span: str) -> int:
    """A plain systemd time span: ``30``, ``30s``, ``2min``, ``1h``."""
    match = re.fullmatch(r"(\d+)\s*(s|sec|m|min|h)?", span.strip())
    assert match, f"time span this test can't read: {span}"
    unit = {"m": 60, "min": 60, "h": 3600}.get(match.group(2) or "s", 1)
    return int(match.group(1)) * unit


def _setting(path: Path, key: str) -> list[str]:
    return re.findall(rf"^{key}=(.*)$", path.read_text(), re.M)


def test_every_timer_fires_inside_a_quiet_range_and_outside_the_window() -> None:
    """A new timer, or a moved one, that lands in the window goes red here."""
    quiet = [_span(words[0]) for words in _named("quiet") if len(words) == 1]
    (window,) = [_span(words[1]) for words in _named("window")]
    timers = sorted(t for t in INFRA.glob("*.timer") if t.name not in FREQUENT_PROBES)
    assert timers
    for timer in timers:
        calendars = _setting(timer, "OnCalendar")
        assert calendars, f"{timer.name} has no OnCalendar= this test can read"
        for calendar in calendars:
            clock = re.search(r"(\d{2}:\d{2})", calendar)
            assert clock, f"{timer.name}: OnCalendar={calendar} names no HH:MM"
            at = _minutes(clock.group(1))
            inside = any(start <= at < end for start, end in quiet)
            assert inside, f"{timer.name} fires outside every quiet range"
            assert not window[0] <= at < window[1], f"{timer.name} fires inside the window"


def test_each_frequent_probe_is_a_timer_with_a_reason() -> None:
    """No stale entry: a removed or renamed probe can't keep an exemption."""
    for name, reason in FREQUENT_PROBES.items():
        assert (INFRA / name).is_file(), name
        assert reason.strip(), name


@pytest.mark.parametrize("name", sorted(FREQUENT_PROBES))
def test_a_frequent_probe_is_monotonic_and_never_overlaps_itself(name: str) -> None:
    """No ``OnCalendar=``: one would make it a scheduled job, which the quiet
    rule binds. Its run is bounded below its interval, so runs never pile up."""
    timer = INFRA / name
    service = timer.with_suffix(".service")
    assert _setting(timer, "OnCalendar") == [], f"{name}: a scheduled job, not a probe"
    (interval,) = _setting(timer, "OnUnitActiveSec")
    (timeout,) = _setting(service, "TimeoutStartSec")
    assert _seconds(timeout) < _seconds(interval), f"{service.name}: a run can outlast its interval"


@pytest.mark.parametrize("name", sorted(FREQUENT_PROBES))
def test_a_frequent_probe_restarts_nothing(name: str) -> None:
    """It runs through the window, so it must not be a restarter a data-store
    step would have to stop. Read the way the skill's scan reads it: the unit's
    Exec lines and each absolute script they name, comments left out."""
    service = (INFRA / name).with_suffix(".service")
    exec_lines = re.findall(r"^Exec\w*=(.*)$", service.read_text(), re.M)
    assert exec_lines
    for line in exec_lines:
        assert not SKILL_RESTART_RE.search(line), f"{service.name}: {line}"
        for word in line.split():
            if word.startswith(DEPLOY_ROOT + "/infra/"):
                script = INFRA / word.removeprefix(DEPLOY_ROOT + "/infra/")
                lines = script.read_text().splitlines()
                code = [ln for ln in lines if not ln.lstrip().startswith("#")]
                hits = [ln for ln in code if SKILL_RESTART_RE.search(ln)]
                assert hits == [], f"{script.name} reads as a restarter: {hits}"


def test_the_restart_pattern_matches_the_vendored_scan() -> None:
    """SKILL_RESTART_RE is a copy: a skill bump that widened the scan's pattern
    would otherwise leave this test green while the real scan reports a probe
    as an undeclared ``restarter:``."""
    probe = REPO_ROOT / "skills" / "patching-hosts" / "scripts" / "probe.sh"
    if not probe.is_file():
        if socket.gethostname() == HOST:
            pytest.fail(f"{probe} missing on the host: run bash .skills/doctor.sh")
        pytest.skip("patching-hosts submodule not initialised")
    (vendored,) = re.findall(r"^RESTART_RE='(.*)'$", probe.read_text(), re.M)
    assert vendored == SKILL_RESTART_RE.pattern


def test_a_libpostal_restart_never_alerts() -> None:
    """An outage of D seconds spans at most ``D // interval + 1`` probe runs, so
    it reaches MISSES_TO_ALERT only if ``D >= (misses - 1) * interval``."""
    (misses,) = re.findall(r"^MISSES_TO_ALERT=(\d+)$", LIBPOSTAL_PROBE.read_text(), re.M)
    (interval,) = _setting(INFRA / "libpostal-probe.timer", "OnUnitActiveSec")
    assert (int(misses) - 1) * _seconds(interval) > LIBPOSTAL_RESTART_SECONDS


def test_the_libpostal_probe_watches_a_real_unit() -> None:
    """``systemctl is-active`` prints ``inactive`` for a unit that isn't loaded,
    so a renamed or dropped sidecar unit would stand the probe down every tick,
    at info: blind, silently."""
    (unit,) = re.findall(r"^UNIT=(\S+)$", LIBPOSTAL_PROBE.read_text(), re.M)
    assert (INFRA / unit).is_file(), unit
    assert [unit] in _named("service"), f"{unit} is no knob service line"


def test_the_libpostal_probe_and_the_patch_health_check_agree() -> None:
    """One synthetic URL for both, so a patch run's health line and the probe
    can't disagree about what answering means."""
    (url,) = re.findall(r"^URL='(.*)'$", LIBPOSTAL_PROBE.read_text(), re.M)
    assert any(url in " ".join(words) for words in _named("health")), url


def test_quiet_ranges_never_overlap_the_window() -> None:
    (window,) = [_span(words[1]) for words in _named("window")]
    for words in _named("quiet"):
        start, end = _span(words[0])
        assert end <= window[0] or start >= window[1], words


FORBIDDEN_IN_COMMANDS = (
    "sudo",
    "runuser",
    "su ",
    "X-API-Key",
    "API_KEY",
    "postgresql://",
    "PGPASSWORD",
)


def test_no_knob_command_escalates_or_holds_a_secret() -> None:
    """``health``/``inflight`` run as the invoking user by design; a ``sudo`` in
    one hands root to whoever commits the knob. The API key is read by the
    recovery check from the env file, never written here."""
    commands = [" ".join(words) for words in _named("health") + _named("inflight")]
    assert commands
    for command in commands:
        for word in FORBIDDEN_IN_COMMANDS:
            assert word not in command, f"{word!r} in a knob command: {command}"


def test_health_proves_recovery_not_just_liveness() -> None:
    """The app fails open while Postgres is down (#235)."""
    commands = [shlex.split(" ".join(words)) for words in _named("health")]
    script = "infra/patching_recovery_check.py"
    recovery = [c for c in commands if any(w.endswith(script) for w in c)]
    assert len(recovery) == 1
    assert recovery[0] == [
        f"{DEPLOY_ROOT}/.venv/bin/python",
        f"{DEPLOY_ROOT}/infra/patching_recovery_check.py",
    ]
    assert (INFRA / "patching_recovery_check.py").is_file()
    assert any("/api/v2/health" in " ".join(c) for c in commands)


def test_the_datastore_is_the_production_database_only() -> None:
    assert _named("datastore") == [["postgres", "postgresql@16-main", "address_validator"]]
    excepted = {words[0] for words in _named("exception")}
    assert "database:address_validator_test" in excepted


def test_the_services_are_the_app_and_its_docker_sidecar() -> None:
    assert _named("service") == [["address-validator.service"], ["libpostal.service"]]


def test_the_callers_are_the_public_ones() -> None:
    assert {words[0] for words in _named("caller")} == PUBLIC_CALLERS


def test_each_third_party_origin_is_followed_and_tailscale_is_its_own_step() -> None:
    assert {words[0]: words[1] for words in _named("origin")} == dict.fromkeys(FOLLOWED, "follow")
    assert ["tailscale", "tailscale"] in _named("hold")


def _pin_records(text: str) -> list[dict[str, str]]:
    records = []
    for block in re.split(r"\n\s*\n", text):
        lines = [ln for ln in block.splitlines() if ":" in ln and not ln.startswith("#")]
        fields = dict(ln.split(":", 1) for ln in lines)
        if fields:
            records.append({k.strip(): v.strip() for k, v in fields.items()})
    return records


def test_apt_pins_scope_each_followed_origin() -> None:
    """policy.md: a ``Package: *`` pin below 500 for the site, and each package
    the origin was added for above 500. Else the probe reports ``unscoped:`` /
    ``unpinned:``, and the maintenance lane can replace an Ubuntu package."""
    records = _pin_records(APT_PREFS.read_text())
    for origin, packages in FOLLOWED.items():
        mine = [r for r in records if r["Pin"] == f"origin {origin}"]
        catch_all = [int(r["Pin-Priority"]) for r in mine if r["Package"] == "*"]
        assert catch_all and all(p < 500 for p in catch_all), origin
        named = [r for r in mine if r["Package"] != "*" and int(r["Pin-Priority"]) > 500]
        pinned = {p for r in named for p in r["Package"].split()}
        assert pinned == packages, origin


def test_apt_pins_installed_match_the_repo() -> None:
    if socket.gethostname() != HOST:
        pytest.skip("host state")
    if not APT_PREFS_INSTALLED.exists():
        pytest.skip(f"{APT_PREFS_INSTALLED} not installed yet (docs/DEPLOYMENT.md)")
    assert APT_PREFS_INSTALLED.read_text() == APT_PREFS.read_text()


def test_unit_scripts_are_absolute_so_the_restarter_scan_can_read_them() -> None:
    """patching-hosts follows an Exec line's script one level deep, but only by
    an absolute path. A relative one is unreadable: each timer service turns
    into ``restarters:unread:``, and every unit whose ``OnFailure=`` runs
    ``unit-failure@`` into an undeclared ``restarter:`` (#311). The program
    itself too: a bare ``infra/x.sh`` is the same unreadable script (#256)."""
    for unit in sorted(INFRA.glob("*.service")):
        for line in re.findall(r"^ExecStart(?:Pre|Post)?=(.*)$", unit.read_text(), re.M):
            words = line.lstrip("-@+!:").split()
            assert words[0].startswith("/"), f"{unit.name}: {line}"
            if words[0].endswith(("/python", "/python3")):
                assert words[1].startswith("/"), f"{unit.name}: {line}"
