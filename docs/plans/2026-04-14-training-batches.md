# Training Batches Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a DB-backed "training batch" lifecycle, rename `session → batch` across the codebase, and rework candidate triage so groups are assigned to batches (M:N) instead of vaguely "reviewed". Surface richer submission context (endpoint, provider, api_version, failure_reason) on the triage detail screen.

**Architecture:** DB (`training_batches` + `candidate_batch_assignments`) is source of truth for status/assignment; filesystem (`training/batches/<slug>/`) holds artifacts (XML, rationale.md, manifest.json, models). ULID primary keys reuse `python-ulid` already in use by `request_id` middleware. State transitions are centralised in `services/training_batches.py` via an `_ALLOWED_TRANSITIONS` dict; admin routes and `scripts/model/*.py` both call through it. Candidate status drops `reviewed` in favour of derived `assigned` (status='new' + ≥1 active assignment).

**Tech Stack:** FastAPI, SQLAlchemy Core (no ORM), asyncpg, Alembic, Jinja2 + HTMX, Tailwind, pytest + pytest-asyncio, python-ulid, PostgreSQL.

**Design doc:** `docs/plans/2026-04-14-training-batches-design.md`

**Issue:** #103

---

## Task 1: Filesystem rename (sessions → batches)

**Files:**
- Rename: `training/sessions/` → `training/batches/`
- Modify: `.gitignore` (if any `training/sessions` references) — none expected
- Modify: `CLAUDE.md` / `AGENTS.md` — the "Session directory structure" and `training/sessions/` references
- Modify: `.claude/skills/train-model/SKILL.md` — all `training/sessions/` references

- [ ] **Step 1: Rename the directory with git mv**

```bash
cd /home/exedev/address-validator
git mv training/sessions training/batches
git status
```

Expected: shows renames under `training/batches/...`.

- [ ] **Step 2: Update AGENTS.md references**

Open `AGENTS.md`. Find every `training/sessions` occurrence and change to `training/batches`. Also search for the words "session" / "sessions" in the training-pipeline context and update to "batch" / "batches". Do NOT touch `# Session lifecycle` header for the systemd service section — that's a different meaning. Search with:

```bash
grep -n "training/sessions\|training session\|training sessions" AGENTS.md
```

Edit each hit with `Edit`, replacing with `training/batches` and `training batch` / `training batches` as appropriate.

- [ ] **Step 3: Update SKILL.md references**

```bash
grep -n "training/sessions\|session\|sessions" .claude/skills/train-model/SKILL.md | head -40
```

Replace `training/sessions/` → `training/batches/` throughout. Rename "session" → "batch" in the training context (e.g., "session directory" → "batch directory", "session-dir" → "batch-dir"). The variable name `--session-dir` stays for this commit (Task 12 handles CLI rename).

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "#103 chore: rename training/sessions -> training/batches"
```

---

## Task 2: Alembic migration 013 — schema

**Files:**
- Create: `alembic/versions/013_training_batches.py`

- [ ] **Step 1: Write the migration**

Create `alembic/versions/013_training_batches.py`:

```python
"""Add training_batches + candidate_batch_assignments; extend model_training_candidates.

Revision ID: 013
Revises: 012
Create Date: 2026-04-14

Introduces batch-level lifecycle and many-to-many candidate assignment.
Denormalises endpoint/provider/api_version/failure_reason onto
model_training_candidates so triage context survives audit archival.
Relaxes the candidate status CHECK: drops 'reviewed', adds 'assigned';
existing 'reviewed' rows are migrated to 'new'.

Seeds the pre-existing multi_unit batch as status='deployed'.
"""

revision: str = "013"
down_revision: str = "012"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402


