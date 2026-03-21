"""Tests for audit log archive script."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

# Add scripts/ to path so we can import the archive module
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from archive_audit import (
    aggregate,
    delete_expired_rows,
    export_parquet,
    fetch_expired_rows,
    group_rows_by_date,
)


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
async def test_aggregate_backfill(db: AsyncEngine) -> None:
    """Backfill mode (no cutoff) aggregates ALL rows."""
    await _seed_old_and_new_rows(db)

    inserted = await aggregate(db)
    assert inserted > 3


@pytest.mark.asyncio
async def test_fetch_expired_rows(db: AsyncEngine) -> None:
    """fetch_expired_rows returns only rows older than cutoff."""
    await _seed_old_and_new_rows(db)
    cutoff = datetime.now(UTC) - timedelta(days=90)

    rows = await fetch_expired_rows(db, cutoff)
    assert len(rows) == 3
    assert all(r["timestamp"] < cutoff for r in rows)


def test_export_parquet_writes_file(tmp_path) -> None:
    """export_parquet creates a valid Parquet file with correct row count."""
    rows = [
        {
            "id": 1,
            "timestamp": datetime.now(UTC),
            "endpoint": "/api/v1/validate",
            "status_code": 200,
            "client_ip": "1.1.1.1",
        },
        {
            "id": 2,
            "timestamp": datetime.now(UTC),
            "endpoint": "/api/v1/parse",
            "status_code": 400,
            "client_ip": "2.2.2.2",
        },
    ]
    dest = tmp_path / "test.parquet"
    count = export_parquet(rows, dest)

    assert count == 2
    assert dest.exists()

    table = pq.read_table(dest)
    assert table.num_rows == 2
    assert "endpoint" in table.column_names
    assert "status_code" in table.column_names


def test_export_parquet_empty_returns_zero(tmp_path) -> None:
    """Empty row list writes nothing and returns 0."""
    dest = tmp_path / "empty.parquet"
    count = export_parquet([], dest)
    assert count == 0
    assert not dest.exists()


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
async def test_full_archive_cycle(db: AsyncEngine, tmp_path) -> None:
    """End-to-end: aggregate → export → delete preserves data integrity."""
    await _seed_old_and_new_rows(db)
    cutoff = datetime.now(UTC) - timedelta(days=90)

    # Step 1: Aggregate
    inserted = await aggregate(db, cutoff=cutoff)
    assert inserted > 0

    # Step 2: Fetch + Export
    rows = await fetch_expired_rows(db, cutoff)
    assert len(rows) == 3

    day_groups = group_rows_by_date(rows)
    total_exported = 0
    for day_key, day_rows in day_groups.items():
        dest = tmp_path / f"audit-{day_key}.parquet"
        total_exported += export_parquet(day_rows, dest)
        assert dest.exists()
    assert total_exported == 3

    # Step 3: Delete (skip GCS upload — tested separately)
    deleted = await delete_expired_rows(db, cutoff)
    assert deleted == 3

    # Verify: live table has only recent rows
    async with db.connect() as conn:
        live_count = (await conn.execute(text("SELECT COUNT(*) FROM audit_log"))).scalar()
        stats_count = (await conn.execute(text("SELECT COUNT(*) FROM audit_daily_stats"))).scalar()
    assert live_count == 2  # Only recent rows
    assert stats_count > 0  # Rollups exist
