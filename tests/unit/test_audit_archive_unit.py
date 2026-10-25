"""Drift guard: the audit archiver's HMAC key arrives as a credential (GH #352).

``/etc/address-validator/.env`` is loaded by both ``audit-archive.service`` and
``address-validator.service``, so a key kept there sits in the API process's
environment too. Pinned here:

- the unit loads the key by ``LoadCredential=`` under the id and from the path
  ``audit_ip_hmac`` reads, with a lone-newline ``SetCredential=`` fallback,
  so a missing file reaches the script's "exit 1 without a valid key" rule.
  An *empty* fallback is no fallback: systemd 255 rejects it ("Invalid
  syntax, ignoring") and the unit fails 243/CREDENTIALS before exec;
- no unit sets the retired ``AUDIT_IP_HMAC_KEY`` in an ``Environment=`` line.

Installed-copy drift is ``install-units.sh --check``'s job (#228).
"""

from __future__ import annotations

import re
from pathlib import Path

import audit_ip_hmac as aih

INFRA = Path(__file__).resolve().parents[2] / "infra"
SERVICE = INFRA / "audit-archive.service"


def _settings(path: Path, key: str) -> list[str]:
    return re.findall(rf"^{key}=(.*)$", path.read_text(), re.M)


def test_the_key_is_a_credential_with_a_newline_fallback() -> None:
    assert _settings(SERVICE, "LoadCredential") == [f"{aih.CREDENTIAL}:{aih.KEY_FILE}"]
    assert _settings(SERVICE, "SetCredential") == [f"{aih.CREDENTIAL}:\\n"]


def test_no_unit_puts_the_key_in_an_environment() -> None:
    for unit in sorted(INFRA.glob("*.service")):
        for line in _settings(unit, "Environment"):
            assert aih.KEY_ENV + "=" not in line, unit.name
