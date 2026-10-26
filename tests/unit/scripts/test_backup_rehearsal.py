"""Backup → restore rehearsal against a real PostgreSQL (#240).

What the fakes can't answer: whether a real ``pg_dump`` of the migrated schema
passes the backup's own verification, survives the (fake) bucket, comes back by
``--latest`` and restores into an empty database that passes the go/no-go —
and whether a truncated real archive is refused. Ported from
wslcb-licensing-tracker's ``tests/test_backup_restore_rehearsal.py``.

The source is the test database ``VALIDATION_CACHE_DSN`` names (an isolated
``address_validator_test_<n>`` when one is exported; docs/RECOVERY.md). The
test role can't create databases, so the restore target is a standing one
beside it, ``<source>_rehearsal``, emptied by each test:

    sudo -u postgres createdb -O address_validator address_validator_test_rehearsal
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime

import backup_db as bd
import pytest
import restore_db as rd
from alembic.config import Config

from alembic import command
from tests.unit.scripts.gcs_fakes import FakeBucket, FakeClient

BUCKET = "rehearsal-bucket"
HOST = "rehearsal-host"
SEED_REQUEST_ID = "backup-rehearsal-240"


def _libpq(url: str) -> str:
    """SQLAlchemy's ``postgresql+asyncpg://`` as a libpq DSN."""
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _psql(dsn: str, sql: str) -> str:
    argv = ["psql", "--no-psqlrc", "-qAt", "-v", "ON_ERROR_STOP=1", f"--dbname={dsn}", "-c", sql]
    return subprocess.run(argv, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture(scope="module")
def source_dsn():
    """The migrated test database, as libpq sees it, holding one seeded audit row."""
    url = os.environ["VALIDATION_CACHE_DSN"]
    assert "address_validator_test" in url
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
    dsn = _libpq(url)
    _psql(
        dsn,
        "INSERT INTO audit_log (timestamp, request_id, client_ip, method, endpoint, "
        "status_code, raw_input) VALUES (now(), "
        f"'{SEED_REQUEST_ID}', '192.0.2.1', 'POST', '/api/v2/parse', 200, '1 Main St')",
    )
    yield dsn
    _psql(dsn, f"DELETE FROM audit_log WHERE request_id = '{SEED_REQUEST_ID}'")


@pytest.fixture
def target_dsn(source_dsn):
    """``<source>_rehearsal``, emptied: its public schema dropped and recreated."""
    base, name = source_dsn.rsplit("/", 1)
    target = f"{name}_rehearsal"
    exists = _psql(source_dsn, f"SELECT 1 FROM pg_database WHERE datname = '{target}'")
    if exists != "1":
        pytest.fail(
            f"restore target {target} missing: "
            f"sudo -u postgres createdb -O address_validator {target}"
        )
    dsn = f"{base}/{target}"
    _psql(dsn, "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public")
    yield dsn
    _psql(dsn, "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public")


@pytest.fixture
def client():
    return FakeClient(FakeBucket(BUCKET))


def _ship(client, source_dsn, tmp_path):
    return bd.run_backup(
        database=source_dsn,
        bucket=BUCKET,
        prefix=HOST,
        client=client,
        workdir=tmp_path,
        host=HOST,
        now=lambda: datetime.now(UTC),
    )


def _fetch(client, tmp_path):
    key = rd.latest_key(client, BUCKET, HOST)
    return rd.fetch(client, BUCKET, key, tmp_path / "fetched", runner=subprocess.run)


def test_round_trip_passes_the_go_no_go(client, source_dsn, target_dsn, tmp_path):
    """Dump, ship, fetch by --latest, restore: same rows, same head."""
    summary = _ship(client, source_dsn, tmp_path / "work")
    assert summary["outcome"] == "uploaded"
    assert summary["alembic_head"] == _psql(source_dsn, "SELECT version_num FROM alembic_version")

    fetched = _fetch(client, tmp_path)
    rd.require_empty(target_dsn, run_as=None, runner=subprocess.run)
    rd.restore_into(fetched.path, target_dsn, run_as=None, runner=subprocess.run)
    report = rd.go_no_go(
        target_dsn, fetched, run_as=None, runner=subprocess.run, roles=("address_validator",)
    )
    assert report.passed, report.lines

    counts = rd.dump_row_counts(fetched.path)
    assert set(counts) == set(bd.REQUIRED_TABLES)
    assert counts["public.audit_log"] >= 1
    seeded = f"SELECT raw_input FROM audit_log WHERE request_id = '{SEED_REQUEST_ID}'"
    assert _psql(target_dsn, seeded) == "1 Main St"


def test_truncated_dump_is_refused(source_dsn, tmp_path):
    """A cut archive still lists; the read-through must catch it."""
    path = tmp_path / "full.dump"
    bd.run_pg_dump(source_dsn, path, runner=subprocess.run)
    data = path.read_bytes()
    cut = tmp_path / "cut.dump"
    cut.write_bytes(data[: len(data) * 3 // 5])
    with pytest.raises(bd.BackupError):
        bd.verify_dump(cut, runner=subprocess.run)


def test_failed_restore_leaves_the_target_as_it_was(client, source_dsn, target_dsn, tmp_path):
    """--single-transaction: a restore that fails part-way loads nothing."""
    _ship(client, source_dsn, tmp_path / "work")
    fetched = _fetch(client, tmp_path)
    _psql(target_dsn, "CREATE TABLE public.audit_log (blocker int)")  # collides mid-restore
    with pytest.raises(rd.RestoreError):
        rd.require_empty(target_dsn, run_as=None, runner=subprocess.run)
    with pytest.raises(rd.RestoreError, match="pg_restore exited"):
        rd.restore_into(fetched.path, target_dsn, run_as=None, runner=subprocess.run)
    tables = _psql(
        target_dsn, "SELECT string_agg(tablename, ',') FROM pg_tables WHERE schemaname = 'public'"
    )
    assert tables == "audit_log"