def upgrade() -> None:
    # --- training_batches ---
    op.execute(
        """
        CREATE TABLE training_batches (
            id TEXT PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL,
            targeted_failure_pattern TEXT,
            status TEXT NOT NULL,
            current_step TEXT,
            manifest_path TEXT,
            upstream_pr TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            activated_at TIMESTAMPTZ,
            deployed_at TIMESTAMPTZ,
            closed_at TIMESTAMPTZ,
            CONSTRAINT ck_training_batches_status CHECK (
                status IN ('planned', 'active', 'deployed', 'observing', 'closed')
            ),
            CONSTRAINT ck_training_batches_current_step CHECK (
                current_step IS NULL OR current_step IN (
                    'identifying', 'labeling', 'training', 'testing',
                    'deployed', 'observing', 'contributed'
                )
            )
        )
        """
    )
    op.create_index("ix_training_batches_status", "training_batches", ["status"])

    # --- candidate_batch_assignments ---
    op.execute(
        """
        CREATE TABLE candidate_batch_assignments (
            raw_address_hash TEXT NOT NULL,
            batch_id TEXT NOT NULL REFERENCES training_batches(id) ON DELETE CASCADE,
            assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            assigned_by TEXT,
            PRIMARY KEY (raw_address_hash, batch_id)
        )
        """
    )
    op.create_index(
        "ix_candidate_batch_assignments_batch",
        "candidate_batch_assignments",
        ["batch_id"],
    )

    # --- extend model_training_candidates ---
    op.execute("ALTER TABLE model_training_candidates ADD COLUMN endpoint TEXT")
    op.execute("ALTER TABLE model_training_candidates ADD COLUMN provider TEXT")
    op.execute("ALTER TABLE model_training_candidates ADD COLUMN api_version TEXT")
    op.execute("ALTER TABLE model_training_candidates ADD COLUMN failure_reason TEXT")

    # Migrate reviewed -> new before tightening the CHECK
    op.execute("UPDATE model_training_candidates SET status = 'new' WHERE status = 'reviewed'")

    op.execute(
        "ALTER TABLE model_training_candidates "
        "DROP CONSTRAINT ck_model_training_candidates_status"
    )
    op.execute(
        "ALTER TABLE model_training_candidates "
        "ADD CONSTRAINT ck_model_training_candidates_status "
        "CHECK (status IN ('new', 'assigned', 'labeled', 'rejected'))"
    )

    # --- seed the pre-existing multi_unit batch ---
    op.execute(
        """
        INSERT INTO training_batches (
            id, slug, description, targeted_failure_pattern,
            status, current_step, manifest_path,
            created_at, activated_at, deployed_at
        ) VALUES (
            '01KMV1103Q0000000000000000',
            '2026_03_28-multi_unit',
            'Multi-unit designator handling — BLDG + APT/STE/UNIT/ROOM patterns (issue #72)',
            'repeated_label_error',
            'deployed',
            'deployed',
            'training/batches/2026_03_28-multi_unit',
            '2026-03-28T20:09:04.375357+00:00',
            '2026-03-28T20:09:04.375357+00:00',
            '2026-03-28T20:09:04.375357+00:00'
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS candidate_batch_assignments")
    op.execute("DROP TABLE IF EXISTS training_batches")

    op.execute(
        "ALTER TABLE model_training_candidates "
        "DROP CONSTRAINT ck_model_training_candidates_status"
    )
    op.execute(
        "ALTER TABLE model_training_candidates "
        "ADD CONSTRAINT ck_model_training_candidates_status "
        "CHECK (status IN ('new', 'reviewed', 'labeled', 'rejected'))"
    )
    op.execute("ALTER TABLE model_training_candidates DROP COLUMN failure_reason")
    op.execute("ALTER TABLE model_training_candidates DROP COLUMN api_version")
    op.execute("ALTER TABLE model_training_candidates DROP COLUMN provider")
    op.execute("ALTER TABLE model_training_candidates DROP COLUMN endpoint")
```

- [ ] **Step 2: Run the migration against a scratch DB**

```bash
source /etc/address-validator/.env
uv run alembic upgrade head
```

Expected: `Running upgrade 012 -> 013, Add training_batches...`. No errors.

- [ ] **Step 3: Verify schema**

```bash
psql "$VALIDATION_CACHE_DSN" -c "\d training_batches" -c "\d candidate_batch_assignments" -c "\d model_training_candidates"
```

Expected: all three tables printed with columns and constraints as specified. Confirm `training_batches` has 1 seed row:

```bash
psql "$VALIDATION_CACHE_DSN" -c "SELECT slug, status FROM training_batches"
```

- [ ] **Step 4: Round-trip downgrade / upgrade test**

```bash
uv run alembic downgrade 012
uv run alembic upgrade 013
```

Expected: clean downgrade then clean upgrade (seed row reappears).

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/013_training_batches.py
git commit -m "#103 feat: add training_batches + candidate_batch_assignments migration"
```

---

## Task 3: SQLAlchemy Core table definitions

**Files:**
- Modify: `src/address_validator/db/tables.py` (add new tables, extend candidate table, update status CHECK)

- [ ] **Step 1: Add test covering the new table shapes**

Create `tests/unit/db/test_training_batches_tables.py`:

```python
"""Verify training_batches + candidate_batch_assignments Table defs match DB schema."""

from address_validator.db.tables import (
    candidate_batch_assignments,
    metadata,
    model_training_candidates,
    training_batches,
)


def test_training_batches_columns() -> None:
    cols = {c.name for c in training_batches.columns}
    assert cols == {
        "id", "slug", "description", "targeted_failure_pattern",
        "status", "current_step", "manifest_path", "upstream_pr",
        "created_at", "activated_at", "deployed_at", "closed_at",
    }


def test_candidate_batch_assignments_columns() -> None:
    cols = {c.name for c in candidate_batch_assignments.columns}
    assert cols == {"raw_address_hash", "batch_id", "assigned_at", "assigned_by"}
    pk_cols = {c.name for c in candidate_batch_assignments.primary_key}
    assert pk_cols == {"raw_address_hash", "batch_id"}


def test_model_training_candidates_has_context_columns() -> None:
    cols = {c.name for c in model_training_candidates.columns}
    assert {"endpoint", "provider", "api_version", "failure_reason"} <= cols


def test_tables_registered_on_metadata() -> None:
    names = set(metadata.tables)
    assert {"training_batches", "candidate_batch_assignments"} <= names
```

- [ ] **Step 2: Run the test — expect failure**

```bash
uv run pytest tests/unit/db/test_training_batches_tables.py -v
```

Expected: `ImportError: cannot import name 'training_batches'` (or similar).

- [ ] **Step 3: Extend `db/tables.py`**

At the end of `src/address_validator/db/tables.py`, append:

```python
# ---------------------------------------------------------------------------
# Training batch lifecycle (migration 013)
# ---------------------------------------------------------------------------

training_batches = sa.Table(
    "training_batches",
    metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column("slug", sa.Text(), nullable=False, unique=True),
    sa.Column("description", sa.Text(), nullable=False),
    sa.Column("targeted_failure_pattern", sa.Text(), nullable=True),
    sa.Column(
        "status",
        sa.Text(),
        sa.CheckConstraint(
            "status IN ('planned', 'active', 'deployed', 'observing', 'closed')",
            name="ck_training_batches_status",
        ),
        nullable=False,
    ),
    sa.Column(
        "current_step",
        sa.Text(),
        sa.CheckConstraint(
            "current_step IS NULL OR current_step IN ("
            "'identifying', 'labeling', 'training', 'testing',"
            " 'deployed', 'observing', 'contributed')",
            name="ck_training_batches_current_step",
        ),
        nullable=True,
    ),
    sa.Column("manifest_path", sa.Text(), nullable=True),
    sa.Column("upstream_pr", sa.Text(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
)

candidate_batch_assignments = sa.Table(
    "candidate_batch_assignments",
    metadata,
    sa.Column("raw_address_hash", sa.Text(), nullable=False, primary_key=True),
    sa.Column(
        "batch_id",
        sa.Text(),
        sa.ForeignKey("training_batches.id", ondelete="CASCADE"),
        nullable=False,
        primary_key=True,
    ),
    sa.Column("assigned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    sa.Column("assigned_by", sa.Text(), nullable=True),
)
```

Then update the existing `model_training_candidates` definition:

- Replace the status `CheckConstraint` string with: `"status IN ('new', 'assigned', 'labeled', 'rejected')"`.
- Add four columns just before `sa.Column("notes", ...)`:

```python
    sa.Column("endpoint", sa.Text(), nullable=True),
    sa.Column("provider", sa.Text(), nullable=True),
    sa.Column("api_version", sa.Text(), nullable=True),
    sa.Column("failure_reason", sa.Text(), nullable=True),
```

- [ ] **Step 4: Re-run the test — expect pass**

```bash
uv run pytest tests/unit/db/test_training_batches_tables.py -v
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/db/tables.py tests/unit/db/test_training_batches_tables.py
git commit -m "#103 feat: add training batch Core table defs; extend candidate columns"
```

---

## Task 4: `services/training_batches.py` — state machine + CRUD

**Files:**
- Create: `src/address_validator/services/training_batches.py`
- Create: `tests/unit/test_training_batches_service.py`

- [ ] **Step 1: Write failing tests for state machine + create**

Create `tests/unit/test_training_batches_service.py`:

```python
"""Tests for the training_batches service: state machine + CRUD helpers."""

from __future__ import annotations

import pytest

from address_validator.services.training_batches import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    assert_transition_allowed,
)


def test_planned_to_active_allowed() -> None:
    assert_transition_allowed("planned", "active")


def test_active_to_deployed_allowed() -> None:
    assert_transition_allowed("active", "deployed")


def test_planned_to_deployed_rejected() -> None:
    with pytest.raises(InvalidTransitionError):
        assert_transition_allowed("planned", "deployed")


def test_closed_is_terminal_from_anywhere() -> None:
    for src in ("planned", "active", "deployed", "observing"):
        assert_transition_allowed(src, "closed")


def test_closed_has_no_outgoing_transitions() -> None:
    assert "closed" not in ALLOWED_TRANSITIONS or not ALLOWED_TRANSITIONS["closed"]


def test_identity_transition_rejected() -> None:
    with pytest.raises(InvalidTransitionError):
        assert_transition_allowed("active", "active")
```

- [ ] **Step 2: Run — expect import error**

```bash
uv run pytest tests/unit/test_training_batches_service.py -v
```

Expected: `ImportError: cannot import name 'ALLOWED_TRANSITIONS'`.

- [ ] **Step 3: Write the service module**

Create `src/address_validator/services/training_batches.py`:

```python
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
VALID_STEPS: frozenset[str] = frozenset({
    "identifying", "labeling", "training", "testing",
    "deployed", "observing", "contributed",
})


class InvalidTransitionError(ValueError):
    """Raised when a status transition violates the state machine."""


def assert_transition_allowed(current: str, target: str) -> None:
    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidTransitionError(
            f"illegal status transition: {current!r} -> {target!r}"
        )


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
            sa.update(training_batches)
            .where(training_batches.c.id == batch_id)
            .values(**values)
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

    Side effect: any candidate row whose rollup becomes `assigned` (i.e. has
    status='new' + now has ≥1 assignment) has its row status set to 'assigned'.
    Returns the number of newly-inserted assignment rows.
    """
    if not raw_address_hashes:
        return 0
    from sqlalchemy.dialects.postgresql import insert as pg_insert

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
        # Flip status='new' rows to 'assigned' for the touched groups.
        await conn.execute(
            sa.text(
                "UPDATE model_training_candidates "
                "SET status = 'assigned' "
                "WHERE raw_address_hash = ANY(:hashes) AND status = 'new'"
            ),
            {"hashes": raw_address_hashes},
        )
        # Trigger transition planned -> active if this is the batch's first assignment.
        batch_status = (
            await conn.execute(
                sa.select(training_batches.c.status).where(training_batches.c.id == batch_id)
            )
        ).scalar_one()
        if batch_status == "planned":
            await conn.execute(
                sa.update(training_batches)
                .where(training_batches.c.id == batch_id)
                .values(status="active", activated_at=datetime.now(UTC))
            )
    return result.rowcount or 0


async def unassign_candidates(
    engine: AsyncEngine,
    *,
    batch_id: str,
    raw_address_hashes: list[str],
) -> int:
    """Remove candidate-batch assignments. Per-batch only.

    Side effect: if a group now has zero assignments and its rows are
    'assigned', revert them to 'new'. Returns rowcount of deleted assignments.
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
        # Revert to 'new' for any hash that no longer has ANY assignment.
        await conn.execute(
            sa.text(
                "UPDATE model_training_candidates c "
                "SET status = 'new' "
                "WHERE c.raw_address_hash = ANY(:hashes) "
                "  AND c.status = 'assigned' "
                "  AND NOT EXISTS ("
                "    SELECT 1 FROM candidate_batch_assignments a "
                "    WHERE a.raw_address_hash = c.raw_address_hash"
                "  )"
            ),
            {"hashes": raw_address_hashes},
        )
    return result.rowcount or 0
```

- [ ] **Step 4: Run state-machine tests — expect pass**

```bash
uv run pytest tests/unit/test_training_batches_service.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/services/training_batches.py tests/unit/test_training_batches_service.py
git commit -m "#103 feat: add training_batches service with state machine + CRUD"
```

---

## Task 5: Integration tests for CRUD / assignment helpers

**Files:**
- Create: `tests/integration/test_training_batches_service_db.py` (if `tests/integration/` exists; else `tests/unit/test_training_batches_service_db.py`)

- [ ] **Step 1: Verify existing integration-test pattern**

```bash
ls tests/ && grep -rln "pytest_asyncio\|async def test" tests/unit/test_training_candidates.py | head -5
```

Expected: candidates tests use pytest-asyncio + an engine fixture from `conftest.py`. Mirror that pattern. If `tests/integration/` does not exist, place these tests next to `test_training_candidates.py`.

- [ ] **Step 2: Write tests**

Create `tests/unit/test_training_batches_service_db.py`:

```python
"""DB-backed tests for training_batches CRUD + assignment helpers."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from address_validator.db.tables import (
    candidate_batch_assignments,
    model_training_candidates,
    training_batches,
)
from address_validator.services.training_batches import (
    InvalidTransitionError,
    assign_candidates,
    create_batch,
    transition_status,
    unassign_candidates,
)

