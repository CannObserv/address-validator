# Admin Candidate Triage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an admin surface at `/admin/candidates/` for coarse triage of `model_training_candidates` — browse by `raw_address` group, mark `reviewed`/`rejected`, edit notes, drill into per-submission detail showing `parsed_tokens` and `recovered_components`.

**Architecture:** New sub-router under the existing admin dashboard (FastAPI + Jinja2 + HTMX), backed by a SQLAlchemy-Core query module. One new Alembic migration adds `pgcrypto` and a generated `raw_address_hash` column for stable URL slugs. The `model_training_candidates.status` CHECK constraint (`new | reviewed | labeled | rejected`) is already in place; this plan starts writing to it. Rows with `status='labeled'` are excluded from all triage queries.

**Tech Stack:** FastAPI, SQLAlchemy Core (async), Alembic, Jinja2, HTMX, pytest (async), PostgreSQL (pgcrypto), Tailwind CSS.

**Design doc:** [docs/plans/2026-04-13-admin-candidate-triage-design.md](2026-04-13-admin-candidate-triage-design.md)
**GitHub issue:** #102

---

## File Structure

### New files

| Path | Responsibility |
|---|---|
| `alembic/versions/012_candidate_raw_address_hash.py` | Install pgcrypto; add generated `raw_address_hash` column + index to `model_training_candidates`. |
| `src/address_validator/routers/admin/candidates.py` | Router — list, detail, status POST, notes POST handlers. |
| `src/address_validator/routers/admin/queries/candidates.py` | SQLAlchemy Core query helpers (grouped list, group summary, per-group submissions, update status, update notes). |
| `src/address_validator/templates/admin/candidates/index.html` | Grouped list view with filter bar and pagination. |
| `src/address_validator/templates/admin/candidates/detail.html` | Detail view with summary card, notes textarea, submissions table. |
| `src/address_validator/templates/admin/candidates/_rows.html` | HTMX partial — list of grouped rows. |
| `src/address_validator/templates/admin/candidates/_status.html` | HTMX partial — one row's status cell + action buttons. |
| `src/address_validator/templates/admin/candidates/_notes.html` | HTMX partial — notes inline editor. |
| `tests/unit/test_admin_candidates_queries.py` | Query-layer tests (rollup, filters, labeled exclusion, UPDATE helpers). |
| `tests/unit/test_admin_candidates_views.py` | Router tests (auth, list, detail 404, HTMX actions, notes round-trip). |

### Modified files

| Path | Change |
|---|---|
| `src/address_validator/db/tables.py` | Add `sa.Column("raw_address_hash", sa.Text(), nullable=False)` to `model_training_candidates` Table. |
| `src/address_validator/routers/admin/_config.py` | Add `CANDIDATE_STATUS_META` dict and register as Jinja2 global `cs_meta`. |
| `src/address_validator/routers/admin/queries/__init__.py` | Re-export new query helpers. |
| `src/address_validator/routers/admin/router.py` | Include `candidates_router`. |
| `src/address_validator/templates/admin/base.html` | Add `("candidates", "/admin/candidates/", "Candidates", none)` nav entry. |

---

## Task 1: Alembic migration — pgcrypto + generated hash column

**Files:**
- Create: `alembic/versions/012_candidate_raw_address_hash.py`

- [ ] **Step 1: Write the migration**

Create `alembic/versions/012_candidate_raw_address_hash.py`:

```python
"""Add raw_address_hash generated column + pgcrypto extension.

Revision ID: 012
Revises: 011
Create Date: 2026-04-13

The admin candidate triage surface needs a stable URL slug per raw_address.
sha256 hex reuses the hashing convention established by cache_provider
(_make_pattern_key). pgcrypto provides sha256() in SQL; the column is
GENERATED STORED so it stays in lock-step with raw_address without app code.
"""

revision: str = "012"
down_revision: str = "011"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(
        "ALTER TABLE model_training_candidates "
        "ADD COLUMN raw_address_hash TEXT "
        "GENERATED ALWAYS AS (encode(sha256(raw_address::bytea), 'hex')) STORED"
    )
    op.execute(
        "ALTER TABLE model_training_candidates "
        "ALTER COLUMN raw_address_hash SET NOT NULL"
    )
    op.create_index(
        "ix_model_training_candidates_raw_address_hash",
        "model_training_candidates",
        ["raw_address_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_model_training_candidates_raw_address_hash")
    op.execute("ALTER TABLE model_training_candidates DROP COLUMN raw_address_hash")
    # Leave pgcrypto installed — harmless and may be used by other future work.
```

- [ ] **Step 2: Apply migration to test DB and verify**

Run:
```bash
uv run alembic -c alembic.ini upgrade head
```
Then verify the column exists:
```bash
PGPASSWORD=address_validator_dev psql -h localhost -U address_validator -d address_validator -c "\d model_training_candidates" | grep raw_address_hash
```
Expected: row like `raw_address_hash | text | not null | generated always as (encode(sha256((raw_address)::bytea), 'hex'::text)) stored`.

- [ ] **Step 3: Smoke-test the downgrade/upgrade round-trip**

