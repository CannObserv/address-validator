#!/usr/bin/env python3
"""Keyed hash of a client IP for the audit archive (GH #233).

The archive (``archive_audit.py``) never holds a raw IP. ``client_ip`` becomes
``client_ip_hmac`` = hex HMAC-SHA256(key, canonical IP) plus
``client_ip_key_id``, the label of the key that made it. The live
``audit_log`` keeps the raw IP for its 90-day window; only the archive is
hashed. Never swap in a plain SHA-256: the IPv4 space is small enough to
brute-force (the flaw that kept ``pattern_key`` out of the archive, #228).

Key (``/etc/address-validator/.env``, read by the unit's ``EnvironmentFile=``):

    AUDIT_IP_HMAC_KEY      hex, at least 32 bytes: ``openssl rand -hex 32``
    AUDIT_IP_HMAC_KEY_ID   label stamped on every row, e.g. ``2026-10``

Without a valid key the archiver refuses to run; there is no raw fallback.
Never print, log or paste the key. Keep a copy of each key, under its key
ID, off the VM (a password manager): losing the only copy makes every file
hashed with it unlinkable, which is shredding by accident.

Input is canonicalized first (``canonical_ip``): an address that parses is
hashed in its ``ipaddress`` compressed form, so IPv6 spellings of one address
match; anything else (``unknown``) is hashed as written, stripped.

Look up a known IP:

    set -a && source /etc/address-validator/.env && set +a
    PYTHONPATH=src .venv/bin/python infra/audit_ip_hmac.py    # prompts, off-screen
    # -> "<key_id> <hmac>"; repeat with each older key you still hold
    gcloud storage cp -r gs://co-address-validator-audit/audit ./audit
    duckdb -c "SELECT * FROM read_parquet('audit/**/*.parquet', hive_partitioning=true)
               WHERE client_ip_hmac = '<hmac>'"

The IP is read with ``getpass`` (or from stdin when piped), never from argv,
so it stays out of shell history and ``ps``. ``--check`` validates the key
and prints only its ID and length.

Rotate: put a new key and a new key ID in the env file; the next nightly file
uses them. Older files keep their key ID. One caller hashes differently under
each key, so joining rows across the boundary needs both keys. Files can't be
re-hashed under a new key: the raw IP is gone.

Destroy (crypto-shred): delete every copy of a key ID (env file, backups,
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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

KEY_ENV = "AUDIT_IP_HMAC_KEY"
KEY_ID_ENV = "AUDIT_IP_HMAC_KEY_ID"
MIN_KEY_BYTES = 32
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


def load_hasher(environ: Mapping[str, str]) -> IpHasher:
    """Build the hasher from ``AUDIT_IP_HMAC_KEY`` / ``AUDIT_IP_HMAC_KEY_ID``.

    Raises KeyConfigError naming the variable, never its value; the
    ``fromhex`` error (which may quote a position) is not chained.
    """
    raw_key = environ.get(KEY_ENV, "").strip()
    if not raw_key:
        raise KeyConfigError(f"{KEY_ENV} not set; refusing to archive raw IPs")
    try:
        key = bytes.fromhex(raw_key)
    except ValueError:
        msg = f"{KEY_ENV} is not hex (generate one with: openssl rand -hex 32)"
        raise KeyConfigError(msg) from None
    if len(key) < MIN_KEY_BYTES:
        msg = f"{KEY_ENV} is shorter than {MIN_KEY_BYTES} bytes ({2 * MIN_KEY_BYTES} hex digits)"
        raise KeyConfigError(msg)
    key_id = environ.get(KEY_ID_ENV, "").strip()
    if not _KEY_ID_RE.fullmatch(key_id):
        msg = f"{KEY_ID_ENV} not set or invalid (1-32 of A-Z a-z 0-9 . _ -)"
        raise KeyConfigError(msg)
    return IpHasher(key, key_id)


def _read_ip() -> str:
    if sys.stdin.isatty():
        return getpass.getpass("IP (not echoed): ")
    return sys.stdin.readline()


def cli(argv: Sequence[str], environ: Mapping[str, str]) -> int:
    """Print ``<key_id> <hmac>`` for an IP read off-screen, or check the key."""
    parser = argparse.ArgumentParser(description="Hash a known IP with the archive key.")
    parser.add_argument("--check", action="store_true", help="Validate the key; print no hash.")
    args = parser.parse_args(argv)
    try:
        hasher = load_hasher(environ)
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
