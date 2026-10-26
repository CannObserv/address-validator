#!/usr/bin/env python3
"""Bring a shipped dump back, and prove the restore (#240).

Ported from CannObserv/wslcb-licensing-tracker's ``restore.py``. Each step
refuses rather than guesses:

1. **Find** a dump: by ``--key``, or ``--latest`` under a ``--prefix`` that is
   always named. A restore usually runs on another host than the one that
   shipped the dump, and once that host's own timer has run, its newest dump is
   a real, verifiable dump of the wrong database. A name later than the bucket's
   own creation time for it (a skewed clock, a forgery) is passed over.
2. **Fetch** it into a private directory (0700, file 0600, ``O_EXCL``), check
   the sha256 the backup recorded, then the backup's own ``pg_restore`` reads.
3. **Restore** into an existing, *empty* database, on stdin, with
   ``--exit-on-error --single-transaction``: a failure leaves it empty, and
   ``postgres`` never reads a file root wrote.
4. **Go/no-go:** every table's row count equals the dump's own ``COPY`` count,
   the alembic head equals the one the backup recorded, and the roles the dump's
   grants name exist. Any miss exits 1.

The dump carries ownership and grants, not roles: on a fresh cluster the runbook
creates ``address_validator`` first. ``docs/RECOVERY.md`` is that runbook.

Run as root with the backup key, from the checkout (never in the unit):
    sudo env PYTHONPATH=src GOOGLE_APPLICATION_CREDENTIALS=/etc/address-validator/backup-gcs.json \\
        .venv/bin/python infra/restore_db.py --list
    ... --latest --prefix address-validator --into address_validator_drill --run-as postgres
    ... --latest --prefix address-validator --download-only /root/restore

Prints names, counts and versions only; never row values (the dump holds PII).
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, NamedTuple

from backup_db import (
    BUCKET_ENV,
    KEY_TIME_FORMAT,
    LIST_TIMEOUT_SECONDS,
    OBJECT_SUFFIX,
    PG_DUMP_TIMEOUT_SECONDS,
    QUERY_TIMEOUT_SECONDS,
    UPLOAD_TIMEOUT_SECONDS,
    BackupError,
    sha256_file,
    tail,
    verify_dump,
)
from google.api_core.exceptions import NotFound

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

logger = logging.getLogger("restore_db")

#: How far a dump's name may run ahead of the bucket's creation time for it.
CLOCK_SKEW_TOLERANCE = timedelta(minutes=10)
#: What ``--list`` prints beside each name, in this order.
LISTED_METADATA = ("dumped_at", "alembic_head", "size_bytes", "source_host", "sha256")
#: Roles the dump's ownership and grants name, and the backup's own.
REQUIRED_ROLES = ("address_validator", "address_validator_backup")

_COPY_RE = re.compile(r"^COPY (\S+) .*FROM stdin;$")
_TABLE_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$")
_COPY_END = "\\."


class RestoreError(Exception):
    """Anything that means nothing was restored, or the restore is not proven."""


def as_user(argv: list[str], run_as: str | None) -> list[str]:
    """Prefix ``argv`` to run as ``run_as`` via ``setpriv``; unchanged when None.

    ``setpriv`` opens no PAM session, and ``--reset-env`` hands the child only
    its passwd entry's basics, so nothing of root's shell (the key path among
    it) reaches a process any ``postgres`` uid can read.
    """
    if run_as is None:
        return argv
    return [
        "setpriv",
        f"--reuid={run_as}",
        f"--regid={run_as}",
        "--init-groups",
        "--reset-env",
        "--",
        *argv,
    ]


class Snapshot(NamedTuple):
    """One shipped dump: its name, its metadata, and the bucket's creation time."""

    name: str
    metadata: dict
    created: datetime | None

    @property
    def named_at(self) -> datetime | None:
        """The time the name claims; None for a name the backup never writes."""
        stamp = PurePosixPath(self.name).name.removesuffix(OBJECT_SUFFIX)
        try:
            return datetime.strptime(stamp, KEY_TIME_FORMAT).replace(tzinfo=UTC)
        except ValueError:
            return None

    @property
    def suspect(self) -> bool:
        """A name claiming a time the bucket's own clock contradicts.

        An honest dump is named when ``pg_dump`` starts and created when the
        upload lands, so its name is never later than its creation beyond clock
        skew. A planted ``2099…`` would otherwise own ``--latest``.
        """
        named_at = self.named_at
        if named_at is None or self.created is None:
            return True
        return named_at > self.created + CLOCK_SKEW_TOLERANCE