pytestmark = pytest.mark.asyncio


async def _insert_candidate(engine, raw: str) -> str:
    """Insert a candidate row, return its raw_address_hash."""
    async with engine.begin() as conn:
        await conn.execute(
            model_training_candidates.insert().values(
                raw_address=raw,
                failure_type="repeated_label_error",
                parsed_tokens=[["123", "AddressNumber"]],
                recovered_components=None,
                status="new",
            )
        )
        row = (
            await conn.execute(
                sa.select(model_training_candidates.c.raw_address_hash).where(
                    model_training_candidates.c.raw_address == raw
                )
            )
        ).first()
    return row.raw_address_hash


async def test_create_batch_starts_planned(engine) -> None:
    batch_id = await create_batch(engine, slug="test-a", description="desc")
    async with engine.connect() as conn:
        row = (
            await conn.execute(sa.select(training_batches).where(training_batches.c.id == batch_id))
        ).first()
    assert row.status == "planned"
    assert row.current_step is None


async def test_transition_planned_to_deployed_rejected(engine) -> None:
    batch_id = await create_batch(engine, slug="test-b", description="desc")
    with pytest.raises(InvalidTransitionError):
        await transition_status(engine, batch_id=batch_id, target="deployed")


async def test_assign_activates_planned_batch(engine) -> None:
    batch_id = await create_batch(engine, slug="test-c", description="desc")
    h = await _insert_candidate(engine, "123 MAIN ST")

    n = await assign_candidates(engine, batch_id=batch_id, raw_address_hashes=[h])
    assert n == 1

    async with engine.connect() as conn:
        batch = (
            await conn.execute(sa.select(training_batches).where(training_batches.c.id == batch_id))
        ).first()
        cand = (
            await conn.execute(
                sa.select(model_training_candidates.c.status).where(
                    model_training_candidates.c.raw_address_hash == h
                )
            )
        ).first()
    assert batch.status == "active"
    assert batch.activated_at is not None
    assert cand.status == "assigned"


async def test_assign_is_idempotent(engine) -> None:
    batch_id = await create_batch(engine, slug="test-d", description="desc")
    h = await _insert_candidate(engine, "456 OAK AVE")
    await assign_candidates(engine, batch_id=batch_id, raw_address_hashes=[h])
    n2 = await assign_candidates(engine, batch_id=batch_id, raw_address_hashes=[h])
    assert n2 == 0


async def test_unassign_last_batch_reverts_to_new(engine) -> None:
    batch_id = await create_batch(engine, slug="test-e", description="desc")
    h = await _insert_candidate(engine, "789 ELM RD")
    await assign_candidates(engine, batch_id=batch_id, raw_address_hashes=[h])

    await unassign_candidates(engine, batch_id=batch_id, raw_address_hashes=[h])

    async with engine.connect() as conn:
        cand = (
            await conn.execute(
                sa.select(model_training_candidates.c.status).where(
                    model_training_candidates.c.raw_address_hash == h
                )
            )
        ).first()
        remaining = (
            await conn.execute(
                sa.select(sa.func.count()).select_from(candidate_batch_assignments).where(
                    candidate_batch_assignments.c.raw_address_hash == h
                )
            )
        ).scalar()
    assert cand.status == "new"
    assert remaining == 0


async def test_unassign_keeps_assigned_when_other_batch_still_holds(engine) -> None:
    b1 = await create_batch(engine, slug="test-f1", description="desc")
    b2 = await create_batch(engine, slug="test-f2", description="desc")
    h = await _insert_candidate(engine, "321 PINE ST")
    await assign_candidates(engine, batch_id=b1, raw_address_hashes=[h])
    await assign_candidates(engine, batch_id=b2, raw_address_hashes=[h])

    await unassign_candidates(engine, batch_id=b1, raw_address_hashes=[h])

    async with engine.connect() as conn:
        cand = (
            await conn.execute(
                sa.select(model_training_candidates.c.status).where(
                    model_training_candidates.c.raw_address_hash == h
                )
            )
        ).first()
    assert cand.status == "assigned"
```

- [ ] **Step 3: Run — expect pass**

```bash
uv run pytest tests/unit/test_training_batches_service_db.py -v
```

Expected: 6 tests pass. Ensure the `engine` fixture exists in `tests/unit/conftest.py`; if not, copy pattern from `test_training_candidates.py`.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_training_batches_service_db.py
git commit -m "#103 test: cover batch CRUD + assign/unassign semantics"
```

---

## Task 6: Extend training-candidate writer with context columns

**Files:**
- Modify: `src/address_validator/services/training_candidates.py`
- Modify: `tests/unit/test_training_candidates.py`

- [ ] **Step 1: Extend failing test first**

Add to `tests/unit/test_training_candidates.py`:

```python
async def test_write_persists_context_columns(engine) -> None:
    from address_validator.services.training_candidates import write_training_candidate
    await write_training_candidate(
        engine,
        raw_address="111 TEST ST",
        failure_type="repeated_label_error",
        parsed_tokens=[("111", "AddressNumber")],
        recovered_components=None,
        endpoint="/api/v1/parse",
        provider=None,
        api_version="1",
        failure_reason="RepeatedLabelError on token 'ROOM'",
    )
    import sqlalchemy as sa
    from address_validator.db.tables import model_training_candidates as mtc
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                sa.select(mtc).where(mtc.c.raw_address == "111 TEST ST")
            )
        ).first()
    assert row.endpoint == "/api/v1/parse"
    assert row.api_version == "1"
    assert row.failure_reason.startswith("RepeatedLabelError")
```

- [ ] **Step 2: Run — expect failure**

```bash
uv run pytest tests/unit/test_training_candidates.py::test_write_persists_context_columns -v
```

Expected: TypeError on unknown keyword arg.

- [ ] **Step 3: Extend `set_candidate_data` and `write_training_candidate`**

In `src/address_validator/services/training_candidates.py`:

Replace `set_candidate_data` signature:

```python
def set_candidate_data(
    *,
    raw_address: str,
    failure_type: str,
    parsed_tokens: list[tuple[str, str]] | list[Any],
    recovered_components: dict[str, str] | None = None,
    failure_reason: str | None = None,
) -> None:
    """Set training candidate data for the current request context."""
    _candidate_data.set(
        {
            "raw_address": raw_address,
            "failure_type": failure_type,
            "parsed_tokens": parsed_tokens,
            "recovered_components": recovered_components,
            "failure_reason": failure_reason,
        }
    )
```

Replace `write_training_candidate`:

```python
async def write_training_candidate(
    engine: AsyncEngine | None,
    *,
    raw_address: str,
    failure_type: str,
    parsed_tokens: list[tuple[str, str]] | list[Any],
    recovered_components: dict[str, str] | None = None,
    endpoint: str | None = None,
    provider: str | None = None,
    api_version: str | None = None,
    failure_reason: str | None = None,
) -> None:
    """Insert a training candidate row. Logs and swallows all errors (fail-open)."""
    if engine is None:
        return
    try:
        tokens_json = [[tok, label] for tok, label in parsed_tokens]
        async with engine.begin() as conn:
            await conn.execute(
                model_training_candidates.insert().values(
                    raw_address=raw_address,
                    failure_type=failure_type,
                    parsed_tokens=tokens_json,
                    recovered_components=recovered_components,
                    endpoint=endpoint,
                    provider=provider,
                    api_version=api_version,
                    failure_reason=failure_reason,
                )
            )
    except Exception:
        logger.warning("training_candidates: failed to write training candidate", exc_info=True)
```

- [ ] **Step 4: Run — expect pass**

```bash
uv run pytest tests/unit/test_training_candidates.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/services/training_candidates.py tests/unit/test_training_candidates.py
git commit -m "#103 feat: extend training candidate writer with context columns"
```

---

## Task 7: Parser + middleware plumbing for context

**Files:**
- Modify: `src/address_validator/services/parser.py` (add failure_reason at both `set_candidate_data` sites)
- Modify: `src/address_validator/middleware/audit.py` (enrich candidate write with endpoint/provider/api_version)

- [ ] **Step 1: Failing middleware test**

Add to `tests/unit/test_audit_middleware.py` (follow patterns already there):

```python
async def test_audit_writes_candidate_with_endpoint_and_version(engine, client):
    """Post an ambiguous address; candidate row should capture endpoint + api_version."""
    resp = await client.post(
        "/api/v1/parse",
        json={"address": "995 9TH ST BLDG 201 ROOM 104 T", "country": "US"},
        headers={"X-API-Key": "test-key"},
    )
    assert resp.status_code == 200

    # Background task is scheduled; give it a tick.
    import asyncio
    await asyncio.sleep(0.05)

    import sqlalchemy as sa
    from address_validator.db.tables import model_training_candidates as mtc
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                sa.select(mtc.c.endpoint, mtc.c.api_version, mtc.c.failure_reason)
                .where(mtc.c.raw_address.like("995 9TH ST%"))
                .order_by(mtc.c.id.desc())
                .limit(1)
            )
        ).first()
    assert row is not None
    assert row.endpoint == "/api/v1/parse"
    assert row.api_version == "1"
    assert row.failure_reason is not None
```

