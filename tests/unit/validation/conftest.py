"""Shared fixtures for validation unit tests.

Provides a PostgreSQL-backed async engine for cache tests.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import command
from tests.conftest import TEST_CACHE_DSN


@pytest.fixture(scope="session", autouse=True)
def run_cache_migrations() -> None:
    """Run Alembic migrations once for the test session (sync)."""
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", TEST_CACHE_DSN)
    command.upgrade(cfg, "head")


@pytest.fixture()
def mock_google_auth():
    """Patch get_credentials to return fake credentials."""
    creds = MagicMock()
    creds.token = "fake-token"
    creds.valid = True
    with patch(
        "address_validator.services.validation.gcp_auth.google.auth.default"
    ) as mock_default:
        mock_default.return_value = (creds, "fake-project")
        yield mock_default


@pytest.fixture()
async def db(run_cache_migrations: None) -> AsyncEngine:
    """Function-scoped engine: truncates all app tables before each test."""
    engine = create_async_engine(TEST_CACHE_DSN)
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE validated_addresses, query_patterns,"
                " audit_log, audit_daily_stats,"
                " candidate_batch_assignments, model_training_candidates,"
                " training_batches"
                " RESTART IDENTITY CASCADE"
            )
        )
    yield engine
    await engine.dispose()
