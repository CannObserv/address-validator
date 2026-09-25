"""Tests for audit log archive script."""

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from archive_audit import (
    ARCHIVE_SCHEMA,
    _check_upload_config,
    _get_config,
    aggregate,
    compute_cutoff,
    delete_expired_rows,
    export_day,
    fetch_expired_dates,
    run_archive,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def _seed_old_and_new_rows(engine: AsyncEngine) -> None:
    """Insert audit rows: 3 old (100 days ago) and 2 recent (today)."""
    old = datetime.now(UTC) - timedelta(days=100)
    now = datetime.now(UTC)
    rows = [
        # Old rows — should be aggregated and archived
        {
            "ts": old,
            "ip": "1.1.1.1",
            "method": "POST",
            "ep": "/api/v1/validate",
            "status": 200,
            "provider": "usps",
            "vs": "confirmed",
            "cache": True,
            "latency": 42,
        },
        {
            "ts": old,
            "ip": "1.1.1.1",
            "method": "POST",
            "ep": "/api/v1/validate",
            "status": 200,
            "provider": "usps",
            "vs": "confirmed",
            "cache": False,
            "latency": 88,
        },
        {
            "ts": old,
            "ip": "2.2.2.2",
            "method": "POST",
            "ep": "/api/v1/parse",
            "status": 400,
            "provider": None,
            "vs": None,
            "cache": None,
            "latency": 5,
        },
        # Recent rows — should remain untouched
        {
            "ts": now,
            "ip": "3.3.3.3",
            "method": "POST",
            "ep": "/api/v1/validate",
            "status": 200,
            "provider": "usps",
            "vs": "confirmed",
            "cache": True,
            "latency": 30,
        },
        {
            "ts": now,
            "ip": "3.3.3.3",
            "method": "POST",
            "ep": "/api/v1/parse",
            "status": 200,
            "provider": None,
            "vs": None,
            "cache": None,
            "latency": 10,
        },
    ]
    async with engine.begin() as conn:
        for r in rows:
            await conn.execute(
                text("""
                    INSERT INTO audit_log (timestamp, client_ip, method, endpoint,
                        status_code, provider, validation_status, cache_hit, latency_ms)
                    VALUES (:ts, :ip, :method, :ep, :status, :provider, :vs, :cache, :latency)
                """),
                r,
            )


@pytest.mark.asyncio
async def test_aggregate_with_cutoff(db: AsyncEngine) -> None:
    """Aggregation rolls up only rows older than cutoff."""
    await _seed_old_and_new_rows(db)
    cutoff = datetime.now(UTC) - timedelta(days=90)

    inserted = await aggregate(db, cutoff=cutoff)
    assert inserted > 0

    async with db.connect() as conn:
        stats = (
            await conn.execute(text("SELECT * FROM audit_daily_stats ORDER BY date"))
        ).fetchall()

    # Old rows: 2 validate/200/usps (cache=True and cache=False), 1 parse/400/null
    assert len(stats) == 3

    validate_cached = [s for s in stats if s.cache_hit is True]
    assert len(validate_cached) == 1
    assert validate_cached[0].request_count == 1
    assert validate_cached[0].endpoint == "/api/v1/validate"

    parse_error = [s for s in stats if s.status_code == 400]
    assert len(parse_error) == 1
    assert parse_error[0].error_count == 1


@pytest.mark.asyncio
async def test_aggregate_idempotent(db: AsyncEngine) -> None:
    """Running aggregation twice inserts zero new rows the second time."""
    await _seed_old_and_new_rows(db)
    cutoff = datetime.now(UTC) - timedelta(days=90)

    first = await aggregate(db, cutoff=cutoff)
    assert first > 0

    second = await aggregate(db, cutoff=cutoff)
    assert second == 0


@pytest.mark.asyncio
async def test_aggregate_backfill_cutoff_excludes_today(db: AsyncEngine) -> None:
    """Backfill stops at today's UTC midnight — a partial day would never be topped up.

    ON CONFLICT DO NOTHING means a rollup written for an incomplete day keeps
    its partial counts forever once later runs re-aggregate that date (#228).
    """
    await _seed_old_and_new_rows(db)

    inserted = await aggregate(db, cutoff=compute_cutoff(datetime.now(UTC), 0))
    assert inserted == 3

    async with db.connect() as conn:
        today_rows = (
            await conn.execute(
                text("SELECT COUNT(*) FROM audit_daily_stats WHERE date = :d"),
                {"d": datetime.now(UTC).date()},
            )
        ).scalar()
    assert today_rows == 0


@pytest.mark.asyncio
async def test_fetch_expired_dates(db: AsyncEngine) -> None:
    """fetch_expired_dates returns only dates with rows older than cutoff."""
    await _seed_old_and_new_rows(db)
    cutoff = datetime.now(UTC) - timedelta(days=90)

    dates = await fetch_expired_dates(db, cutoff)
    assert len(dates) == 1  # All 3 old rows are on the same day


async def _seed_day(engine: AsyncEngine, stamps: list[datetime], **cols) -> None:
    """Insert one validate row per timestamp; ``cols`` overrides per-row columns by index."""
    async with engine.begin() as conn:
        for i, ts in enumerate(stamps):
            row = {
                "ts": ts,
                "provider": "usps",
                "cache": True,
                "parse_type": "usaddress",
                "pattern_key": f"pk{i}",
                "raw_input": f"{i} Main St",
            }
            row.update({k: v[i] for k, v in cols.items()})
            await conn.execute(
                text("""
                    INSERT INTO audit_log (timestamp, client_ip, method, endpoint,
                        status_code, provider, cache_hit, latency_ms,
                        parse_type, pattern_key, raw_input)
                    VALUES (:ts, '1.1.1.1', 'POST', '/api/v2/validate', 200,
                        :provider, :cache, 10, :parse_type, :pattern_key, :raw_input)
                """),
                row,
            )


def _utc_midnight_days_ago(days: int) -> datetime:
    return (datetime.now(UTC) - timedelta(days=days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def test_compute_cutoff_floors_to_utc_midnight() -> None:
    """The cutoff is a UTC day boundary, never the 03:00 timer time (#228)."""
    now = datetime(2026, 9, 25, 3, 0, 7, tzinfo=UTC)
    assert compute_cutoff(now, 90) == datetime(2026, 6, 27, tzinfo=UTC)
    assert compute_cutoff(now, 0) == datetime(2026, 9, 25, tzinfo=UTC)


def test_compute_cutoff_normalizes_to_utc() -> None:
    """A non-UTC ``now`` floors to the UTC day, not the local one."""
    # 2026-09-25 01:00 at +02:00 is 2026-09-24 23:00 UTC.
    now = datetime(2026, 9, 25, 1, 0, tzinfo=timezone(timedelta(hours=2)))
    assert compute_cutoff(now, 0) == datetime(2026, 9, 24, tzinfo=UTC)


@pytest.mark.asyncio
async def test_export_day_streams_fixed_schema(db: AsyncEngine, tmp_path: Path) -> None:
    """Batched export writes every row under ARCHIVE_SCHEMA.

    The first batch has all-NULL provider/cache_hit; a schema inferred per batch
    would type those columns ``null`` and reject the next batch.
    """
    day = _utc_midnight_days_ago(100)
    stamps = [day + timedelta(hours=h) for h in range(5)]
    await _seed_day(
        db,
        stamps,
        provider=[None, None, "usps", "google", "usps"],
        cache=[None, None, True, False, True],
    )

    dest = tmp_path / "day.parquet"
    written = await export_day(db, day, day + timedelta(days=1), dest, batch_size=2)

    assert written == 5
    table = pq.read_table(dest)
    assert table.num_rows == 5
    assert table.schema.equals(ARCHIVE_SCHEMA)
    assert table.column("provider").to_pylist() == [None, None, "usps", "google", "usps"]


def test_archive_schema_omits_raw_input() -> None:
    """raw_input (address PII) is never archived; it ends at audit retention (#147, #228)."""
    assert "raw_input" not in ARCHIVE_SCHEMA.names
    assert {"parse_type", "pattern_key"} <= set(ARCHIVE_SCHEMA.names)


@pytest.mark.asyncio
async def test_export_day_clamps_to_cutoff(db: AsyncEngine, tmp_path: Path) -> None:
    """Rows at or after the cutoff are never exported, even mid-day."""
    day = _utc_midnight_days_ago(100)
    await _seed_day(db, [day + timedelta(hours=1), day + timedelta(hours=5)])

    dest = tmp_path / "day.parquet"
    written = await export_day(db, day, day + timedelta(hours=3), dest)
    assert written == 1


@pytest.mark.asyncio
async def test_export_day_empty_writes_nothing(db: AsyncEngine, tmp_path: Path) -> None:
    """A day with no rows writes no file and returns 0."""
    day = _utc_midnight_days_ago(100)
    dest = tmp_path / "empty.parquet"
    assert await export_day(db, day, day + timedelta(days=1), dest) == 0
    assert not dest.exists()


class _FakeUploader:
    """Records row counts per blob, read before run_archive unlinks the file."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def __call__(self, local_path: Path, blob_name: str) -> None:
        self.calls.append((blob_name, pq.read_table(local_path).num_rows))


@pytest.mark.asyncio
async def test_consecutive_nights_keep_boundary_day_whole(db: AsyncEngine) -> None:
    """Two nightly runs archive a day exactly once, with every row (#228).

    With a cutoff at the 03:00 timer time, night 1 took 00:00-03:00 of day D;
    night 2's rollup for D hit ON CONFLICT DO NOTHING and its Parquet upload
    overwrote night 1's blob — both lost the early slice.
    """
    day = _utc_midnight_days_ago(100)
    stamps = [day + timedelta(hours=h, minutes=m) for h, m in ((1, 0), (2, 59), (10, 0), (23, 0))]
    await _seed_day(db, stamps)
    uploader = _FakeUploader()
    blob = f"audit/year={day:%Y}/month={day:%m}/audit-{day:%Y-%m-%d}.parquet"

    night1 = day + timedelta(days=90, hours=3)
    await run_archive(db, compute_cutoff(night1, 90), prefix="audit/", upload=uploader)
    assert uploader.calls == []

    night2 = night1 + timedelta(days=1)
    await run_archive(db, compute_cutoff(night2, 90), prefix="audit/", upload=uploader)
    assert uploader.calls == [(blob, 4)]

    async with db.connect() as conn:
        rolled = (
            await conn.execute(
                text("SELECT SUM(request_count) FROM audit_daily_stats WHERE date = :d"),
                {"d": day.date()},
            )
        ).scalar()
        live = (await conn.execute(text("SELECT COUNT(*) FROM audit_log"))).scalar()
    assert rolled == 4
    assert live == 0


@pytest.mark.asyncio
async def test_delete_expired_rows(db: AsyncEngine) -> None:
    """delete_expired_rows removes only rows older than cutoff."""
    await _seed_old_and_new_rows(db)
    cutoff = datetime.now(UTC) - timedelta(days=90)

    deleted = await delete_expired_rows(db, cutoff, batch_size=2)
    assert deleted == 3

    async with db.connect() as conn:
        remaining = (await conn.execute(text("SELECT COUNT(*) FROM audit_log"))).scalar()
    assert remaining == 2


@pytest.mark.asyncio
async def test_delete_no_expired_rows(db: AsyncEngine) -> None:
    """delete_expired_rows returns 0 when nothing to delete."""
    await _seed_old_and_new_rows(db)
    cutoff = datetime.now(UTC) - timedelta(days=200)

    deleted = await delete_expired_rows(db, cutoff)
    assert deleted == 0


@pytest.mark.asyncio
async def test_full_archive_cycle(db: AsyncEngine) -> None:
    """End-to-end: aggregate → export per-day → upload → delete preserves data integrity."""
    await _seed_old_and_new_rows(db)
    uploader = _FakeUploader()

    await run_archive(db, compute_cutoff(datetime.now(UTC), 90), prefix="audit/", upload=uploader)

    assert len(uploader.calls) == 1
    assert uploader.calls[0][1] == 3
    async with db.connect() as conn:
        live_count = (await conn.execute(text("SELECT COUNT(*) FROM audit_log"))).scalar()
        rolled = (
            await conn.execute(text("SELECT SUM(request_count) FROM audit_daily_stats"))
        ).scalar()
    assert live_count == 2  # Only recent rows
    assert rolled == 3


@pytest.mark.asyncio
async def test_run_archive_logs_actual_upload_count(
    db: AsyncEngine, caplog: pytest.LogCaptureFixture
) -> None:
    """The verified-count log line counts uploads, not expired dates."""
    await _seed_old_and_new_rows(db)
    caplog.set_level("INFO", logger="archive_audit")

    await run_archive(
        db, compute_cutoff(datetime.now(UTC), 90), prefix="audit/", upload=_FakeUploader()
    )

    assert "Uploaded and verified 1 Parquet file(s)." in caplog.messages


@pytest.mark.asyncio
async def test_run_archive_skip_upload_still_deletes(db: AsyncEngine) -> None:
    """upload=None (explicit --skip-upload) aggregates and deletes without uploading."""
    await _seed_old_and_new_rows(db)

    await run_archive(db, compute_cutoff(datetime.now(UTC), 90), prefix="audit/", upload=None)

    async with db.connect() as conn:
        live_count = (await conn.execute(text("SELECT COUNT(*) FROM audit_log"))).scalar()
    assert live_count == 2


def test_check_upload_config_exits_without_bucket() -> None:
    """No bucket and no --skip-upload aborts before anything is deleted (#228)."""
    with pytest.raises(SystemExit) as exc:
        _check_upload_config(None, skip_upload=False)
    assert exc.value.code == 1


def test_check_upload_config_allows_explicit_skip_or_bucket() -> None:
    """An explicit --skip-upload or a configured bucket passes."""
    _check_upload_config(None, skip_upload=True)
    _check_upload_config("bucket", skip_upload=False)


def test_get_config_exits_on_non_integer_retention(monkeypatch) -> None:
    """A non-integer AUDIT_RETENTION_DAYS aborts with exit code 1, not a traceback."""
    monkeypatch.setenv("VALIDATION_CACHE_DSN", "postgresql+asyncpg://x/y")
    monkeypatch.setenv("AUDIT_RETENTION_DAYS", "not-a-number")
    with pytest.raises(SystemExit) as exc:
        _get_config()
    assert exc.value.code == 1