- [ ] **Step 2: Run — expect failure**

```bash
uv run pytest tests/unit/test_audit_middleware.py::test_audit_writes_candidate_with_endpoint_and_version -v
```

Expected: FAIL (row columns NULL).

- [ ] **Step 3: Add failure_reason in parser**

Edit `src/address_validator/services/parser.py` — both `set_candidate_data` call sites around lines 445 and 475:

At line ~445 (RepeatedLabelError branch):
```python
        set_candidate_data(
            raw_address=raw,
            failure_type="repeated_label_error",
            parsed_tokens=list(exc.parsed_string),
            recovered_components=component_values,
            failure_reason=f"usaddress.RepeatedLabelError: {exc}".replace("\n", " ")[:400],
        )
```

At line ~475 (post-parse recovery branch):
```python
        set_candidate_data(
            raw_address=raw,
            failure_type="post_parse_recovery",
            parsed_tokens=[(v, k) for k, v in tagged.items()],
            recovered_components=component_values,
            failure_reason="; ".join(w for w in warnings if "recovered" in w.lower())[:400]
            or "post-parse recovery heuristics matched",
        )
```

- [ ] **Step 4: Enrich middleware write**

In `src/address_validator/middleware/audit.py`, replace the candidate-write block (lines ~192-198) with:

```python
        # Fire-and-forget training candidate write if parser flagged one
        candidate = get_candidate_data()
        if candidate is not None:
            api_version: str | None = None
            if path.startswith("/api/v1/"):
                api_version = "1"
            elif path.startswith("/api/v2/"):
                api_version = "2"
            candidate_task = asyncio.create_task(
                write_training_candidate(
                    engine=engine,
                    endpoint=path,
                    provider=provider,
                    api_version=api_version,
                    **candidate,
                )
            )
            _background_tasks.add(candidate_task)
            candidate_task.add_done_callback(_background_tasks.discard)
```

Note: `**candidate` already carries `failure_reason` now that Task 6 is done.

- [ ] **Step 5: Run — expect pass**

```bash
uv run pytest tests/unit/test_audit_middleware.py -v
uv run pytest tests/unit/test_parser.py -v
```

Expected: both pass.

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/services/parser.py src/address_validator/middleware/audit.py tests/unit/test_audit_middleware.py
git commit -m "#103 feat: record endpoint, provider, api_version, failure_reason on candidates"
```

---

## Task 8: Admin candidate queries — assignment-aware

**Files:**
- Modify: `src/address_validator/routers/admin/queries/candidates.py`
- Modify: `tests/unit/test_admin_candidates_queries.py`

- [ ] **Step 1: Failing test for new `assigned` rollup + batches column**

Add to `tests/unit/test_admin_candidates_queries.py`:

```python
async def test_rollup_assigned_when_linked_to_batch(engine) -> None:
    from address_validator.services.training_batches import assign_candidates, create_batch
    from address_validator.routers.admin.queries.candidates import get_candidate_groups

    # seed a candidate
    import sqlalchemy as sa
    from address_validator.db.tables import model_training_candidates as mtc
    async with engine.begin() as conn:
        await conn.execute(
            mtc.insert().values(
                raw_address="ASSIGN ME",
                failure_type="repeated_label_error",
                parsed_tokens=[["1", "AddressNumber"]],
                status="new",
            )
        )
        h = (await conn.execute(
            sa.select(mtc.c.raw_address_hash).where(mtc.c.raw_address == "ASSIGN ME")
        )).scalar_one()

    batch_id = await create_batch(engine, slug="q-test", description="d")
    await assign_candidates(engine, batch_id=batch_id, raw_address_hashes=[h])

    rows, _ = await get_candidate_groups(
        engine, status="assigned", failure_type=None,
        since=None, until=None, limit=10, offset=0,
    )
    assert any(r["raw_hash"] == h for r in rows)
    match = next(r for r in rows if r["raw_hash"] == h)
    assert "q-test" in (match.get("batch_slugs") or [])
    assert match["rollup_status"] == "assigned"
```

- [ ] **Step 2: Run — expect failure**

```bash
uv run pytest tests/unit/test_admin_candidates_queries.py::test_rollup_assigned_when_linked_to_batch -v
```

Expected: fails (no `assigned` status filter, no `batch_slugs` key).

- [ ] **Step 3: Update `queries/candidates.py`**

Replace the file's top section and filter helpers — specifically:

Update `WRITE_STATUSES` block:

```python
# Statuses an admin may set via the triage UI. `labeled` is reserved for the
# training pipeline; `mixed` is a derived rollup, never a stored value.
# `assigned` is set by services/training_batches.assign_candidates and cleared
# by unassign_candidates — admins never POST it directly.
WRITE_STATUSES: frozenset[str] = frozenset({"new", "rejected"})
```

Update `_status_filter`:

```python
def _status_filter(rollup_col: ColumnElement, status: str | None) -> ColumnElement | None:
    """Translate UI status filter to a HAVING-clause expression on rollup."""
    if status is None or status == "all":
        return None
    if status == "new":
        return rollup_col.in_(("new", "mixed"))
    return rollup_col == status
```

(No logical change — `assigned` flows through the `else` branch.)

Update `get_candidate_groups` select to include `batch_slugs`:

```python
from address_validator.db.tables import (
    candidate_batch_assignments as cba,
    training_batches as tb,
)

...

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

    # Subquery: array_agg of distinct batch slugs for each raw_address_hash.
    batch_slugs_sub = (
        sa.select(
            cba.c.raw_address_hash,
            sa.func.array_agg(sa.distinct(tb.c.slug)).label("batch_slugs"),
        )
        .select_from(cba.join(tb, cba.c.batch_id == tb.c.id))
        .group_by(cba.c.raw_address_hash)
        .subquery()
    )

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
            sa.func.max(batch_slugs_sub.c.batch_slugs).label("batch_slugs"),
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
```

Apply the same `batch_slugs` addition to `get_candidate_group`:

```python
async def get_candidate_group(engine: AsyncEngine, *, raw_hash: str) -> dict | None:
    rollup = _rollup_status_expr()
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
            sa.func.max(batch_slugs_sub.c.batch_slugs).label("batch_slugs"),
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
```

Update `update_candidate_status` — raise on `'assigned'` / `'labeled'`:

```python
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
```

Also extend `get_candidate_submissions` to return the new context columns:

```python
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
```

- [ ] **Step 4: Run — expect pass**

```bash
uv run pytest tests/unit/test_admin_candidates_queries.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/routers/admin/queries/candidates.py tests/unit/test_admin_candidates_queries.py
git commit -m "#103 feat: surface batch slugs + context columns in candidate queries"
```

---

## Task 9: Admin batch queries module

**Files:**
- Create: `src/address_validator/routers/admin/queries/batches.py`
- Modify: `src/address_validator/routers/admin/queries/__init__.py`
- Create: `tests/unit/test_admin_batches_queries.py`

- [ ] **Step 1: Failing test**

Create `tests/unit/test_admin_batches_queries.py`:

```python
"""Tests for admin batch query helpers."""

from __future__ import annotations

import pytest

from address_validator.routers.admin.queries.batches import (
    get_assignable_batches,
    get_batch_by_slug,
    get_batch_candidates,
    list_batches,
)
from address_validator.services.training_batches import assign_candidates, create_batch

pytestmark = pytest.mark.asyncio


async def test_list_batches_returns_seeded_row(engine) -> None:
    await create_batch(engine, slug="q-a", description="d")
    rows = await list_batches(engine, status=None)
    slugs = {r["slug"] for r in rows}
    assert "q-a" in slugs


async def test_list_batches_filters_by_status(engine) -> None:
    await create_batch(engine, slug="q-plan", description="d")
    rows = await list_batches(engine, status="planned")
    assert all(r["status"] == "planned" for r in rows)


async def test_get_batch_by_slug_unknown_returns_none(engine) -> None:
    assert await get_batch_by_slug(engine, slug="no-such-batch") is None


async def test_assignable_batches_excludes_closed(engine) -> None:
    await create_batch(engine, slug="q-p2", description="d")
    rows = await get_assignable_batches(engine)
    for r in rows:
        assert r["status"] in ("planned", "active")
```

- [ ] **Step 2: Run — expect ImportError**

```bash
uv run pytest tests/unit/test_admin_batches_queries.py -v
```

- [ ] **Step 3: Create the module**

Create `src/address_validator/routers/admin/queries/batches.py`:

```python
"""Admin batch query helpers — list, detail, assignable, and assigned candidates."""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa

from address_validator.db.tables import (
    candidate_batch_assignments as cba,
    model_training_candidates as mtc,
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
        .select_from(
            cba.join(mtc, mtc.c.raw_address_hash == cba.c.raw_address_hash)
        )
        .where(cba.c.batch_id == batch_id)
        .group_by(mtc.c.raw_address, mtc.c.raw_address_hash)
        .order_by(sa.func.max(cba.c.assigned_at).desc())
    )
    async with engine.connect() as conn:
        return [dict(r._mapping) for r in (await conn.execute(stmt))]  # noqa: SLF001
