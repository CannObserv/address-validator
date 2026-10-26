"""Drift guard: the nightly backup's unit, timer and role script (GH #240).

The unit's sandbox is the job's security boundary, and most of its failure
modes are silent. Pinned here, ported from wslcb-licensing-tracker's
``tests/test_infra_backup_units.py``:

- its own dynamic user, named as the role script's role (peer auth maps one to
  the other), with no capabilities;
- both keys as ``LoadCredential=`` and **no** ``SetCredential=`` fallback: unlike
  the liveness unit (#354), a backup without its key must fail the start
  (243/CREDENTIALS → ``OnFailure=``) and miss its check-in;
- no ``EnvironmentFile=``: ``/etc/address-validator/.env`` holds the API's
  secrets, none of which this needs;
- an empty ``/home`` with only the checkout bound back, read-only, and a venv
  interpreter outside ``/home`` (a uv-managed one would be hidden, 203/EXEC);
- 02:30 UTC, finishing by its timeout before the 03:00 archive deletes rows,
  and a co-status grace that outlasts the timeout (a killed run can't check in).

Quiet-range placement is ``test_patching_knob.py``'s; ``OnFailure=`` is
``test_unit_failure_hooks.py``'s; installed-copy drift is ``install-units.sh --check``'s.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import status_checkin as sc

REPO_ROOT = Path(__file__).resolve().parents[2]
INFRA = REPO_ROOT / "infra"
SERVICE = INFRA / "address-validator-backup.service"
TIMER = INFRA / "address-validator-backup.timer"
ARCHIVE_TIMER = INFRA / "audit-archive.timer"
ROLE_SQL = REPO_ROOT / "scripts" / "db" / "setup-backup-role.sql"
RECOVERY = REPO_ROOT / "docs" / "RECOVERY.md"
CHECKOUT = "/home/exedev/address-validator"
ULID = re.compile(r"[0-9A-HJKMNP-TV-Z]{26}")


def _directives(path: Path, section: str) -> dict[str, list[str]]:
    """Every ``Key=value`` in ``[section]``, values in order, comments skipped."""
    found: dict[str, list[str]] = {}
    current = None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            continue
        if current != section or not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        found.setdefault(key.strip(), []).append(value.strip())
    return found


def _minutes(span: str) -> int:
    match = re.fullmatch(r"(\d+)\s*(min|m|s|sec)?", span)
    assert match, span
    return int(match[1]) if match[2] in ("min", "m") else int(match[1]) // 60


def _clock(calendar: str) -> int:
    match = re.fullmatch(r"(\d\d):(\d\d) UTC", calendar)
    assert match, calendar
    return int(match[1]) * 60 + int(match[2])


@pytest.fixture(scope="module")
def service():
    return _directives(SERVICE, "Service")


def _env(service) -> dict[str, str]:
    return dict(line.split("=", 1) for line in service["Environment"])


def test_dynamic_unprivileged_user(service):
    assert service["DynamicUser"] == ["yes"]
    assert service["User"] == ["address_validator_backup"]
    assert service["CapabilityBoundingSet"] == [""]
    assert service["NoNewPrivileges"] == ["yes"]


def test_role_script_names_the_unit_s_user(service):
    assert f"\\set backup_role {service['User'][0]}" in ROLE_SQL.read_text()


def test_keys_are_credentials_with_no_fallback(service):
    assert service["LoadCredential"] == [
        "gcs:/etc/address-validator/backup-gcs.json",
        f"{sc.KEY_CREDENTIAL}:/etc/address-validator/status-checkin.key",
    ]
    assert "SetCredential" not in service
    assert _env(service)["GOOGLE_APPLICATION_CREDENTIALS"] == "%d/gcs"


def test_no_env_file_and_the_secret_files_are_hidden(service):
    assert "EnvironmentFile" not in service
    assert "/etc/address-validator" in service["InaccessiblePaths"]
    assert f"-{CHECKOUT}/.env" in service["InaccessiblePaths"]


def test_configuration(service):
    env = _env(service)
    assert env["BACKUP_BUCKET"] == "co-address-validator-backup"
    assert env["BACKUP_CHECKIN_BASE_URL"] == "http://status:9000"
    assert env["BACKUP_MONITOR_ID"] == "" or ULID.fullmatch(env["BACKUP_MONITOR_ID"])
    assert env["PYTHONPATH"] == f"{CHECKOUT}/src"


def test_sandbox(service):
    assert service["ProtectSystem"] == ["strict"]
    assert service["ProtectHome"] == ["tmpfs"]
    assert service["BindReadOnlyPaths"] == [CHECKOUT]
    assert service["WorkingDirectory"] == [CHECKOUT]
    assert service["PrivateTmp"] == ["yes"]
    assert service["Type"] == ["oneshot"]
    assert service["MemoryMax"]


def test_runs_the_venv_python_not_uv(service):
    """uv wants a writable cache and may sync; the sandbox refuses both."""
    assert service["ExecStart"] == [f"{CHECKOUT}/.venv/bin/python {CHECKOUT}/infra/backup_db.py"]


def test_venv_python_resolves_outside_home():
    python = Path(CHECKOUT) / ".venv" / "bin" / "python"
    if not python.exists():
        pytest.skip("no production venv on this host")
    target = python.resolve()
    assert not target.is_relative_to("/home") or target.is_relative_to(CHECKOUT)


def test_ordered_after_postgres_and_the_tailnet_without_binding():
    unit = _directives(SERVICE, "Unit")
    after = set(" ".join(unit["After"]).split())
    assert {"postgresql.service", "tailscaled.service", "network-online.target"} <= after
    for key in ("Requires", "BindsTo", "PartOf"):
        assert key not in unit, key


def test_a_run_ends_before_the_archive_deletes_rows(service):
    (calendar,) = _directives(TIMER, "Timer")["OnCalendar"]
    (archive,) = _directives(ARCHIVE_TIMER, "Timer")["OnCalendar"]
    timeout = _minutes(service["TimeoutStartSec"][0])
    assert _clock(calendar) == 2 * 60 + 30
    assert _clock(calendar) + timeout < _clock(archive)
    assert _directives(TIMER, "Timer")["Persistent"] == ["true"]
    assert "RandomizedDelaySec" not in _directives(TIMER, "Timer")


def test_monitor_grace_outlasts_the_timeout(service):
    """A run killed by TimeoutStartSec can't check in: the grace must cover it,
    plus the timer's default one-minute accuracy."""
    (grace,) = re.findall(r"`grace_seconds` \| `(\d+)`", RECOVERY.read_text())
    (interval,) = re.findall(r"`interval_seconds` \| `(\d+)`", RECOVERY.read_text())
    assert int(interval) == 86400
    assert int(grace) > (_minutes(service["TimeoutStartSec"][0]) + 1) * 60
