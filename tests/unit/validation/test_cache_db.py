"""Unit tests for services.validation.cache_db."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

import address_validator.services.validation.cache_db as cache_db_module
from address_validator.services.validation.cache_db import close_engine, get_engine
from tests.unit.validation.conftest import TEST_CACHE_DSN


@pytest.fixture(autouse=True)
async def reset_engine() -> None:
    """Close and reset the engine singleton between tests."""
    await close_engine()
    yield
    await close_engine()


class TestGetEngine:
    async def test_missing_dsn_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VALIDATION_CACHE_DSN", raising=False)
        with pytest.raises(RuntimeError, match="VALIDATION_CACHE_DSN"):
            await get_engine()

    async def test_returns_engine(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)
        engine = await get_engine()
        assert isinstance(engine, AsyncEngine)

    async def test_returns_singleton(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)
        engine1 = await get_engine()
        engine2 = await get_engine()
        assert engine1 is engine2

    async def test_close_engine_resets_singleton(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)
        engine1 = await get_engine()
        await close_engine()
        engine2 = await get_engine()
        assert engine1 is not engine2

    async def test_close_engine_when_none_is_noop(self) -> None:
        cache_db_module._engine = None
        await close_engine()  # must not raise


class TestSchema:
    async def test_tables_exist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)
        engine = await get_engine()

        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = ANY(:names)"
                ),
                {"names": ["validated_addresses", "query_patterns"]},
            )
            tables = {row[0] for row in result.fetchall()}

        assert "validated_addresses" in tables
        assert "query_patterns" in tables

    async def test_validated_at_column_exists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)
        engine = await get_engine()

        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'validated_addresses' AND column_name = 'validated_at'"
                )
            )
            assert result.fetchone() is not None

    async def test_qp_canonical_key_index_exists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)
        engine = await get_engine()

        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE tablename = 'query_patterns' AND indexname = 'idx_qp_canonical_key'"
                )
            )
            assert result.fetchone() is not None

    async def test_timestamp_columns_are_timestamptz(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Migration 003 converts all timestamp columns from TEXT to TIMESTAMPTZ."""
        monkeypatch.setenv("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)
        engine = await get_engine()

        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT column_name, data_type "
                    "FROM information_schema.columns "
                    "WHERE table_name = 'validated_addresses' "
                    "  AND column_name IN ('created_at', 'last_seen_at', 'validated_at') "
                    "ORDER BY column_name"
                )
            )
            rows = {r[0]: r[1] for r in result.fetchall()}

        assert rows["created_at"] == "timestamp with time zone"
        assert rows["last_seen_at"] == "timestamp with time zone"
        assert rows["validated_at"] == "timestamp with time zone"

        # Also check query_patterns.created_at
        async with engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name = 'query_patterns' AND column_name = 'created_at'"
                )
            )
            assert result.scalar() == "timestamp with time zone"

    async def test_foreign_key_enforced(self, db: AsyncEngine) -> None:
        """Inserting a query_pattern referencing a non-existent canonical_key must fail."""
        with pytest.raises(IntegrityError):
            async with db.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO query_patterns (pattern_key, canonical_key, created_at) "
                        "VALUES ('pat', 'no-such-canonical', :ts)"
                    ),
                    {"ts": datetime.now(UTC)},
                )
