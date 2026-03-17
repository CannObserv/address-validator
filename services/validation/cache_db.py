"""SQLite-backed validation cache — connection management and schema.

Environment variables
---------------------
VALIDATION_CACHE_DB
    Absolute path to the SQLite database file.
    Default: /var/lib/address-validator/validation_cache.db
    Set to ``:memory:`` in tests.
"""

import logging
import os

import aiosqlite

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = "/var/lib/address-validator/validation_cache.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS validated_addresses (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_key    TEXT    NOT NULL,
    provider         TEXT    NOT NULL,
    status           TEXT    NOT NULL,
    dpv_match_code   TEXT,
    address_line_1   TEXT,
    address_line_2   TEXT,
    city             TEXT,
    region           TEXT,
    postal_code      TEXT,
    country          TEXT    NOT NULL,
    validated        TEXT,
    components_json  TEXT,
    latitude         REAL,
    longitude        REAL,
    warnings_json    TEXT    NOT NULL DEFAULT '[]',
    created_at       TEXT    NOT NULL,
    last_seen_at     TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_validated_addresses_canonical_key
    ON validated_addresses (canonical_key);

CREATE TABLE IF NOT EXISTS query_patterns (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_key      TEXT    NOT NULL,
    canonical_key    TEXT    NOT NULL REFERENCES validated_addresses(canonical_key),
    created_at       TEXT    NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_query_patterns_pattern_key
    ON query_patterns (pattern_key);
"""

# Module-level singleton — shared across all requests.
_db: aiosqlite.Connection | None = None


async def get_db() -> aiosqlite.Connection:
    """Return the shared SQLite connection, creating it lazily on first call."""
    global _db  # noqa: PLW0603
    if _db is None:
        path = os.environ.get("VALIDATION_CACHE_DB", _DEFAULT_DB_PATH).strip()
        logger.debug("cache_db: opening connection path=%s", path)
        _db = await aiosqlite.connect(path)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA foreign_keys=ON")
        await _init_schema(_db)
    return _db


async def close_db() -> None:
    """Close the shared connection. Called from the FastAPI lifespan shutdown hook."""
    global _db  # noqa: PLW0603
    if _db is not None:
        await _db.close()
        _db = None
        logger.debug("cache_db: connection closed")


async def _init_schema(db: aiosqlite.Connection) -> None:
    await db.executescript(_SCHEMA)
    logger.debug("cache_db: schema initialised")
