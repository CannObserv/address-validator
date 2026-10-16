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


def test_every_timer_fires_inside_a_quiet_range_and_outside_the_window() -> None:
    """A new timer, or a moved one, that lands in the window goes red here."""
    quiet = [_span(words[0]) for words in _named("quiet") if len(words) == 1]
    (window,) = [_span(words[1]) for words in _named("window")]
    timers = sorted(INFRA.glob("*.timer"))
    assert timers
    for timer in timers:
        calendars = re.findall(r"^OnCalendar=(.*)$", timer.read_text(), re.M)
        assert calendars, f"{timer.name} has no OnCalendar= this test can read"
        for calendar in calendars:
            clock = re.search(r"(\d{2}:\d{2})", calendar)
            assert clock, f"{timer.name}: OnCalendar={calendar} names no HH:MM"
            at = _minutes(clock.group(1))
            inside = any(start <= at < end for start, end in quiet)
            assert inside, f"{timer.name} fires outside every quiet range"
            assert not window[0] <= at < window[1], f"{timer.name} fires inside the window"


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
    ``unit-failure@`` into an undeclared ``restarter:`` (#311)."""
    for unit in sorted(INFRA.glob("*.service")):
        for line in re.findall(r"^ExecStart(?:Pre|Post)?=(.*)$", unit.read_text(), re.M):
            words = line.lstrip("-@+!:").split()
            if words[0].endswith(("/python", "/python3")):
                assert words[1].startswith("/"), f"{unit.name}: {line}"
