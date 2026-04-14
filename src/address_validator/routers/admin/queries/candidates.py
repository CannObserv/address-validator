"""Admin candidate-triage query helpers.

Grouping convention: a "group" is a set of model_training_candidates rows
sharing the same raw_address. Rows with status='labeled' are excluded from
the triage view entirely — once a submission has been included in training
data, it is considered done.
"""

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
    from datetime import datetime

    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncEngine


_NON_LABELED = mtc.c.status != "labeled"

# Statuses an admin may set via the triage UI. `labeled` is reserved for the
# training pipeline; `mixed` and `assigned` are derived rollups, never stored
# values. `assigned` is derived at read time from candidate_batch_assignments.
WRITE_STATUSES: frozenset[str] = frozenset({"new", "rejected"})

# Default time window for candidate-triage views. Drives both the list view's
# `since=30d` querystring default AND the nav-badge lookback so the count
# matches what the user lands on. Change in one place.
DEFAULT_LOOKBACK_DAYS = 30


def _rollup_status_expr(has_assignment: ColumnElement) -> ColumnElement:
    """Compute rollup status from aggregated row statuses + assignment presence.

    Precedence (top wins):
      1. all non-labeled rows rejected -> 'rejected' (admin intent overrides)
      2. any assignment exists -> 'assigned' (derived from join)
      3. all non-labeled rows new -> 'new'
      4. otherwise -> 'mixed'

    Caller MUST apply the `_NON_LABELED` predicate in the WHERE clause —
    this function trusts that `labeled` rows have already been filtered out.
    """
    all_rejected = sa.func.bool_and(mtc.c.status == sa.literal("rejected"))
    all_new = sa.func.bool_and(mtc.c.status == sa.literal("new"))
    return sa.case(
        (all_rejected, sa.literal("rejected")),
        (has_assignment.isnot(None), sa.literal("assigned")),
        (all_new, sa.literal("new")),
        else_=sa.literal("mixed"),
    ).label("rollup_status")


def _status_filter(rollup_col: ColumnElement, status: str | None) -> ColumnElement | None:
    """Translate UI status filter to a HAVING-clause expression on rollup."""
    if status is None or status == "all":
        return None
    if status == "new":
        return rollup_col.in_(("new", "mixed"))
    return rollup_col == status


async def get_candidate_groups(
    engine: AsyncEngine,
    *,
    status: str | None,
    failure_type: str | None,
    since: datetime | None,
    until: datetime | None,
    limit: int,
    offset: int,
) -> tuple[list[dict], int]:
    """Return grouped candidate rows with rollup status + total group count."""
    where: list[ColumnElement] = [_NON_LABELED]
    if failure_type:
        where.append(mtc.c.failure_type == failure_type)
    if since is not None:
        where.append(mtc.c.created_at >= since)
    if until is not None:
        where.append(mtc.c.created_at <= until)

    last_seen = sa.func.max(mtc.c.created_at).label("last_seen")

    batch_slugs_sub = (
        sa.select(
            cba.c.raw_address_hash,
            sa.func.array_agg(sa.distinct(tb.c.slug)).label("batch_slugs"),
        )
        .select_from(cba.join(tb, cba.c.batch_id == tb.c.id))
        .group_by(cba.c.raw_address_hash)
        .subquery()
    )

    batch_slugs_agg = sa.func.max(batch_slugs_sub.c.batch_slugs)
    rollup = _rollup_status_expr(batch_slugs_agg)

    group_stmt = (
        sa.select(
            mtc.c.raw_address.label("raw_address"),
            mtc.c.raw_address_hash.label("raw_hash"),
            rollup,
            sa.func.array_agg(sa.distinct(mtc.c.failure_type)).label("failure_types"),
            sa.func.count().label("count"),
            sa.func.min(mtc.c.created_at).label("first_seen"),
            last_seen,
            sa.func.max(mtc.c.notes).label("notes"),
            batch_slugs_agg.label("batch_slugs"),
        )
        .select_from(
            mtc.outerjoin(
                batch_slugs_sub,
                mtc.c.raw_address_hash == batch_slugs_sub.c.raw_address_hash,
            )
        )
        .where(*where)
        .group_by(mtc.c.raw_address, mtc.c.raw_address_hash)
    )
    status_filter = _status_filter(rollup, status)
    if status_filter is not None:
        group_stmt = group_stmt.having(status_filter)

    count_stmt = sa.select(sa.func.count()).select_from(group_stmt.subquery())
    list_stmt = group_stmt.order_by(last_seen.desc()).limit(limit).offset(offset)

    async with engine.connect() as conn:
        total = (await conn.execute(count_stmt)).scalar() or 0
        rows = [dict(r._mapping) for r in (await conn.execute(list_stmt))]  # noqa: SLF001
    return rows, total


