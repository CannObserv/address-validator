"""Training batch lifecycle — state machine, CRUD, and assignment helpers.

A batch owns a group of candidates destined for a specific training run.
The status machine enforces legal transitions; admin routes and the
/train-model skill both go through assert_transition_allowed() before
writing, so illegal states are caught in one place.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from ulid import ULID

from address_validator.db.tables import (
    candidate_batch_assignments,
    training_batches,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

# Coarse-grained status. `closed` is terminal and absorbs the prior
# "contributed" terminal state (contribution recorded via upstream_pr).
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "planned": frozenset({"active", "closed"}),
    "active": frozenset({"deployed", "closed"}),
    "deployed": frozenset({"observing", "closed"}),
    "observing": frozenset({"closed"}),
    "closed": frozenset(),
}

# Fine-grained step within a batch. Advances independently of status;
# status transitions typically piggy-back on step boundaries in the skill.
VALID_STEPS: frozenset[str] = frozenset(
    {
        "identifying",
        "labeling",
        "training",
        "testing",
        "deployed",
        "observing",
        "contributed",
    }
)


class InvalidTransitionError(ValueError):
    """Raised when a status transition violates the state machine."""


def assert_transition_allowed(current: str, target: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidTransitionError(f"illegal status transition: {current!r} -> {target!r}")


def _new_batch_id() -> str:
    return str(ULID())


async def create_batch(
    engine: AsyncEngine,
    *,
    slug: str,
    description: str,
    targeted_failure_pattern: str | None = None,
    manifest_path: str | None = None,
) -> str:
    """Insert a planned batch. Returns the new batch id (ULID)."""
    batch_id = _new_batch_id()
    stmt = training_batches.insert().values(
        id=batch_id,
        slug=slug,
        description=description,
        targeted_failure_pattern=targeted_failure_pattern,
        status="planned",
        current_step=None,
        manifest_path=manifest_path,
    )
    async with engine.begin() as conn:
        await conn.execute(stmt)
    return batch_id


async def transition_status(
    engine: AsyncEngine,
    *,
    batch_id: str,
    target: str,
) -> None:
    """Move a batch to a new status. Raises InvalidTransitionError on illegal moves."""
    async with engine.begin() as conn:
        row = (
            await conn.execute(
                sa.select(training_batches.c.status).where(training_batches.c.id == batch_id)
            )
        ).first()
        if row is None:
            raise ValueError(f"batch not found: {batch_id}")
        assert_transition_allowed(row.status, target)

        now = datetime.now(UTC)
        values: dict[str, object] = {"status": target}
        if target == "active":
            values["activated_at"] = now
        elif target == "deployed":
            values["deployed_at"] = now
        elif target == "closed":
            values["closed_at"] = now

        await conn.execute(
            sa.update(training_batches).where(training_batches.c.id == batch_id).values(**values)
        )


async def advance_step(
    engine: AsyncEngine,
    *,
    batch_id: str,
    step: str,
) -> None:
    """Set the batch's current_step. Validates against VALID_STEPS."""
    if step not in VALID_STEPS:
        raise ValueError(f"invalid step: {step!r}")
    async with engine.begin() as conn:
        await conn.execute(
            sa.update(training_batches)
            .where(training_batches.c.id == batch_id)
            .values(current_step=step)
        )


async def assign_candidates(
    engine: AsyncEngine,
    *,
    batch_id: str,
    raw_address_hashes: list[str],
    assigned_by: str | None = None,
) -> int:
    """Assign candidate groups to a batch. Idempotent (ON CONFLICT DO NOTHING).

    Row statuses are not modified. The 'assigned' rollup is derived at read
    time by joining to candidate_batch_assignments. Auto-activates a planned
    batch on first assignment via a WHERE status='planned' guard (idempotent
    under concurrency — a concurrent assign sees status='active' and skips).
    Returns the number of newly-inserted assignment rows.
    """
    if not raw_address_hashes:
        return 0
    rows = [
        {
            "raw_address_hash": h,
            "batch_id": batch_id,
            "assigned_by": assigned_by,
        }
        for h in raw_address_hashes
    ]
    stmt = pg_insert(candidate_batch_assignments).values(rows).on_conflict_do_nothing()

    async with engine.begin() as conn:
        result = await conn.execute(stmt)
        # Trigger transition planned -> active if this is the batch's first assignment.
        # WHERE status='planned' guard makes this idempotent under concurrency —
        # a second concurrent assign sees status='active' and skips the UPDATE.
        await conn.execute(
            sa.update(training_batches)
            .where(
                training_batches.c.id == batch_id,
                training_batches.c.status == "planned",
            )
            .values(status="active", activated_at=datetime.now(UTC))
        )
    return result.rowcount or 0


async def get_batch_id_by_slug(engine: AsyncEngine, *, slug: str) -> str | None:
    """Return the batch id for a given slug, or None if not found."""
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                sa.select(training_batches.c.id).where(training_batches.c.slug == slug)
            )
        ).first()
    return row.id if row else None


async def record_upstream_pr(engine: AsyncEngine, *, batch_id: str, upstream_pr: str) -> None:
    """Record the upstream PR URL on a batch."""
    async with engine.begin() as conn:
        await conn.execute(
            sa.update(training_batches)
            .where(training_batches.c.id == batch_id)
            .values(upstream_pr=upstream_pr)
        )


async def unassign_candidates(
    engine: AsyncEngine,
    *,
    batch_id: str,
    raw_address_hashes: list[str],
) -> int:
    """Remove candidate-batch assignments for a single batch. Per-batch semantics only.

    Row statuses are not affected. The rollup switches back to 'new'
    automatically once the last assignment row is removed. Returns
    rowcount of deleted assignment rows.
    """
    if not raw_address_hashes:
        return 0
    async with engine.begin() as conn:
        result = await conn.execute(
            candidate_batch_assignments.delete().where(
                candidate_batch_assignments.c.batch_id == batch_id,
                candidate_batch_assignments.c.raw_address_hash.in_(raw_address_hashes),
            )
        )
    return result.rowcount or 0
