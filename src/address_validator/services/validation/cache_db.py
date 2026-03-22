"""PostgreSQL-backed validation cache — engine management and schema migrations.

Environment variables
---------------------
VALIDATION_CACHE_DSN
    PostgreSQL connection string in SQLAlchemy async format.
    Example: postgresql+asyncpg://user:pass@localhost/address_validator
    When set, ``init_engine()`` creates the shared engine and runs Alembic
    migrations at startup.  When unset, init is a no-op (parse/standardize
    endpoints work without a database).
"""

import asyncio
import logging
import os
from urllib.parse import urlparse, urlunparse

from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import command

logger = logging.getLogger(__name__)

# Module-level singleton — shared across all requests.
_engine: AsyncEngine | None = None


async def init_engine() -> None:
    """Create the shared async engine and run Alembic migrations.

    Must be called exactly once during application startup (from the FastAPI
    lifespan hook) before any request handling begins.  Raises ``RuntimeError``
    if ``VALIDATION_CACHE_DSN`` is not set or the database is unreachable.
    """
    global _engine  # noqa: PLW0603
    if _engine is not None:
        return
    dsn = os.environ.get("VALIDATION_CACHE_DSN", "").strip()
    if not dsn:
        logger.debug("cache_db: VALIDATION_CACHE_DSN not set — skipping engine init")
        return
    logger.debug("cache_db: creating engine dsn=%s", _redact_dsn(dsn))
    _engine = create_async_engine(dsn, pool_size=5, max_overflow=10)
    try:
        await _run_migrations(dsn)
    except Exception:
        await _engine.dispose()
        _engine = None
        raise


def get_engine() -> AsyncEngine:
    """Return the shared async engine.

    Raises ``RuntimeError`` if :func:`init_engine` has not been called.
    """
    if _engine is None:
        raise RuntimeError("Engine not initialised — call init_engine() during startup")
    return _engine


async def close_engine() -> None:
    """Dispose the shared engine. Called from the FastAPI lifespan shutdown hook."""
    global _engine  # noqa: PLW0603
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        logger.debug("cache_db: engine disposed")


async def _run_migrations(dsn: str) -> None:
    """Run ``alembic upgrade head`` programmatically against *dsn*."""
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", dsn)

    logger.debug("cache_db: running alembic upgrade head")
    await asyncio.get_running_loop().run_in_executor(None, lambda: command.upgrade(cfg, "head"))
    logger.debug("cache_db: schema up to date")


def _redact_dsn(dsn: str) -> str:
    """Return the DSN with the password replaced by '***'."""
    try:
        parsed = urlparse(dsn)
        if parsed.password:
            netloc = parsed.hostname or ""
            if parsed.username:
                netloc = f"{parsed.username}:***@{netloc}"
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            redacted = parsed._replace(netloc=netloc)
            return urlunparse(redacted)
    except Exception:  # noqa: S110
        pass
    return dsn