```

Update `src/address_validator/routers/admin/queries/__init__.py` — add imports + `__all__` entries:

```python
from address_validator.routers.admin.queries.batches import (
    get_assignable_batches,
    get_batch_by_slug,
    get_batch_candidates,
    list_batches,
)
```

And add each name to `__all__`.

- [ ] **Step 4: Run — expect pass**

```bash
uv run pytest tests/unit/test_admin_batches_queries.py -v
```

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/routers/admin/queries/batches.py \
  src/address_validator/routers/admin/queries/__init__.py \
  tests/unit/test_admin_batches_queries.py
git commit -m "#103 feat: add admin batch query helpers"
```

---

## Task 10: Admin batches router

**Files:**
- Create: `src/address_validator/routers/admin/batches.py`
- Modify: `src/address_validator/routers/admin/router.py`
- Create: `tests/unit/test_admin_batches_views.py`

- [ ] **Step 1: Failing router test**

Create `tests/unit/test_admin_batches_views.py`:

```python
"""Route-level tests for /admin/batches/."""

import pytest

pytestmark = pytest.mark.asyncio


async def test_list_page_renders(admin_client):
    resp = await admin_client.get("/admin/batches/")
    assert resp.status_code == 200
    assert "Training Batches" in resp.text


async def test_plan_batch_creates_row(admin_client):
    resp = await admin_client.post(
        "/admin/batches/",
        data={"slug": "plan-from-ui", "description": "desc",
              "targeted_failure_pattern": "repeated_label_error"},
    )
    assert resp.status_code in (200, 303)
    listing = await admin_client.get("/admin/batches/")
    assert "plan-from-ui" in listing.text


async def test_invalid_transition_returns_400(admin_client):
    # Seed: create a planned batch, try to jump straight to deployed.
    create = await admin_client.post(
        "/admin/batches/",
        data={"slug": "bad-trans", "description": "d", "targeted_failure_pattern": ""},
    )
    assert create.status_code in (200, 303)
    # Look up the slug, then POST illegal transition.
    page = await admin_client.get("/admin/batches/bad-trans")
    assert page.status_code == 200
    resp = await admin_client.post(
        "/admin/batches/bad-trans/status",
        data={"status": "deployed"},
    )
    assert resp.status_code == 400
```

Ensure `admin_client` fixture exists in conftest; mirror `client` fixture used by existing admin tests.

- [ ] **Step 2: Run — expect failure**

```bash
uv run pytest tests/unit/test_admin_batches_views.py -v
```

- [ ] **Step 3: Implement router**

Create `src/address_validator/routers/admin/batches.py`:

```python
"""Admin batch views — list, detail, plan-new, transition status."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import Response

from address_validator.routers.admin._config import get_css_version, templates
from address_validator.routers.admin.deps import AdminContext, get_admin_context
from address_validator.routers.admin.queries import (
    get_batch_by_slug,
    get_batch_candidates,
    list_batches,
)
from address_validator.services.training_batches import (
    InvalidTransitionError,
    create_batch,
    transition_status,
)

router = APIRouter(prefix="/batches")

_VALID_STATUSES: frozenset[str] = frozenset(
    {"planned", "active", "deployed", "observing", "closed"}
)


@router.get("/", response_class=HTMLResponse, response_model=None)
async def batches_list(
    request: Request,
    status: str | None = Query(None),
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    filter_status = status if status in _VALID_STATUSES else None
    rows = await list_batches(ctx.engine, status=filter_status)
    return templates.TemplateResponse(
        "admin/batches/index.html",
        {
            "request": request,
            "user": ctx.user,
            "active_nav": "batches",
            "css_version": get_css_version(),
            "rows": rows,
            "filter_status": filter_status,
        },
    )


@router.post("/", response_class=HTMLResponse, response_model=None)
async def batches_create(
    request: Request,
    slug: str = Form(...),
    description: str = Form(...),
    targeted_failure_pattern: str = Form(""),
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    pattern = targeted_failure_pattern.strip() or None
    await create_batch(
        ctx.engine,
        slug=slug.strip(),
        description=description.strip(),
        targeted_failure_pattern=pattern,
    )
    return RedirectResponse(url=f"/admin/batches/{slug.strip()}", status_code=303)


@router.get("/{slug}", response_class=HTMLResponse, response_model=None)
async def batches_detail(
    request: Request,
    slug: str,
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    batch = await get_batch_by_slug(ctx.engine, slug=slug)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    candidates = await get_batch_candidates(ctx.engine, batch_id=batch["id"])
    return templates.TemplateResponse(
        "admin/batches/detail.html",
        {
            "request": request,
            "user": ctx.user,
            "active_nav": "batches",
            "css_version": get_css_version(),
            "batch": batch,
            "candidates": candidates,
        },
    )


@router.post("/{slug}/status", response_class=HTMLResponse, response_model=None)
async def batches_transition(
    request: Request,
    slug: str,
    status: str = Form(...),
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    batch = await get_batch_by_slug(ctx.engine, slug=slug)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    try:
        await transition_status(ctx.engine, batch_id=batch["id"], target=status)
    except InvalidTransitionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/admin/batches/{slug}", status_code=303)
```

Modify `src/address_validator/routers/admin/router.py`:

```python
from address_validator.routers.admin.batches import router as batches_router
...
admin_router.include_router(batches_router)
```

- [ ] **Step 4: Add minimal templates**

Create `src/address_validator/templates/admin/batches/index.html`:

```html
{% extends "admin/base.html" %}
{% block title %}Training Batches{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-6">Training Batches</h1>

<form class="flex gap-3 mb-6 items-end" hx-get="/admin/batches/" hx-target="body" hx-push-url="true">
    <div>
        <label class="block text-xs text-gray-500 mb-1">Status</label>
        <select name="status" class="border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 rounded px-2 py-1 text-sm">
            <option value="">All</option>
            {% for s in ['planned','active','deployed','observing','closed'] %}
            <option value="{{ s }}" {% if filter_status == s %}selected{% endif %}>{{ s }}</option>
            {% endfor %}
        </select>
    </div>
    <button class="bg-co-purple text-white px-4 rounded text-sm font-medium min-h-[32px]">Filter</button>
</form>

<details class="mb-6">
    <summary class="cursor-pointer text-sm text-co-purple">+ Plan new batch</summary>
    <form method="post" action="/admin/batches/" class="mt-3 grid grid-cols-1 md:grid-cols-3 gap-3 items-end">
        <div>
            <label class="block text-xs text-gray-500 mb-1">Slug</label>
            <input name="slug" required class="border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 rounded px-2 py-1 text-sm w-full">
        </div>
        <div>
            <label class="block text-xs text-gray-500 mb-1">Description</label>
            <input name="description" required class="border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 rounded px-2 py-1 text-sm w-full">
        </div>
        <div>
            <label class="block text-xs text-gray-500 mb-1">Targeted failure pattern (optional)</label>
            <input name="targeted_failure_pattern" class="border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 rounded px-2 py-1 text-sm w-full">
        </div>
        <button class="bg-co-purple text-white px-4 py-1 rounded text-sm font-medium md:col-span-3 justify-self-end">Create planned batch</button>
    </form>
</details>

<div class="overflow-x-auto bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
    <table class="w-full text-left">
        <thead class="bg-gray-50 dark:bg-gray-700 border-b border-gray-200 dark:border-gray-600 text-xs text-gray-500 uppercase">
            <tr>
                <th class="px-3 py-2">Slug</th>
                <th class="px-3 py-2">Status</th>
                <th class="px-3 py-2">Current Step</th>
                <th class="px-3 py-2">Assigned</th>
                <th class="px-3 py-2">Description</th>
                <th class="px-3 py-2">Created</th>
            </tr>
        </thead>
        <tbody>
            {% for r in rows %}
            <tr class="border-t border-gray-200 dark:border-gray-700 text-sm">
                <td class="px-3 py-2"><a class="text-co-purple hover:underline" href="/admin/batches/{{ r.slug }}">{{ r.slug }}</a></td>
                <td class="px-3 py-2">{{ r.status }}</td>
                <td class="px-3 py-2">{{ r.current_step or '—' }}</td>
                <td class="px-3 py-2">{{ r.assigned_count }}</td>
                <td class="px-3 py-2">{{ r.description }}</td>
                <td class="px-3 py-2">{{ r.created_at.strftime('%Y-%m-%d') if r.created_at else '—' }}</td>
            </tr>
            {% else %}
            <tr><td colspan="6" class="px-3 py-4 text-gray-400">No batches.</td></tr>
            {% endfor %}
        </tbody>
    </table>
</div>
{% endblock %}
```

Create `src/address_validator/templates/admin/batches/detail.html`:

