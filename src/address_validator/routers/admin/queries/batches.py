"""Admin batch query helpers — list, detail, assignable, and assigned candidates."""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from address_validator.db.tables import (
    candidate_batch_assignments as cba,
)
from address_validator.db.tables import (
    model_training_candidates as mtc,
)
from address_validator.db.tables import (
    training_batches as tb,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


async def list_batches(
    engine: AsyncEngine,
    *,
    status: str | None,
) -> list[dict]:
    """Return all batches optionally filtered by status, newest-first."""
    assigned_count = (
        sa.select(
            cba.c.batch_id,
            sa.func.count().label("assigned_count"),
        )
        .group_by(cba.c.batch_id)
        .subquery()
    )
    stmt = (
        sa.select(
            tb.c.id,
            tb.c.slug,
            tb.c.description,
            tb.c.targeted_failure_pattern,
            tb.c.status,
            tb.c.current_step,
            tb.c.manifest_path,
            tb.c.upstream_pr,
            tb.c.created_at,
            tb.c.activated_at,
            tb.c.deployed_at,
            tb.c.closed_at,
            sa.func.coalesce(assigned_count.c.assigned_count, 0).label("assigned_count"),
        )
        .select_from(tb.outerjoin(assigned_count, tb.c.id == assigned_count.c.batch_id))
        .order_by(tb.c.created_at.desc())
    )
    if status:
        stmt = stmt.where(tb.c.status == status)
    async with engine.connect() as conn:
        return [dict(r._mapping) for r in (await conn.execute(stmt))]  # noqa: SLF001


async def get_batch_by_slug(engine: AsyncEngine, *, slug: str) -> dict | None:
    stmt = sa.select(tb).where(tb.c.slug == slug)
    async with engine.connect() as conn:
        row = (await conn.execute(stmt)).mappings().first()
    return dict(row) if row else None


async def get_assignable_batches(engine: AsyncEngine) -> list[dict]:
    """Return planned+active batches suitable for the 'Assign to batch' dropdown."""
    stmt = (
        sa.select(tb.c.id, tb.c.slug, tb.c.description, tb.c.status)
        .where(tb.c.status.in_(("planned", "active")))
        .order_by(tb.c.created_at.desc())
    )
    async with engine.connect() as conn:
        return [dict(r._mapping) for r in (await conn.execute(stmt))]  # noqa: SLF001


async def get_batch_candidates(engine: AsyncEngine, *, batch_id: str) -> list[dict]:
    """Return candidate groups assigned to this batch."""
    stmt = (
        sa.select(
            mtc.c.raw_address.label("raw_address"),
            mtc.c.raw_address_hash.label("raw_hash"),
            sa.func.count().label("submission_count"),
            sa.func.max(mtc.c.created_at).label("last_seen"),
            sa.func.max(mtc.c.status).label("sample_status"),
            sa.func.max(cba.c.assigned_at).label("assigned_at"),
            sa.func.max(cba.c.assigned_by).label("assigned_by"),
        )
        .select_from(cba.join(mtc, mtc.c.raw_address_hash == cba.c.raw_address_hash))
        .where(cba.c.batch_id == batch_id)
        .group_by(mtc.c.raw_address, mtc.c.raw_address_hash)
        .order_by(sa.func.max(cba.c.assigned_at).desc())
    )
    async with engine.connect() as conn:
        return [dict(r._mapping) for r in (await conn.execute(stmt))]  # noqa: SLF001
