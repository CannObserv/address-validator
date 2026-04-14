"""Tests for admin candidate-triage SQL query helpers."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from address_validator.db.tables import model_training_candidates as mtc_tbl
from address_validator.routers.admin.queries.candidates import (
    get_candidate_group,
    get_candidate_groups,
    get_candidate_submissions,
    get_new_candidate_count,
    update_candidate_notes,
    update_candidate_status,
)
from address_validator.services.training_batches import assign_candidates, create_batch


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
        # Group B: one `new` + one `assigned` — rolls up to `mixed`
        {
            "raw": "addr B",
            "ft": "post_parse_recovery",
            "status": "new",
            "ts": now - timedelta(days=2),
        },
        {
            "raw": "addr B",
            "ft": "post_parse_recovery",
            "status": "assigned",
            "ts": now - timedelta(hours=1),
        },
        # Group C: one `assigned` row — rolls up to `assigned`
        {
            "raw": "addr C",
            "ft": "repeated_label_error",
            "status": "assigned",
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
        await conn.execute(
            text("TRUNCATE model_training_candidates, training_batches RESTART IDENTITY CASCADE")
        )
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
    assert by_raw["addr C"]["rollup_status"] == "assigned"
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


async def test_get_candidate_submissions_returns_rows(seeded_db: AsyncEngine) -> None:
    h = _hex("addr A")
    rows = await get_candidate_submissions(seeded_db, raw_hash=h)
    assert len(rows) == 2
    # Newest first
    assert rows[0]["created_at"] >= rows[1]["created_at"]
    assert all(r["status"] != "labeled" for r in rows)


async def test_update_candidate_status_applies_to_group(seeded_db: AsyncEngine) -> None:
    h = _hex("addr A")
    n = await update_candidate_status(seeded_db, raw_hash=h, status="rejected")
    assert n == 2
    rows = await get_candidate_submissions(seeded_db, raw_hash=h)
    assert all(r["status"] == "rejected" for r in rows)


async def test_update_candidate_status_skips_labeled(seeded_db: AsyncEngine) -> None:
    h = _hex("addr D")
    # Try to touch addr D (labeled only) — rowcount must be 0.
    n = await update_candidate_status(seeded_db, raw_hash=h, status="rejected")
    assert n == 0


async def test_update_candidate_status_rejects_labeled_as_input(seeded_db: AsyncEngine) -> None:
    h = _hex("addr A")
    with pytest.raises(ValueError, match="invalid status"):
        await update_candidate_status(seeded_db, raw_hash=h, status="labeled")


async def test_update_candidate_notes_round_trip(seeded_db: AsyncEngine) -> None:
    h = _hex("addr A")
    n = await update_candidate_notes(seeded_db, raw_hash=h, notes="chained unit: STE X, SMP Y")
    assert n == 2
    group = await get_candidate_group(seeded_db, raw_hash=h)
    assert group is not None
    assert group["notes"] == "chained unit: STE X, SMP Y"


async def test_update_candidate_notes_empty_string_stores_null(seeded_db: AsyncEngine) -> None:
    h = _hex("addr A")
    await update_candidate_notes(seeded_db, raw_hash=h, notes="first note")
    await update_candidate_notes(seeded_db, raw_hash=h, notes="")
    group = await get_candidate_group(seeded_db, raw_hash=h)
    assert group is not None
    assert group["notes"] is None


async def test_update_candidate_notes_whitespace_only_stores_null(seeded_db: AsyncEngine) -> None:
    h = _hex("addr A")
    await update_candidate_notes(seeded_db, raw_hash=h, notes="first note")
    await update_candidate_notes(seeded_db, raw_hash=h, notes="   \n\t  ")
    group = await get_candidate_group(seeded_db, raw_hash=h)
    assert group is not None
    assert group["notes"] is None


async def test_update_candidate_notes_strips_surrounding_whitespace(seeded_db: AsyncEngine) -> None:
    h = _hex("addr A")
    await update_candidate_notes(seeded_db, raw_hash=h, notes="  meaningful note  ")
    group = await get_candidate_group(seeded_db, raw_hash=h)
    assert group is not None
    assert group["notes"] == "meaningful note"


async def test_get_new_candidate_count_counts_new_and_mixed(seeded_db: AsyncEngine) -> None:
    # Seeded data: A=new, B=mixed, C=reviewed, D=labeled (excluded).
    # Badge counts new + mixed = 2.
    n = await get_new_candidate_count(seeded_db, since=None)
    assert n == 2


async def test_get_new_candidate_count_zero_when_nothing_actionable(seeded_db: AsyncEngine) -> None:
    # Mark A and B as rejected; only C remains, already assigned; expect 0.
    await update_candidate_status(seeded_db, raw_hash=_hex("addr A"), status="rejected")
    await update_candidate_status(seeded_db, raw_hash=_hex("addr B"), status="rejected")
    n = await get_new_candidate_count(seeded_db, since=None)
    assert n == 0


async def test_get_new_candidate_count_respects_since(seeded_db: AsyncEngine) -> None:
    # All seeded `new`/`mixed` rows are within the last 3 days.
    # A `since` of 1 hour ago should exclude older rows: addr A's newest
    # row is "now"; addr B's newest is "1 hour ago" — both qualify only if
    # the rollup window includes them. Use a tight 30s window: 0 groups match.
    cutoff = datetime.now(UTC) + timedelta(seconds=30)
    n = await get_new_candidate_count(seeded_db, since=cutoff)
    assert n == 0


async def test_rollup_assigned_when_linked_to_batch(seeded_db: AsyncEngine) -> None:
    # seed a new candidate
    async with seeded_db.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO model_training_candidates "
                "(raw_address, failure_type, parsed_tokens, status) "
                "VALUES ('ASSIGN ME', 'repeated_label_error', '[]'::jsonb, 'new')"
            )
        )
        h = (
            await conn.execute(
                sa.select(mtc_tbl.c.raw_address_hash).where(mtc_tbl.c.raw_address == "ASSIGN ME")
            )
        ).scalar_one()

    batch_id = await create_batch(seeded_db, slug="q-test", description="d")
    await assign_candidates(seeded_db, batch_id=batch_id, raw_address_hashes=[h])

    rows, _ = await get_candidate_groups(
        seeded_db,
        status="assigned",
        failure_type=None,
        since=None,
        until=None,
        limit=10,
        offset=0,
    )
    match = next((r for r in rows if r["raw_hash"] == h), None)
    assert match is not None
    assert "q-test" in (match.get("batch_slugs") or [])
    assert match["rollup_status"] == "assigned"
