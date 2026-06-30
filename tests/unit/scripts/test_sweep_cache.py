"""Tests for the validation-cache TTL sweeper."""

from datetime import UTC, datetime, timedelta

import pytest
import sweep_cache
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sweep_cache import sweep_expired


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
    canonical_key: str | None,
) -> None:
    """Insert one query_patterns row pointing at canonical_key (may be NULL)."""
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


@pytest.mark.asyncio
async def test_sweep_ignores_null_canonical_key_patterns(db: AsyncEngine) -> None:
    """Partial-registration rows (canonical_key NULL) are untouched."""
    await _insert_pattern(db, pattern_key="orphan-pk", canonical_key=None)
    cutoff = datetime.now(UTC) - timedelta(days=30)

    qp_deleted, va_deleted = await sweep_expired(db, cutoff)

    assert (qp_deleted, va_deleted) == (0, 0)
    assert await _count(db, "query_patterns") == 1


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
