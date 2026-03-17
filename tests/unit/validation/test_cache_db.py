"""Unit tests for services.validation.cache_db."""

import aiosqlite
import pytest

import services.validation.cache_db as cache_db_module
from services.validation.cache_db import _init_schema, close_db, get_db


@pytest.fixture(autouse=True)
async def reset_db() -> None:
    """Close and reset the DB singleton between tests."""
    await close_db()
    yield
    await close_db()


class TestGetDb:
    async def test_returns_connection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_CACHE_DB", ":memory:")
        db = await get_db()
        assert db is not None

    async def test_returns_singleton(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_CACHE_DB", ":memory:")
        db1 = await get_db()
        db2 = await get_db()
        assert db1 is db2

    async def test_close_db_resets_singleton(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_CACHE_DB", ":memory:")
        db1 = await get_db()
        await close_db()
        db2 = await get_db()
        assert db1 is not db2

    async def test_close_db_when_none_is_noop(self) -> None:
        cache_db_module._db = None
        await close_db()  # Should not raise


class TestSchema:
    async def test_schema_creates_tables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_CACHE_DB", ":memory:")
        db = await get_db()

        # Both tables should exist
        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ) as cur:
            tables = {row[0] for row in await cur.fetchall()}

        assert "validated_addresses" in tables
        assert "query_patterns" in tables

    async def test_schema_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Running _init_schema twice should not raise."""
        monkeypatch.setenv("VALIDATION_CACHE_DB", ":memory:")
        db = await get_db()
        await _init_schema(db)  # Second call — should be a no-op

    async def test_indexes_exist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_CACHE_DB", ":memory:")
        db = await get_db()

        async with db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"
        ) as cur:
            indexes = {row[0] for row in await cur.fetchall()}

        assert "idx_validated_addresses_canonical_key" in indexes
        assert "idx_query_patterns_pattern_key" in indexes

    async def test_foreign_keys_enforced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Inserting a query_pattern referencing a non-existent canonical_key should fail."""
        monkeypatch.setenv("VALIDATION_CACHE_DB", ":memory:")
        db = await get_db()

        with pytest.raises(aiosqlite.IntegrityError):
            await db.execute(
                "INSERT INTO query_patterns (pattern_key, canonical_key, created_at) "
                "VALUES ('pat', 'no-such-canonical', '2026-01-01T00:00:00+00:00')"
            )
            await db.commit()