Run:
```bash
uv run alembic downgrade -1 && uv run alembic upgrade head
```
Expected: both commands exit 0.

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/012_candidate_raw_address_hash.py
git commit -m "#102 feat: add raw_address_hash generated column for candidate triage"
```

---

## Task 2: Update Table definition for `raw_address_hash`

**Files:**
- Modify: `src/address_validator/db/tables.py` (the `model_training_candidates` Table definition around line 112)

- [ ] **Step 1: Add the column to the Table**

In `src/address_validator/db/tables.py`, add `raw_address_hash` to `model_training_candidates` — insert after the `raw_address` column. The column is generated, so from the app's perspective it's read-only; SQLAlchemy Core just needs to know it exists so queries can select on it.

```python
sa.Column("raw_address_hash", sa.Text(), nullable=False),
```

Full Table definition for reference (place the new column on line 117 just below `raw_address`):

```python
model_training_candidates = sa.Table(
    "model_training_candidates",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("raw_address", sa.Text(), nullable=False),
    sa.Column("raw_address_hash", sa.Text(), nullable=False),
    sa.Column("failure_type", sa.Text(), nullable=False),
    sa.Column("parsed_tokens", JSONB(), nullable=False),
    sa.Column("recovered_components", JSONB(), nullable=True),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
    sa.Column(
        "status",
        sa.Text(),
        sa.CheckConstraint(
            "status IN ('new', 'reviewed', 'labeled', 'rejected')",
            name="ck_model_training_candidates_status",
        ),
        nullable=False,
        server_default=sa.text("'new'"),
    ),
    sa.Column("notes", sa.Text(), nullable=True),
)
```

- [ ] **Step 2: Run the test suite to confirm nothing regresses**

Run:
```bash
uv run pytest tests/unit -x --no-cov
```
Expected: all passing (the column addition is purely additive; existing inserts using `raw_address` will get the generated value).

- [ ] **Step 3: Commit**

```bash
git add src/address_validator/db/tables.py
git commit -m "#102 feat: add raw_address_hash to model_training_candidates Table"
```

---

## Task 3: Candidate status metadata in `_config.py`

**Files:**
- Modify: `src/address_validator/routers/admin/_config.py`

- [ ] **Step 1: Add `CANDIDATE_STATUS_META` dict and register as Jinja global**

Append to `src/address_validator/routers/admin/_config.py` after the `VS_META` registration:

```python
# Candidate-triage status display metadata — exposed to templates as "cs_meta".
CANDIDATE_STATUS_META: dict[str, dict[str, str]] = {
    "new": {"symbol": "\u25cf", "label": "New", "color": "blue"},
    "reviewed": {"symbol": "\u2713", "label": "Reviewed", "color": "green"},
    "rejected": {"symbol": "\u2717", "label": "Rejected", "color": "gray"},
    "mixed": {"symbol": "~", "label": "Mixed", "color": "amber"},
}

templates.env.globals["cs_meta"] = CANDIDATE_STATUS_META
```

- [ ] **Step 2: Commit**

```bash
git add src/address_validator/routers/admin/_config.py
git commit -m "#102 feat: add CANDIDATE_STATUS_META to admin config"
```

---

## Task 4: Query helpers — grouped list + group summary

**Files:**
- Create: `src/address_validator/routers/admin/queries/candidates.py`
- Modify: `src/address_validator/routers/admin/queries/__init__.py`
- Test: `tests/unit/test_admin_candidates_queries.py`

- [ ] **Step 1: Write failing tests for `get_candidate_groups` and `get_candidate_group`**

Create `tests/unit/test_admin_candidates_queries.py`:

```python
"""Tests for admin candidate-triage SQL query helpers."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from address_validator.db.tables import model_training_candidates
from address_validator.routers.admin.queries.candidates import (
    get_candidate_group,
    get_candidate_groups,
    get_candidate_submissions,
    update_candidate_notes,
    update_candidate_status,
)


def _hex(s: str) -> str:
    """Compute sha256 hex of `s` the way the DB's generated column does."""
    import hashlib

    return hashlib.sha256(s.encode("utf-8")).hexdigest()


