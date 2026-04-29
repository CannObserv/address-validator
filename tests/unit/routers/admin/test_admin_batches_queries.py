"""Tests for admin batch query helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa

from address_validator.routers.admin.queries.batches import (
    get_assignable_batches,
    get_batch_by_slug,
    get_batch_candidates,
    list_batches,
)
from address_validator.services.training_batches import assign_candidates, create_batch

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


@pytest.fixture()
async def clean_db(db: AsyncEngine) -> AsyncEngine:
    async with db.begin() as conn:
        await conn.execute(
            sa.text(
                "TRUNCATE candidate_batch_assignments, training_batches,"
                " model_training_candidates RESTART IDENTITY CASCADE"
            )
        )
    return db


async def test_list_batches_returns_seeded_row(clean_db: AsyncEngine) -> None:
    await create_batch(clean_db, slug="q-a", description="d")
    rows = await list_batches(clean_db, status=None)
    slugs = {r["slug"] for r in rows}
    assert "q-a" in slugs


async def test_list_batches_filters_by_status(clean_db: AsyncEngine) -> None:
    await create_batch(clean_db, slug="q-plan", description="d")
    rows = await list_batches(clean_db, status="planned")
    assert all(r["status"] == "planned" for r in rows)
    assert any(r["slug"] == "q-plan" for r in rows)


async def test_get_batch_by_slug_unknown_returns_none(clean_db: AsyncEngine) -> None:
    assert await get_batch_by_slug(clean_db, slug="no-such-batch") is None


async def test_assignable_batches_only_planned_or_active(clean_db: AsyncEngine) -> None:
    await create_batch(clean_db, slug="q-p2", description="d")
    rows = await get_assignable_batches(clean_db)
    for r in rows:
        assert r["status"] in ("planned", "active")


async def test_get_batch_candidates_returns_assigned_rows(clean_db: AsyncEngine) -> None:
    batch_id = await create_batch(clean_db, slug="q-cand", description="d")
    async with clean_db.begin() as conn:
        await conn.execute(
            sa.text(
                "INSERT INTO model_training_candidates "
                "(raw_address, failure_type, parsed_tokens, status) "
                "VALUES ('999 QUERY ST', 'repeated_label_error', '[]'::jsonb, 'new')"
            )
        )
        h = (
            await conn.execute(
                sa.text(
                    "SELECT raw_address_hash FROM model_training_candidates "
                    "WHERE raw_address = '999 QUERY ST'"
                )
            )
        ).scalar_one()
    await assign_candidates(clean_db, batch_id=batch_id, raw_address_hashes=[h])

    rows = await get_batch_candidates(clean_db, batch_id=batch_id)
    assert len(rows) == 1
    assert rows[0]["raw_address"] == "999 QUERY ST"
