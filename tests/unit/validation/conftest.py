"""Shared fixtures for validation unit tests.

Provides a PostgreSQL-backed async engine for cache tests.
Migrations are applied once per session; tables are truncated between tests.
"""

from __future__ import annotations

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import command

TEST_CACHE_DSN = (
    "postgresql+asyncpg://address_validator:address_validator_dev@localhost/address_validator_test"
)


@pytest.fixture(scope="session", autouse=True)
def run_cache_migrations() -> None:
    """Run Alembic migrations once for the test session (sync)."""
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", TEST_CACHE_DSN)
    command.upgrade(cfg, "head")


@pytest.fixture()
async def db(run_cache_migrations: None) -> AsyncEngine:
    """Function-scoped engine: truncates both cache tables before each test."""
    engine = create_async_engine(TEST_CACHE_DSN)
    async with engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE validated_addresses, query_patterns RESTART IDENTITY CASCADE")
        )
    yield engine
    await engine.dispose()