async def _seed(engine: AsyncEngine) -> None:
    """Seed three distinct raw_address groups with varying statuses."""
    now = datetime.now(UTC)
    rows = [
        # Group A: two `new` rows — rolls up to `new`
        {"raw": "addr A", "ft": "repeated_label_error", "status": "new", "ts": now - timedelta(days=1)},
        {"raw": "addr A", "ft": "repeated_label_error", "status": "new", "ts": now},
        # Group B: one `new` + one `reviewed` — rolls up to `mixed`
        {"raw": "addr B", "ft": "post_parse_recovery", "status": "new", "ts": now - timedelta(days=2)},
        {"raw": "addr B", "ft": "post_parse_recovery", "status": "reviewed", "ts": now - timedelta(hours=1)},
        # Group C: one `reviewed` row — rolls up to `reviewed`
        {"raw": "addr C", "ft": "repeated_label_error", "status": "reviewed", "ts": now - timedelta(days=3)},
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
    groups, total = await get_candidate_groups(seeded_db, status=None, failure_type=None,
                                               since=None, until=None, limit=50, offset=0)
    by_raw = {g["raw_address"]: g for g in groups}
    assert by_raw["addr A"]["rollup_status"] == "new"
    assert by_raw["addr A"]["count"] == 2
    assert by_raw["addr B"]["rollup_status"] == "mixed"
    assert by_raw["addr B"]["count"] == 2
    assert by_raw["addr C"]["rollup_status"] == "reviewed"
    assert by_raw["addr C"]["count"] == 1
    # labeled group D is excluded from triage
    assert "addr D" not in by_raw
    assert total == 3


async def test_get_candidate_groups_filter_new_includes_mixed(seeded_db: AsyncEngine) -> None:
    # status='new' filter must include `new` rollups AND `mixed` rollups
    # (a mixed group has at least one `new` row — still something to triage).
    groups, _ = await get_candidate_groups(seeded_db, status="new", failure_type=None,
                                           since=None, until=None, limit=50, offset=0)
    raws = {g["raw_address"] for g in groups}
    assert raws == {"addr A", "addr B"}


async def test_get_candidate_groups_filter_failure_type(seeded_db: AsyncEngine) -> None:
    groups, _ = await get_candidate_groups(seeded_db, status=None,
                                           failure_type="post_parse_recovery",
                                           since=None, until=None, limit=50, offset=0)
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
```

Note: the `db` fixture comes from `tests/unit/conftest.py` (re-exported from `tests/unit/validation/conftest.py`). It truncates the usual cache tables but not `model_training_candidates`, so the `seeded_db` fixture truncates and seeds explicitly.

- [ ] **Step 2: Run tests — confirm they fail with ImportError**

Run:
```bash
uv run pytest tests/unit/test_admin_candidates_queries.py -x --no-cov
```
Expected: FAIL — `ModuleNotFoundError: address_validator.routers.admin.queries.candidates`.

- [ ] **Step 3: Implement `get_candidate_groups` and `get_candidate_group`**

Create `src/address_validator/routers/admin/queries/candidates.py`:

```python
"""Admin candidate-triage query helpers.

Grouping convention: a "group" is a set of model_training_candidates rows
sharing the same raw_address. Rows with status='labeled' are excluded from
the triage view entirely — once a submission has been included in training
data, it is considered done.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import sqlalchemy as sa

from address_validator.db.tables import model_training_candidates as mtc

if TYPE_CHECKING:
    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncEngine


_NON_LABELED = mtc.c.status != "labeled"


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
        # "new" filter includes mixed groups (they still need triage).
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

    group_stmt = (
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
        .where(*where)
        .group_by(mtc.c.raw_address, mtc.c.raw_address_hash)
    )
    status_filter = _status_filter(rollup, status)
    if status_filter is not None:
        group_stmt = group_stmt.having(status_filter)

    # Total count — wrap the grouped query as a subquery and count rows.
    count_stmt = sa.select(sa.func.count()).select_from(group_stmt.subquery())

    list_stmt = (
        group_stmt.order_by(sa.desc("last_seen"))
        .limit(limit)
        .offset(offset)
    )

    async with engine.connect() as conn:
        total = (await conn.execute(count_stmt)).scalar() or 0
        rows = [dict(r._mapping) for r in (await conn.execute(list_stmt))]  # noqa: SLF001
    return rows, total


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


async def get_candidate_submissions(
    engine: AsyncEngine, *, raw_hash: str
) -> list[dict]:
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


async def update_candidate_status(
    engine: AsyncEngine, *, raw_hash: str, status: str
) -> int:
    """Set status on every non-labeled row in the group. Returns rowcount."""
    if status not in {"new", "reviewed", "rejected"}:
        raise ValueError(f"invalid status: {status!r}")
    stmt = (
        sa.update(mtc)
        .where(_NON_LABELED, mtc.c.raw_address_hash == raw_hash)
        .values(status=status)
    )
    async with engine.begin() as conn:
        result = await conn.execute(stmt)
    return result.rowcount or 0


async def update_candidate_notes(
    engine: AsyncEngine, *, raw_hash: str, notes: str | None
) -> int:
    """Set notes on every non-labeled row in the group. Returns rowcount."""
    normalized = notes if notes else None
    stmt = (
        sa.update(mtc)
        .where(_NON_LABELED, mtc.c.raw_address_hash == raw_hash)
        .values(notes=normalized)
    )
    async with engine.begin() as conn:
        result = await conn.execute(stmt)
    return result.rowcount or 0
```

- [ ] **Step 4: Re-export from `queries/__init__.py`**

Update `src/address_validator/routers/admin/queries/__init__.py`:

```python
"""Admin dashboard query helpers — backward-compatible re-exports."""

from address_validator.routers.admin.queries.audit import get_audit_rows
from address_validator.routers.admin.queries.candidates import (
    get_candidate_group,
    get_candidate_groups,
    get_candidate_submissions,
    update_candidate_notes,
    update_candidate_status,
)
from address_validator.routers.admin.queries.dashboard import (
    get_dashboard_stats,
    get_sparkline_data,
)
from address_validator.routers.admin.queries.endpoint import get_endpoint_stats
from address_validator.routers.admin.queries.provider import (
    get_provider_daily_usage,
    get_provider_stats,
)

__all__ = [
    "get_audit_rows",
    "get_candidate_group",
    "get_candidate_groups",
    "get_candidate_submissions",
    "get_dashboard_stats",
    "get_endpoint_stats",
    "get_provider_daily_usage",
    "get_provider_stats",
    "get_sparkline_data",
    "update_candidate_notes",
    "update_candidate_status",
]
```

- [ ] **Step 5: Run tests — confirm they pass**

Run:
```bash
uv run pytest tests/unit/test_admin_candidates_queries.py -x --no-cov
```
Expected: the 6 tests from Step 1 pass (tests that reference `update_*` will still pass because they aren't defined yet — Task 5 adds them).

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/routers/admin/queries/candidates.py \
        src/address_validator/routers/admin/queries/__init__.py \
        tests/unit/test_admin_candidates_queries.py
git commit -m "#102 feat: add candidate-triage query helpers (grouped list + detail)"
```

---

## Task 5: Query helpers — submissions + update status/notes

**Files:**
- Test: `tests/unit/test_admin_candidates_queries.py` (append)

(The implementation was already written in Task 4; this task adds tests for the write path and submissions helper.)

- [ ] **Step 1: Append failing tests**

Append to `tests/unit/test_admin_candidates_queries.py`:

```python
async def test_get_candidate_submissions_returns_rows(seeded_db: AsyncEngine) -> None:
    h = _hex("addr A")
    rows = await get_candidate_submissions(seeded_db, raw_hash=h)
    assert len(rows) == 2
    # Newest first
    assert rows[0]["created_at"] >= rows[1]["created_at"]
    assert all(r["status"] != "labeled" for r in rows)


async def test_update_candidate_status_applies_to_group(seeded_db: AsyncEngine) -> None:
    h = _hex("addr A")
    n = await update_candidate_status(seeded_db, raw_hash=h, status="reviewed")
    assert n == 2
    rows = await get_candidate_submissions(seeded_db, raw_hash=h)
    assert all(r["status"] == "reviewed" for r in rows)


async def test_update_candidate_status_skips_labeled(seeded_db: AsyncEngine) -> None:
    # Try to touch addr D (labeled only) — rowcount must be 0.
    h = _hex("addr D")
    n = await update_candidate_status(seeded_db, raw_hash=h, status="reviewed")
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
```

- [ ] **Step 2: Run tests — confirm they pass**

Run:
```bash
uv run pytest tests/unit/test_admin_candidates_queries.py -x --no-cov
```
Expected: all ~12 tests pass.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_admin_candidates_queries.py
git commit -m "#102 test: cover candidate-triage write helpers and submissions"
```

---

## Task 6: Router — list view

**Files:**
- Create: `src/address_validator/routers/admin/candidates.py`
- Modify: `src/address_validator/routers/admin/router.py`
- Test: `tests/unit/test_admin_candidates_views.py`

- [ ] **Step 1: Write failing router tests (list view only)**

Create `tests/unit/test_admin_candidates_views.py`:

```python
"""Integration tests for admin candidate triage views."""

from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient


@pytest.fixture(autouse=True)
def _mock_engine(client: TestClient):
    """Set a fake engine on app.state so admin routes don't 503."""
    original = getattr(client.app.state, "engine", None)  # type: ignore[union-attr]
    client.app.state.engine = "fake-engine"  # type: ignore[union-attr]
    yield
    client.app.state.engine = original  # type: ignore[union-attr]


def test_candidates_list_requires_auth(client: TestClient) -> None:
    r = client.get("/admin/candidates/", follow_redirects=False)
    # Unauth raises AdminAuthRequired -> handled as a redirect by main.py
    assert r.status_code in (302, 307)


def test_candidates_list_renders(client: TestClient, admin_headers: dict) -> None:
    with (
        patch(
            "address_validator.routers.admin.candidates.get_candidate_groups",
            new=AsyncMock(return_value=([], 0)),
        ),
    ):
        r = client.get("/admin/candidates/", headers=admin_headers)
    assert r.status_code == 200
    assert "Candidates" in r.text


def test_candidates_list_filters_pass_through(client: TestClient, admin_headers: dict) -> None:
    mock = AsyncMock(return_value=([], 0))
    with patch(
        "address_validator.routers.admin.candidates.get_candidate_groups",
        new=mock,
    ):
        r = client.get(
            "/admin/candidates/?status=reviewed&failure_type=repeated_label_error&since=7d",
            headers=admin_headers,
        )
    assert r.status_code == 200
    kwargs = mock.call_args.kwargs
    assert kwargs["status"] == "reviewed"
    assert kwargs["failure_type"] == "repeated_label_error"
    # since=7d → parsed into a datetime roughly 7 days ago (non-None)
    assert kwargs["since"] is not None
```

- [ ] **Step 2: Run tests — confirm they fail**

Run:
```bash
uv run pytest tests/unit/test_admin_candidates_views.py -x --no-cov
```
Expected: FAIL — 404 or module missing.

- [ ] **Step 3: Create the router module (list endpoint only)**

Create `src/address_validator/routers/admin/candidates.py`:

```python
"""Admin candidate-triage views — browse, triage, and annotate training candidates."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from address_validator.routers.admin._config import get_css_version, templates
from address_validator.routers.admin.deps import AdminContext, get_admin_context
from address_validator.routers.admin.queries import (
    get_candidate_group,
    get_candidate_groups,
    get_candidate_submissions,
    update_candidate_notes,
    update_candidate_status,
)

router = APIRouter(prefix="/candidates")

_PER_PAGE = 50
_VALID_FAILURE_TYPES = {"repeated_label_error", "post_parse_recovery"}
_VALID_STATUSES = {"new", "reviewed", "rejected", "all"}
_VALID_WRITE_STATUSES = {"new", "reviewed", "rejected"}


def _parse_since(raw: str | None) -> datetime | None:
    """Parse a `--since` querystring: '7d', '30d', '90d', 'all', or ISO date."""
    if not raw or raw == "all":
        return None
    try:
        if raw.endswith("d"):
            return datetime.now(UTC) - timedelta(days=int(raw[:-1]))
        if raw.endswith("h"):
            return datetime.now(UTC) - timedelta(hours=int(raw[:-1]))
        return datetime.fromisoformat(raw).replace(tzinfo=UTC)
    except ValueError:
        return None


@router.get("/", response_class=HTMLResponse, response_model=None)
async def candidates_list(
    request: Request,
    page: int = Query(1, ge=1),
    status: str = Query("new"),
    failure_type: str | None = Query(None),
    since: str = Query("30d"),
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    # Normalise filters
    if status not in _VALID_STATUSES:
        status = "new"
    if failure_type not in (None, "") and failure_type not in _VALID_FAILURE_TYPES:
        failure_type = None
    if not failure_type:
        failure_type = None
    since_dt = _parse_since(since)

    query_status = None if status == "all" else status

    rows, total = await get_candidate_groups(
        ctx.engine,
        status=query_status,
        failure_type=failure_type,
        since=since_dt,
        until=None,
        limit=_PER_PAGE,
        offset=(page - 1) * _PER_PAGE,
    )
    total_pages = max(1, math.ceil(total / _PER_PAGE))
    filters = {"status": status, "failure_type": failure_type, "since": since}

    if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"):
        return templates.TemplateResponse(
            "admin/candidates/_rows.html",
            {"request": request, "rows": rows},
        )

    return templates.TemplateResponse(
        "admin/candidates/index.html",
        {
            "request": request,
            "user": ctx.user,
            "active_nav": "candidates",
            "css_version": get_css_version(),
            "rows": rows,
            "page": page,
            "total_pages": total_pages,
            "filters": filters,
        },
    )
```

Note: this references templates that are created in Task 9. Don't run the tests yet — wait until Task 9 is complete. Proceed to hook up the router.

- [ ] **Step 4: Include the new sub-router**

Modify `src/address_validator/routers/admin/router.py`:

```python
"""Top-level admin router — mounts all dashboard sub-routers."""

from fastapi import APIRouter

from address_validator.routers.admin.audit_views import router as audit_router
from address_validator.routers.admin.candidates import router as candidates_router
from address_validator.routers.admin.dashboard import router as dashboard_router
from address_validator.routers.admin.endpoints import router as endpoints_router
from address_validator.routers.admin.providers import router as providers_router

admin_router = APIRouter(prefix="/admin")
admin_router.include_router(dashboard_router)
admin_router.include_router(audit_router)
admin_router.include_router(candidates_router)
admin_router.include_router(endpoints_router)
admin_router.include_router(providers_router)
```

- [ ] **Step 5: Commit** (templates still missing — tests will pass in Task 9)

```bash
git add src/address_validator/routers/admin/candidates.py \
        src/address_validator/routers/admin/router.py \
        tests/unit/test_admin_candidates_views.py
git commit -m "#102 feat: add candidate triage list route (templates pending)"
```

---

## Task 7: Router — detail view

**Files:**
- Modify: `src/address_validator/routers/admin/candidates.py`
- Modify: `tests/unit/test_admin_candidates_views.py`

- [ ] **Step 1: Append failing detail-view tests**

Append to `tests/unit/test_admin_candidates_views.py`:

```python
def test_candidates_detail_404_on_unknown_hash(client: TestClient, admin_headers: dict) -> None:
    with patch(
        "address_validator.routers.admin.candidates.get_candidate_group",
        new=AsyncMock(return_value=None),
    ):
        r = client.get("/admin/candidates/deadbeef" + "0" * 56, headers=admin_headers)
    assert r.status_code == 404


def test_candidates_detail_renders(client: TestClient, admin_headers: dict) -> None:
    group_mock = AsyncMock(return_value={
        "raw_address": "123 MAIN ST STE 1, SMP - 2 SEATTLE WA 98101",
        "raw_hash": "a" * 64,
        "rollup_status": "new",
        "failure_types": ["repeated_label_error"],
        "count": 3,
        "first_seen": None,
        "last_seen": None,
        "notes": None,
    })
    subs_mock = AsyncMock(return_value=[
        {"id": 1, "raw_address": "x", "failure_type": "repeated_label_error",
         "parsed_tokens": [["STE", "OccupancyIdentifier"]], "recovered_components": None,
         "created_at": None, "status": "new"},
    ])
    with (
        patch("address_validator.routers.admin.candidates.get_candidate_group", new=group_mock),
        patch("address_validator.routers.admin.candidates.get_candidate_submissions", new=subs_mock),
    ):
        r = client.get("/admin/candidates/" + "a" * 64, headers=admin_headers)
    assert r.status_code == 200
    assert "123 MAIN ST STE 1" in r.text
```

- [ ] **Step 2: Add detail handler to the router module**

Append to `src/address_validator/routers/admin/candidates.py`:

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
    return templates.TemplateResponse(
        "admin/candidates/detail.html",
        {
            "request": request,
            "user": ctx.user,
            "active_nav": "candidates",
            "css_version": get_css_version(),
            "group": group,
            "submissions": submissions,
        },
    )
```

- [ ] **Step 3: Commit**

```bash
git add src/address_validator/routers/admin/candidates.py \
        tests/unit/test_admin_candidates_views.py
git commit -m "#102 feat: add candidate triage detail route"
```

---

## Task 8: Router — HTMX write endpoints (status + notes)

**Files:**
- Modify: `src/address_validator/routers/admin/candidates.py`
- Modify: `tests/unit/test_admin_candidates_views.py`

- [ ] **Step 1: Append failing tests for POST /status and /notes**

Append to `tests/unit/test_admin_candidates_views.py`:

```python
def test_candidates_status_post_updates_and_renders_partial(
    client: TestClient, admin_headers: dict
) -> None:
    update_mock = AsyncMock(return_value=2)
    group_mock = AsyncMock(return_value={
        "raw_address": "x", "raw_hash": "a" * 64, "rollup_status": "reviewed",
        "failure_types": [], "count": 2, "first_seen": None, "last_seen": None, "notes": None,
    })
    with (
        patch("address_validator.routers.admin.candidates.update_candidate_status", new=update_mock),
        patch("address_validator.routers.admin.candidates.get_candidate_group", new=group_mock),
    ):
        r = client.post(
            "/admin/candidates/" + "a" * 64 + "/status",
            headers={**admin_headers, "HX-Request": "true"},
            data={"status": "reviewed"},
        )
    assert r.status_code == 200
    update_mock.assert_awaited_once()
    kwargs = update_mock.call_args.kwargs
    assert kwargs["status"] == "reviewed"
    assert kwargs["raw_hash"] == "a" * 64


def test_candidates_status_post_rejects_invalid_status(
    client: TestClient, admin_headers: dict
) -> None:
    r = client.post(
        "/admin/candidates/" + "a" * 64 + "/status",
        headers={**admin_headers, "HX-Request": "true"},
        data={"status": "labeled"},
    )
    assert r.status_code == 400


def test_candidates_notes_post_round_trip(
    client: TestClient, admin_headers: dict
) -> None:
    update_mock = AsyncMock(return_value=1)
    group_mock = AsyncMock(return_value={
        "raw_address": "x", "raw_hash": "a" * 64, "rollup_status": "new",
        "failure_types": [], "count": 1, "first_seen": None, "last_seen": None,
        "notes": "chained STE",
    })
    with (
        patch("address_validator.routers.admin.candidates.update_candidate_notes", new=update_mock),
        patch("address_validator.routers.admin.candidates.get_candidate_group", new=group_mock),
    ):
        r = client.post(
            "/admin/candidates/" + "a" * 64 + "/notes",
            headers={**admin_headers, "HX-Request": "true"},
            data={"notes": "chained STE"},
        )
    assert r.status_code == 200
    assert "chained STE" in r.text
    update_mock.assert_awaited_once()
    assert update_mock.call_args.kwargs["notes"] == "chained STE"
```

- [ ] **Step 2: Implement POST /status and POST /notes**

Append to `src/address_validator/routers/admin/candidates.py`:

```python
@router.post("/{raw_hash}/status", response_class=HTMLResponse, response_model=None)
async def candidates_update_status(
    request: Request,
    raw_hash: str,
    status: str = Form(...),
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    if status not in _VALID_WRITE_STATUSES:
        raise HTTPException(status_code=400, detail=f"invalid status: {status}")
    await update_candidate_status(ctx.engine, raw_hash=raw_hash, status=status)
    group = await get_candidate_group(ctx.engine, raw_hash=raw_hash)
    if group is None:
        # All non-labeled rows evaporated between UPDATE and SELECT — unlikely
        # but possible. Return an empty fragment so HTMX replaces the cell.
        return HTMLResponse("", status_code=200)
    return templates.TemplateResponse(
        "admin/candidates/_status.html",
        {"request": request, "group": group},
    )


@router.post("/{raw_hash}/notes", response_class=HTMLResponse, response_model=None)
async def candidates_update_notes(
    request: Request,
    raw_hash: str,
    notes: str = Form(""),
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    await update_candidate_notes(ctx.engine, raw_hash=raw_hash, notes=notes or None)
    group = await get_candidate_group(ctx.engine, raw_hash=raw_hash)
    if group is None:
        return HTMLResponse("", status_code=200)
    return templates.TemplateResponse(
        "admin/candidates/_notes.html",
        {"request": request, "group": group},
    )
```

- [ ] **Step 3: Commit**

```bash
git add src/address_validator/routers/admin/candidates.py \
        tests/unit/test_admin_candidates_views.py
git commit -m "#102 feat: add candidate triage HTMX status and notes endpoints"
```

---

## Task 9: Templates — index, rows, status, notes, detail

**Files:**
- Create: `src/address_validator/templates/admin/candidates/index.html`
- Create: `src/address_validator/templates/admin/candidates/_rows.html`
- Create: `src/address_validator/templates/admin/candidates/_status.html`
- Create: `src/address_validator/templates/admin/candidates/_notes.html`
- Create: `src/address_validator/templates/admin/candidates/detail.html`

- [ ] **Step 1: Create `_status.html` partial**

Create `src/address_validator/templates/admin/candidates/_status.html`:

```jinja
{#- Renders one row's status cell and the Review/Reject/Reset action buttons. -#}
{#- Required context: `group` — dict with raw_hash, rollup_status. -#}
{% set meta = cs_meta.get(group.rollup_status, cs_meta["new"]) %}
<div id="status-{{ group.raw_hash }}" class="flex items-center gap-2">
    <span class="inline-flex items-center gap-1 {% if meta.color == 'green' %}text-green-700 dark:text-green-400{% elif meta.color == 'gray' %}text-gray-500 dark:text-gray-400{% elif meta.color == 'amber' %}text-amber-600 dark:text-amber-400{% else %}text-blue-600 dark:text-blue-400{% endif %}"
          title="{{ meta.label }}">
        {{ meta.symbol }} <span class="text-xs">{{ meta.label }}</span>
    </span>
    <div class="flex items-center gap-1 ml-2">
        {% set btn_cls = "px-2 py-0.5 text-xs rounded border focus:outline-none focus:ring-2 focus:ring-co-purple-700" %}
        <button class="{{ btn_cls }} border-green-300 dark:border-green-700 text-green-700 dark:text-green-400 hover:bg-green-50 dark:hover:bg-green-900/20"
                hx-post="/admin/candidates/{{ group.raw_hash }}/status"
                hx-vals='{"status": "reviewed"}'
                hx-target="#status-{{ group.raw_hash }}"
                hx-swap="outerHTML">Review</button>
        <button class="{{ btn_cls }} border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700"
                hx-post="/admin/candidates/{{ group.raw_hash }}/status"
                hx-vals='{"status": "rejected"}'
                hx-target="#status-{{ group.raw_hash }}"
                hx-swap="outerHTML">Reject</button>
        <button class="{{ btn_cls }} border-blue-300 dark:border-blue-700 text-blue-600 dark:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/20"
                hx-post="/admin/candidates/{{ group.raw_hash }}/status"
                hx-vals='{"status": "new"}'
                hx-target="#status-{{ group.raw_hash }}"
                hx-swap="outerHTML">Reset</button>
    </div>
</div>
```

- [ ] **Step 2: Create `_notes.html` partial**

Create `src/address_validator/templates/admin/candidates/_notes.html`:

```jinja
{#- Inline notes editor. Required context: `group`. -#}
<form id="notes-{{ group.raw_hash }}"
      hx-post="/admin/candidates/{{ group.raw_hash }}/notes"
      hx-target="#notes-{{ group.raw_hash }}"
      hx-swap="outerHTML"
      class="flex items-start gap-2">
    <textarea name="notes" rows="2"
              class="flex-1 border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-2 py-1 text-xs font-mono focus:outline-none focus:ring-2 focus:ring-co-purple-700"
              placeholder="Add a note…">{{ group.notes or "" }}</textarea>
    <button type="submit"
            class="px-2 py-0.5 text-xs rounded border border-co-purple text-co-purple hover:bg-co-purple hover:text-white focus:outline-none focus:ring-2 focus:ring-co-purple-700">Save</button>
</form>
```

- [ ] **Step 3: Create `_rows.html` partial**

Create `src/address_validator/templates/admin/candidates/_rows.html`:

```jinja
{% for row in rows %}
<tr class="border-b border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 text-sm">
    <td class="px-3 py-2 max-w-sm">
        <a href="/admin/candidates/{{ row.raw_hash }}"
           class="text-co-purple hover:underline font-mono text-xs block truncate"
           title="{{ row.raw_address }}">{{ row.raw_address }}</a>
    </td>
    <td class="px-3 py-2 whitespace-nowrap">
        {% include "admin/candidates/_status.html" with context %}
    </td>
    <td class="px-3 py-2 whitespace-nowrap text-xs text-gray-600 dark:text-gray-400">
        {{ (row.failure_types or []) | join(", ") }}
    </td>
    <td class="px-3 py-2 whitespace-nowrap text-right text-gray-700 dark:text-gray-300">{{ row.count }}</td>
    <td class="px-3 py-2 whitespace-nowrap text-xs text-gray-500 dark:text-gray-400">{{ row.first_seen.strftime('%Y-%m-%d') if row.first_seen else '' }}</td>
    <td class="px-3 py-2 whitespace-nowrap text-xs text-gray-500 dark:text-gray-400">{{ row.last_seen.strftime('%Y-%m-%d %H:%M') if row.last_seen else '' }}</td>
    <td class="px-3 py-2 text-xs text-gray-600 dark:text-gray-400 max-w-xs">
        {% if row.notes %}<span class="block truncate" title="{{ row.notes }}">{{ row.notes }}</span>{% else %}<span class="text-gray-400">—</span>{% endif %}
    </td>
</tr>
{% set group = row %}
{% else %}
<tr><td colspan="7" class="px-3 py-8 text-center text-gray-400 dark:text-gray-500">No candidate groups found.</td></tr>
{% endfor %}
```

- [ ] **Step 4: Create `index.html`**

Create `src/address_validator/templates/admin/candidates/index.html`:

```jinja
{% extends "admin/base.html" %}
{% block title %}Candidates{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-6">Training Candidates</h1>

<form class="flex flex-wrap gap-3 mb-6 items-end"
      hx-get="/admin/candidates/"
      hx-target="#candidate-rows"
      hx-push-url="true">
    <div>
        <label for="status" class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Status</label>
        <select name="status" id="status"
                class="border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-2 py-1 text-sm">
            {% for opt, label in [('new', 'New + Mixed'), ('reviewed', 'Reviewed'), ('rejected', 'Rejected'), ('all', 'All')] %}
            <option value="{{ opt }}" {% if filters.status == opt %}selected{% endif %}>{{ label }}</option>
            {% endfor %}
        </select>
    </div>
    <div>
        <label for="failure_type" class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Failure Type</label>
        <select name="failure_type" id="failure_type"
                class="border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-2 py-1 text-sm">
            <option value="">Any</option>
            {% for ft in ['repeated_label_error', 'post_parse_recovery'] %}
            <option value="{{ ft }}" {% if filters.failure_type == ft %}selected{% endif %}>{{ ft }}</option>
            {% endfor %}
        </select>
    </div>
    <div>
        <label for="since" class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Since</label>
        <select name="since" id="since"
                class="border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-2 py-1 text-sm">
            {% for opt, label in [('7d', '7 days'), ('30d', '30 days'), ('90d', '90 days'), ('all', 'All time')] %}
            <option value="{{ opt }}" {% if filters.since == opt %}selected{% endif %}>{{ label }}</option>
            {% endfor %}
        </select>
    </div>
    <button type="submit"
            class="bg-co-purple text-white px-4 py-1 rounded text-sm font-medium hover:bg-co-purple-700">Filter</button>
    <a href="/admin/candidates/" hx-target="body" class="{{ clear_btn_cls }}">Clear</a>
</form>

<div class="overflow-x-auto bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
    <table class="w-full text-left">
        <thead class="sticky top-0 bg-gray-50 dark:bg-gray-700 border-b border-gray-200 dark:border-gray-600 text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wide">
            <tr>
                <th class="px-3 py-2">Raw Address</th>
                <th class="px-3 py-2">Status</th>
                <th class="px-3 py-2">Failure</th>
                <th class="px-3 py-2 text-right">Count</th>
                <th class="px-3 py-2">First Seen</th>
                <th class="px-3 py-2">Last Seen</th>
                <th class="px-3 py-2">Notes</th>
            </tr>
        </thead>
        <tbody id="candidate-rows">
            {% include "admin/candidates/_rows.html" %}
        </tbody>
    </table>
</div>

{% if total_pages > 1 %}
<nav class="flex items-center gap-2 mt-4 text-sm" aria-label="Pagination">
    {% set qs = '&status=' ~ (filters.status or '') ~ '&failure_type=' ~ (filters.failure_type or '') ~ '&since=' ~ (filters.since or '') %}
    {% if page > 1 %}
    <a href="?page={{ page - 1 }}{{ qs }}" class="px-3 py-1 rounded border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300">&laquo; Prev</a>
    {% endif %}
    <span class="text-gray-500 dark:text-gray-400">Page {{ page }} of {{ total_pages }}</span>
    {% if page < total_pages %}
    <a href="?page={{ page + 1 }}{{ qs }}" class="px-3 py-1 rounded border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300">Next &raquo;</a>
    {% endif %}
</nav>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Create `detail.html`**

Create `src/address_validator/templates/admin/candidates/detail.html`:

```jinja
{% extends "admin/base.html" %}
{% block title %}Candidate Detail{% endblock %}
{% block content %}
<div class="mb-4"><a href="/admin/candidates/" class="text-sm text-co-purple hover:underline">&larr; Back to candidates</a></div>

<h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-2">Candidate Group</h1>
<p class="font-mono text-sm text-gray-700 dark:text-gray-300 mb-6 break-all">{{ group.raw_address }}</p>

<div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4 mb-6 grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
    <div><div class="text-xs text-gray-500 uppercase mb-1">Status</div>{% with %}{% set group = group %}{% include "admin/candidates/_status.html" %}{% endwith %}</div>
    <div><div class="text-xs text-gray-500 uppercase mb-1">Submissions</div>{{ group.count }}</div>
    <div><div class="text-xs text-gray-500 uppercase mb-1">Failure Types</div>{{ (group.failure_types or []) | join(", ") }}</div>
    <div><div class="text-xs text-gray-500 uppercase mb-1">Last Seen</div>{{ group.last_seen.strftime('%Y-%m-%d %H:%M') if group.last_seen else '—' }}</div>
</div>

<div class="mb-6">
    <div class="text-xs text-gray-500 uppercase mb-2">Notes</div>
    {% include "admin/candidates/_notes.html" %}
</div>

<h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-3">Submissions</h2>
<div class="space-y-3">
    {% for s in submissions %}
    <div class="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded p-3 text-sm">
        <div class="flex items-center gap-3 mb-2">
            <span class="text-xs text-gray-500">#{{ s.id }}</span>
            <span class="text-xs text-gray-500">{{ s.created_at.strftime('%Y-%m-%d %H:%M') if s.created_at else '' }}</span>
            <span class="text-xs font-medium text-gray-700 dark:text-gray-300">{{ s.failure_type }}</span>
            <span class="text-xs text-gray-500">{{ s.status }}</span>
        </div>
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
    {% else %}
    <div class="text-sm text-gray-400">No submissions.</div>
    {% endfor %}
</div>
{% endblock %}
```

- [ ] **Step 6: Run the router test suite — all green**

Run:
```bash
uv run pytest tests/unit/test_admin_candidates_views.py -x --no-cov
```
Expected: all tests from Tasks 6, 7, 8 pass.

- [ ] **Step 7: Commit**

```bash
git add src/address_validator/templates/admin/candidates/
git commit -m "#102 feat: add candidate triage templates (list, detail, HTMX partials)"
```

---

## Task 10: Nav entry + Tailwind rebuild

**Files:**
- Modify: `src/address_validator/templates/admin/base.html`

- [ ] **Step 1: Add nav entry**

In `src/address_validator/templates/admin/base.html`, update the `nav_items` list (around line 19-22) to include the Candidates entry:

```jinja
{% set nav_items = [
    ('dashboard', '/admin/', 'Dashboard', none),
    ('audit', '/admin/audit/', 'Audit Log', none),
    ('candidates', '/admin/candidates/', 'Candidates', none),
] %}
```

- [ ] **Step 2: Rebuild Tailwind CSS**

Run:
```bash
npm run build:css
```
Expected: exits 0; `src/address_validator/static/admin/css/tailwind.css` is regenerated to include any new utility classes used by the templates.

- [ ] **Step 3: Commit**

```bash
git add src/address_validator/templates/admin/base.html \
        src/address_validator/static/admin/css/tailwind.css
git commit -m "#102 feat: add Candidates nav entry"
```

---

## Task 11: Full test + lint sweep and end-to-end smoke

**Files:** (no file changes — verification only)

- [ ] **Step 1: Run the full test suite**

Run:
```bash
uv run pytest --no-cov
```
Expected: all tests pass.

- [ ] **Step 2: Run with coverage — must stay ≥ 80%**

Run:
```bash
uv run pytest
```
Expected: passes; coverage line + branch ≥ 80% (baseline ~93%).

- [ ] **Step 3: Lint + format**

Run:
```bash
uv run ruff check . && uv run ruff format --check .
```
Expected: both exit 0. If `ruff check` complains, fix inline and re-run. If format is off, run `uv run ruff format .` and re-check.

- [ ] **Step 4: End-to-end smoke**

Start the dev server on port 8001 from the current worktree:

```bash
lsof -ti:8001 | xargs kill 2>/dev/null; \
set -a && source /etc/address-validator/.env && set +a && \
uv run uvicorn address_validator.main:app --host 0.0.0.0 --port 8001 --reload &
```

Open `https://address-validator.exe.xyz:8001/admin/candidates/` in a browser (or curl with admin headers). Verify:

1. Page renders without Jinja errors.
2. The real-data groups show up (should see ~25 distinct `raw_address` groups from the seeded candidates).
3. Clicking a group navigates to `/admin/candidates/<hash>`.
4. Clicking "Review" on a row flips the status badge to Reviewed without a full page reload (HTMX partial swap).
5. Editing notes and hitting Save persists — refresh the page and the note is still there.
6. Filtering `status=rejected` collapses the list appropriately.

If anything fails, fix before moving on — do not proceed to Task 12 with a broken UI.

- [ ] **Step 5: Stop the dev server**

```bash
lsof -ti:8001 | xargs kill 2>/dev/null
```

- [ ] **Step 6: Commit any fixes from Step 4**

```bash
git add -A && git commit -m "#102 fix: <describe any smoke-test fixes>"
```

(If nothing to commit, skip this step.)

---

## Task 12: PR

**Files:** (no file changes — PR only)

- [ ] **Step 1: Push the branch**

```bash
git push -u origin HEAD
```

- [ ] **Step 2: Open the PR**

```bash
export GH_TOKEN=$(grep GH_TOKEN .env | cut -d= -f2)
gh pr create --title "Admin candidate triage surface (#102)" --body "$(cat <<'EOF'
## Summary
- New `/admin/candidates/` surface: browse training candidates grouped by `raw_address`, mark Review/Reject/Reset, edit notes, drill into per-submission detail with `parsed_tokens` and `recovered_components`.
- Excludes `status='labeled'` rows from the triage view.
- New Alembic migration `012` adds pgcrypto + generated `raw_address_hash` column for stable URL slugs.
- Query + router + template tests; no JS changes.

Closes #102

Design: `docs/plans/2026-04-13-admin-candidate-triage-design.md`
Plan: `docs/plans/2026-04-13-admin-candidate-triage.md`

## Test plan
- [ ] `uv run pytest` passes (coverage ≥ 80%)
- [ ] `uv run ruff check . && uv run ruff format --check .` clean
- [ ] Dev-server smoke on port 8001: list renders, filter works, HTMX status flip works, notes round-trip works, detail page shows parsed_tokens

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Report the PR URL**

Copy the URL from the `gh pr create` output into a message for the user.

---

## Self-Review Checklist

**Spec coverage — every design section maps to a task:**

| Design section | Covered by |
|---|---|
| URL surface (list, detail, status, notes) | Tasks 6, 7, 8 |
| `raw_hash` via generated column + pgcrypto | Task 1, 2 |
| Group semantics: `labeled` excluded, rollup `mixed` | Task 4 (rollup expr, `_NON_LABELED`) |
| Write actions apply to group, skip `labeled` | Tasks 4, 5 |
| List columns, filters, default sort | Task 4 (query), Task 9 (template) |
| Detail drill-down with `parsed_tokens`/`recovered_components` | Task 7 (router), Task 9 (`detail.html`) |
| `CANDIDATE_STATUS_META` | Task 3 |
| Nav entry | Task 10 |
| No schema change to existing columns | (plan-level: no Task touches existing columns) |
| Auth via existing `AdminContext` | Tasks 6-8 (uses `get_admin_context`) |
| No CSRF (follows admin convention) | (implicit — no CSRF code added) |
| Test strategy: queries + router + labeled exclusion + notes round-trip | Tasks 4, 5, 6, 7, 8 |
| Tailwind rebuild | Task 10 |

**Type/name consistency verified:**

- `raw_hash` (kwarg) used consistently across `get_candidate_group`, `get_candidate_submissions`, `update_candidate_status`, `update_candidate_notes`, and router handlers.
- `rollup_status` label used by query + template consistently.
- `cs_meta` Jinja global matches template usage in `_status.html`.
- `_NON_LABELED` reused across all queries — single source of truth for the exclusion rule.
- Router URL path `/candidates/{raw_hash}` matches test paths and query kwargs.

**Placeholder scan:** none — every code block is complete.
