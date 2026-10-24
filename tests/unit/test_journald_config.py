"""Drift guard: the journal persists, capped at 512M (GH #241).

The Feb-2026 exeuntu image masked ``systemd-journal-flush.service``, so journald
stayed in ``/run`` despite ``Storage=persistent`` and the 2026-09-29 reboot lost
51 days. ``infra/journald.conf.d/address-validator.conf`` tracks the setting and
the cap; the host checks catch a re-mask (a rebuild from that image) and an
installed copy that drifted. They run only on the host, and skip there until the
drop-in is installed.
"""

import socket
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DROPIN = REPO_ROOT / "infra" / "journald.conf.d" / "address-validator.conf"
INSTALLED = Path("/etc/systemd/journald.conf.d/address-validator.conf")
HOST = "address-validator"
# gregoryfoster/skills#425's cohort drop-in sets the same; it sorts last and wins.
CAP = "512M"

on_the_host = pytest.mark.skipif(socket.gethostname() != HOST, reason=f"not {HOST}")
installed = pytest.mark.skipif(not INSTALLED.exists(), reason=f"{INSTALLED} not installed yet")


def _settings(text: str) -> dict[str, str]:
    """Last value wins per key, as journald reads a chain of conf files."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";", "[")):
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip()
    return out


def test_the_drop_in_sets_persistent_storage_and_the_cap_only() -> None:
    text = DROPIN.read_text(encoding="utf-8")
    assert "[Journal]" in text
    assert _settings(text) == {"Storage": "persistent", "SystemMaxUse": CAP}


@on_the_host
@installed
def test_the_installed_copy_matches_the_tracked_one() -> None:
    assert INSTALLED.read_text(encoding="utf-8") == DROPIN.read_text(encoding="utf-8"), (
        f"{INSTALLED} differs from infra/journald.conf.d/{DROPIN.name}: "
        f"sudo install -m 644 infra/journald.conf.d/{DROPIN.name} {INSTALLED.parent}/"
    )


@on_the_host
@installed
def test_the_live_chain_resolves_to_persistent_and_the_cap() -> None:
    """Every conf.d file, in the order journald reads them."""
    chain = subprocess.run(
        ["systemd-analyze", "cat-config", "systemd/journald.conf"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    settings = _settings(chain)
    assert settings.get("Storage") == "persistent"
    assert settings.get("SystemMaxUse") == CAP


@on_the_host
@installed
def test_the_flush_is_not_masked_and_the_journal_is_on_disk() -> None:
    """A mask leaves Storage=persistent inert: read where the journal is."""
    state = subprocess.run(
        ["systemctl", "is-enabled", "systemd-journal-flush.service"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    assert state != "masked", "sudo systemctl unmask systemd-journal-flush (docs/LOGGING.md)"
    machine_id = Path("/etc/machine-id").read_text(encoding="utf-8").strip()
    assert Path(f"/var/log/journal/{machine_id}/system.journal").exists()
