"""Tests for the one-off raw-IP -> HMAC rewrite of archived Parquet files (GH #233)."""

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
import pytest
import rehash_archive_ips as rai
from archive_audit import ARCHIVE_SCHEMA, export_day
from audit_ip_hmac import KEY_ENV, KEY_ID_ENV, load_hasher
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

KEY_HEX = "7c" * 32
HASHER = load_hasher({KEY_ENV: KEY_HEX, KEY_ID_ENV: "2026-10"})
DAY = datetime(2026, 7, 1, tzinfo=UTC)
IPS = ["198.51.100.4", "198.51.100.4", "203.0.113.9", "127.0.0.1", "unknown"]


def _hmac(ip: str) -> str:
    return hmac.new(bytes.fromhex(KEY_HEX), ip.encode(), hashlib.sha256).hexdigest()


def _assert_no_raw_ip(path: Path, ips: set[str]) -> None:
    """No value in any column equals or contains a raw IP.

    Read back, not grepped: Snappy can split a literal, so a byte search of
    the file alone could miss one (#233).
    """
    table = pq.read_table(path)
    for name in table.column_names:
        for value in table.column(name).to_pylist():
            if isinstance(value, str):
                assert not any(ip in value for ip in ips), name
    raw = path.read_bytes()
    assert not any(ip.encode() in raw for ip in ips)


def _legacy_rows(ips: list[str] = IPS) -> list[dict]:
    return [
        {
            "id": i + 1,
            "timestamp": DAY + timedelta(hours=i),
            "request_id": f"req{i}",
            "client_ip": ip,
            "method": "POST",
            "endpoint": "/api/v2/validate" if i % 2 == 0 else "/contact",
            "status_code": 200,
            "latency_ms": 10 + i,
            "provider": None if i == 0 else "usps",
            "validation_status": None,
            "cache_hit": None if i == 0 else True,
            "error_detail": None,
            "parse_type": "usaddress",
        }
        for i, ip in enumerate(ips)
    ]


def _write_legacy(path: Path, ips: list[str] = IPS) -> None:
    """A pre-#233 archive file, written the way archive_audit.py wrote them."""
    table = pa.Table.from_pylist(_legacy_rows(ips), schema=rai.LEGACY_ARCHIVE_SCHEMA)
    with pq.ParquetWriter(path, rai.LEGACY_ARCHIVE_SCHEMA, compression="snappy") as w:
        w.write_table(table)


def _write_current(path: Path) -> None:
    rai.rehash_file(_legacy_path(path.parent), path, HASHER)


def _legacy_path(tmp: Path) -> Path:
    p = tmp / "legacy-src.parquet"
    if not p.exists():
        _write_legacy(p)
    return p


# --- the pure rewrite -------------------------------------------------------


def test_legacy_schema_is_the_pre_233_archive_schema() -> None:
    """Pinned: the 141 blobs uploaded before #233 match it exactly (measured 2026-10-24)."""
    names = list(ARCHIVE_SCHEMA.names)
    i = names.index("client_ip_hmac")
    expected = [*names[:i], "client_ip", *names[i + 2 :]]
    assert rai.LEGACY_ARCHIVE_SCHEMA.names == expected
    for field in rai.LEGACY_ARCHIVE_SCHEMA:
        if field.name != "client_ip":
            assert ARCHIVE_SCHEMA.field(field.name).type == field.type


def test_classify() -> None:
    assert rai.classify(rai.LEGACY_ARCHIVE_SCHEMA) == "legacy"
    assert rai.classify(ARCHIVE_SCHEMA) == "current"
    with pytest.raises(rai.UnknownSchemaError):
        rai.classify(pa.schema([("id", pa.int64())]))
    with pytest.raises(rai.UnknownSchemaError):  # right names, wrong type
        rai.classify(rai.LEGACY_ARCHIVE_SCHEMA.set(0, pa.field("id", pa.int32())))


def test_rehash_table_swaps_only_the_ip() -> None:
    legacy = pa.Table.from_pylist(_legacy_rows(), schema=rai.LEGACY_ARCHIVE_SCHEMA)

    out = rai.rehash_table(legacy, HASHER)

    assert out.schema.equals(ARCHIVE_SCHEMA)
    assert "client_ip" not in out.schema.names
    assert out.column("client_ip_hmac").to_pylist() == [_hmac(ip) for ip in IPS]
    assert out.column("client_ip_key_id").to_pylist() == ["2026-10"] * len(IPS)
    for name in rai.LEGACY_ARCHIVE_SCHEMA.names:
        if name != "client_ip":
            assert out.column(name).equals(legacy.column(name)), name


def test_rehash_file_verifies_and_holds_no_raw_ip(tmp_path: Path) -> None:
    src, dest = tmp_path / "src.parquet", tmp_path / "dest.parquet"
    _write_legacy(src)

    assert rai.rehash_file(src, dest, HASHER) == len(IPS)

    assert pq.read_schema(dest).equals(ARCHIVE_SCHEMA)
    _assert_no_raw_ip(dest, set(IPS))


