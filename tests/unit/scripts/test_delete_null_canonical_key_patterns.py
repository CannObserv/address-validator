"""Tests for the one-off NULL-canonical_key query_patterns cleanup (#150)."""

from datetime import UTC, datetime

import pytest
from db.delete_null_canonical_key_patterns import (
    count_null,
    delete_null_by_keys,
    delete_null_patterns,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def _insert_validated(engine: AsyncEngine, *, canonical_key: str) -> None:
    """Insert one validated_addresses parent row (FK target for non-NULL patterns)."""
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO validated_addresses
                    (canonical_key, provider, status, country, validated,
                     created_at, last_seen_at, validated_at)
                VALUES
                    (:ck, 'usps', 'confirmed', 'US', 'true', :ts, :ts, :ts)
            """),
            {"ck": canonical_key, "ts": now},
        )


async def _insert_pattern(
    engine: AsyncEngine, *, pattern_key: str, canonical_key: str | None
) -> None:
    """Insert one query_patterns row (canonical_key may be NULL)."""
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO query_patterns (pattern_key, canonical_key, created_at)
                VALUES (:pk, :ck, :ts)
            """),
            {"pk": pattern_key, "ck": canonical_key, "ts": now},
        )


async def _count(engine: AsyncEngine, table: str) -> int:
    async with engine.connect() as conn:
        return (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()


@pytest.mark.asyncio
async def test_count_null_counts_only_null_rows(db: AsyncEngine) -> None:
    """count_null reports NULL-canonical_key rows, ignoring non-NULL ones."""
    await _insert_validated(db, canonical_key="ck-1")
    await _insert_pattern(db, pattern_key="pk-null-1", canonical_key=None)
    await _insert_pattern(db, pattern_key="pk-null-2", canonical_key=None)
    await _insert_pattern(db, pattern_key="pk-valid", canonical_key="ck-1")

    assert await count_null(db) == 2


@pytest.mark.asyncio
async def test_deletes_null_rows_and_keeps_valid(db: AsyncEngine) -> None:
    """NULL-canonical_key rows are deleted; rows pointing at a canonical parent stay."""
    await _insert_validated(db, canonical_key="ck-1")
    await _insert_pattern(db, pattern_key="pk-null", canonical_key=None)
    await _insert_pattern(db, pattern_key="pk-valid", canonical_key="ck-1")

    deleted = await delete_null_patterns(db)

    assert deleted == 1
    assert await count_null(db) == 0
    assert await _count(db, "query_patterns") == 1
    async with db.connect() as conn:
        remaining = (
            await conn.execute(text("SELECT pattern_key FROM query_patterns"))
        ).scalar_one()
    assert remaining == "pk-valid"
    # Parent validated_addresses row is untouched by the cleanup.
    assert await _count(db, "validated_addresses") == 1


@pytest.mark.asyncio
async def test_delete_is_idempotent(db: AsyncEngine) -> None:
    """A second run deletes nothing — safe to re-run."""
    await _insert_pattern(db, pattern_key="pk-null", canonical_key=None)

    assert await delete_null_patterns(db) == 1
    assert await delete_null_patterns(db) == 0


@pytest.mark.asyncio
async def test_delete_noop_when_no_null_rows(db: AsyncEngine) -> None:
    """No NULL rows → nothing deleted, valid rows preserved."""
    await _insert_validated(db, canonical_key="ck-1")
    await _insert_pattern(db, pattern_key="pk-valid", canonical_key="ck-1")

    assert await delete_null_patterns(db) == 0
    assert await _count(db, "query_patterns") == 1


@pytest.mark.asyncio
async def test_delete_by_keys_skips_row_promoted_after_selection(db: AsyncEngine) -> None:
    """Race guard: a key gathered while NULL but promoted before the DELETE is preserved.

    Reproduces the concurrency hazard deterministically: `keys` is captured while the
    row is NULL, then the row is back-filled (as a concurrent successful validation
    would via `_store`'s ON CONFLICT), then the delete runs. The DELETE's
    `canonical_key IS NULL` predicate must skip the now-valid row. Without that
    predicate this row would be dropped.
    """
    await _insert_pattern(db, pattern_key="pk-race", canonical_key=None)

    # Simulate the SELECT half of the batch loop — capture the key while still NULL.
    keys = ["pk-race"]

    # Concurrent successful validation promotes the legacy row to non-NULL.
    await _insert_validated(db, canonical_key="ck-promoted")
    async with db.begin() as conn:
        await conn.execute(
            text(
                "UPDATE query_patterns SET canonical_key = 'ck-promoted' "
                "WHERE pattern_key = 'pk-race'"
            )
        )

    deleted = await delete_null_by_keys(db, keys)

    assert deleted == 0, "promoted row must not be deleted by a stale key"
    assert await _count(db, "query_patterns") == 1
    async with db.connect() as conn:
        remaining = (
            await conn.execute(
                text("SELECT canonical_key FROM query_patterns WHERE pattern_key = 'pk-race'")
            )
        ).scalar_one()
    assert remaining == "ck-promoted"
