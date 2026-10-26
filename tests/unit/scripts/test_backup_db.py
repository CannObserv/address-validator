"""The nightly backup's rules (#240), against a fake runner and a fake bucket.

The real ``pg_dump`` → bucket → ``pg_restore`` round trip is
``test_backup_rehearsal.py``. Here: what is refused, what is shipped, what each
outcome checks in, and that no failure path loses its alert.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import backup_db as bd
import pytest
import status_checkin as sc
from google.auth.exceptions import RefreshError

from tests.unit.scripts.gcs_fakes import FakeBucket, FakeClient

BUCKET = "co-address-validator-backup"
HOST = "address-validator"
NOW = datetime(2026, 10, 27, 2, 30, 1, 123456, tzinfo=UTC)
DUMP_BYTES = b"PGDMP fake custom-format archive"


def _toc(tables=bd.REQUIRED_TABLES, server="16.15 (Ubuntu)", dumper="16.15 (Ubuntu)"):
    lines = [
        ";",
        "; Archive created at 2026-10-27 02:30:01 UTC",
        f";     Dumped from database version: {server}",
        f";     Dumped by pg_dump version: {dumper}",
        ";     TOC Entries: 42",
        ";",
    ]
    for n, table in enumerate(tables, start=3000):
        schema, name = table.split(".")
        lines.append(f"{n}; 0 16400 TABLE DATA {schema} {name} address_validator")
    return "\n".join(lines) + "\n"


class FakeRunner:
    """psql, pg_dump and pg_restore as the backup calls them."""

    def __init__(self, *, toc=None, fail=None, head="022"):
        self.toc = toc if toc is not None else _toc()
        self.fail = fail or {}
        self.head = head
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        program = argv[0]
        if "--list" in argv:
            program = "pg_restore --list"
        if program in self.fail:
            return subprocess.CompletedProcess(argv, 1, "", self.fail[program])
        if program == "psql":
            return subprocess.CompletedProcess(argv, 0, f"{self.head}\n", "")
        if program == "pg_dump":
            out = next(a.removeprefix("--file=") for a in argv if a.startswith("--file="))
            with Path(out).open("wb") as handle:
                handle.write(DUMP_BYTES)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if program == "pg_restore --list":
            return subprocess.CompletedProcess(argv, 0, self.toc, "")
        return subprocess.CompletedProcess(argv, 0, "", "")


@pytest.fixture
def bucket():
    return FakeBucket(BUCKET)


@pytest.fixture
def client(bucket):
    return FakeClient(bucket)


def _run(client, tmp_path, runner=None):
    return bd.run_backup(
        database="address_validator",
        bucket=BUCKET,
        prefix=HOST,
        client=client,
        workdir=tmp_path,
        runner=runner or FakeRunner(),
        host=HOST,
        now=lambda: NOW,
    )


# --- pure ---


def test_required_tables_are_alembic_and_every_mirrored_table():
    assert bd.REQUIRED_TABLES[0] == "public.alembic_version"
    assert "public.audit_log" in bd.REQUIRED_TABLES
    assert "public.model_training_candidates" in bd.REQUIRED_TABLES


def test_parse_toc():
    toc = bd.parse_toc(_toc())
    assert toc.server_version == "16.15 (Ubuntu)"
    assert toc.pg_dump_version == "16.15 (Ubuntu)"
    assert toc.entries == 42
    assert toc.tables_with_data == frozenset(bd.REQUIRED_TABLES)


def test_object_key_is_host_and_utc_stamp():
    assert bd.object_key("address-validator/", NOW) == "address-validator/20261027T023001Z.dump"


def test_iso():
    assert bd.iso(NOW) == "2026-10-27T02:30:01Z"


@pytest.mark.parametrize(
    ("version", "expected"),
    [("16.15 (Ubuntu 16.15-0ubuntu0.24.04.1)", "16"), ("", None), (None, None)],
)
def test_major(version, expected):
    assert bd.major(version) == expected


def test_tail_drops_lines_that_can_quote_row_values():
    stderr = (
        'pg_dump: error: Dumping the contents of table "audit_log" failed\n'
        'pg_dump: detail: Error message from server: ERROR:  invalid input "1 Main St"\n'
        'CONTEXT:  COPY audit_log, line 1: "1 Main St"\n'
        "pg_dump: error: the end\n"
    )
    out = bd.tail(stderr)
    assert "Main St" not in out
    assert "failed" in out and "the end" in out


@pytest.mark.parametrize(
    ("environ", "refused"),
    [
        ({}, False),
        ({"CREDENTIALS_DIRECTORY": "/run/creds/x", bd.KEY_PATH_ENV: "/run/creds/x/gcs"}, False),
        (
            {"CREDENTIALS_DIRECTORY": "/run/creds/x", bd.KEY_PATH_ENV: "/etc/address-validator/k"},
            True,
        ),
        ({"CREDENTIALS_DIRECTORY": "/run/creds/x"}, True),
    ],
)
def test_misplaced_key(environ, refused):
    assert (bd.misplaced_key(environ) is not None) is refused


# --- one run ---


def test_ships_a_verified_dump_with_its_metadata(client, bucket, tmp_path):
    runner = FakeRunner()
    summary = _run(client, tmp_path, runner)
    key = "address-validator/20261027T023001Z.dump"
    assert summary == {
        "outcome": "uploaded",
        "object": f"gs://{BUCKET}/{key}",
        "dumped_at": "2026-10-27T02:30:01Z",
        "size_bytes": len(DUMP_BYTES),
        "sha256": bd.sha256_file(tmp_path / "address_validator.dump"),
        "alembic_head": "022",
        "source_host": HOST,
    }
    assert bucket.objects[key] == DUMP_BYTES
    assert bucket.preconditions == [0]
    meta = bucket.metadata[key]
    assert meta["sha256"] == summary["sha256"]
    assert meta["alembic_head"] == "022"
    assert meta["source_host"] == HOST
    assert meta["server_version"] == meta["pg_dump_version"] == "16.15 (Ubuntu)"
    assert meta["toc_entries"] == "42"
    programs = [argv[0] for argv in runner.calls]
    assert programs == ["psql", "pg_dump", "pg_restore", "pg_restore"]
    assert "--file=/dev/null" in runner.calls[-1]
    assert any(a.startswith("--file=") for a in runner.calls[1])


def test_a_dump_of_the_wrong_database_is_refused(client, bucket, tmp_path):
    runner = FakeRunner(toc=_toc(tables=("public.alembic_version", "public.sources")))
    with pytest.raises(bd.BackupError, match=r"no data for public\.audit_daily_stats"):
        _run(client, tmp_path, runner)
    assert bucket.objects == {}


def test_a_dump_by_another_major_is_refused(client, bucket, tmp_path):
    runner = FakeRunner(toc=_toc(dumper="17.2"))
    with pytest.raises(bd.BackupError, match="not the server's major"):
        _run(client, tmp_path, runner)
    assert bucket.objects == {}


@pytest.mark.parametrize(
    ("program", "match"),
    [
        ("psql", "alembic_version"),
        ("pg_dump", "pg_dump exited 1"),
        ("pg_restore --list", "--list rejected"),
        ("pg_restore", "could not read .* through"),
    ],
)
def test_any_failed_step_ships_nothing(client, bucket, tmp_path, program, match):
    runner = FakeRunner(fail={program: "boom"})
    with pytest.raises(bd.BackupError, match=match):
        _run(client, tmp_path, runner)
    assert bucket.objects == {}


def test_a_missing_bucket_fails_before_the_dump(bucket, tmp_path):
    client = FakeClient(bucket, missing=frozenset({BUCKET}))
    runner = FakeRunner()
    with pytest.raises(bd.BackupError, match="not found"):
        _run(client, tmp_path, runner)
    assert runner.calls == []


def test_a_retried_upload_of_the_same_dump_is_unchanged(client, bucket, tmp_path):
    first = _run(client, tmp_path)
    second = _run(client, tmp_path)
    assert first["outcome"] == "uploaded"
    assert second["outcome"] == "unchanged"


def test_an_existing_object_with_other_contents_is_never_overwritten(client, bucket, tmp_path):
    key = "address-validator/20261027T023001Z.dump"
    bucket.put(key, b"older", {"sha256": "0" * 64})
    with pytest.raises(bd.BackupError, match="other contents; not overwritten"):
        _run(client, tmp_path)
    assert bucket.objects[key] == b"older"


def test_any_exception_becomes_a_backup_error(client, bucket, tmp_path):
    bucket.fail_uploads = RefreshError("key revoked")
    with pytest.raises(bd.BackupError, match="RefreshError: key revoked"):
        _run(client, tmp_path)


# --- main: exit status and check-ins ---


@pytest.fixture
def checkins(monkeypatch):
    sent = []

    def fake(status, variables, *, environ, fault=None, post=None):
        sent.append((status, variables, fault))
        return True

    monkeypatch.setattr(bd, "post_checkin", fake)
    return sent


def _main(client, environ, runner=None):
    return bd.main(
        [],
        environ=environ,
        client_factory=lambda: client,
        runner=runner or FakeRunner(),
        host=HOST,
        now=lambda: NOW,
    )


def test_main_ok_checks_in_the_summary(client, bucket, checkins):
    assert _main(client, {bd.BUCKET_ENV: BUCKET}) == 0
    ((status, variables, fault),) = checkins
    assert status == "ok"
    assert fault is None
    assert variables["object"] == f"gs://{BUCKET}/address-validator/20261027T023001Z.dump"
    assert variables["alembic_head"] == "022"


def test_main_honours_a_prefix_override(client, bucket, checkins):
    assert _main(client, {bd.BUCKET_ENV: BUCKET, bd.PREFIX_ENV: "address-validator-next"}) == 0
    assert list(bucket.objects) == ["address-validator-next/20261027T023001Z.dump"]


def test_main_without_a_bucket_exits_2_and_alerts(client, checkins):
    assert _main(client, {}) == 2
    ((status, variables, fault),) = checkins
    assert (status, fault) == ("alert", bd.FAULT_MISCONFIGURED)
    assert variables == {"source_host": HOST, "outcome": "failed", "error": "BACKUP_BUCKET not set"}


def test_main_refuses_a_key_outside_its_credentials(client, checkins):
    env = {bd.BUCKET_ENV: BUCKET, "CREDENTIALS_DIRECTORY": "/run/c", bd.KEY_PATH_ENV: "/etc/k"}
    assert _main(client, env) == 2
    assert checkins[0][2] == bd.FAULT_MISCONFIGURED


def test_main_unreadable_key_exits_1_before_the_dump(checkins):
    runner = FakeRunner()

    def factory():
        raise FileNotFoundError("gcs")

    code = bd.main(
        [], environ={bd.BUCKET_ENV: BUCKET}, client_factory=factory, runner=runner, host=HOST
    )
    assert code == 1
    assert runner.calls == []
    assert checkins[0][0] == "alert"
    assert "FileNotFoundError" in checkins[0][1]["error"]


def test_main_failed_run_exits_1_and_alerts(client, checkins):
    assert _main(client, {bd.BUCKET_ENV: BUCKET}, FakeRunner(fail={"pg_dump": "boom"})) == 1
    ((status, variables, fault),) = checkins
    assert (status, fault) == ("alert", bd.FAULT_FAILED)
    assert "pg_dump exited 1" in variables["error"]


def test_main_with_the_real_checkin_unconfigured_still_succeeds(client, caplog):
    """No monitor id yet: the run ships, warns, and exits 0."""
    assert _main(client, {bd.BUCKET_ENV: BUCKET}) == 0
    assert "not configured" in caplog.text


def test_the_checkin_reads_the_unit_s_credential_id():
    assert sc.KEY_CREDENTIAL == "checkin-key"
