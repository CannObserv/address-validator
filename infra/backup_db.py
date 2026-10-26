#!/usr/bin/env python3
"""Ship a verified dump of the production database off the node, nightly (#240).

The cohort's backup pattern (CannObserv/provisioner ``docs/BACKUP.md``), ported
from CannObserv/wslcb-licensing-tracker's ``backup.py`` without its file-archive
phase and monthly tier. Built to be dumb in the ways that keep a backup honest:

- **No database credential, no privilege.** ``address-validator-backup.service``
  runs as its own dynamic user, ``address_validator_backup``, with no
  capabilities. ``pg_dump`` and ``psql`` connect by peer auth over the local
  socket to the role of the same name, which holds ``pg_read_all_data`` and
  nothing else (``scripts/db/setup-backup-role.sql``). The GCS key arrives as a
  systemd credential, and a key path from anywhere else is refused.
- **Verified before it ships.** ``pg_restore --list`` must find a data section
  for ``alembic_version`` and every table ``db/tables.py`` mirrors (itself
  drift-tested against the schema), so a readable dump of the wrong database is
  refused. Then ``pg_restore --file=/dev/null`` reads every block: a truncated
  custom archive still lists. The dump must come from a ``pg_dump`` of the
  server's own major version.
- **Named by its snapshot:** ``<hostname>/<YYYYMMDDTHHMMSSZ>.dump``, the time
  ``pg_dump`` started, with sha256, versions, alembic head and source host as
  object metadata.
- **Created, never overwritten or deleted.** ``if_generation_match=0`` here;
  ``objectCreator`` + ``objectViewer`` and no delete at IAM. Retention is the
  bucket's 30-day lifecycle rule. A 412 is ``unchanged`` only when the object
  there records this dump's sha256.
- **Loud failure, loud silence.** A failed run exits non-zero
  (``OnFailure=unit-failure@``) and checks in ``alert``; a good one checks in
  ``ok`` (``status_checkin.py``). co-status alarms when neither arrives.

**PII.** The dump carries ``audit_log`` (``raw_input``, raw client IPs) and the
training candidates. It exists only in the unit's private ``/tmp`` for the run.
Log lines and check-ins carry the object name, size, sha256, versions and error
text only; ``pg_dump``/``pg_restore`` stderr is passed on as a short tail with
any ``DETAIL``/``CONTEXT`` line dropped (the journal persists, #241).

Restore is ``restore_db.py``; ``docs/RECOVERY.md`` is the runbook around both.

Usage (the unit's; by hand only through ``systemctl start``):
    /home/exedev/address-validator/.venv/bin/python infra/backup_db.py

Env vars:
    BACKUP_BUCKET                    GCS bucket (required; no default)
    BACKUP_PREFIX                    object prefix (default: the hostname)
    GOOGLE_APPLICATION_CREDENTIALS   must be the unit's credential copy, ``%d/gcs``
    BACKUP_MONITOR_ID, BACKUP_CHECKIN_BASE_URL   the check-in (``status_checkin.py``)
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from google.api_core.exceptions import NotFound, PreconditionFailed
from journal_logging import configure_logging
from status_checkin import CREDENTIALS_DIRECTORY_ENV, post_checkin

from address_validator.db.tables import metadata

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

logger = logging.getLogger("backup_db")

DATABASE = "address_validator"
BUCKET_ENV = "BACKUP_BUCKET"
PREFIX_ENV = "BACKUP_PREFIX"
KEY_PATH_ENV = "GOOGLE_APPLICATION_CREDENTIALS"
OBJECT_SUFFIX = ".dump"
# Basic-format ISO 8601, UTC: sorts as it reads, and no ':' for a shell to trip on.
KEY_TIME_FORMAT = "%Y%m%dT%H%M%SZ"
CONTENT_TYPE = "application/octet-stream"

#: A dump lacking any of these data sections is not a backup of this database.
REQUIRED_TABLES = ("public.alembic_version", *sorted(f"public.{t}" for t in metadata.tables))

# The dump measured 12.7 MB in 1.5 s (#235, 2026-09-29). Generous bounds, all
# inside the unit's TimeoutStartSec=15min, so a wedged call fails the run.
PG_DUMP_TIMEOUT_SECONDS = 600
QUERY_TIMEOUT_SECONDS = 60
UPLOAD_TIMEOUT_SECONDS = 300.0
LIST_TIMEOUT_SECONDS = 30.0

# metadata.fault on an alert check-in: a change of fault reports at once.
FAULT_MISCONFIGURED = "misconfigured"
FAULT_FAILED = "failed"

_HEADER_RE = re.compile(r"^;\s+(Dumped from database version|Dumped by pg_dump version): (.+)$")
_ENTRIES_RE = re.compile(r"^;\s+TOC Entries: (\d+)$")
_TABLE_DATA_RE = re.compile(r"^\d+; \d+ \d+ TABLE DATA (\S+) (\S+) ")
# libpq's DETAIL and CONTEXT lines can quote row values (a COPY line, a key).
_QUOTING_RE = re.compile(r"\b(detail|context):", re.IGNORECASE)
_TAIL_LINES = 3


class BackupError(Exception):
    """Anything that means the dump was not shipped."""


@dataclass(frozen=True)
class Toc:
    """What ``pg_restore --list`` says about an archive."""

    server_version: str | None = None
    pg_dump_version: str | None = None
    entries: int | None = None
    tables_with_data: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Dump:
    """A verified dump on local disk, and what it says about itself."""

    path: Path
    size_bytes: int
    sha256: str
    dumped_at: datetime
    alembic_head: str | None
    toc: Toc


# --- pure ---


def parse_toc(text: str) -> Toc:
    """The header fields and the tables with a data section."""
    versions: dict[str, str] = {}
    entries: int | None = None
    tables: set[str] = set()
    for line in text.splitlines():
        if match := _HEADER_RE.match(line):
            versions[match.group(1)] = match.group(2).strip()
        elif match := _ENTRIES_RE.match(line):
            entries = int(match.group(1))
        elif match := _TABLE_DATA_RE.match(line):
            tables.add(f"{match.group(1)}.{match.group(2)}")
    return Toc(
        server_version=versions.get("Dumped from database version"),
        pg_dump_version=versions.get("Dumped by pg_dump version"),
        entries=entries,
        tables_with_data=frozenset(tables),
    )


def major(version: str | None) -> str | None:
    """``16`` from ``16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)``."""
    match = re.match(r"(\d+)", version or "")
    return match.group(1) if match else None


def object_key(prefix: str, dumped_at: datetime) -> str:
    """``<prefix>/<stamp>.dump``."""
    stamp = dumped_at.astimezone(UTC).strftime(KEY_TIME_FORMAT)
    return f"{prefix.strip('/')}/{stamp}{OBJECT_SUFFIX}"


def iso(at: datetime) -> str:
    """ISO 8601, UTC, second precision, ``Z``."""
    return at.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def misplaced_key(environ: Mapping[str, str]) -> str | None:
    """Why the GCS key path isn't this run's credential copy; None when it is.

    Under the unit the path is ``%d/gcs``, systemd's private copy. Any other
    value (a drop-in, a stray ``Environment=``) aims the SDK at a file this
    dynamic user can't read, or at another identity's key: refused, rather than
    failing "Permission denied", which reads as a reason to loosen the key's
    mode. Outside a unit there is no credential directory to compare against.
    """
    directory = environ.get(CREDENTIALS_DIRECTORY_ENV, "").strip()
    if not directory:
        return None
    key = environ.get(KEY_PATH_ENV, "").strip()
    if key and Path(key).is_relative_to(directory):
        return None
    return f"{KEY_PATH_ENV} is {key!r}, not this run's credential copy under {directory}"


def sha256_file(path: Path) -> str:
    """Hex sha256 of a file, streamed."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tail(text: str | bytes | None) -> str:
    """The last few stderr lines, minus any that can quote row values."""
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    lines = [ln for ln in (text or "").strip().splitlines() if not _QUOTING_RE.search(ln)]
    return " | ".join(lines[-_TAIL_LINES:])