async def get_new_candidate_count(engine: AsyncEngine, *, since: datetime | None) -> int:
    """Count candidate groups with rollup status in ('new', 'mixed') for the badge."""
    where: list[ColumnElement] = [_NON_LABELED]
    if since is not None:
        where.append(mtc.c.created_at >= since)

    batch_slugs_sub = (
        sa.select(
            cba.c.raw_address_hash,
            sa.func.array_agg(sa.distinct(tb.c.slug)).label("batch_slugs"),
        )
        .select_from(cba.join(tb, cba.c.batch_id == tb.c.id))
        .group_by(cba.c.raw_address_hash)
        .subquery()
    )

    rollup = _rollup_status_expr(sa.func.max(batch_slugs_sub.c.batch_slugs))
    group_stmt = (
        sa.select(mtc.c.raw_address_hash)
        .select_from(
            mtc.outerjoin(
                batch_slugs_sub,
                mtc.c.raw_address_hash == batch_slugs_sub.c.raw_address_hash,
            )
        )
        .where(*where)
        .group_by(mtc.c.raw_address, mtc.c.raw_address_hash)
        .having(rollup.in_(("new", "mixed")))
    )
    count_stmt = sa.select(sa.func.count()).select_from(group_stmt.subquery())
    async with engine.connect() as conn:
        return (await conn.execute(count_stmt)).scalar() or 0


async def get_candidate_group(engine: AsyncEngine, *, raw_hash: str) -> dict | None:
    """Return the summary for a single group identified by raw_hash, or None."""
    batch_slugs_sub = (
        sa.select(
            cba.c.raw_address_hash,
            sa.func.array_agg(sa.distinct(tb.c.slug)).label("batch_slugs"),
        )
        .select_from(cba.join(tb, cba.c.batch_id == tb.c.id))
        .where(cba.c.raw_address_hash == raw_hash)
        .group_by(cba.c.raw_address_hash)
        .subquery()
    )
    batch_slugs_agg = sa.func.max(batch_slugs_sub.c.batch_slugs)
    rollup = _rollup_status_expr(batch_slugs_agg)
    stmt = (
        sa.select(
            mtc.c.raw_address.label("raw_address"),
            mtc.c.raw_address_hash.label("raw_hash"),
            rollup,
            sa.func.array_agg(sa.distinct(mtc.c.failure_type)).label("failure_types"),
            sa.func.count().label("count"),
            sa.func.min(mtc.c.created_at).label("first_seen"),
            sa.func.max(mtc.c.created_at).label("last_seen"),
            sa.func.max(mtc.c.notes).label("notes"),
            batch_slugs_agg.label("batch_slugs"),
        )
        .select_from(
            mtc.outerjoin(
                batch_slugs_sub,
                mtc.c.raw_address_hash == batch_slugs_sub.c.raw_address_hash,
            )
        )
        .where(_NON_LABELED, mtc.c.raw_address_hash == raw_hash)
        .group_by(mtc.c.raw_address, mtc.c.raw_address_hash)
    )
    async with engine.connect() as conn:
        row = (await conn.execute(stmt)).mappings().first()
    return dict(row) if row else None


async def get_candidate_submissions(engine: AsyncEngine, *, raw_hash: str) -> list[dict]:
    """Return every non-labeled submission for a group, newest first."""
    stmt = (
        sa.select(
            mtc.c.id,
            mtc.c.raw_address,
            mtc.c.failure_type,
            mtc.c.failure_reason,
            mtc.c.endpoint,
            mtc.c.provider,
            mtc.c.api_version,
            mtc.c.parsed_tokens,
            mtc.c.recovered_components,
            mtc.c.created_at,
            mtc.c.status,
        )
        .where(_NON_LABELED, mtc.c.raw_address_hash == raw_hash)
        .order_by(mtc.c.created_at.desc())
    )
    async with engine.connect() as conn:
        return [dict(r._mapping) for r in (await conn.execute(stmt))]  # noqa: SLF001


async def update_candidate_status(engine: AsyncEngine, *, raw_hash: str, status: str) -> int:
    """Set status on every non-labeled row in the group. Returns rowcount.

    Only 'new' and 'rejected' are admin-settable. 'assigned' is set via
    services.training_batches.assign_candidates; 'labeled' via the training pipeline.
    """
    if status not in WRITE_STATUSES:
        raise ValueError(f"invalid status: {status!r}")
    stmt = (
        sa.update(mtc).where(_NON_LABELED, mtc.c.raw_address_hash == raw_hash).values(status=status)
    )
    async with engine.begin() as conn:
        result = await conn.execute(stmt)
    return result.rowcount or 0


async def update_candidate_notes(engine: AsyncEngine, *, raw_hash: str, notes: str | None) -> int:
    """Set notes on every non-labeled row in the group. Returns rowcount."""
    stripped = notes.strip() if notes else None
    normalized = stripped if stripped else None
    stmt = (
        sa.update(mtc)
        .where(_NON_LABELED, mtc.c.raw_address_hash == raw_hash)
        .values(notes=normalized)
    )
    async with engine.begin() as conn:
        result = await conn.execute(stmt)
    return result.rowcount or 0
