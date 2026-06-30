#!/usr/bin/env python3
"""Backfill audit_log.raw_input from query_patterns where still joinable (#147).

raw_input was denormalized onto audit_log so it survives the full audit retention
window independent of cache TTL / sweeps. Historical audit_log rows still carry it
only via query_patterns.pattern_key. This script copies query_patterns.raw_input
onto matching audit_log rows (join on pattern_key) for rows whose pattern has not
yet been swept by #144. Rows whose pattern was already deleted stay NULL.

Set-based UPDATE ... FROM, batched over audit_log.id ranges to bound lock duration
on the ~500k-row prod table. Idempotent: only fills rows where raw_input IS NULL.

Usage:
    uv run python scripts/db/backfill_audit_raw_input.py           # dry-run (report only)
    uv run python scripts/db/backfill_audit_raw_input.py --apply    # actually update rows

Env vars:
    VALIDATION_CACHE_DSN    PostgreSQL DSN (required)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from address_validator.db.tables import audit_log, query_patterns

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_BATCH_SIZE = 10_000


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill audit_log.raw_input from query_patterns.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually update rows (default is dry-run).",
    )
    return parser.parse_args()


# Audit rows eligible for backfill: a pattern_key set, raw_input still empty, and a
# matching query_patterns row that still carries raw_input.
_eligible = (
    audit_log.c.raw_input.is_(None)
    & audit_log.c.pattern_key.isnot(None)
    & select(query_patterns.c.id)
    .where(
        query_patterns.c.pattern_key == audit_log.c.pattern_key,
        query_patterns.c.raw_input.isnot(None),
    )
    .exists()
)


async def _count_eligible(engine: AsyncEngine) -> int:
    stmt = select(func.count()).select_from(audit_log).where(_eligible)
    async with engine.connect() as conn:
        return (await conn.execute(stmt)).scalar() or 0


async def _max_id(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        return (await conn.execute(select(func.max(audit_log.c.id)))).scalar() or 0


async def _apply(engine: AsyncEngine, max_id: int) -> int:
    """Copy query_patterns.raw_input onto eligible audit_log rows, batched by id."""
    updated = 0
    for lo in range(0, max_id + 1, _BATCH_SIZE):
        hi = lo + _BATCH_SIZE
        stmt = (
            sa.update(audit_log)
            .where(
                audit_log.c.id >= lo,
                audit_log.c.id < hi,
                _eligible,
            )
            .values(
                raw_input=select(query_patterns.c.raw_input)
                .where(
                    query_patterns.c.pattern_key == audit_log.c.pattern_key,
                    query_patterns.c.raw_input.isnot(None),
                )
                .scalar_subquery()
            )
        )
        async with engine.begin() as conn:
            result = await conn.execute(stmt)
        updated += result.rowcount or 0
        logger.info("Batch id [%d, %d): %d total updated...", lo, hi, updated)
    return updated


async def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    dsn = os.environ.get("VALIDATION_CACHE_DSN", "").strip()
    if not dsn:
        logger.error("VALIDATION_CACHE_DSN not set")
        sys.exit(1)

    engine = create_async_engine(dsn)
    try:
        eligible = await _count_eligible(engine)
        logger.info("Eligible audit_log rows (joinable, raw_input NULL): %d", eligible)
        if eligible == 0:
            logger.info("Nothing to do.")
            return

        if not args.apply:
            logger.info("DRY-RUN — re-run with --apply to backfill %d rows.", eligible)
            return

        max_id = await _max_id(engine)
        updated = await _apply(engine, max_id)
        logger.info("Updated %d audit_log rows.", updated)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
