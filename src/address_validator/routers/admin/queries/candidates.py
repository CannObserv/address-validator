"""Admin candidate-triage query helpers.

Grouping convention: a "group" is a set of model_training_candidates rows
sharing the same raw_address. Rows with status='labeled' are excluded from
the triage view entirely — once a submission has been included in training
data, it is considered done.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from address_validator.db.tables import model_training_candidates as mtc

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncEngine


_NON_LABELED = mtc.c.status != "labeled"

# Statuses an admin may set via the triage UI. `labeled` is reserved for the
# training pipeline; `mixed` is a derived rollup, never a stored value.
WRITE_STATUSES: frozenset[str] = frozenset({"new", "reviewed", "rejected"})

# Default time window for candidate-triage views. Drives both the list view's
# `since=30d` querystring default AND the nav-badge lookback so the count
# matches what the user lands on. Change in one place.
DEFAULT_LOOKBACK_DAYS = 30


def _rollup_status_expr() -> ColumnElement:
    """CASE expression: single status -> that status; multiple -> 'mixed'."""
    return sa.case(
        (sa.func.count(sa.distinct(mtc.c.status)) == 1, sa.func.min(mtc.c.status)),
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

    rollup = _rollup_status_expr()
    last_seen = sa.func.max(mtc.c.created_at).label("last_seen")

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
    rollup = _rollup_status_expr()
    group_stmt = (
        sa.select(mtc.c.raw_address_hash)
        .where(*where)
        .group_by(mtc.c.raw_address, mtc.c.raw_address_hash)
        .having(rollup.in_(("new", "mixed")))
    )
    count_stmt = sa.select(sa.func.count()).select_from(group_stmt.subquery())
    async with engine.connect() as conn:
        return (await conn.execute(count_stmt)).scalar() or 0


async def get_candidate_group(engine: AsyncEngine, *, raw_hash: str) -> dict | None:
    """Return the summary for a single group identified by raw_hash, or None."""
    rollup = _rollup_status_expr()
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
    """Set status on every non-labeled row in the group. Returns rowcount."""
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