```html
{% extends "admin/base.html" %}
{% block title %}Batch {{ batch.slug }}{% endblock %}
{% block content %}
<div class="mb-4"><a href="/admin/batches/" class="text-sm text-co-purple hover:underline">&larr; Back to batches</a></div>

<h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-2">{{ batch.slug }}</h1>
<p class="text-sm text-gray-600 dark:text-gray-300 mb-4">{{ batch.description }}</p>

<div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 mb-6 grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
    <div><div class="text-xs text-gray-500 uppercase mb-1">Status</div>{{ batch.status }}</div>
    <div><div class="text-xs text-gray-500 uppercase mb-1">Current Step</div>{{ batch.current_step or '—' }}</div>
    <div><div class="text-xs text-gray-500 uppercase mb-1">Targeted Pattern</div>{{ batch.targeted_failure_pattern or '—' }}</div>
    <div><div class="text-xs text-gray-500 uppercase mb-1">Manifest</div><span class="font-mono text-xs">{{ batch.manifest_path or '—' }}</span></div>
    <div><div class="text-xs text-gray-500 uppercase mb-1">Created</div>{{ batch.created_at.strftime('%Y-%m-%d %H:%M') if batch.created_at else '—' }}</div>
    <div><div class="text-xs text-gray-500 uppercase mb-1">Activated</div>{{ batch.activated_at.strftime('%Y-%m-%d %H:%M') if batch.activated_at else '—' }}</div>
    <div><div class="text-xs text-gray-500 uppercase mb-1">Deployed</div>{{ batch.deployed_at.strftime('%Y-%m-%d %H:%M') if batch.deployed_at else '—' }}</div>
    <div><div class="text-xs text-gray-500 uppercase mb-1">Closed</div>{{ batch.closed_at.strftime('%Y-%m-%d %H:%M') if batch.closed_at else '—' }}</div>
</div>

{% set next_statuses = {
    'planned': ['active','closed'],
    'active': ['deployed','closed'],
    'deployed': ['observing','closed'],
    'observing': ['closed'],
    'closed': []
}[batch.status] %}
{% if next_statuses %}
<form method="post" action="/admin/batches/{{ batch.slug }}/status" class="flex gap-2 items-center mb-6">
    <label class="text-xs text-gray-500 uppercase">Transition to</label>
    <select name="status" class="border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 rounded px-2 py-1 text-sm">
        {% for s in next_statuses %}<option value="{{ s }}">{{ s }}</option>{% endfor %}
    </select>
    <button class="bg-co-purple text-white px-3 py-1 rounded text-sm font-medium">Apply</button>
</form>
{% endif %}

<h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-3">Assigned Candidates ({{ candidates|length }})</h2>
<div class="overflow-x-auto bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
    <table class="w-full text-left">
        <thead class="bg-gray-50 dark:bg-gray-700 text-xs text-gray-500 uppercase">
            <tr>
                <th class="px-3 py-2">Raw Address</th>
                <th class="px-3 py-2">Submissions</th>
                <th class="px-3 py-2">Last Seen</th>
                <th class="px-3 py-2">Assigned At</th>
                <th class="px-3 py-2">Assigned By</th>
            </tr>
        </thead>
        <tbody>
            {% for c in candidates %}
            <tr class="border-t border-gray-200 dark:border-gray-700 text-sm">
                <td class="px-3 py-2 font-mono text-xs"><a class="text-co-purple hover:underline" href="/admin/candidates/{{ c.raw_hash }}">{{ c.raw_address }}</a></td>
                <td class="px-3 py-2">{{ c.submission_count }}</td>
                <td class="px-3 py-2">{{ c.last_seen.strftime('%Y-%m-%d %H:%M') if c.last_seen else '—' }}</td>
                <td class="px-3 py-2">{{ c.assigned_at.strftime('%Y-%m-%d %H:%M') if c.assigned_at else '—' }}</td>
                <td class="px-3 py-2">{{ c.assigned_by or '—' }}</td>
            </tr>
            {% else %}
            <tr><td colspan="5" class="px-3 py-4 text-gray-400">No candidates assigned.</td></tr>
            {% endfor %}
        </tbody>
    </table>
</div>
{% endblock %}
```

- [ ] **Step 5: Run — expect pass**

```bash
uv run pytest tests/unit/test_admin_batches_views.py -v
```

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/routers/admin/batches.py \
  src/address_validator/routers/admin/router.py \
  src/address_validator/templates/admin/batches/ \
  tests/unit/test_admin_batches_views.py
git commit -m "#103 feat: add /admin/batches list, detail, create, and status transition"
```

---

## Task 11: Candidate triage UI — assign / unassign + submission context

**Files:**
- Modify: `src/address_validator/routers/admin/candidates.py` — add assign/unassign routes
- Modify: `src/address_validator/templates/admin/candidates/index.html` — status chips, Batches column
- Modify: `src/address_validator/templates/admin/candidates/_rows.html` — Batches column + assign action
- Modify: `src/address_validator/templates/admin/candidates/detail.html` — submission context, batches panel

- [ ] **Step 1: Failing route test**

Add to `tests/unit/test_admin_candidates_views.py`:

```python
async def test_assign_candidate_to_batch(admin_client, engine):
    # Seed: a candidate + a planned batch.
    import sqlalchemy as sa
    from address_validator.db.tables import model_training_candidates as mtc
    async with engine.begin() as conn:
        await conn.execute(
            mtc.insert().values(
                raw_address="TRIAGE ASSIGN",
                failure_type="repeated_label_error",
                parsed_tokens=[["1", "AddressNumber"]],
                status="new",
            )
        )
        h = (await conn.execute(
            sa.select(mtc.c.raw_address_hash).where(mtc.c.raw_address == "TRIAGE ASSIGN")
        )).scalar_one()
    from address_validator.services.training_batches import create_batch
    batch_id = await create_batch(engine, slug="ui-assign", description="d")

    resp = await admin_client.post(
        f"/admin/candidates/{h}/batches",
        data={"batch_id": batch_id},
    )
    assert resp.status_code in (200, 303)

    from address_validator.db.tables import candidate_batch_assignments as cba
    async with engine.connect() as conn:
        count = (await conn.execute(
            sa.select(sa.func.count()).select_from(cba).where(cba.c.raw_address_hash == h)
        )).scalar()
    assert count == 1
```

- [ ] **Step 2: Run — expect 404 / missing route**

```bash
uv run pytest tests/unit/test_admin_candidates_views.py::test_assign_candidate_to_batch -v
```

- [ ] **Step 3: Add assign/unassign routes**

In `src/address_validator/routers/admin/candidates.py`, add near the bottom:

```python
from address_validator.routers.admin.queries.batches import (
    get_assignable_batches,
    get_batch_by_slug,
)
from address_validator.services.training_batches import (
    assign_candidates,
    unassign_candidates,
)


