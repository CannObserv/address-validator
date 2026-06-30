#!/usr/bin/env python3
"""Delete validation-cache rows older than the TTL window.

Deletes ``validated_addresses`` rows whose ``COALESCE(validated_at, created_at)``
is older than ``VALIDATION_CACHE_TTL_DAYS``, plus their dangling
``query_patterns`` pointers. TTL semantics match the lookup-time check in
``cache_provider._lookup`` exactly.

Usage:
    uv run python infra/sweep_cache.py             # sweep expired rows
    uv run python infra/sweep_cache.py --dry-run    # report counts, delete nothing

Env vars:
    VALIDATION_CACHE_DSN        PostgreSQL DSN (required)
    VALIDATION_CACHE_TTL_DAYS   TTL window in days (default: 30; 0 = never sweep)
"""

from __future__ import annotations

import argparse  # noqa: F401  # consumed by main() in a later task
import asyncio  # noqa: F401  # consumed by main() in a later task
import logging
import os  # noqa: F401  # consumed by config helper in a later task
import sys  # noqa: F401  # consumed by config helper in a later task
from datetime import UTC, timedelta  # noqa: F401  # consumed by main() in a later task
from typing import TYPE_CHECKING

from sqlalchemy import (  # noqa: F401  # text used by VACUUM in a later task
    delete,
    func,
    select,
    text,
)
from sqlalchemy.ext.asyncio import (
    create_async_engine,  # noqa: F401  # consumed by main() in a later task
)

from address_validator.db.tables import query_patterns, validated_addresses

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncEngine
    from sqlalchemy.sql.elements import ColumnElement

logger = logging.getLogger(__name__)

DEFAULT_TTL_DAYS = 30
DEFAULT_BATCH_SIZE = 10_000


def _expiry_column() -> ColumnElement:
    """Match cache_provider._lookup: expire on validated_at, fall back to created_at."""
    return func.coalesce(
        validated_addresses.c.validated_at,
        validated_addresses.c.created_at,
    )


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
