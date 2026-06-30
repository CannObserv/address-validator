#!/usr/bin/env python3
"""Find (and optionally clear) cached rows with a mangled duplicate secondary unit.

Before the parser learned to collapse an identical-duplicate secondary unit
(``"STE B, STE B"``), such inputs standardized to a line 2 with a repeated
designator — historically ``"STE STE B, B"`` (plain-concat era) and later
``"STE B STE B"`` (RLE-routing era).  Those values were cached in
``validated_addresses.address_line_2``.

Because ``pattern_key`` is derived from the *standardized* component values,
the corrected parser produces a new key, so a re-submission of the same raw
input is a cache miss that re-validates cleanly.  The stale rows are therefore
orphaned — never looked up again — but they linger until TTL and still surface
in admin/history views.  This script reports them and, with ``--apply``,
deletes each affected ``validated_addresses`` row together with the
``query_patterns`` rows that point at it (FK ``fk_query_patterns_canonical_key``),
so the next submission re-validates through the fixed pipeline.

Detection: ``address_line_2`` contains the same USPS unit designator token twice
(POSIX backreference), which catches both ``"STE STE …"`` and ``"STE B STE B"``.
A genuine two-suite address with two *distinct* designators (``"SMP 2 STE J"``)
has no repeated token and is not flagged.  A real repeated-designator address
(``"STE 100 STE 200"``) is rare; review the dry-run sample before ``--apply``.

Usage:
    uv run python scripts/db/dedupe_mangled_secondary_units.py            # report only
    uv run python scripts/db/dedupe_mangled_secondary_units.py --apply    # delete rows

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
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from address_validator.db.tables import query_patterns, validated_addresses
from address_validator.usps_data.units import UNIT_MAP

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# Canonical USPS designators, longest-first so the alternation prefers the
# longer token (e.g. BSMT before any future B*).  Word-boundary anchored.
_DESIGNATORS = sorted(set(UNIT_MAP.values()) - {"#"}, key=len, reverse=True)
# POSIX ARE: same designator token appearing twice in the line.
_DUP_DESIGNATOR_RE = r"\m(" + "|".join(_DESIGNATORS) + r")\M.*\m\1\M"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report/clear cached rows with a mangled duplicate secondary unit.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete affected rows (default is a read-only report).",
    )
    parser.add_argument(
        "--limit-samples",
        type=int,
        default=20,
        help="How many sample rows to print in the report (default 20).",
    )
    return parser.parse_args()


async def _fetch_affected(engine: AsyncEngine) -> list[dict]:
    """Return affected validated_addresses rows joined to their raw input."""
    stmt = (
        select(
            validated_addresses.c.canonical_key,
            validated_addresses.c.address_line_1,
            validated_addresses.c.address_line_2,
            validated_addresses.c.provider,
            validated_addresses.c.validated_at,
            query_patterns.c.raw_input,
        )
        .select_from(
            validated_addresses.outerjoin(
                query_patterns,
                query_patterns.c.canonical_key == validated_addresses.c.canonical_key,
            )
        )
        .where(
            validated_addresses.c.address_line_2.op("~*")(_DUP_DESIGNATOR_RE),
        )
        .order_by(validated_addresses.c.validated_at)
    )
    async with engine.connect() as conn:
        result = await conn.execute(stmt)
        return [dict(r._mapping) for r in result]  # noqa: SLF001


async def _delete_affected(engine: AsyncEngine, canonical_keys: list[str]) -> int:
    """Delete affected validated_addresses rows and their query_patterns pointers."""
    deleted = 0
    for key in canonical_keys:
        async with engine.begin() as conn:
            # Remove FK referrers first, then the canonical row.
            await conn.execute(
                sa.delete(query_patterns).where(query_patterns.c.canonical_key == key)
            )
            await conn.execute(
                sa.delete(validated_addresses).where(validated_addresses.c.canonical_key == key)
            )
            deleted += 1
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
        affected = await _fetch_affected(engine)
        total = len(affected)
        # Distinct canonical rows (a row may join to several query_patterns).
        keys = sorted({r["canonical_key"] for r in affected})

        mode = "APPLY" if args.apply else "DRY-RUN"
        logger.info("--- %s Summary ---", mode)
        logger.info("Affected canonical rows: %d", len(keys))
        logger.info("Join rows (with raw_input duplicates): %d", total)

        seen: set[str] = set()
        shown = 0
        for r in affected:
            if r["canonical_key"] in seen:
                continue
            seen.add(r["canonical_key"])
            if shown < args.limit_samples:
                logger.info(
                    "  line2=%r  raw=%r  provider=%s  at=%s",
                    r["address_line_2"],
                    r["raw_input"],
                    r["provider"],
                    r["validated_at"],
                )
                shown += 1

        if not keys:
            logger.info("Nothing to do.")
            return

        if not args.apply:
            logger.info("Re-run with --apply to delete %d canonical rows.", len(keys))
            return

        deleted = await _delete_affected(engine, keys)
        logger.info("Deleted %d canonical rows (+ their query_patterns pointers).", deleted)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
