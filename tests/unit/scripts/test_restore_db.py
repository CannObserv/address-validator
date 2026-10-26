"""The restore's refusals and its go/no-go (#240), against fakes.

The real round trip is ``test_backup_rehearsal.py``.
"""

from __future__ import annotations

import hashlib
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
import restore_db as rd

from tests.unit.scripts.gcs_fakes import FakeBucket, FakeClient
from tests.unit.scripts.test_backup_db import FakeRunner

BUCKET = "co-address-validator-backup"
HOST = "address-validator"
CREATED = datetime(2026, 10, 27, 2, 30, 5, tzinfo=UTC)
DATA = b"PGDMP a dump"


def _seed(bucket, name, *, data=DATA, created=CREATED, **meta):
    metadata = {"sha256": hashlib.sha256(data).hexdigest(), "alembic_head": "022", **meta}
    bucket.put(name, data, metadata, created=created)


@pytest.fixture
def bucket():
    return FakeBucket(BUCKET)


@pytest.fixture
def client(bucket):
    return FakeClient(bucket)


# --- find ---


def test_latest_is_the_newest_stamp_under_the_named_host(client, bucket):
    _seed(bucket, f"{HOST}/20261025T023001Z.dump", created=CREATED - timedelta(days=2))
    _seed(bucket, f"{HOST}/20261027T023001Z.dump")
    _seed(bucket, "other-host/20261027T050000Z.dump", created=CREATED + timedelta(hours=3))
    assert rd.latest_key(client, BUCKET, HOST) == f"{HOST}/20261027T023001Z.dump"


def test_latest_passes_over_a_name_later_than_its_creation(client, bucket, caplog):
    _seed(bucket, f"{HOST}/20261027T023001Z.dump")
    _seed(bucket, f"{HOST}/20991231T000000Z.dump")
    assert rd.latest_key(client, BUCKET, HOST) == f"{HOST}/20261027T023001Z.dump"
    assert "20991231T000000Z" in caplog.text


def test_latest_with_nothing_usable_is_an_error(client, bucket):
    _seed(bucket, f"{HOST}/not-a-stamp.dump")
    with pytest.raises(rd.RestoreError, match="passed over 1 suspect"):
        rd.latest_key(client, BUCKET, HOST)


def test_list_skips_non_dump_objects(client, bucket):
    _seed(bucket, f"{HOST}/20261027T023001Z.dump")
    bucket.put("probe/20261026T000000Z", b"probe")
    assert [s.name for s in rd.list_snapshots(client, BUCKET, None)] == [
        f"{HOST}/20261027T023001Z.dump"
    ]


# --- fetch ---


def test_fetch_writes_a_private_verified_file(client, bucket, tmp_path):
    key = f"{HOST}/20261027T023001Z.dump"
    _seed(bucket, key)
    runner = FakeRunner()
    fetched = rd.fetch(client, BUCKET, key, tmp_path / "out", runner=runner)
    assert fetched.path.read_bytes() == DATA
    assert fetched.path.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "out").stat().st_mode & 0o777 == 0o700
    assert fetched.metadata["alembic_head"] == "022"
    assert [argv[0] for argv in runner.calls] == ["pg_restore", "pg_restore"]


def test_fetch_refuses_a_sha256_mismatch_and_removes_the_file(client, bucket, tmp_path):
    key = f"{HOST}/20261027T023001Z.dump"
    bucket.put(key, DATA, {"sha256": "0" * 64}, created=CREATED)
    with pytest.raises(rd.RestoreError, match="does not match"):
        rd.fetch(client, BUCKET, key, tmp_path / "out", runner=FakeRunner())
    assert list((tmp_path / "out").iterdir()) == []


def test_fetch_refuses_an_object_without_a_recorded_sha256(client, bucket, tmp_path):
    key = f"{HOST}/20261027T023001Z.dump"
    bucket.put(key, DATA, {}, created=CREATED)
    with pytest.raises(rd.RestoreError, match="no recorded sha256"):
        rd.fetch(client, BUCKET, key, tmp_path / "out", runner=FakeRunner())


def test_fetch_refuses_a_dump_the_backup_checks_reject(client, bucket, tmp_path):
    key = f"{HOST}/20261027T023001Z.dump"
    _seed(bucket, key)
    with pytest.raises(rd.RestoreError, match="through"):
        rd.fetch(client, BUCKET, key, tmp_path / "out", runner=FakeRunner(fail={"pg_restore": "x"}))
    assert list((tmp_path / "out").iterdir()) == []


def test_fetch_never_writes_over_a_file(client, bucket, tmp_path):
    key = f"{HOST}/20261027T023001Z.dump"
    _seed(bucket, key)
    out = tmp_path / "out"
    out.mkdir(mode=0o700)
    (out / "20261027T023001Z.dump").write_bytes(b"mine")
    with pytest.raises(rd.RestoreError, match="already exists"):
        rd.fetch(client, BUCKET, key, out, runner=FakeRunner())
    assert (out / "20261027T023001Z.dump").read_bytes() == b"mine"


def test_fetch_refuses_a_directory_others_can_read(client, bucket, tmp_path):
    key = f"{HOST}/20261027T023001Z.dump"
    _seed(bucket, key)
    out = tmp_path / "out"
    out.mkdir()
    out.chmod(0o755)
    with pytest.raises(rd.RestoreError, match="not private"):
        rd.fetch(client, BUCKET, key, out, runner=FakeRunner())


