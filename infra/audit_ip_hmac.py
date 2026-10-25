#!/usr/bin/env python3
"""Keyed hash of a client IP for the audit archive (GH #233).

The archive (``archive_audit.py``) never holds a raw IP. ``client_ip`` becomes
``client_ip_hmac`` = hex HMAC-SHA256(key, canonical IP) plus
``client_ip_key_id``, the label of the key that made it. The live
``audit_log`` keeps the raw IP for its 90-day window; only the archive is
hashed. Never swap in a plain SHA-256: the IPv4 space is small enough to
brute-force (the flaw that kept ``pattern_key`` out of the archive, #228).

Key (#352): a root-only file, ``/etc/address-validator/audit-ip-hmac.key``
(root:root, 0600), holding the hex key, at least 32 bytes
(``openssl rand -hex 32``). It never enters an environment:
``audit-archive.service`` hands it over by ``LoadCredential=`` (read from
``$CREDENTIALS_DIRECTORY``), and the hand-run tools take ``--key-file``. The
API loads the same ``.env`` as the archiver, so a key there would sit in the
API's ``/proc/<pid>/environ`` too. ``AUDIT_IP_HMAC_KEY`` in the environment is
refused, not used.

Key ID: ``AUDIT_IP_HMAC_KEY_ID`` in ``/etc/address-validator/.env``, the label
stamped on every row, e.g. ``2026-10``. Not secret, so it stays an env var.

Without a valid key, or with the old env var still set, every caller exits 1;
there is no raw fallback. Never print, log or paste the key. Keep a copy of
each key, under its key ID, off the VM (a password manager): losing the only
copy makes every file hashed with it unlinkable, which is shredding by
accident.

Input is canonicalized first (``canonical_ip``): an address that parses is
hashed in its ``ipaddress`` compressed form, so IPv6 spellings of one address
match; anything else (``unknown``) is hashed as written, stripped.

Look up a known IP:

    set -a && source /etc/address-validator/.env && set +a    # the key ID
    PYTHONPATH=src .venv/bin/python infra/audit_ip_hmac.py \
        --key-file <(sudo cat /etc/address-validator/audit-ip-hmac.key)    # prompts, off-screen
    # -> "<key_id> <hmac>"; repeat with each older key you still hold
    gcloud storage cp -r gs://co-address-validator-audit/audit ./audit
    duckdb -c "SELECT * FROM read_parquet('audit/**/*.parquet', hive_partitioning=true)
               WHERE client_ip_hmac = '<hmac>'"

The IP is read with ``getpass`` (or from stdin when piped), never from argv,
so it stays out of shell history and ``ps``. ``--key-file <(sudo cat ...)``
passes the key through a pipe: argv shows only ``/dev/fd/N``, and the tool
runs as you, not root. ``--check`` validates the key and prints only its ID
and length.

Check the unit's own path (``LoadCredential=``, as exedev; prints ID and length):

    sudo systemd-run --wait --pipe -q -p User=exedev \
        -p EnvironmentFile=/etc/address-validator/.env \
        -p LoadCredential=audit-ip-hmac.key:/etc/address-validator/audit-ip-hmac.key \
        $PWD/.venv/bin/python $PWD/infra/audit_ip_hmac.py --check

Rotate: write the new key to the key file and the new key ID to the env file
in one sitting (a key under the wrong ID mislabels every row), then
``--check``; the next nightly file uses both. Write the file so the key is
never echoed and never world-readable:

    K=/etc/address-validator/audit-ip-hmac.key
    sudo install -m 600 -o root -g root /dev/null $K.new
    openssl rand -hex 32 | sudo tee $K.new >/dev/null && sudo mv $K.new $K
 Older files keep their key ID.
One caller hashes differently under each key, so joining rows across the
boundary needs both keys. Files can't be re-hashed under a new key: the raw
IP is gone.

Destroy (crypto-shred): delete every copy of a key ID (key file, backups,
password manager). Its files can then no longer be linked to any IP; they
stay pseudonymous activity records (timestamps, endpoints, request IDs).

Stdlib only, like ``audit_retention.py``.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import ipaddress
import os
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

KEY_ENV = "AUDIT_IP_HMAC_KEY"  # retired (#352): refused when set, never read
KEY_ID_ENV = "AUDIT_IP_HMAC_KEY_ID"
CREDENTIAL = "audit-ip-hmac.key"  # the LoadCredential= id in audit-archive.service
KEY_FILE = "/etc/address-validator/audit-ip-hmac.key"  # root:root 0600
CREDENTIALS_DIR_ENV = "CREDENTIALS_DIRECTORY"
MIN_KEY_BYTES = 32
_MAX_KEY_FILE_BYTES = 4096  # a hex key is ~65; more means the wrong file (e.g. /dev/urandom)
_KEY_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,32}")


class KeyConfigError(Exception):
    """The key or key ID is missing or malformed. The message never holds a value."""


def canonical_ip(ip: str) -> str:
    """The ``ipaddress`` compressed form when ``ip`` parses; otherwise ``ip`` stripped."""
    stripped = ip.strip()
    try:
        return ipaddress.ip_address(stripped).compressed
    except ValueError:
        return stripped


class IpHasher:
    """HMAC-SHA256 of a canonical IP under one key. Never exposes the key."""

    __slots__ = ("_key", "key_id")

    def __init__(self, key: bytes, key_id: str) -> None:
        self._key = key
        self.key_id = key_id

    @property
    def key_length(self) -> int:
        """Key size in bytes, for ``--check``; the key itself never leaves the object."""
        return len(self._key)

    def hash(self, ip: str) -> str:
        """Hex HMAC-SHA256(key, canonical_ip(ip))."""
        return hmac.new(self._key, canonical_ip(ip).encode(), hashlib.sha256).hexdigest()

    def __repr__(self) -> str:
        return f"IpHasher(key_id={self.key_id!r}, key=<redacted>)"

    __str__ = __repr__


def _key_source(environ: Mapping[str, str], key_file: str | None) -> tuple[Path, str]:
    """``(path, label)`` of the key: ``--key-file``, else the unit's credential."""
    if key_file:
        return Path(key_file), "--key-file"
    cred_dir = environ.get(CREDENTIALS_DIR_ENV, "").strip()
    if not cred_dir:
        msg = (
            f"no key: run under audit-archive.service (LoadCredential={CREDENTIAL}) "
            f"or pass --key-file <(sudo cat {KEY_FILE}); refusing to archive raw IPs"
        )
        raise KeyConfigError(msg)
    return Path(cred_dir) / CREDENTIAL, f"credential {CREDENTIAL}"


