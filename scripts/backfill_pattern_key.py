#!/usr/bin/env python3
"""Backfill NULL pattern_key values on audit_log rows for /api/v1/validate.

A bug caused some audit_log rows to be written with NULL pattern_key even
though the validation cache store succeeded and query_patterns has the data.
This script matches affected audit rows to query_patterns rows by correlating
timestamps: a query_patterns.created_at should fall within the audit row's
request window [audit.timestamp - latency_ms, audit.timestamp].

Only rows with exactly one match are updated (ambiguous matches are skipped).

Usage:
    uv run python scripts/backfill_pattern_key.py          # dry-run (report only)
    uv run python scripts/backfill_pattern_key.py --apply   # actually update rows

Env vars:
    VALIDATION_CACHE_DSN    PostgreSQL DSN (required)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import timedelta
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from address_validator.db.tables import audit_log, query_patterns

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill NULL pattern_key on audit_log validate rows.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually update rows (default is dry-run).",
    )
    return parser.parse_args()


async def _fetch_affected_rows(engine: AsyncEngine) -> list[dict]:
    """Find audit_log rows eligible for pattern_key backfill."""
    stmt = (
        select(
            audit_log.c.id,
            audit_log.c.timestamp,
            audit_log.c.latency_ms,
        )
        .where(
            audit_log.c.endpoint == "/api/v1/validate",
            audit_log.c.pattern_key.is_(None),
            audit_log.c.cache_hit.is_(False),
            audit_log.c.provider.isnot(None),
        )
        .order_by(audit_log.c.id)
    )
    async with engine.connect() as conn:
        result = await conn.execute(stmt)
        return [dict(r._mapping) for r in result]  # noqa: SLF001


async def _find_match(conn: AsyncConnection, row: dict) -> str | None:
    """Find a unique query_patterns match for an audit row.

    Returns the pattern_key if exactly one match, "AMBIGUOUS" if multiple,
    or None if no match.
    """
    ts = row["timestamp"]
    latency_ms = row["latency_ms"]

    if latency_ms is not None and latency_ms > 0:
        window_start = ts - timedelta(milliseconds=latency_ms)
    else:
        # Fallback: 2-second window if no latency data
        window_start = ts - timedelta(seconds=2)

    # DISTINCT guards against duplicate created_at from upserts.
    # Multiple distinct pattern_keys in the window → ambiguous → skipped.
    stmt = (
        select(query_patterns.c.pattern_key)
        .where(
            query_patterns.c.created_at >= window_start,
            query_patterns.c.created_at <= ts,
        )
        .distinct()
    )
    result = await conn.execute(stmt)
    matches = [r.pattern_key for r in result]

    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return "AMBIGUOUS"
    return None


async def _match_rows(
    engine: AsyncEngine,
    affected_rows: list[dict],
) -> tuple[list[tuple[int, str]], int, int]:
    """Match affected audit rows to query_patterns.

    Returns (updates, ambiguous_count, unmatched_count).
    """
    updates: list[tuple[int, str]] = []
    ambiguous = 0
    unmatched = 0

    async with engine.connect() as conn:
        for row in affected_rows:
            match = await _find_match(conn, row)
            if match is None:
                unmatched += 1
            elif match == "AMBIGUOUS":
                ambiguous += 1
            else:
                updates.append((row["id"], match))

    return updates, ambiguous, unmatched


_BATCH_SIZE = 100


async def _apply_updates(engine: AsyncEngine, updates: list[tuple[int, str]]) -> int:
    """Write pattern_key updates to audit_log. Commits every _BATCH_SIZE rows."""
    updated = 0
    for batch_start in range(0, len(updates), _BATCH_SIZE):
        batch = updates[batch_start : batch_start + _BATCH_SIZE]
        async with engine.begin() as conn:
            for audit_id, pattern_key in batch:
                stmt = (
                    sa.update(audit_log)
                    .where(audit_log.c.id == audit_id)
                    .values(pattern_key=pattern_key)
                )
                await conn.execute(stmt)
                updated += 1
        logger.info("Committed %d/%d...", updated, len(updates))
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
        affected_rows = await _fetch_affected_rows(engine)
        total = len(affected_rows)
        logger.info("Found %d audit_log rows with NULL pattern_key.", total)
        if total == 0:
            logger.info("Nothing to do.")
            return

        updates, ambiguous, unmatched = await _match_rows(engine, affected_rows)
        matched = len(updates)

        mode = "APPLY" if args.apply else "DRY-RUN"
        logger.info("--- %s Summary ---", mode)
        logger.info("Total NULL rows:  %d", total)
        logger.info("Matched (1:1):    %d", matched)
        logger.info("Ambiguous (>1):   %d", ambiguous)
        logger.info("Unmatched (0):    %d", unmatched)

        if not args.apply:
            logger.info("Re-run with --apply to update %d rows.", matched)
            return

        if not updates:
            logger.info("No rows to update.")
            return

        updated = await _apply_updates(engine, updates)
        logger.info("Updated %d audit_log rows.", updated)

    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
