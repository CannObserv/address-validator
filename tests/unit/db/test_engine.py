"""Unit tests for db.engine."""

import logging
import os
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

import address_validator.db.engine as engine_module
from address_validator.db.engine import (
    close_engine,
    get_engine,
    init_engine,
)
from tests.unit.conftest import TEST_CACHE_DSN


@pytest.fixture(autouse=True)
async def reset_engine() -> None:
    """Close and reset the engine singleton between tests."""
    await close_engine()
    yield
    await close_engine()


class TestInitEngine:
    async def test_missing_dsn_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VALIDATION_CACHE_DSN", raising=False)
        await init_engine()  # must not raise
        with pytest.raises(RuntimeError, match="init_engine"):
            get_engine()

    async def test_creates_engine(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)
        await init_engine()
        engine = get_engine()
        assert isinstance(engine, AsyncEngine)

    async def test_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)
        await init_engine()
        engine1 = get_engine()
        await init_engine()  # second call is a no-op
        engine2 = get_engine()
        assert engine1 is engine2

    async def test_close_engine_resets_singleton(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)
        await init_engine()
        engine1 = get_engine()
        await close_engine()
        await init_engine()
        engine2 = get_engine()
        assert engine1 is not engine2

    async def test_migration_failure_rolls_back_engine(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)
        with (
            patch(
                "address_validator.db.engine._run_migrations",
                side_effect=RuntimeError("migration boom"),
            ),
            pytest.raises(RuntimeError, match="migration boom"),
        ):
            await init_engine()
        # Engine must be None — not left in a half-initialised state
        assert engine_module._engine is None
        with pytest.raises(RuntimeError, match="init_engine"):
            get_engine()


class TestMigrationsPreserveLoggingConfig:
    """Regression for #124: alembic env.py must not clobber app logging.

    alembic.ini's [logger_root] sets level=WARNING. Before #124 the embedded
    migration run swapped the root logger out from under the app, silently
    dropping every INFO log from address_validator.* for the rest of the
    process.
    """

    async def test_app_info_records_survive_migrations(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Real invariant: an INFO record emitted by address_validator.* after
        migrations must propagate to a root-level handler.

        Mirrors the production pipeline: app sets root=INFO via basicConfig,
        INFO records propagate from address_validator.* to a root handler.
        Uses a private handler (rather than pytest's caplog) because
        ``caplog.at_level`` forces a level on the target logger, which would
        mask root-level clobbering — the exact failure mode #124 was about.
        """
        monkeypatch.setenv("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)
        root = logging.getLogger()
        original_level = root.level
        records: list[logging.LogRecord] = []

        class _CaptureHandler(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        handler = _CaptureHandler(level=logging.NOTSET)
        root.setLevel(logging.INFO)
        root.addHandler(handler)
        try:
            await init_engine()
            logging.getLogger("address_validator.test").info("post-migration probe")
            assert any(
                rec.name == "address_validator.test" and rec.getMessage() == "post-migration probe"
                for rec in records
            ), "INFO record from address_validator.* dropped after migrations"
        finally:
            if handler in root.handlers:
                root.removeHandler(handler)
            root.setLevel(original_level)

    async def test_skip_env_var_is_unset_after_migrations(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The guard var must not leak — standalone alembic invocations from
        the same process must still pretty-print via alembic.ini."""
        monkeypatch.setenv("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)
        monkeypatch.delenv("ALEMBIC_SKIP_LOGGING_CONFIG", raising=False)
        await init_engine()
        assert "ALEMBIC_SKIP_LOGGING_CONFIG" not in os.environ

    async def test_skip_env_var_cleared_after_migration_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The try/finally in _run_migrations must clean up the guard var even
        when command.upgrade() raises mid-flight.
        """
        monkeypatch.setenv("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)
        monkeypatch.delenv("ALEMBIC_SKIP_LOGGING_CONFIG", raising=False)
        with (
            patch(
                "address_validator.db.engine.command.upgrade",
                side_effect=RuntimeError("upgrade boom"),
            ),
            pytest.raises(RuntimeError, match="upgrade boom"),
        ):
            await init_engine()
        assert "ALEMBIC_SKIP_LOGGING_CONFIG" not in os.environ

    async def test_skip_env_var_prior_value_restored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If the var was set in the parent process (e.g. exported in the
        launching shell), _run_migrations must restore it rather than pop.
        """
        monkeypatch.setenv("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)
        monkeypatch.setenv("ALEMBIC_SKIP_LOGGING_CONFIG", "external-sentinel")
        await init_engine()
        assert os.environ.get("ALEMBIC_SKIP_LOGGING_CONFIG") == "external-sentinel"


class TestGetEngine:
    def test_raises_before_init(self) -> None:
        with pytest.raises(RuntimeError, match="init_engine"):
            get_engine()

    async def test_returns_singleton(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)
        await init_engine()
        engine1 = get_engine()
        engine2 = get_engine()
        assert engine1 is engine2


class TestCloseEngine:
    async def test_close_engine_when_none_is_noop(self) -> None:
        engine_module._engine = None
        await close_engine()  # must not raise


class TestSchema:
    async def test_tables_exist(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_CACHE_DSN", TEST_CACHE_DSN)
        await init_engine()
        engine = get_engine()

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
        await init_engine()
        engine = get_engine()

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
        await init_engine()
        engine = get_engine()

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
        await init_engine()
        engine = get_engine()

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
