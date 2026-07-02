#!/usr/bin/env python3
"""Stamp pre-#145 validated_addresses rows with the current pipeline version.

Migration 019 adds ``pipeline_version`` as nullable; NULL rows mismatch every
lookup and lazily re-validate. Run this once at deploy so the existing cache —
built by the pipeline shipping in the same release — keeps serving hits instead
of turning into a fleet of lazy misses (a hit-rate cliff plus provider-quota
spike on hot inputs). Skip it only if you *want* the whole cache re-validated.

Loads the same custom model the service loads (CUSTOM_MODEL_PATH) so the stamped
composite version matches what the running service computes. Idempotent: only
fills rows where pipeline_version IS NULL.

Usage:
    uv run python scripts/db/backfill_pipeline_version.py           # dry-run (report only)
    uv run python scripts/db/backfill_pipeline_version.py --apply    # actually update rows

Env vars:
    VALIDATION_CACHE_DSN    PostgreSQL DSN (required)
    CUSTOM_MODEL_PATH       optional; must match the service's setting
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

from address_validator.core.pipeline_version import get_pipeline_version, load_custom_model
from address_validator.db.tables import validated_addresses

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_BATCH_SIZE = 10_000

_eligible = validated_addresses.c.pipeline_version.is_(None)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stamp NULL validated_addresses.pipeline_version rows with the "
        "current pipeline version.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually update rows (default is dry-run).",
    )
    return parser.parse_args()


async def _count_eligible(engine: AsyncEngine) -> int:
    stmt = select(func.count()).select_from(validated_addresses).where(_eligible)
    async with engine.connect() as conn:
        return (await conn.execute(stmt)).scalar() or 0


async def _max_id(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        return (await conn.execute(select(func.max(validated_addresses.c.id)))).scalar() or 0


async def _apply(engine: AsyncEngine, max_id: int, version: str) -> int:
    """Stamp eligible rows, batched by id range to bound lock duration."""
    updated = 0
    for lo in range(0, max_id + 1, _BATCH_SIZE):
        hi = lo + _BATCH_SIZE
        stmt = (
            sa.update(validated_addresses)
            .where(
                validated_addresses.c.id >= lo,
                validated_addresses.c.id < hi,
                _eligible,
            )
            .values(pipeline_version=version)
        )
        async with engine.begin() as conn:
            result = await conn.execute(stmt)
        updated += result.rowcount or 0
        logger.info("Batch id [%d, %d): %d total stamped...", lo, hi, updated)
    return updated


async def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    dsn = os.environ.get("VALIDATION_CACHE_DSN", "").strip()
    if not dsn:
        logger.error("VALIDATION_CACHE_DSN not set")
        sys.exit(1)

    # Mirror service startup so the model fingerprint (and thus the composite
    # version) matches what the running service will compute.
    load_custom_model()
    version = get_pipeline_version()
    logger.info("Current pipeline version: %s", version)

    engine = create_async_engine(dsn)
    try:
        eligible = await _count_eligible(engine)
        logger.info("validated_addresses rows with NULL pipeline_version: %d", eligible)
        if eligible == 0:
            logger.info("Nothing to do.")
            return

        if not args.apply:
            logger.info("DRY-RUN — re-run with --apply to stamp %d rows.", eligible)
            return

        max_id = await _max_id(engine)
        updated = await _apply(engine, max_id, version)
        logger.info("Stamped %d validated_addresses rows with %s.", updated, version)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