def test_rehash_file_refuses_a_current_file(tmp_path: Path) -> None:
    dest = tmp_path / "current.parquet"
    _write_current(dest)
    with pytest.raises(rai.UnknownSchemaError):
        rai.rehash_file(dest, tmp_path / "again.parquet", HASHER)


# --- mixed generation (#321's contract, extended to #233's rewrite) ---------


def test_rehashed_file_keeps_the_parquet_contract(tmp_path: Path) -> None:
    """A rewritten file matches the nightly file contract (see test_export_day_parquet_contract)."""
    src, dest = tmp_path / "src.parquet", tmp_path / "dest.parquet"
    _write_legacy(src)
    rai.rehash_file(src, dest, HASHER)

    md = pq.read_metadata(dest)
    assert md.format_version == "2.6"
    assert b"ARROW:schema" in md.metadata
    ts = md.schema.column(ARCHIVE_SCHEMA.get_field_index("timestamp"))
    assert ts.physical_type == "INT64"
    logical = json.loads(ts.logical_type.to_json())
    assert logical["Type"] == "Timestamp"
    assert logical["isAdjustedToUTC"] is True
    assert logical["timeUnit"] == "microseconds"
    assert md.row_group(0).column(0).compression == "SNAPPY"


@pytest.mark.asyncio
async def test_rehashed_and_new_files_read_as_one_dataset(db: AsyncEngine, tmp_path: Path) -> None:
    """A rewritten old day and a freshly exported day read together, and one IP links across them.

    After the rewrite the bucket holds one schema; this pins that a rewritten
    file and a nightly file are the same generation (#321, #233).
    """
    old = tmp_path / "audit-2026-07-01.parquet"
    _write_legacy(tmp_path / "legacy.parquet")
    rai.rehash_file(tmp_path / "legacy.parquet", old, HASHER)

    day = (datetime.now(UTC) - timedelta(days=100)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    async with db.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO audit_log (timestamp, client_ip, method, endpoint, status_code)"
                " VALUES (:ts, '198.51.100.4', 'POST', '/api/v2/validate', 200)"
            ),
            {"ts": day + timedelta(hours=1)},
        )
    new = tmp_path / "audit-new.parquet"
    assert await export_day(db, day, day + timedelta(days=1), new, hasher=HASHER) == 1

    table = ds.dataset([str(old), str(new)], format="parquet").to_table()
    assert table.schema.equals(ARCHIVE_SCHEMA)
    assert table.num_rows == len(IPS) + 1
    hashes = table.column("client_ip_hmac").to_pylist()
    assert hashes.count(_hmac("198.51.100.4")) == 3  # two old rows + one new


# --- the bucket walk --------------------------------------------------------


class _FakeBlob:
    def __init__(self, bucket: "_FakeBucket", name: str) -> None:
        self._bucket = bucket
        self.name = name

    @property
    def generation(self) -> int | None:
        entry = self._bucket.objects.get(self.name)
        return entry[1] if entry else None

    def download_to_filename(self, path: str, *, if_generation_match: int) -> None:
        data, gen = self._bucket.objects[self.name]
        if gen != if_generation_match:
            raise RuntimeError("412 precondition failed")
        Path(path).write_bytes(data)

    def upload_from_filename(self, path: str, *, if_generation_match: int) -> None:
        _data, gen = self._bucket.objects[self.name]
        if gen != if_generation_match or self._bucket.fail_upload:
            raise RuntimeError("412 precondition failed")
        self._bucket.next_gen += 1
        self._bucket.objects[self.name] = (Path(path).read_bytes(), self._bucket.next_gen)
        self._bucket.uploads.append(self.name)


class _FakeBucket:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, int]] = {}
        self.next_gen = 100
        self.uploads: list[str] = []
        self.fail_upload = False

    def put(self, name: str, path: Path) -> None:
        self.next_gen += 1
        self.objects[name] = (path.read_bytes(), self.next_gen)

    def list_blobs(self, *, prefix: str) -> list[_FakeBlob]:
        return [_FakeBlob(self, n) for n in sorted(self.objects) if n.startswith(prefix)]

    def blob(self, name: str) -> _FakeBlob:
        return _FakeBlob(self, name)


def _bucket_with(tmp_path: Path, kinds: dict[str, str]) -> _FakeBucket:
    bucket = _FakeBucket()
    for name, kind in kinds.items():
        p = tmp_path / f"seed-{len(bucket.objects)}.parquet"
        if kind == "legacy":
            _write_legacy(p)
        elif kind == "current":
            _write_current(p)
        else:
            pq.write_table(pa.table({"id": [1]}), p)
        bucket.put(name, p)
    return bucket


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    bucket = _bucket_with(
        tmp_path,
        {"audit/a.parquet": "legacy", "audit/b.parquet": "current", "audit/c.parquet": "legacy"},
    )
    before = dict(bucket.objects)

    summary = rai.rehash_bucket(bucket, "audit/", HASHER, apply=False, workdir=tmp_path)

    assert (summary.legacy, summary.current, summary.rewritten) == (2, 1, 0)
    assert bucket.objects == before


