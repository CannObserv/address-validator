"""Tests for admin candidate-triage SQL query helpers."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from address_validator.routers.admin.queries.candidates import (
    get_candidate_group,
    get_candidate_groups,
)


def _hex(s: str) -> str:
    """Compute sha256 hex of `s` the way the DB's generated column does."""
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


async def _seed(engine: AsyncEngine) -> None:
    """Seed three distinct raw_address groups with varying statuses."""
    now = datetime.now(UTC)
    rows = [
        # Group A: two `new` rows — rolls up to `new`
        {
            "raw": "addr A",
            "ft": "repeated_label_error",
            "status": "new",
            "ts": now - timedelta(days=1),
        },
        {"raw": "addr A", "ft": "repeated_label_error", "status": "new", "ts": now},
        # Group B: one `new` + one `reviewed` — rolls up to `mixed`
        {
            "raw": "addr B",
            "ft": "post_parse_recovery",
            "status": "new",
            "ts": now - timedelta(days=2),
        },
        {
            "raw": "addr B",
            "ft": "post_parse_recovery",
            "status": "reviewed",
            "ts": now - timedelta(hours=1),
        },
        # Group C: one `reviewed` row — rolls up to `reviewed`
        {
            "raw": "addr C",
            "ft": "repeated_label_error",
            "status": "reviewed",
            "ts": now - timedelta(days=3),
        },
        # Group D: one `labeled` — must be EXCLUDED from all triage queries
        {"raw": "addr D", "ft": "repeated_label_error", "status": "labeled", "ts": now},
    ]
    async with engine.begin() as conn:
        for r in rows:
            await conn.execute(
                text(
                    "INSERT INTO model_training_candidates "
                    "(raw_address, failure_type, parsed_tokens, status, created_at) "
                    "VALUES (:raw, :ft, '{}'::jsonb, :status, :ts)"
                ),
                r,
            )


@pytest.fixture()
async def seeded_db(db: AsyncEngine) -> AsyncEngine:
    """`db` fixture truncates everything; this extends by seeding candidate rows."""
    async with db.begin() as conn:
        await conn.execute(text("TRUNCATE model_training_candidates RESTART IDENTITY"))
    await _seed(db)
    return db


async def test_get_candidate_groups_rolls_up_status(seeded_db: AsyncEngine) -> None:
    groups, total = await get_candidate_groups(
        seeded_db,
        status=None,
        failure_type=None,
        since=None,
        until=None,
        limit=50,
        offset=0,
    )
    by_raw = {g["raw_address"]: g for g in groups}
    assert by_raw["addr A"]["rollup_status"] == "new"
    assert by_raw["addr A"]["count"] == 2
    assert by_raw["addr B"]["rollup_status"] == "mixed"
    assert by_raw["addr B"]["count"] == 2
    assert by_raw["addr C"]["rollup_status"] == "reviewed"
    assert by_raw["addr C"]["count"] == 1
    assert "addr D" not in by_raw
    assert total == 3


async def test_get_candidate_groups_filter_new_includes_mixed(seeded_db: AsyncEngine) -> None:
    groups, _ = await get_candidate_groups(
        seeded_db,
        status="new",
        failure_type=None,
        since=None,
        until=None,
        limit=50,
        offset=0,
    )
    raws = {g["raw_address"] for g in groups}
    assert raws == {"addr A", "addr B"}


async def test_get_candidate_groups_filter_failure_type(seeded_db: AsyncEngine) -> None:
    groups, _ = await get_candidate_groups(
        seeded_db,
        status=None,
        failure_type="post_parse_recovery",
        since=None,
        until=None,
        limit=50,
        offset=0,
    )
    raws = {g["raw_address"] for g in groups}
    assert raws == {"addr B"}


async def test_get_candidate_group_returns_summary(seeded_db: AsyncEngine) -> None:
    h = _hex("addr A")
    group = await get_candidate_group(seeded_db, raw_hash=h)
    assert group is not None
    assert group["raw_address"] == "addr A"
    assert group["rollup_status"] == "new"
    assert group["count"] == 2


async def test_get_candidate_group_returns_none_for_labeled_only(seeded_db: AsyncEngine) -> None:
    h = _hex("addr D")
    group = await get_candidate_group(seeded_db, raw_hash=h)
    assert group is None


async def test_get_candidate_group_returns_none_for_unknown_hash(seeded_db: AsyncEngine) -> None:
    group = await get_candidate_group(seeded_db, raw_hash="deadbeef" * 8)
    assert group is None