def _read_key(path: Path, label: str) -> bytes:
    """The key bytes in ``path``. Errors name ``label``, never the content."""
    try:
        with path.open("rb") as fh:
            raw = fh.read(_MAX_KEY_FILE_BYTES + 1).strip()
    except OSError as exc:
        msg = f"cannot read the key ({label}): {exc.strerror}"
        raise KeyConfigError(msg) from None
    if len(raw) > _MAX_KEY_FILE_BYTES:
        msg = f"the key ({label}) is over {_MAX_KEY_FILE_BYTES} bytes: not a key file"
        raise KeyConfigError(msg)
    if not raw:
        # Under the unit a missing file arrives as the lone-newline SetCredential= fallback.
        where = KEY_FILE if label.startswith("credential") else "the file given"
        msg = f"the key ({label}) is missing or empty: {where}; refusing to archive raw IPs"
        raise KeyConfigError(msg)
    try:
        key = bytes.fromhex(raw.decode("ascii"))
    except ValueError:  # UnicodeDecodeError included; both may quote the key
        msg = f"the key ({label}) is not hex (generate one with: openssl rand -hex 32)"
        raise KeyConfigError(msg) from None
    if len(key) < MIN_KEY_BYTES:
        msg = (
            f"the key ({label}) is shorter than {MIN_KEY_BYTES} bytes "
            f"({2 * MIN_KEY_BYTES} hex digits)"
        )
        raise KeyConfigError(msg)
    return key


def load_hasher(environ: Mapping[str, str], key_file: str | None = None) -> IpHasher:
    """Build the hasher from the key file and ``AUDIT_IP_HMAC_KEY_ID``.

    The key comes from ``key_file`` when given (the hand-run tools'
    ``--key-file``), else from ``$CREDENTIALS_DIRECTORY/audit-ip-hmac.key``
    (the unit's ``LoadCredential=``). ``AUDIT_IP_HMAC_KEY`` in ``environ`` is
    refused: the key never lives in an environment (#352).

    Raises KeyConfigError naming the source, never a value; the decode and
    ``fromhex`` errors (which may quote a byte or a position) are not chained.
    """
    if environ.get(KEY_ENV, "").strip():
        msg = (
            f"{KEY_ENV} is set in the environment; the key is read only from "
            f"{KEY_FILE} (#352): remove it from the env file"
        )
        raise KeyConfigError(msg)
    key = _read_key(*_key_source(environ, key_file))
    key_id = environ.get(KEY_ID_ENV, "").strip()
    if not _KEY_ID_RE.fullmatch(key_id):
        msg = f"{KEY_ID_ENV} not set or invalid (1-32 of A-Z a-z 0-9 . _ -)"
        raise KeyConfigError(msg)
    return IpHasher(key, key_id)


def add_key_file_argument(parser: argparse.ArgumentParser) -> None:
    """The hand-run tools' ``--key-file``; the unit passes none and uses its credential."""
    parser.add_argument(
        "--key-file",
        metavar="PATH",
        help=(
            f"Read the HMAC key from PATH, e.g. <(sudo cat {KEY_FILE}). "
            f"Default: ${CREDENTIALS_DIR_ENV}/{CREDENTIAL} (the unit's LoadCredential=)."
        ),
    )


def _read_ip() -> str:
    if sys.stdin.isatty():
        return getpass.getpass("IP (not echoed): ")
    return sys.stdin.readline()


def cli(argv: Sequence[str], environ: Mapping[str, str]) -> int:
    """Print ``<key_id> <hmac>`` for an IP read off-screen, or check the key."""
    parser = argparse.ArgumentParser(description="Hash a known IP with the archive key.")
    parser.add_argument("--check", action="store_true", help="Validate the key; print no hash.")
    add_key_file_argument(parser)
    args = parser.parse_args(argv)
    try:
        hasher = load_hasher(environ, args.key_file)
    except KeyConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.check:
        print(f"key id {hasher.key_id}, {hasher.key_length} bytes: ok")
        return 0
    ip = _read_ip().strip()
    if not ip:
        print("error: no IP given", file=sys.stderr)
        return 1
    print(f"{hasher.key_id} {hasher.hash(ip)}")
    return 0


if __name__ == "__main__":
    sys.exit(cli(sys.argv[1:], os.environ))