def describe(exc: BaseException) -> str:
    """An error's text for a log line or check-in."""
    return str(exc) if isinstance(exc, BackupError) else f"{type(exc).__name__}: {exc}"


# --- the database and the dump file ---


def read_alembic_head(database: str, *, runner: Callable[..., Any]) -> str | None:
    """The schema version, recorded so a restore can be checked against it."""
    argv = [
        "psql",
        "--no-password",
        "--no-psqlrc",
        "--quiet",
        "--tuples-only",
        "--no-align",
        f"--dbname={database}",
        "--command=SELECT version_num FROM alembic_version",
    ]
    result = runner(
        argv, capture_output=True, text=True, check=False, timeout=QUERY_TIMEOUT_SECONDS
    )
    if result.returncode != 0:
        msg = f"psql could not read alembic_version: {tail(result.stderr)}"
        raise BackupError(msg)
    return result.stdout.strip() or None


def run_pg_dump(database: str, out: Path, *, runner: Callable[..., Any]) -> None:
    """Custom format, written by ``pg_dump`` itself to ``out``.

    ``--file``, not stdout: through a pipe ``pg_dump`` appends a second table of
    contents, and a cut removing only that copy passed both verification reads
    (measured by wslcb-licensing-tracker, 2026-10-09).
    """
    argv = ["pg_dump", "--format=custom", "--no-password", f"--file={out}", f"--dbname={database}"]
    result = runner(
        argv, capture_output=True, text=True, check=False, timeout=PG_DUMP_TIMEOUT_SECONDS
    )
    if result.returncode != 0:
        msg = f"pg_dump exited {result.returncode}: {tail(result.stderr)}"
        raise BackupError(msg)