def list_snapshots(client: Any, bucket: str, prefix: str | None) -> list[Snapshot]:
    """Every dump under ``prefix``'s host (every host's, if None), by name."""
    under = f"{prefix.strip('/')}/" if prefix else None
    snapshots = [
        Snapshot(blob.name, dict(blob.metadata or {}), blob.time_created)
        for blob in client.list_blobs(bucket, prefix=under, timeout=LIST_TIMEOUT_SECONDS)
        if blob.name.endswith(OBJECT_SUFFIX)
    ]
    return sorted(snapshots, key=lambda snapshot: snapshot.name)


def latest_key(client: Any, bucket: str, prefix: str) -> str:
    """The newest dump the named host shipped, by stamp, passing over suspects."""
    snapshots = list_snapshots(client, bucket, prefix)
    suspect = [snapshot.name for snapshot in snapshots if snapshot.suspect]
    if suspect:
        logger.warning(
            "passing over dumps named later than the bucket created them: %s", ", ".join(suspect)
        )
    candidates = [snapshot for snapshot in snapshots if not snapshot.suspect]
    if not candidates:
        passed = f" (passed over {len(suspect)} suspect)" if suspect else ""
        msg = f"no dumps for {prefix} in gs://{bucket}{passed}"
        raise RestoreError(msg)
    return max(candidates, key=lambda snapshot: snapshot.named_at).name


def _private_dir(dest_dir: Path) -> None:
    """Create ``dest_dir`` 0700, or accept only a private one of this user's own.

    The fetch runs as root and writes the whole production database: a directory
    another user made first would let them read it, or plant a link for root to
    write through.
    """
    dest_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = dest_dir.lstat()
    if not stat.S_ISDIR(st.st_mode):
        msg = f"{dest_dir} is not a directory; refusing it"
        raise RestoreError(msg)
    if st.st_uid != os.geteuid():
        msg = f"{dest_dir} is owned by uid {st.st_uid}, not this user; refusing it"
        raise RestoreError(msg)
    if st.st_mode & 0o077:
        msg = f"{dest_dir} is not private (mode {stat.S_IMODE(st.st_mode):o}); use a new directory"
        raise RestoreError(msg)