def test_apply_rewrites_legacy_and_is_idempotent(tmp_path: Path) -> None:
    bucket = _bucket_with(
        tmp_path,
        {"audit/a.parquet": "legacy", "audit/b.parquet": "current", "other/x.parquet": "legacy"},
    )

    summary = rai.rehash_bucket(bucket, "audit/", HASHER, apply=True, workdir=tmp_path)

    assert (summary.legacy, summary.current, summary.rewritten) == (1, 1, 1)
    assert summary.rows == len(IPS)
    assert bucket.uploads == ["audit/a.parquet"]
    out = tmp_path / "check.parquet"
    out.write_bytes(bucket.objects["audit/a.parquet"][0])
    assert pq.read_schema(out).equals(ARCHIVE_SCHEMA)
    _assert_no_raw_ip(out, set(IPS))
    # outside the prefix: untouched
    assert pq.read_schema(_dump(bucket, "other/x.parquet", tmp_path)).equals(
        rai.LEGACY_ARCHIVE_SCHEMA
    )

    again = rai.rehash_bucket(bucket, "audit/", HASHER, apply=True, workdir=tmp_path)
    assert (again.legacy, again.current, again.rewritten) == (0, 2, 0)
    assert bucket.uploads == ["audit/a.parquet"]


def _dump(bucket: _FakeBucket, name: str, tmp_path: Path) -> Path:
    p = tmp_path / "dump.parquet"
    p.write_bytes(bucket.objects[name][0])
    return p


def test_unknown_schema_aborts_before_any_further_write(tmp_path: Path) -> None:
    bucket = _bucket_with(
        tmp_path,
        {"audit/a.parquet": "legacy", "audit/b.parquet": "odd", "audit/c.parquet": "legacy"},
    )

    with pytest.raises(rai.UnknownSchemaError):
        rai.rehash_bucket(bucket, "audit/", HASHER, apply=True, workdir=tmp_path)

    assert bucket.uploads == ["audit/a.parquet"]


def test_unknown_schema_aborts_a_dry_run_too(tmp_path: Path) -> None:
    bucket = _bucket_with(tmp_path, {"audit/b.parquet": "odd"})
    with pytest.raises(rai.UnknownSchemaError):
        rai.rehash_bucket(bucket, "audit/", HASHER, apply=False, workdir=tmp_path)


def test_non_parquet_blobs_are_skipped(tmp_path: Path) -> None:
    bucket = _bucket_with(tmp_path, {"audit/a.parquet": "legacy"})
    note = tmp_path / "note.txt"
    note.write_text("hello")
    bucket.put("audit/README.txt", note)

    summary = rai.rehash_bucket(bucket, "audit/", HASHER, apply=True, workdir=tmp_path)

    assert (summary.rewritten, summary.skipped) == (1, 1)
    assert bucket.objects["audit/README.txt"][0] == b"hello"


def test_a_changed_blob_is_not_overwritten(tmp_path: Path) -> None:
    """The upload is conditional on the generation that was read."""
    bucket = _bucket_with(tmp_path, {"audit/a.parquet": "legacy"})
    bucket.fail_upload = True
    with pytest.raises(RuntimeError, match="412"):
        rai.rehash_bucket(bucket, "audit/", HASHER, apply=True, workdir=tmp_path)
    assert bucket.uploads == []


def test_logs_names_and_counts_only(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """No raw IP, key or HMAC reaches the (persistent, #241) journal."""
    bucket = _bucket_with(tmp_path, {"audit/a.parquet": "legacy"})
    caplog.set_level(logging.DEBUG)

    rai.rehash_bucket(bucket, "audit/", HASHER, apply=True, workdir=tmp_path)

    assert "audit/a.parquet" in caplog.text
    for secret in (*IPS[:3], KEY_HEX, _hmac(IPS[0]), _hmac(IPS[2])):
        assert secret not in caplog.text


def test_main_refuses_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (KEY_ENV, KEY_ID_ENV):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUDIT_ARCHIVE_BUCKET", "bucket")
    monkeypatch.setattr(rai, "configure_logging", lambda: None)

    def _no_client(*_a, **_k):
        raise AssertionError("GCS client created without a key")

    monkeypatch.setattr(rai.gcs, "Client", _no_client)
    assert rai.main(["--apply"]) == 1


def test_main_refuses_without_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(KEY_ENV, KEY_HEX)
    monkeypatch.setenv(KEY_ID_ENV, "2026-10")
    monkeypatch.delenv("AUDIT_ARCHIVE_BUCKET", raising=False)
    monkeypatch.setattr(rai, "configure_logging", lambda: None)
    assert rai.main([]) == 1
