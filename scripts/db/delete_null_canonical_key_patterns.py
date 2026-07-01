#!/usr/bin/env python3
"""Delete obsolete NULL-canonical_key query_patterns rows (#150).

Before #150 the validation path eagerly inserted a query_patterns row with
canonical_key=NULL before calling the inner provider, so rate-limited requests
would produce a joinable row carrying raw_input. That purpose is obsolete after
#147/#148 (raw_input is denormalized onto audit_log and read directly). Such
rows can never produce a cache hit (`_lookup` treats NULL canonical_key as a
miss) and the TTL sweeper never removes them (it deletes only rows whose
canonical_key points at an expired validated_addresses parent).

#150 stops creating them at the source; this one-off removes the ~1,393 legacy
rows in prod. Ship + run this *together with* the code change — deleting before
the code stops creating them would just let them re-accumulate.

Safe: nothing reads these rows. Batched over pattern_key to bound lock duration.
Idempotent: only touches canonical_key IS NULL rows, so re-running is a no-op
once drained.

Usage:
    uv run python scripts/db/delete_null_canonical_key_patterns.py           # dry-run (report only)
    uv run python scripts/db/delete_null_canonical_key_patterns.py --apply    # actually delete rows

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

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import create_async_engine

from address_validator.db.tables import query_patterns

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_BATCH_SIZE = 10_000


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Delete NULL-canonical_key query_patterns rows (#150).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete rows (default is dry-run).",
    )
    return parser.parse_args()


async def count_null(engine: AsyncEngine) -> int:
    """Count query_patterns rows with canonical_key IS NULL."""
    stmt = (
        select(func.count())
        .select_from(query_patterns)
        .where(query_patterns.c.canonical_key.is_(None))
    )
    async with engine.connect() as conn:
        return (await conn.execute(stmt)).scalar() or 0


async def delete_null_by_keys(engine: AsyncEngine, keys: list[str]) -> int:
    """Delete rows whose pattern_key ∈ *keys* AND canonical_key is still NULL.

    The ``canonical_key IS NULL`` predicate re-checks at delete time so that a row
    promoted to non-NULL after *keys* was gathered — a concurrent successful
    validation back-filling a legacy row via ``_store``'s ON CONFLICT — is not
    dropped. Returns the number of rows actually deleted.
    """
    if not keys:
        return 0
    async with engine.begin() as conn:
        result = await conn.execute(
            delete(query_patterns).where(
                query_patterns.c.pattern_key.in_(keys),
                query_patterns.c.canonical_key.is_(None),
            )
        )
    return result.rowcount or 0


async def delete_null_patterns(engine: AsyncEngine) -> int:
    """Delete NULL-canonical_key rows in batches, keyed by pattern_key.

    Idempotent: only NULL rows are ever touched (see ``delete_null_by_keys`` for
    the concurrency-safe delete predicate).
    """
    deleted = 0
    while True:
        async with engine.connect() as conn:
            keys = list(
                (
                    await conn.execute(
                        select(query_patterns.c.pattern_key)
                        .where(query_patterns.c.canonical_key.is_(None))
                        .limit(_BATCH_SIZE)
                    )
                ).scalars()
            )
        if not keys:
            break
        deleted += await delete_null_by_keys(engine, keys)
        logger.info("Deleted %d NULL-canonical_key rows so far...", deleted)
    return deleted


async def main() -> None:
    args = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    dsn = os.environ.get("VALIDATION_CACHE_DSN", "").strip()
    if not dsn:
        logger.error("VALIDATION_CACHE_DSN not set")
        sys.exit(1)

    engine = create_async_engine(dsn)
    try:
        null_rows = await count_null(engine)
        logger.info("NULL-canonical_key query_patterns rows: %d", null_rows)
        if null_rows == 0:
            logger.info("Nothing to do.")
            return

        if not args.apply:
            logger.info("DRY-RUN — re-run with --apply to delete %d rows.", null_rows)
            return

        deleted = await delete_null_patterns(engine)
        logger.info("Deleted %d query_patterns rows.", deleted)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