@router.post("/{raw_hash}/batches", response_class=HTMLResponse, response_model=None)
async def candidates_assign_batch(
    request: Request,
    raw_hash: str,
    batch_id: str = Form(...),
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    await assign_candidates(
        ctx.engine,
        batch_id=batch_id,
        raw_address_hashes=[raw_hash],
        assigned_by=ctx.user.email if ctx.user else None,
    )
    return RedirectResponse(url=f"/admin/candidates/{raw_hash}", status_code=303)


@router.post("/{raw_hash}/batches/{batch_slug}/unassign", response_class=HTMLResponse, response_model=None)
async def candidates_unassign_batch(
    request: Request,
    raw_hash: str,
    batch_slug: str,
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    batch = await get_batch_by_slug(ctx.engine, slug=batch_slug)
    if batch is None:
        raise HTTPException(status_code=404, detail="batch not found")
    await unassign_candidates(
        ctx.engine, batch_id=batch["id"], raw_address_hashes=[raw_hash]
    )
    return RedirectResponse(url=f"/admin/candidates/{raw_hash}", status_code=303)
```

Add `from fastapi.responses import RedirectResponse` at the top.

Update `candidates_detail` to also pass `assignable_batches`:

```python
@router.get("/{raw_hash}", response_class=HTMLResponse, response_model=None)
async def candidates_detail(
    request: Request,
    raw_hash: str,
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    group = await get_candidate_group(ctx.engine, raw_hash=raw_hash)
    if group is None:
        raise HTTPException(status_code=404, detail="candidate group not found")
    submissions = await get_candidate_submissions(ctx.engine, raw_hash=raw_hash)
    assignable = await get_assignable_batches(ctx.engine)
    return templates.TemplateResponse(
        "admin/candidates/detail.html",
        {
            "request": request,
            "user": ctx.user,
            "active_nav": "candidates",
            "css_version": get_css_version(),
            "group": group,
            "submissions": submissions,
            "assignable_batches": assignable,
        },
    )
```

- [ ] **Step 4: Update status-chip set in `index.html`**

Replace the status select options list:

```html
            {% for opt, label in [('new', 'New + Mixed'), ('assigned', 'Assigned'), ('rejected', 'Rejected'), ('all', 'All')] %}
```

Add a "Batches" `<th>` between "Status" and "Failure":

```html
                <th class="px-3 py-2">Batches</th>
```

- [ ] **Step 5: Update `_rows.html` to show batch pills**

Find the file, add a cell between Status and Failure:

```html
<td class="px-3 py-2">
    {% for slug in (r.batch_slugs or []) %}
    <a href="/admin/batches/{{ slug }}" class="inline-block bg-gray-200 dark:bg-gray-700 text-gray-800 dark:text-gray-200 text-xs rounded px-2 py-0.5 mr-1">{{ slug }}</a>
    {% else %}<span class="text-gray-400 text-xs">—</span>{% endfor %}
</td>
```

(If this file does not exist at the exact name, inspect `admin/candidates/_rows.html` and apply the same insertion in the row `<tr>` template.)

- [ ] **Step 6: Update `detail.html`**

Replace the submission card body (inside the `{% for s in submissions %}` loop) with:

```html
    <div class="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded p-3 text-sm">
        <div class="flex items-center gap-3 mb-2 flex-wrap">
            <span class="text-xs text-gray-500">#{{ s.id }}</span>
            <span class="text-xs text-gray-500">{{ s.created_at.strftime('%Y-%m-%d %H:%M') if s.created_at else '' }}</span>
            <span class="text-xs font-medium text-gray-700 dark:text-gray-300" title="{{ s.failure_reason or '' }}">{{ s.failure_type }}</span>
            <span class="text-xs text-gray-500">status: {{ s.status }}</span>
            {% if s.endpoint %}<span class="text-xs text-gray-500">endpoint: <span class="font-mono">{{ s.endpoint }}</span></span>{% endif %}
            {% if s.api_version %}<span class="text-xs text-gray-500">v{{ s.api_version }}</span>{% endif %}
            {% if s.provider %}<span class="text-xs text-gray-500">provider: {{ s.provider }}</span>{% endif %}
        </div>
        {% if s.failure_reason %}
        <div class="text-xs text-gray-600 dark:text-gray-400 mb-2 italic">{{ s.failure_reason }}</div>
        {% endif %}
        <details class="text-xs">
            <summary class="cursor-pointer text-co-purple">parsed_tokens</summary>
            <pre class="mt-2 p-2 bg-gray-50 dark:bg-gray-900 rounded overflow-x-auto">{{ s.parsed_tokens | tojson(indent=2) }}</pre>
        </details>
        {% if s.recovered_components %}
        <details class="text-xs mt-2">
            <summary class="cursor-pointer text-co-purple">recovered_components</summary>
            <pre class="mt-2 p-2 bg-gray-50 dark:bg-gray-900 rounded overflow-x-auto">{{ s.recovered_components | tojson(indent=2) }}</pre>
        </details>
        {% endif %}
    </div>
```

Add a Batches panel before the Submissions heading (insert after the Notes block):

```html
<div class="mb-6">
    <div class="text-xs text-gray-500 uppercase mb-2">Batches</div>
    <div class="flex flex-wrap gap-2 items-center mb-2">
        {% for slug in (group.batch_slugs or []) %}
        <span class="inline-flex items-center bg-gray-200 dark:bg-gray-700 text-gray-800 dark:text-gray-200 text-xs rounded px-2 py-0.5">
            <a href="/admin/batches/{{ slug }}" class="hover:underline">{{ slug }}</a>
            <form method="post" action="/admin/candidates/{{ group.raw_hash }}/batches/{{ slug }}/unassign" class="ml-2">
                <button class="text-xs text-red-600 hover:text-red-800" title="Remove from this batch">&times;</button>
            </form>
        </span>
        {% else %}<span class="text-gray-400 text-xs">not assigned to any batch</span>{% endfor %}
    </div>
    <form method="post" action="/admin/candidates/{{ group.raw_hash }}/batches" class="flex gap-2 items-center">
        <select name="batch_id" required class="border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 rounded px-2 py-1 text-sm">
            <option value="">Assign to batch…</option>
            {% for b in assignable_batches %}
            <option value="{{ b.id }}">{{ b.slug }} ({{ b.status }})</option>
            {% endfor %}
        </select>
        <button class="bg-co-purple text-white px-3 py-1 rounded text-sm font-medium">Assign</button>
    </form>
</div>
```

- [ ] **Step 7: Run — expect pass**

```bash
uv run pytest tests/unit/test_admin_candidates_views.py -v
```

- [ ] **Step 8: Commit**

```bash
git add src/address_validator/routers/admin/candidates.py \
  src/address_validator/templates/admin/candidates/ \
  tests/unit/test_admin_candidates_views.py
git commit -m "#103 feat: assign/unassign candidates to batches + surface submission context"
```

---

## Task 12: Training-skill CLI integration

**Files:**
- Modify: `scripts/model/identify.py` (add `--batch` / `--create-batch`)
- Modify: `scripts/model/train.py` (advance_step on success)
- Modify: `scripts/model/deploy.py` (advance_step + transition to `deployed`)
- Modify: `scripts/model/performance.py` (transition to `observing`)
- Modify: `scripts/model/contribute.py` (transition to `closed`, set `upstream_pr`)
- Modify: `.claude/skills/train-model/SKILL.md` (reflect batch language + new flags)

**Required cleanup passes (do these FIRST in this task):**

1. **Rename `--session-dir` CLI flag → `--batch-dir` in all of `scripts/model/*.py`** and every `SKILL.md` example. Search:
   ```bash
   grep -rn "session-dir\|session_dir" scripts/model/ .claude/skills/train-model/
   ```
   Update call sites in SKILL.md examples and any Python string literals. Keep argparse `dest=` aligned so `args.batch_dir` replaces `args.session_dir`.

2. **Rename `session_dir` key in `manifest.json`** writes. In `scripts/model/train.py` (or wherever the manifest is composed), rename the JSON key `session_dir` → `batch_dir` on new writes. Also patch the existing file:
   ```bash
   python -c "
   import json, pathlib
   p = pathlib.Path('training/batches/2026_03_28-multi_unit/manifest.json')
   d = json.loads(p.read_text())
   if 'session_dir' in d:
       d['batch_dir'] = d.pop('session_dir')
   p.write_text(json.dumps(d, indent=2) + '\n')
   "
   ```
   Any reader (`performance.py`, `contribute.py`) that consumes this key must be updated in lock-step.

3. **Scrub user-facing "session" strings** from `scripts/model/*.py` — help text, log messages, print statements, docstrings. Example:
   ```bash
   grep -rn "session" scripts/model/ | grep -v "\.pyc"
   ```
   Replace training-context uses with "batch". Keep unrelated uses (e.g. HTTP `session`, pytest `session`) intact.

4. After the three cleanups, run the baseline suite: `uv run pytest --no-cov -x scripts/` (or a targeted `scripts/model` test if one exists). This isolates the rename from the lifecycle wiring below.

- [ ] **Step 1: Read current identify.py**

```bash
grep -n "argparse\|add_argument\|async def" scripts/model/identify.py | head -30
```

Use this to locate the right insertion point for the new flags.

- [ ] **Step 2: Add flags to identify.py**

In `scripts/model/identify.py`, add to the `export` subparser:

```python
    export.add_argument("--batch", help="slug of an existing batch to assign exported candidates to")
    export.add_argument(
        "--create-batch",
        metavar="SLUG",
        help="create a new planned batch with this slug and assign exported candidates to it",
    )
    export.add_argument(
        "--batch-description",
        help="description for --create-batch (required when --create-batch is used)",
    )
```

In the `export` command handler (after the CSV is written and the `raw_address_hash` values are known), add:

```python
from address_validator.services.training_batches import (
    assign_candidates,
    create_batch,
)

if args.create_batch:
    if not args.batch_description:
        parser.error("--create-batch requires --batch-description")
    batch_id = await create_batch(
        engine,
        slug=args.create_batch,
        description=args.batch_description,
    )
elif args.batch:
    import sqlalchemy as sa
    from address_validator.db.tables import training_batches as tb
    async with engine.connect() as conn:
        row = (await conn.execute(
            sa.select(tb.c.id).where(tb.c.slug == args.batch)
        )).first()
    if row is None:
        parser.error(f"unknown batch slug: {args.batch}")
    batch_id = row.id
else:
    batch_id = None

if batch_id:
    await assign_candidates(
        engine,
        batch_id=batch_id,
        raw_address_hashes=list(hashes),  # collected during CSV export
        assigned_by="scripts/model/identify.py",
    )
```

The collection of `hashes` may already happen in the export loop — add the list if not.

- [ ] **Step 3: Hook `advance_step` into the other scripts**

In each of `scripts/model/train.py`, `deploy.py`, `performance.py`, `contribute.py`, accept a `--batch <slug>` arg and, on successful completion, call the appropriate transition:

| Script | On success call |
|---|---|
| `train.py` | `advance_step(engine, batch_id=..., step="training")` |
| `deploy.py` | `transition_status(engine, batch_id=..., target="deployed")` then `advance_step(step="deployed")` |
| `performance.py` (report subcommand) | `transition_status(engine, batch_id=..., target="observing")` + `advance_step(step="observing")` |
| `contribute.py` (upstream stage success) | `transition_status(engine, batch_id=..., target="closed")` + update `upstream_pr` column |

Each script resolves `batch_id` by slug via a small helper in `address_validator.services.training_batches`:

Add to `training_batches.py`:

```python
async def get_batch_id_by_slug(engine: AsyncEngine, *, slug: str) -> str | None:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                sa.select(training_batches.c.id).where(training_batches.c.slug == slug)
            )
        ).first()
    return row.id if row else None


async def record_upstream_pr(engine: AsyncEngine, *, batch_id: str, upstream_pr: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(
            sa.update(training_batches)
            .where(training_batches.c.id == batch_id)
            .values(upstream_pr=upstream_pr)
        )
```

- [ ] **Step 4: Update SKILL.md**

Edit `.claude/skills/train-model/SKILL.md`:

- Replace the "Session directory structure" heading with "Batch directory structure".
- Replace `training/sessions/` with `training/batches/` everywhere.
- Under Step 1 (IDENTIFY), add after the export example:

```bash
uv run python scripts/model/identify.py export --type <failure_type> \
  --create-batch <YYYY_MM_DD-slug> \
  --batch-description "<description>" \
  --out training/batches/<YYYY_MM_DD-slug>/candidates.csv
```

> Creating the batch eagerly means any candidate-group triaged before the
> batch existed can be assigned to it from the admin UI. Use `--batch <slug>`
> to reuse an existing `planned` or `active` batch.

- Under Step 3 (TRAIN), Step 5 (DEPLOY), Step 6 (OBSERVE), Step 7 (CONTRIBUTE): add `--batch <slug>` to the example command and note that the script advances the batch lifecycle automatically.

- [ ] **Step 5: Manual smoke test**

```bash
source /etc/address-validator/.env
uv run python scripts/model/identify.py export --type repeated_label_error \
  --create-batch "$(date +%Y_%m_%d)-smoke" \
  --batch-description "smoke test" \
  --out /tmp/smoke-candidates.csv || true

psql "$VALIDATION_CACHE_DSN" -c "SELECT slug, status FROM training_batches ORDER BY created_at DESC LIMIT 3"
```

Expected: the new `-smoke` row appears, `status` is `active` if any candidates were exported (auto-activated by `assign_candidates`) or `planned` otherwise.

Clean up: delete the smoke batch via `psql`.

- [ ] **Step 6: Commit**

```bash
git add scripts/model/ src/address_validator/services/training_batches.py .claude/skills/train-model/SKILL.md
git commit -m "#103 feat: wire training-batch lifecycle into scripts/model + skill"
```

---

## Task 13: Nav + final wiring

**Files:**
- Modify: `src/address_validator/templates/admin/base.html` (add Batches nav link)
- Modify: `src/address_validator/routers/admin/partials.py` (optional: add a "planned batches" badge — YAGNI check first)

- [ ] **Step 1: Add Batches nav link**

```bash
grep -n "candidates" src/address_validator/templates/admin/base.html
```

Find the `<nav>` where Dashboard/Audit/Endpoints/Providers/Candidates live, and add a Batches entry **after Candidates**. Use the same pattern as existing links, with `active_nav == "batches"` highlight.

- [ ] **Step 2: Visual check**

```bash
lsof -ti:8001 | xargs kill 2>/dev/null; sleep 1
uv run uvicorn address_validator.main:app --port 8001 --reload &
sleep 3
curl -s -H "X-ExeDev-UserID: test" -H "X-ExeDev-Email: test@exe.dev" \
  http://localhost:8001/admin/batches/ | grep -c "Training Batches"
```

Expected: `1`.

- [ ] **Step 3: Run full suite**

```bash
uv run pytest --no-cov -x
uv run ruff check .
uv run ruff format --check .
```

Expected: all pass; coverage ≥ 80%.

- [ ] **Step 4: Commit**

```bash
git add src/address_validator/templates/admin/base.html
git commit -m "#103 feat: add Batches entry to admin nav"
```

---

## Task 14: AGENTS.md + docs updates

**Files:**
- Modify: `AGENTS.md` — add `training_batches` to Architecture tree + Sensitive Areas
- Modify: `docs/plans/2026-04-14-training-batches-design.md` — add "Implementation complete" footer link to issue #103 (optional)

- [ ] **Step 1: Extend AGENTS.md**

In `AGENTS.md`, under the Architecture tree, add:

```
services/training_batches.py  state machine (ALLOWED_TRANSITIONS) + CRUD; create_batch/transition_status/advance_step/assign_candidates/unassign_candidates; admin routes AND scripts/model/*.py call through this for lifecycle transitions
routers/admin/batches.py      GET /admin/batches/, /admin/batches/{slug}; POST /admin/batches/ (plan new); POST /admin/batches/{slug}/status (transition)
routers/admin/queries/batches.py  list_batches, get_batch_by_slug, get_assignable_batches, get_batch_candidates
```

Add to the Sensitive Areas table:

```
| `src/address_validator/services/training_batches.py` | `ALLOWED_TRANSITIONS` is the single source of truth for batch status moves; assign_candidates side-effects flip row status to 'assigned' and auto-activate a planned batch on first assignment; unassign_candidates reverts row status to 'new' only when the group has zero remaining assignments. |
| `alembic/versions/013_training_batches.py` | Seeds the pre-existing multi_unit batch with a hard-coded ULID ('01KMV1103Q0000000000000000'); downgrade deletes assignments + batches and restores the old status CHECK (reviewed). |
```

Also update the "Key conventions" area to mention that triage-list nav badge counts `new` groups (unchanged) and that `assigned` status is derived, not admin-settable.

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "#103 docs: document training_batches in AGENTS.md"
```

---

## Task 15: PR

- [ ] **Step 1: Push branch and open PR**

```bash
git push -u origin HEAD
gh pr create --title "#103 Training batches: lifecycle + candidate assignment" \
  --body "$(cat <<'EOF'
## Summary

Closes #103. Builds on #102. See `docs/plans/2026-04-14-training-batches-design.md`.

- Renames `training/sessions/` → `training/batches/` and updates the `/train-model` skill accordingly.
- Adds `training_batches` (ULID PK) + `candidate_batch_assignments` (M:N) tables via Alembic 013; seeds the pre-existing multi_unit batch.
- Candidate status: `reviewed` dropped, `assigned` added; `assigned` is derived (status='new' + ≥1 assignment) and admin routes cannot set it directly.
- Denormalises `endpoint`, `provider`, `api_version`, `failure_reason` onto `model_training_candidates` for triage-detail context.
- New `services/training_batches.py` owns the state machine (`ALLOWED_TRANSITIONS`) and all lifecycle writes; admin routes AND `scripts/model/*.py` call through it.
- `/admin/batches/` list + detail + plan-new + transition-status UI.
- `/admin/candidates/` gains Batches column; detail screen gains submission-context line + assign/unassign panel.

## Test plan

- [ ] `uv run pytest --no-cov -x`
- [ ] `uv run ruff check .` clean
- [ ] Manual: `/admin/batches/` lists seeded `multi_unit` + any planned batches
- [ ] Manual: assign a candidate group to a planned batch; verify status flips to `active` and row becomes `assigned`
- [ ] Manual: unassign the last batch; verify row reverts to `new`
- [ ] Manual: `scripts/model/identify.py --create-batch smoke` creates a row
EOF
)"
```

- [ ] **Step 2: Report PR URL to user**

---

## Self-Review Notes

- **Spec coverage:** every section of the design doc maps to tasks — nomenclature (Task 1, 12, 14), data model (Tasks 2-3, 6), admin UX (Tasks 10-11, 13), services (Tasks 4-5), parser/middleware plumbing (Task 7), queries (Tasks 8-9), skill integration (Task 12), docs (Task 14). ULID seed value `01KMV1103Q0000000000000000` has a real 2026-03-28 timestamp prefix (decoded from `created_at=2026-03-28T20:09:04.375Z`) with a zero-padded random suffix — deterministic and timestamp-sortable alongside future real ULIDs.
- **Mid-plan test-suite breakage:** between Task 2 (migration drops `reviewed` from the candidate status CHECK) and Task 11 (templates + tests updated to use `assigned`), existing tests at `tests/unit/test_admin_candidates_queries.py` and `tests/unit/test_admin_candidates_views.py` that insert `status='reviewed'` fail with an IntegrityError. This is expected mid-plan; intermediate commits are not independently greenable. CI should only run against the final branch state; the branch is designed to land as a single squash/merge unit once Task 11 is green.
- **Type consistency:** `assign_candidates` / `unassign_candidates` / `create_batch` / `transition_status` / `advance_step` / `get_batch_id_by_slug` / `record_upstream_pr` — these names are used consistently across Tasks 4, 5, 9, 10, 11, 12.
- **Batch-slug display:** `batch_slugs` is the column added by Task 8 and consumed by Tasks 11 (row template) and 13 (detail template). Consistent.
- **Admin-settable statuses:** `WRITE_STATUSES` narrowed to `{'new', 'rejected'}` in Task 8; `assigned` and `labeled` not directly settable — matches design doc.
