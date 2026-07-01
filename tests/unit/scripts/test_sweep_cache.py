"""Tests for the validation-cache TTL sweeper."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
import sweep_cache
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sweep_cache import count_expired, sweep_expired

from tests.conftest import TEST_CACHE_DSN


async def _insert_validated(
    engine: AsyncEngine,
    *,
    canonical_key: str,
    validated_at: datetime,
) -> None:
    """Insert one validated_addresses row with the given validated_at."""
    async with engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO validated_addresses
                    (canonical_key, provider, status, country, validated,
                     created_at, last_seen_at, validated_at)
                VALUES
                    (:ck, 'usps', 'confirmed', 'US', 'true',
                     :ts, :ts, :ts)
            """),
            {"ck": canonical_key, "ts": validated_at},
        )


async def _insert_pattern(
    engine: AsyncEngine,
    *,
    pattern_key: str,
    canonical_key: str,
) -> None:
    """Insert one query_patterns row pointing at canonical_key (NOT NULL since migration 018)."""
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO query_patterns (pattern_key, canonical_key, created_at)
                VALUES (:pk, :ck, :ts)
            """),
            {"pk": pattern_key, "ck": canonical_key, "ts": now},
        )


async def _count(engine: AsyncEngine, table: str) -> int:
    async with engine.connect() as conn:
        return (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()


@pytest.mark.asyncio
async def test_sweep_deletes_expired_and_keeps_fresh(db: AsyncEngine) -> None:
    """Expired rows (and their pointers) go; fresh rows stay."""
    old = datetime.now(UTC) - timedelta(days=100)
    fresh = datetime.now(UTC)
    await _insert_validated(db, canonical_key="old-ck", validated_at=old)
    await _insert_pattern(db, pattern_key="old-pk", canonical_key="old-ck")
    await _insert_validated(db, canonical_key="fresh-ck", validated_at=fresh)
    await _insert_pattern(db, pattern_key="fresh-pk", canonical_key="fresh-ck")

    cutoff = datetime.now(UTC) - timedelta(days=30)
    qp_deleted, va_deleted = await sweep_expired(db, cutoff)

    assert (qp_deleted, va_deleted) == (1, 1)
    assert await _count(db, "validated_addresses") == 1
    assert await _count(db, "query_patterns") == 1
    async with db.connect() as conn:
        remaining = (
            await conn.execute(text("SELECT canonical_key FROM validated_addresses"))
        ).scalar_one()
    assert remaining == "fresh-ck"


@pytest.mark.asyncio
async def test_sweep_is_idempotent(db: AsyncEngine) -> None:
    """Second sweep deletes nothing — safe to re-run."""
    old = datetime.now(UTC) - timedelta(days=100)
    await _insert_validated(db, canonical_key="old-ck", validated_at=old)
    await _insert_pattern(db, pattern_key="old-pk", canonical_key="old-ck")
    cutoff = datetime.now(UTC) - timedelta(days=30)

    first = await sweep_expired(db, cutoff)
    assert first == (1, 1)

    second = await sweep_expired(db, cutoff)
    assert second == (0, 0)


@pytest.mark.asyncio
async def test_sweep_batches_across_multiple_rounds(db: AsyncEngine) -> None:
    """batch_size smaller than the expired set still deletes everything."""
    old = datetime.now(UTC) - timedelta(days=100)
    for i in range(5):
        await _insert_validated(db, canonical_key=f"old-{i}", validated_at=old)
        await _insert_pattern(db, pattern_key=f"pk-{i}", canonical_key=f"old-{i}")
    cutoff = datetime.now(UTC) - timedelta(days=30)

    qp_deleted, va_deleted = await sweep_expired(db, cutoff, batch_size=2)

    assert (qp_deleted, va_deleted) == (5, 5)
    assert await _count(db, "validated_addresses") == 0
    assert await _count(db, "query_patterns") == 0


def test_get_config_defaults(monkeypatch) -> None:
    """TTL defaults to 30 when the env var is unset."""
    monkeypatch.setenv("VALIDATION_CACHE_DSN", "postgresql+asyncpg://x/y")
    monkeypatch.delenv("VALIDATION_CACHE_TTL_DAYS", raising=False)
    dsn, ttl_days = sweep_cache._get_config()
    assert dsn == "postgresql+asyncpg://x/y"
    assert ttl_days == 30


def test_get_config_reads_ttl(monkeypatch) -> None:
    """TTL is read from VALIDATION_CACHE_TTL_DAYS."""
    monkeypatch.setenv("VALIDATION_CACHE_DSN", "postgresql+asyncpg://x/y")
    monkeypatch.setenv("VALIDATION_CACHE_TTL_DAYS", "7")
    _, ttl_days = sweep_cache._get_config()
    assert ttl_days == 7


def test_get_config_exits_without_dsn(monkeypatch) -> None:
    """Missing DSN aborts with exit code 1."""
    monkeypatch.delenv("VALIDATION_CACHE_DSN", raising=False)
    with pytest.raises(SystemExit) as exc:
        sweep_cache._get_config()
    assert exc.value.code == 1


def test_get_config_exits_on_non_integer_ttl(monkeypatch) -> None:
    """A non-integer TTL aborts with exit code 1 instead of a raw traceback."""
    monkeypatch.setenv("VALIDATION_CACHE_DSN", "postgresql+asyncpg://x/y")
    monkeypatch.setenv("VALIDATION_CACHE_TTL_DAYS", "not-a-number")
    with pytest.raises(SystemExit) as exc:
        sweep_cache._get_config()
    assert exc.value.code == 1


@pytest.mark.asyncio
async def test_main_real_sweep_deletes_and_runs(db: AsyncEngine, monkeypatch) -> None:
    """main() (no --dry-run) deletes expired rows + pointers and VACUUMs."""
    monkeypatch.setattr("sys.argv", ["sweep_cache"])
    monkeypatch.setenv("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)
    monkeypatch.setenv("VALIDATION_CACHE_TTL_DAYS", "30")
    old = datetime.now(UTC) - timedelta(days=100)
    await _insert_validated(db, canonical_key="old-ck", validated_at=old)
    await _insert_pattern(db, pattern_key="old-pk", canonical_key="old-ck")

    await sweep_cache.main()

    assert await _count(db, "validated_addresses") == 0
    assert await _count(db, "query_patterns") == 0


@pytest.mark.asyncio
async def test_main_dry_run_does_not_delete(db: AsyncEngine, monkeypatch) -> None:
    """main() --dry-run reports but leaves rows in place."""
    monkeypatch.setattr("sys.argv", ["sweep_cache", "--dry-run"])
    monkeypatch.setenv("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)
    monkeypatch.setenv("VALIDATION_CACHE_TTL_DAYS", "30")
    old = datetime.now(UTC) - timedelta(days=100)
    await _insert_validated(db, canonical_key="old-ck", validated_at=old)

    await sweep_cache.main()

    assert await _count(db, "validated_addresses") == 1


@pytest.mark.asyncio
async def test_main_disabled_when_ttl_non_positive(db: AsyncEngine, monkeypatch) -> None:
    """main() with ttl<=0 short-circuits before deleting anything."""
    monkeypatch.setattr("sys.argv", ["sweep_cache"])
    monkeypatch.setenv("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)
    monkeypatch.setenv("VALIDATION_CACHE_TTL_DAYS", "0")
    old = datetime.now(UTC) - timedelta(days=100)
    await _insert_validated(db, canonical_key="old-ck", validated_at=old)

    await sweep_cache.main()

    assert await _count(db, "validated_addresses") == 1


@pytest.mark.asyncio
async def test_main_skips_vacuum_when_nothing_swept(db: AsyncEngine, monkeypatch, caplog) -> None:
    """main() logs the skip path and runs no VACUUM when no rows are expired."""
    monkeypatch.setattr("sys.argv", ["sweep_cache"])
    monkeypatch.setenv("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)
    monkeypatch.setenv("VALIDATION_CACHE_TTL_DAYS", "30")
    # db fixture truncated all tables — nothing is expired.

    with caplog.at_level(logging.INFO, logger="sweep_cache"):
        await sweep_cache.main()

    assert "Nothing swept — skipping VACUUM." in caplog.text


@pytest.mark.asyncio
async def test_count_expired_counts_only_expired_and_does_not_delete(
    db: AsyncEngine,
) -> None:
    """count_expired (dry-run path) reports expired rows without deleting them."""
    old = datetime.now(UTC) - timedelta(days=100)
    fresh = datetime.now(UTC)
    await _insert_validated(db, canonical_key="old-ck", validated_at=old)
    await _insert_validated(db, canonical_key="fresh-ck", validated_at=fresh)
    cutoff = datetime.now(UTC) - timedelta(days=30)

    expired = await count_expired(db, cutoff)

    assert expired == 1
    # Dry-run count must not delete anything.
    assert await _count(db, "validated_addresses") == 2
