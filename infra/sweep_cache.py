#!/usr/bin/env python3
"""Delete validation-cache rows older than the TTL window.

Deletes ``validated_addresses`` rows whose ``validated_at`` is older than
``VALIDATION_CACHE_TTL_DAYS``, plus their dangling ``query_patterns`` pointers.
TTL semantics match the lookup-time check in ``cache_provider._lookup``.

Usage:
    uv run python infra/sweep_cache.py             # sweep expired rows
    uv run python infra/sweep_cache.py --dry-run    # report counts, delete nothing

Env vars:
    VALIDATION_CACHE_DSN        PostgreSQL DSN (required)
    VALIDATION_CACHE_TTL_DAYS   TTL window in days (default: 30; 0 = never sweep)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from address_validator.db.tables import query_patterns, validated_addresses

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine
    from sqlalchemy.sql.elements import ColumnElement

logger = logging.getLogger(__name__)

DEFAULT_TTL_DAYS = 30
DEFAULT_BATCH_SIZE = 10_000


def _expiry_column() -> ColumnElement:
    """Expire on validated_at.

    ``validated_at`` is ``NOT NULL`` in the schema, so a plain column reference is
    behaviourally identical to ``cache_provider._lookup``'s defensive
    ``validated_at or created_at`` while remaining index-friendly (a COALESCE
    expression would foreclose use of ``idx_validated_addresses_validated_at``).
    """
    return validated_addresses.c.validated_at


async def sweep_expired(
    engine: AsyncEngine,
    cutoff: datetime,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[int, int]:
    """Delete expired validated_addresses rows + their query_patterns pointers.

    Each batch runs in a single transaction, deleting the child query_patterns
    rows before the parent validated_addresses rows so the FK
    (fk_query_patterns_canonical_key, ON DELETE NO ACTION) is never violated.

    Returns (query_patterns_deleted, validated_addresses_deleted).
    """
    total_qp = 0
    total_va = 0
    while True:
        async with engine.begin() as conn:
            keys = list(
                (
                    await conn.execute(
                        select(validated_addresses.c.canonical_key)
                        .where(_expiry_column() < cutoff)
                        .limit(batch_size)
                    )
                ).scalars()
            )
            if not keys:
                break

            qp_res = await conn.execute(
                delete(query_patterns).where(query_patterns.c.canonical_key.in_(keys))
            )
            va_res = await conn.execute(
                delete(validated_addresses).where(validated_addresses.c.canonical_key.in_(keys))
            )
            total_qp += qp_res.rowcount
            total_va += va_res.rowcount

        if len(keys) < batch_size:
            break
        logger.info("Swept %d cache rows so far...", total_va)

    return total_qp, total_va


def _get_config() -> tuple[str, int]:
    """Read and validate env vars. Returns (dsn, ttl_days). Exits 1 if DSN missing."""
    dsn = os.environ.get("VALIDATION_CACHE_DSN", "").strip()
    if not dsn:
        logger.error("VALIDATION_CACHE_DSN not set")
        sys.exit(1)
    raw_ttl = os.environ.get("VALIDATION_CACHE_TTL_DAYS", str(DEFAULT_TTL_DAYS))
    try:
        ttl_days = int(raw_ttl)
    except ValueError:
        logger.error("VALIDATION_CACHE_TTL_DAYS must be an integer, got %r", raw_ttl)
        sys.exit(1)
    return dsn, ttl_days


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep expired validation-cache rows.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many rows would be swept without deleting.",
    )
    return parser.parse_args()


async def count_expired(engine: AsyncEngine, cutoff: datetime) -> int:
    """Count validated_addresses rows that would be swept (for --dry-run)."""
    async with engine.connect() as conn:
        return (
            await conn.execute(
                select(func.count())
                .select_from(validated_addresses)
                .where(_expiry_column() < cutoff)
            )
        ).scalar_one()


async def vacuum_cache_tables(engine: AsyncEngine) -> None:
    """VACUUM ANALYZE the swept tables. Must run outside a transaction."""
    async with engine.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
        await conn.execute(text("VACUUM ANALYZE validated_addresses"))
        await conn.execute(text("VACUUM ANALYZE query_patterns"))


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    args = _parse_args()
    dsn, ttl_days = _get_config()

    if ttl_days <= 0:
        logger.info("VALIDATION_CACHE_TTL_DAYS=%d — sweeping disabled. Done.", ttl_days)
        return

    engine = create_async_engine(dsn)
    cutoff = datetime.now(UTC) - timedelta(days=ttl_days)

    try:
        if args.dry_run:
            expired = await count_expired(engine, cutoff)
            logger.info(
                "Dry run: %d validated_addresses rows older than %s would be swept.",
                expired,
                cutoff.date(),
            )
            return

        logger.info("Sweeping cache rows older than %s...", cutoff.date())
        qp_deleted, va_deleted = await sweep_expired(engine, cutoff)
        logger.info(
            "Swept %d validated_addresses rows and %d query_patterns pointers.",
            va_deleted,
            qp_deleted,
        )

        if va_deleted:
            await vacuum_cache_tables(engine)
            logger.info("VACUUM ANALYZE complete.")
        else:
            logger.info("Nothing swept — skipping VACUUM.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