def test_as_user_drops_to_the_user_with_a_clean_environment():
    assert rd.as_user(["psql"], None) == ["psql"]
    argv = rd.as_user(["psql"], "postgres")
    assert argv[0] == "setpriv"
    assert "--reset-env" in argv
    assert argv[-2:] == ["--", "psql"]


# --- go/no-go ---


def test_count_copy_rows():
    lines = [
        "SET statement_timeout = 0;\n",
        "COPY public.audit_log (id, raw_input) FROM stdin;\n",
        "1\t1 Main St\\nApt 2\n",
        "2\t\\N\n",
        "\\.\n",
        "COPY public.training_batches (id) FROM stdin;\n",
        "\\.\n",
    ]
    assert rd.count_copy_rows(lines) == {"public.audit_log": 2, "public.training_batches": 0}


def test_count_copy_rows_refuses_an_unterminated_block():
    with pytest.raises(rd.RestoreError, match="never ends"):
        rd.count_copy_rows(["COPY public.audit_log (id) FROM stdin;\n", "1\n"])


class DbRunner:
    """psql answers for the go/no-go and the emptiness check."""

    def __init__(self, counts, head="022", roles=rd.REQUIRED_ROLES, tables="0"):
        self.counts, self.head, self.roles, self.tables = counts, head, roles, tables
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        sql = next(a.removeprefix("--command=") for a in argv if a.startswith("--command="))
        if sql.startswith("SELECT count(*) FROM pg_class"):
            out = self.tables
        elif sql.startswith("SELECT count(*) FROM "):
            out = str(self.counts[sql.removeprefix("SELECT count(*) FROM ")])
        elif "alembic_version" in sql:
            out = self.head
        else:
            out = "\n".join(self.roles)
        return subprocess.CompletedProcess(argv, 0, out + "\n", "")


def _fetched(tmp_path, head="022"):
    return rd.Fetched(tmp_path / "x.dump", "k", {"alembic_head": head})


EXPECTED = {"public.audit_log": 2, "public.alembic_version": 1}


def test_go_when_counts_head_and_roles_match(tmp_path):
    report = rd.go_no_go(
        "drill",
        _fetched(tmp_path),
        run_as=None,
        runner=DbRunner(EXPECTED),
        expected_counts=EXPECTED,
    )
    assert report.passed
    assert "ok   rows public.audit_log: 2 (dump 2)" in report.lines


@pytest.mark.parametrize(
    ("runner", "head", "failed"),
    [
        (DbRunner({**EXPECTED, "public.audit_log": 1}), "022", "rows public.audit_log"),
        (DbRunner(EXPECTED, head="021"), "022", "alembic head"),
        (DbRunner(EXPECTED), "", "alembic head"),
        (DbRunner(EXPECTED, roles=("address_validator",)), "022", "role address_validator_backup"),
    ],
)
def test_no_go_on_any_mismatch(tmp_path, runner, head, failed):
    report = rd.go_no_go(
        "drill", _fetched(tmp_path, head), run_as=None, runner=runner, expected_counts=EXPECTED
    )
    assert not report.passed
    assert any(line.startswith("FAIL") and failed in line for line in report.lines)


def test_go_no_go_refuses_a_table_name_it_would_not_quote(tmp_path):
    with pytest.raises(rd.RestoreError, match="won't query"):
        rd.go_no_go(
            "drill",
            _fetched(tmp_path),
            run_as=None,
            runner=DbRunner({}),
            expected_counts={"public.x; DROP TABLE y": 1},
        )


def test_require_empty_refuses_a_database_with_tables():
    with pytest.raises(rd.RestoreError, match="already holds 8 table"):
        rd.require_empty("address_validator", run_as=None, runner=DbRunner({}, tables="8"))


# --- the command ---


def _main(client, *argv, runner=None):
    return rd.main(["--bucket", BUCKET, *argv], client_factory=lambda: client, runner=runner)


def test_latest_without_a_prefix_is_refused(client):
    with pytest.raises(SystemExit):
        _main(client, "--latest", "--into", "drill")


def test_a_fetch_must_say_where(client):
    with pytest.raises(SystemExit):
        _main(client, "--key", "k")


def test_into_a_non_empty_database_fetches_nothing(client, bucket, tmp_path):
    _seed(bucket, f"{HOST}/20261027T023001Z.dump")
    runner = DbRunner({}, tables="8")
    assert (
        _main(client, "--latest", "--prefix", HOST, "--into", "address_validator", runner=runner)
        == 1
    )
    assert len(runner.calls) == 1


def test_list_prints_names_and_metadata(client, bucket, capsys):
    _seed(bucket, f"{HOST}/20261027T023001Z.dump", source_host=HOST)
    _seed(bucket, f"{HOST}/20991231T000000Z.dump")
    assert _main(client, "--list") == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith(f"{HOST}/20261027T023001Z.dump dumped_at=- alembic_head=022")
    assert out[1].startswith(f"SUSPECT {HOST}/20991231T000000Z.dump")


def test_download_only(client, bucket, tmp_path, capsys):
    _seed(bucket, f"{HOST}/20261027T023001Z.dump")
    out = tmp_path / "dl"
    code = _main(
        client, "--latest", "--prefix", HOST, "--download-only", str(out), runner=FakeRunner()
    )
    assert code == 0
    assert (out / "20261027T023001Z.dump").read_bytes() == DATA
    assert "fetched and verified" in capsys.readouterr().out
