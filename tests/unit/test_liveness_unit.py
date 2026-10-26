"""Drift guard: the liveness check-in's key handling and cadence (GH #255).

The probe's behaviour is pinned by ``tests/shell/address-validator-liveness-test.sh``;
its cadence against the patching knob by ``test_patching_knob.py``. Pinned here:

- the check-in key arrives as a ``LoadCredential=`` credential, never from an
  env file, with a lone-newline ``SetCredential=`` fallback: without it a missing
  key file fails the unit (243/CREDENTIALS) on every tick, and every tick
  alerts. Not an empty value: systemd 255 ignores that as invalid syntax (#354);
- the script reads the credential the unit loads, under the same id;
- the monitor id is a plain ``Environment=``: empty (unconfigured) or a ULID;
- the timer's interval is the co-status monitor's ``interval_seconds``.
"""

from __future__ import annotations

import re
from pathlib import Path

INFRA = Path(__file__).resolve().parents[2] / "infra"
SCRIPT = INFRA / "address-validator-liveness.sh"
SERVICE = INFRA / "address-validator-liveness.service"
TIMER = INFRA / "address-validator-liveness.timer"
KEY_FILE = "/etc/address-validator/status-checkin.key"
# co-address-validator-live, as requested on CannObserv/status
MONITOR_INTERVAL_SECONDS = 300
ULID = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}")


def _settings(path: Path, key: str) -> list[str]:
    return re.findall(rf"^{key}=(.*)$", path.read_text(), re.M)


def _credential() -> str:
    (name,) = re.findall(r"^CREDENTIAL=(\S+)$", SCRIPT.read_text(), re.M)
    return name


def test_the_key_is_a_credential_with_a_lone_newline_fallback() -> None:
    name = _credential()
    assert _settings(SERVICE, "LoadCredential") == [f"{name}:{KEY_FILE}"]
    assert _settings(SERVICE, "SetCredential") == [f"{name}:\\n"]


def test_no_env_file_reaches_the_unit() -> None:
    """The key is never in ``.env``; the unit reads no env file at all."""
    assert _settings(SERVICE, "EnvironmentFile") == []


def test_the_monitor_id_is_unconfigured_or_a_ulid() -> None:
    (assignment,) = _settings(SERVICE, "Environment")
    name, _, value = assignment.partition("=")
    assert name == "LIVENESS_MONITOR_ID"
    assert value == "" or ULID.fullmatch(value), value
    assert "${LIVENESS_MONITOR_ID:-}" in SCRIPT.read_text()


def test_the_key_never_reaches_argv() -> None:
    """curl takes its headers from stdin (``-H @-``); the key is printed into the
    pipe by bash's builtin printf, which forks no process."""
    text = SCRIPT.read_text()
    assert "printf 'X-API-Key: %s\\n' \"$key\" | curl" in text
    assert text.count("X-API-Key") == 1


def test_the_timer_keeps_the_monitors_interval() -> None:
    (interval,) = _settings(TIMER, "OnUnitActiveSec")
    assert interval == f"{MONITOR_INTERVAL_SECONDS // 60}min"