def verify_dump(path: Path, *, runner: Callable[..., Any]) -> Toc:
    """Refuse an archive ``pg_restore`` can't read, or one of the wrong database."""
    listed = runner(
        ["pg_restore", "--list", str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=QUERY_TIMEOUT_SECONDS,
    )
    if listed.returncode != 0:
        msg = f"pg_restore --list rejected {path.name}: {tail(listed.stderr)}"
        raise BackupError(msg)
    toc = parse_toc(listed.stdout)
    missing = [table for table in REQUIRED_TABLES if table not in toc.tables_with_data]
    if missing:
        msg = f"dump has no data for {', '.join(missing)}; refusing to ship it"
        raise BackupError(msg)
    # pg_restore must be at least pg_dump's major to read the archive; a pg_dump
    # newer than the server would make one the cluster's own tools can't restore.
    if major(toc.pg_dump_version) != major(toc.server_version):
        msg = (
            f"pg_dump {toc.pg_dump_version} is not the server's major version "
            f"({toc.server_version}); refusing to ship it"
        )
        raise BackupError(msg)
    # Custom format writes its table of contents first: a truncated archive lists.
    full = runner(
        ["pg_restore", "--file=/dev/null", str(path)],
        capture_output=True,
        text=True,
        check=False,
        timeout=PG_DUMP_TIMEOUT_SECONDS,
    )
    if full.returncode != 0:
        msg = f"pg_restore could not read {path.name} through: {tail(full.stderr)}"
        raise BackupError(msg)
    return toc


def take_dump(
    database: str,
    workdir: Path,
    *,
    runner: Callable[..., Any],
    now: Callable[[], datetime],
) -> Dump:
    """Dump, verify, describe. The start time names it: ``pg_dump`` snapshots as it begins."""
    dumped_at = now().astimezone(UTC).replace(microsecond=0)
    alembic_head = read_alembic_head(database, runner=runner)
    workdir.mkdir(parents=True, exist_ok=True)
    path = workdir / f"{DATABASE}.dump"
    run_pg_dump(database, path, runner=runner)
    toc = verify_dump(path, runner=runner)
    return Dump(
        path=path,
        size_bytes=path.stat().st_size,
        sha256=sha256_file(path),
        dumped_at=dumped_at,
        alembic_head=alembic_head,
        toc=toc,
    )


# --- the bucket ---


def preflight(client: Any, bucket: str, prefix: str) -> None:
    """Prove the bucket is there and this key can list it, before the dump.

    A one-object listing, not ``exists()``: the SDK turns a missing bucket's 404
    into ``False``. The listing is lazy, so it is advanced. Listing also proves
    the key still reads, which the restore needs.
    """
    listing = client.list_blobs(
        bucket, max_results=1, prefix=f"{prefix.strip('/')}/", timeout=LIST_TIMEOUT_SECONDS
    )
    try:
        next(iter(listing), None)
    except NotFound as exc:
        msg = f"bucket {bucket!r} not found, or not listable: {exc}"
        raise BackupError(msg) from exc


def upload(client: Any, bucket: str, key: str, dump: Dump, *, host: str) -> str:
    """Create the object; ``unchanged`` only if this very dump is already there."""
    blob = client.bucket(bucket).blob(key)
    blob.metadata = {
        "dumped_at": iso(dump.dumped_at),
        "sha256": dump.sha256,
        "size_bytes": str(dump.size_bytes),
        "alembic_head": dump.alembic_head or "",
        "server_version": dump.toc.server_version or "",
        "pg_dump_version": dump.toc.pg_dump_version or "",
        "toc_entries": "" if dump.toc.entries is None else str(dump.toc.entries),
        "source_host": host,
    }
    try:
        blob.upload_from_filename(
            str(dump.path),
            content_type=CONTENT_TYPE,
            if_generation_match=0,
            timeout=UPLOAD_TIMEOUT_SECONDS,
        )
    except NotFound as exc:
        msg = f"bucket {bucket!r} not found: {exc}"
        raise BackupError(msg) from exc
    except PreconditionFailed:
        # Named to the second: a retried upload of this dump, a run in the same
        # second, or a clock gone back. Only the first is ``unchanged``.
        existing = client.bucket(bucket).get_blob(key, timeout=LIST_TIMEOUT_SECONDS)
        if existing is None:
            msg = f"gs://{bucket}/{key} already exists, and could not be read back"
            raise BackupError(msg) from None
        if (existing.metadata or {}).get("sha256") == dump.sha256:
            return "unchanged"
        msg = f"gs://{bucket}/{key} already exists with other contents; not overwritten"
        raise BackupError(msg) from None
    return "uploaded"


# --- one run ---


def run_backup(
    *,
    database: str,
    bucket: str,
    prefix: str,
    client: Any,
    workdir: Path,
    runner: Callable[..., Any] | None = None,
    host: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Preflight, dump, verify, create. Any failure is a ``BackupError``.

    ``runner`` defaults to ``subprocess.run`` looked up at call time, so a test
    that replaces it can never fall through to the real binaries. A revoked key
    arrives as google.auth's own hierarchy, not a ``GoogleAPICallError``: every
    exception is wrapped.
    """
    host = host or socket.gethostname()
    runner = runner if runner is not None else subprocess.run
    try:
        preflight(client, bucket, prefix)
        dump = take_dump(database, workdir, runner=runner, now=now)
        key = object_key(prefix, dump.dumped_at)
        outcome = upload(client, bucket, key, dump, host=host)
    except Exception as exc:
        raise BackupError(describe(exc)) from exc
    return {
        "outcome": outcome,
        "object": f"gs://{bucket}/{key}",
        "dumped_at": iso(dump.dumped_at),
        "size_bytes": dump.size_bytes,
        "sha256": dump.sha256,
        "alembic_head": dump.alembic_head,
        "source_host": host,
    }


def _client() -> Any:
    from google.cloud import storage  # noqa: PLC0415 — the seam main() takes

    return storage.Client()


def main(
    argv: list[str] | None = None,
    *,
    environ: Mapping[str, str] = os.environ,
    client_factory: Callable[[], Any] = _client,
    runner: Callable[..., Any] | None = None,
    host: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> int:
    """The timer's entry point: exit 0 only when the dump is in the bucket.

    Every return checks in: ``ok`` with the run's summary, or ``alert`` with
    ``source_host``, ``outcome`` and ``error``. A check-in never changes the
    exit status. Exit 2 is misconfiguration, 1 a failed run.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--database", default=DATABASE, help="a name, or a libpq DSN in tests")
    args = parser.parse_args(argv)
    host = host or socket.gethostname()

    def fail(code: int, error: str, fault: str) -> int:
        variables = {"source_host": host, "outcome": "failed", "error": error}
        post_checkin("alert", variables, environ=environ, fault=fault)
        return code

    bucket = environ.get(BUCKET_ENV, "").strip()
    if not bucket:
        # No default: guessing one is how bytes land where nobody reads them.
        logger.error("%s not set: nowhere to ship the dump", BUCKET_ENV)
        return fail(2, f"{BUCKET_ENV} not set", FAULT_MISCONFIGURED)
    if misplaced := misplaced_key(environ):
        logger.error("not starting: %s", misplaced)
        return fail(2, misplaced, FAULT_MISCONFIGURED)
    prefix = environ.get(PREFIX_ENV, "").strip() or host

    try:
        client = client_factory()  # first, so a missing or unreadable key fails before the dump
    except Exception as exc:
        error = describe(exc)
        logger.error("backup failed before it started: %s", error)
        return fail(1, error, FAULT_FAILED)
    with tempfile.TemporaryDirectory(prefix="address-validator-backup-") as work:
        try:
            summary = run_backup(
                database=args.database,
                bucket=bucket,
                prefix=prefix,
                client=client,
                workdir=Path(work),
                runner=runner,
                host=host,
                now=now,
            )
        except BackupError as exc:
            logger.error("backup failed: %s", exc)
            return fail(1, str(exc), FAULT_FAILED)
    logger.info(
        "backup %s: %s (%d bytes, sha256 %s, alembic head %s)",
        summary["outcome"],
        summary["object"],
        summary["size_bytes"],
        summary["sha256"],
        summary["alembic_head"],
    )
    post_checkin("ok", summary, environ=environ)
    return 0


if __name__ == "__main__":
    configure_logging()
    sys.exit(main())