def _download_new(blob: Any, path: Path) -> None:
    """Download into a file created ``O_EXCL | O_NOFOLLOW`` at 0600."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError as exc:
        msg = f"{path} already exists; refusing to write over it"
        raise RestoreError(msg) from exc
    with os.fdopen(fd, "wb") as handle:
        try:
            blob.download_to_file(handle, timeout=UPLOAD_TIMEOUT_SECONDS)
        except NotFound as exc:
            msg = f"{blob.name} not found"
            raise RestoreError(msg) from exc


@dataclass(frozen=True)
class Fetched:
    """A fetched, verified dump and what the backup recorded about it."""

    path: Path
    key: str
    metadata: dict


def fetch(
    client: Any, bucket: str, key: str, dest_dir: Path, *, runner: Callable[..., Any]
) -> Fetched:
    """Download ``key`` into ``dest_dir`` and prove it is the dump that was shipped.

    A failed fetch removes what it wrote.
    """
    blob = client.bucket(bucket).get_blob(key, timeout=LIST_TIMEOUT_SECONDS)
    if blob is None:
        msg = f"gs://{bucket}/{key} not found"
        raise RestoreError(msg)
    meta = dict(blob.metadata or {})
    expected = meta.get("sha256")
    if not expected:
        msg = f"gs://{bucket}/{key} carries no recorded sha256; refusing it"
        raise RestoreError(msg)
    _private_dir(dest_dir)
    path = dest_dir / PurePosixPath(key).name
    _download_new(blob, path)
    try:
        actual = sha256_file(path)
        if actual != expected:
            msg = f"{key}: sha256 {actual} does not match the recorded {expected}"
            raise RestoreError(msg)
        try:
            verify_dump(path, runner=runner)
        except BackupError as exc:
            raise RestoreError(str(exc)) from exc
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return Fetched(path, key, meta)


def _psql(database: str, sql: str, *, run_as: str | None, runner: Callable[..., Any]) -> str:
    """One query's unaligned output. ``sql`` is this module's own, never input."""
    argv = as_user(
        [
            "psql",
            "--no-password",
            "--no-psqlrc",
            "--quiet",
            "--tuples-only",
            "--no-align",
            "--set=ON_ERROR_STOP=1",
            f"--dbname={database}",
            f"--command={sql}",
        ],
        run_as,
    )
    result = runner(
        argv, capture_output=True, text=True, check=False, timeout=QUERY_TIMEOUT_SECONDS
    )
    if result.returncode != 0:
        msg = f"psql on {database} failed: {tail(result.stderr)}"
        raise RestoreError(msg)
    return result.stdout.strip()


def require_empty(database: str, *, run_as: str | None, runner: Callable[..., Any]) -> None:
    """Refuse a target that holds any table: the restore loads into an empty database."""
    sql = (
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE c.relkind IN ('r', 'p') "
        "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
        "AND n.nspname NOT LIKE 'pg_toast%'"
    )
    count = _psql(database, sql, run_as=run_as, runner=runner)
    if count != "0":
        msg = f"{database} already holds {count} table(s); restore only into an empty database"
        raise RestoreError(msg)


def restore_into(
    path: Path, database: str, *, run_as: str | None, runner: Callable[..., Any]
) -> None:
    """Load ``path`` into ``database``, all or nothing, on stdin."""
    argv = as_user(
        [
            "pg_restore",
            "--no-password",
            "--exit-on-error",
            "--single-transaction",
            f"--dbname={database}",
        ],
        run_as,
    )
    with path.open("rb") as handle:
        result = runner(
            argv,
            stdin=handle,
            capture_output=True,
            text=True,
            check=False,
            timeout=PG_DUMP_TIMEOUT_SECONDS,
        )
    if result.returncode != 0:
        msg = f"pg_restore exited {result.returncode}: {tail(result.stderr)}"
        raise RestoreError(msg)


# --- go/no-go ---


def count_copy_rows(lines: Iterable[str]) -> dict[str, int]:
    """Each table's row count from a plain-text dump's ``COPY ... FROM stdin`` blocks.

    One line per row: COPY's text format escapes a newline inside a value as
    ``\\n``. The lines themselves are never kept or shown.
    """
    counts: dict[str, int] = {}
    table: str | None = None
    for raw in lines:
        line = raw.rstrip("\n")
        if table is None:
            if match := _COPY_RE.match(line):
                table = match.group(1)
                counts[table] = 0
        elif line == _COPY_END:
            table = None
        else:
            counts[table] += 1
    if table is not None:
        msg = f"the dump's COPY block for {table} never ends"
        raise RestoreError(msg)
    return counts


def dump_row_counts(path: Path) -> dict[str, int]:
    """Row counts per table, as the dump itself holds them."""
    argv = ["pg_restore", "--data-only", "--file=-", str(path)]
    with subprocess.Popen(  # noqa: S603 — fixed argv
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    ) as proc:
        assert proc.stdout is not None
        counts = count_copy_rows(proc.stdout)
        stderr = proc.stderr.read() if proc.stderr else ""
    if proc.returncode != 0:
        msg = f"pg_restore --data-only exited {proc.returncode}: {tail(stderr)}"
        raise RestoreError(msg)
    return counts


@dataclass(frozen=True)
class GateReport:
    """The go/no-go result, each gate's verdict and the lines to print."""

    lines: list[str]
    passed: bool


def go_no_go(
    database: str,
    fetched: Fetched,
    *,
    run_as: str | None,
    runner: Callable[..., Any],
    roles: Iterable[str] = REQUIRED_ROLES,
    expected_counts: dict[str, int] | None = None,
) -> GateReport:
    """Row counts, alembic head and roles against what the dump and the backup recorded."""
    lines: list[str] = []
    passed = True
    expected = expected_counts if expected_counts is not None else dump_row_counts(fetched.path)
    for table, want in sorted(expected.items()):
        if not _TABLE_NAME_RE.match(table):
            msg = f"the dump names a table this check won't query: {table!r}"
            raise RestoreError(msg)
        sql = f"SELECT count(*) FROM {table}"  # noqa: S608 — table matched _TABLE_NAME_RE
        got = int(_psql(database, sql, run_as=run_as, runner=runner))
        ok = got == want
        passed &= ok
        lines.append(f"{'ok  ' if ok else 'FAIL'} rows {table}: {got} (dump {want})")

    recorded = fetched.metadata.get("alembic_head", "")
    head = _psql(database, "SELECT version_num FROM alembic_version", run_as=run_as, runner=runner)
    ok = bool(recorded) and head == recorded
    passed &= ok
    lines.append(f"{'ok  ' if ok else 'FAIL'} alembic head: {head} (recorded {recorded or '-'})")

    # Roles are cluster state: pg_roles reads the same from any database.
    wanted = sorted(roles)
    if not wanted or not all(re.fullmatch(r"[a-z_][a-z0-9_]*", r) for r in wanted):
        msg = f"roles to check must be plain names: {wanted!r}"
        raise RestoreError(msg)
    names = ", ".join(f"'{r}'" for r in wanted)
    sql = f"SELECT rolname FROM pg_roles WHERE rolname IN ({names})"  # noqa: S608 — checked above
    present = set(_psql(database, sql, run_as=run_as, runner=runner).split())
    for role in wanted:
        ok = role in present
        passed &= ok
        lines.append(f"{'ok  ' if ok else 'FAIL'} role {role}: {'present' if ok else 'missing'}")
    return GateReport(lines, passed)


# --- the command ---


def _client() -> Any:
    from google.cloud import storage  # noqa: PLC0415 — the seam main() takes

    return storage.Client()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bucket", default=os.environ.get(BUCKET_ENV, ""), help="GCS bucket")
    pick = parser.add_mutually_exclusive_group(required=True)
    pick.add_argument("--list", action="store_true", help="list dumps (all hosts, or --prefix)")
    pick.add_argument("--latest", action="store_true", help="the newest dump under --prefix")
    pick.add_argument("--key", help="one object, by name")
    parser.add_argument("--prefix", help="the source host's prefix; required with --latest")
    into = parser.add_mutually_exclusive_group()
    into.add_argument("--into", metavar="DB", help="an existing, empty database to restore into")
    into.add_argument("--download-only", metavar="DIR", type=Path, help="fetch and verify only")
    parser.add_argument("--run-as", metavar="USER", help="run pg_restore and psql as USER")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    client_factory: Callable[[], Any] = _client,
    runner: Callable[..., Any] | None = None,
) -> int:
    """Exit 0 on a listing, a verified fetch, or a restore that passed go/no-go."""
    parser = _parser()
    args = parser.parse_args(argv)
    runner = runner if runner is not None else subprocess.run
    if not args.bucket:
        parser.error(f"--bucket or {BUCKET_ENV} is required")
    if args.latest and not args.prefix:
        parser.error("--latest needs --prefix: name the source host, never assume this one")
    if not args.list and not (args.into or args.download_only):
        parser.error("say where: --into DB or --download-only DIR")
    client = client_factory()
    try:
        if args.list:
            for snapshot in list_snapshots(client, args.bucket, args.prefix):
                flag = "SUSPECT " if snapshot.suspect else ""
                fields = " ".join(f"{k}={snapshot.metadata.get(k, '-')}" for k in LISTED_METADATA)
                print(f"{flag}{snapshot.name} {fields}")
            return 0
        key = args.key or latest_key(client, args.bucket, args.prefix)
        if args.download_only:
            fetched = fetch(client, args.bucket, key, args.download_only, runner=runner)
            print(f"fetched and verified: {fetched.path} (sha256 {fetched.metadata['sha256']})")
            return 0
        require_empty(args.into, run_as=args.run_as, runner=runner)
        with tempfile.TemporaryDirectory(prefix="address-validator-restore-") as work:
            fetched = fetch(client, args.bucket, key, Path(work) / "dump", runner=runner)
            print(f"restoring gs://{args.bucket}/{key} into {args.into}")
            restore_into(fetched.path, args.into, run_as=args.run_as, runner=runner)
            report = go_no_go(args.into, fetched, run_as=args.run_as, runner=runner)
    except (RestoreError, BackupError) as exc:
        logger.error("%s", exc)
        return 1
    print("\n".join(report.lines))
    print("GO" if report.passed else "NO-GO")
    return 0 if report.passed else 1


if __name__ == "__main__":
    # By hand at a terminal, never under a unit: plain lines, not journal priorities.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    sys.exit(main())
