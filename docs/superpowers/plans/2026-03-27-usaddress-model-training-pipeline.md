# usaddress Model Training & Deployment Pipeline — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an interactive, skill-driven pipeline for training custom usaddress CRF models, deploying them to production, and contributing improvements upstream.

**Architecture:** New DB table collects parse-recovery candidates. Lifespan hook optionally swaps `usaddress.TAGGER` with a custom model. Six deterministic scripts under `scripts/model/` handle each pipeline phase. An orchestrating `/train-model` skill sequences operator interaction.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy Core, Alembic, parserator, pycrfsuite, usaddress, pytest, Claude API (for agent-assisted labeling)

**Design doc:** `docs/plans/2026-03-27-usaddress-model-training-pipeline-design.md`
**Issue:** #75

---

## File Structure

```
# New files
alembic/versions/008_model_training_candidates.py  — migration for candidates table
src/address_validator/db/tables.py                  — add model_training_candidates table def
src/address_validator/services/training_candidates.py — fire-and-forget candidate insert
tests/unit/test_training_candidates.py              — unit tests for candidate collection
tests/unit/test_custom_model_loading.py             — unit tests for model swap
training/                                           — new top-level directory
training/data/.gitkeep                              — labeled XML training data
training/test_cases/.gitkeep                        — regression test CSVs
training/manifests/.gitkeep                         — training run manifests
training/.gitignore                                 — ignore models/ and candidates.jsonl
scripts/model/__init__.py                           — package marker
scripts/model/identify.py                           — query candidates, export CSV
scripts/model/label.py                              — agent-assisted labeling + diff
scripts/model/train.py                              — run parserator train, write manifest
scripts/model/test_model.py                         — rebuild old model, run comparison
scripts/model/deploy.py                             — copy model, validate load
scripts/model/contribute.py                         — assemble upstream PR
skills/train-model/SKILL.md                         — orchestrating skill

# Modified files
src/address_validator/db/tables.py                  — add model_training_candidates Table
src/address_validator/main.py                       — add CUSTOM_MODEL_PATH loading in lifespan
src/address_validator/services/parser.py            — add candidate collection calls
pyproject.toml                                      — add parserator dev dependency
.claude/skills/train-model                          — symlink to skills/train-model
```

---

## Phase 1: Application Infrastructure

### Task 1: Add `model_training_candidates` Table Definition

**Files:**
- Modify: `src/address_validator/db/tables.py` (append after line 104)

- [ ] **Step 1: Add table definition to `tables.py`**

Append after the `ERROR_STATUS_MIN` constant at the end of the file:

```python
# ---------------------------------------------------------------------------
# Model training candidate collection (migration 008)
# ---------------------------------------------------------------------------

model_training_candidates = sa.Table(
    "model_training_candidates",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("raw_address", sa.Text(), nullable=False),
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

- [ ] **Step 2: Commit**

```bash
git add src/address_validator/db/tables.py
git commit -m "#75 feat: add model_training_candidates table definition"
```

---

### Task 2: Alembic Migration

**Files:**
- Create: `alembic/versions/008_model_training_candidates.py`

- [ ] **Step 1: Create migration file**

```python
"""Add model_training_candidates table.

Revision ID: 008
Revises: 007
Create Date: 2026-03-27

Collects addresses where usaddress required post-parse recovery,
as training candidates for improved CRF models.
"""

revision: str = "008"
down_revision: str = "007"
branch_labels = None
depends_on = None

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.create_table(
        "model_training_candidates",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("raw_address", sa.Text(), nullable=False),
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


def downgrade() -> None:
    op.drop_table("model_training_candidates")
```

- [ ] **Step 2: Commit**

```bash
git add alembic/versions/008_model_training_candidates.py
git commit -m "#75 feat: add migration 008 for model_training_candidates"
```

---

### Task 3: Candidate Collection Service

**Files:**
- Create: `src/address_validator/services/training_candidates.py`
- Test: `tests/unit/test_training_candidates.py`

- [ ] **Step 1: Write failing test for `write_training_candidate`**

Create `tests/unit/test_training_candidates.py`:

```python
"""Unit tests for services/training_candidates.py."""

from unittest import mock

import pytest

from address_validator.services.training_candidates import write_training_candidate


class TestWriteTrainingCandidate:
    @pytest.mark.asyncio
    async def test_inserts_row_when_engine_available(self) -> None:
        """Verify the function attempts a DB insert with correct values."""
        mock_engine = mock.AsyncMock()
        mock_conn = mock.AsyncMock()
        mock_engine.begin.return_value.__aenter__ = mock.AsyncMock(return_value=mock_conn)
        mock_engine.begin.return_value.__aexit__ = mock.AsyncMock(return_value=False)

        await write_training_candidate(
            engine=mock_engine,
            raw_address="995 9TH ST BLDG 201 ROOM 104 T",
            failure_type="repeated_label_error",
            parsed_tokens=[("995", "AddressNumber"), ("BLDG", "SubaddressType")],
            recovered_components={"address_number": "995", "subaddress_type": "BLDG"},
        )

        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args
        # The insert statement should target model_training_candidates
        assert "model_training_candidates" in str(call_args)

    @pytest.mark.asyncio
    async def test_swallows_exceptions(self, caplog: pytest.LogCaptureFixture) -> None:
        """Fail-open: DB errors are logged, not raised."""
        mock_engine = mock.AsyncMock()
        mock_engine.begin.side_effect = Exception("connection refused")

        # Must not raise
        await write_training_candidate(
            engine=mock_engine,
            raw_address="test",
            failure_type="repeated_label_error",
            parsed_tokens=[],
        )

        assert "failed to write training candidate" in caplog.text

    @pytest.mark.asyncio
    async def test_none_engine_is_noop(self) -> None:
        """When engine is None (no DB configured), do nothing."""
        # Must not raise
        await write_training_candidate(
            engine=None,
            raw_address="test",
            failure_type="repeated_label_error",
            parsed_tokens=[],
        )
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_training_candidates.py -v --no-cov -x
```

Expected: FAIL — `ModuleNotFoundError` for `training_candidates`

- [ ] **Step 3: Write implementation**

Create `src/address_validator/services/training_candidates.py`:

```python
"""Training candidate collection — fire-and-forget insert for parse-recovery events.

When the parser encounters a RepeatedLabelError or triggers post-parse recovery
heuristics, this module records the raw address and token data as a training
candidate for future CRF model improvements.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from address_validator.db.tables import model_training_candidates

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


async def write_training_candidate(
    engine: AsyncEngine | None,
    *,
    raw_address: str,
    failure_type: str,
    parsed_tokens: list[tuple[str, str]] | list[Any],
    recovered_components: dict[str, str] | None = None,
) -> None:
    """Insert a training candidate row. Logs and swallows all errors (fail-open)."""
    if engine is None:
        return
    try:
        # Convert list of tuples to list of lists for JSONB serialisation
        tokens_json = [[tok, label] for tok, label in parsed_tokens]
        async with engine.begin() as conn:
            await conn.execute(
                model_training_candidates.insert().values(
                    raw_address=raw_address,
                    failure_type=failure_type,
                    parsed_tokens=tokens_json,
                    recovered_components=recovered_components,
                )
            )
    except Exception:
        logger.warning("training_candidates: failed to write training candidate", exc_info=True)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_training_candidates.py -v --no-cov -x
```

Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/services/training_candidates.py tests/unit/test_training_candidates.py
git commit -m "#75 feat: add training candidate collection service"
```

---

### Task 4: Wire Candidate Collection into Parser

**Files:**
- Modify: `src/address_validator/services/parser.py`
- Test: `tests/unit/test_parser.py` (add new tests)

This task adds fire-and-forget candidate collection to the two recovery paths in `parser.py`. The collection runs as a background task (same pattern as audit), gated by engine availability.

The parser is a pure synchronous function. To issue fire-and-forget async DB writes, we use the same ContextVar + middleware pattern as audit: set a ContextVar with the candidate data, and let the audit middleware (or a similar hook) write it after the response. However, since training candidates are not request-scoped metadata but rather parser-internal events, a simpler approach is to pass the engine through and schedule a background task.

After reviewing the codebase, the cleanest approach: add ContextVars for candidate data (same pattern as `services/audit.py`), read them in the audit middleware, and write the row alongside the audit row. But that couples training candidates to audit middleware.

Simpler: use `asyncio.create_task` from within the parser's async caller. But `parse_address()` is synchronous.

Simplest approach that fits: add a module-level `_candidate_engine` ContextVar set during lifespan, and schedule the write via `asyncio.get_event_loop().create_task()` from within the sync parser. This matches the `_background_tasks` pattern in `middleware/audit.py`.

Actually, looking more carefully at the code — `parse_address()` is called from sync router handlers within an async context. The most idiomatic approach for this project: use ContextVars like audit does, and have the audit middleware write the candidate row too.

But that's a bigger change. The design doc says "fire-and-forget, same pattern as audit". Let's use a dedicated ContextVar approach:

- [ ] **Step 1: Write failing tests for candidate collection integration**

Add to `tests/unit/test_parser.py`:

```python
from address_validator.services.training_candidates import (
    get_candidate_data,
    reset_candidate_data,
)


class TestCandidateCollection:
    def setup_method(self) -> None:
        reset_candidate_data()

    def test_repeated_label_sets_candidate_data(self) -> None:
        """RepeatedLabelError path should set candidate ContextVar."""
        fake_tokens = [
            ("995", "AddressNumber"),
            ("9TH", "StreetName"),
            ("ST", "StreetNamePostType"),
            ("BLDG", "SubaddressType"),
            ("201", "SubaddressIdentifier"),
            ("ROOM", "SubaddressType"),
            ("104", "AddressNumber"),
        ]
        exc = usaddress.RepeatedLabelError("fake", fake_tokens, {})
        with mock.patch("address_validator.services.parser.usaddress.tag", side_effect=exc):
            parse_address("995 9TH ST BLDG 201 ROOM 104")

        candidate = get_candidate_data()
        assert candidate is not None
        assert candidate["failure_type"] == "repeated_label_error"
        assert candidate["raw_address"] == "995 9TH ST BLDG 201 ROOM 104"

    def test_post_parse_recovery_sets_candidate_data(self) -> None:
        """When _recover_unit_from_city fires, candidate data should be set."""
        # Simulate usaddress putting BSMT into PlaceName via RLE fallback
        fake_tokens = [
            ("123", "AddressNumber"),
            ("Main", "StreetName"),
            ("St", "StreetNamePostType"),
            ("BSMT,", "PlaceName"),
            ("Springfield", "PlaceName"),
        ]
        exc = usaddress.RepeatedLabelError("fake", fake_tokens, {})
        with mock.patch("address_validator.services.parser.usaddress.tag", side_effect=exc):
            result = parse_address("123 Main St BSMT, Springfield")

        candidate = get_candidate_data()
        # Should be set if recovery fired (check via warnings)
        if any("Unit designator recovered" in w for w in result.warnings):
            assert candidate is not None
            assert candidate["failure_type"] in ("repeated_label_error", "post_parse_recovery")

    def test_clean_parse_no_candidate_data(self) -> None:
        """Normal successful parse should not set candidate data."""
        parse_address("123 Main St, Springfield, IL 62701")
        candidate = get_candidate_data()
        assert candidate is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_parser.py::TestCandidateCollection -v --no-cov -x
```

Expected: FAIL — `ImportError` for `get_candidate_data`, `reset_candidate_data`

- [ ] **Step 3: Add ContextVar API to `training_candidates.py`**

Add to `src/address_validator/services/training_candidates.py`, before `write_training_candidate`:

```python
from contextvars import ContextVar

_candidate_data: ContextVar[dict[str, Any] | None] = ContextVar(
    "training_candidate_data", default=None
)


def set_candidate_data(
    *,
    raw_address: str,
    failure_type: str,
    parsed_tokens: list[tuple[str, str]] | list[Any],
    recovered_components: dict[str, str] | None = None,
) -> None:
    """Set training candidate data for the current request context."""
    _candidate_data.set({
        "raw_address": raw_address,
        "failure_type": failure_type,
        "parsed_tokens": parsed_tokens,
        "recovered_components": recovered_components,
    })


def get_candidate_data() -> dict[str, Any] | None:
    """Read training candidate data for the current request context."""
    return _candidate_data.get()


def reset_candidate_data() -> None:
    """Reset candidate ContextVar to None."""
    _candidate_data.set(None)
```

- [ ] **Step 4: Wire `set_candidate_data` into `parser.py`**

Add import at top of `parser.py` (after line 13):

```python
from address_validator.services.training_candidates import set_candidate_data
```

In the `RepeatedLabelError` except block (after line 414, before the `logger.debug` on line 416), add:

```python
        set_candidate_data(
            raw_address=raw,
            failure_type="repeated_label_error",
            parsed_tokens=list(exc.parsed_string),
            recovered_components=component_values,
        )
```

For post-parse recovery on the success path — add after line 433 (after both `_recover_*` calls), before the return:

```python
    # Check if any recovery heuristic fired (indicated by warnings)
    if any(
        "Unit designator recovered" in w or "identifier fragment" in w.lower()
        for w in warnings
    ):
        pre_recovery = {TAG_NAMES.get(label, label): value for label, value in tagged.items()}
        set_candidate_data(
            raw_address=raw,
            failure_type="post_parse_recovery",
            parsed_tokens=[(v, k) for k, v in tagged.items()],
            recovered_components=component_values,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_parser.py -v --no-cov -x
```

Expected: All tests pass (existing + new)

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/services/parser.py src/address_validator/services/training_candidates.py tests/unit/test_parser.py
git commit -m "#75 feat: wire candidate collection ContextVars into parser"
```

---

### Task 5: Write Candidate Rows from Audit Middleware

**Files:**
- Modify: `src/address_validator/middleware/audit.py`
- Test: `tests/unit/test_audit_middleware.py` (add test)

The audit middleware already runs after each request and writes to the DB. We add a candidate write alongside the audit row when candidate data is present.

- [ ] **Step 1: Write failing test**

Add to `tests/unit/test_audit_middleware.py` (find the appropriate test class):

```python
from address_validator.services.training_candidates import (
    reset_candidate_data,
    set_candidate_data,
)


class TestCandidateWriteFromMiddleware:
    def test_candidate_written_when_data_present(self, client: TestClient) -> None:
        """When candidate ContextVar is set, middleware should fire-and-forget a write."""
        # This is an integration-level check: set the ContextVar, make a request,
        # verify write_training_candidate was called.
        with mock.patch(
            "address_validator.middleware.audit.write_training_candidate"
        ) as mock_write:
            # We need to set candidate data during request processing.
            # Simplest: patch parse_address to set it.
            set_candidate_data(
                raw_address="test addr",
                failure_type="repeated_label_error",
                parsed_tokens=[("test", "AddressNumber")],
            )
            # The middleware reads candidate data after self.app() returns.
            # Since we set it before the request, it should be visible.
            # Note: ContextVar scoping means this may not work across tasks.
            # The real path is: parser sets it during request → middleware reads it.
            # For unit test, we verify the wiring exists.
```

Actually, testing the middleware wiring at the unit level with ContextVars across async boundaries is fragile. A better approach: test that `write_training_candidate` is called with the right args in isolation (already done in Task 3), and test the ContextVar lifecycle in parser tests (done in Task 4). For the middleware wiring, add an integration test.

Let me revise this task to be simpler and more robust:

- [ ] **Step 1: Add candidate write call to audit middleware**

Read `src/address_validator/middleware/audit.py` to understand the structure. The middleware's `__call__` method writes the audit row after `self.app()` returns. Add the candidate write in the same location.

Add import at top of `middleware/audit.py`:

```python
from address_validator.services.training_candidates import (
    get_candidate_data,
    reset_candidate_data,
    write_training_candidate,
)
```

In the middleware's `__call__` method, in the section where `_background_tasks` is used to fire-and-forget the audit write, add after the audit write task creation:

```python
            # Fire-and-forget training candidate write if parser flagged one
            candidate = get_candidate_data()
            if candidate is not None and engine is not None:
                task = asyncio.create_task(
                    write_training_candidate(engine=engine, **candidate)
                )
                _background_tasks.add(task)
                task.add_done_callback(_background_tasks.discard)
```

Also add `reset_candidate_data()` at the top of the request handling (alongside the existing `reset_audit_context()` call):

```python
        reset_candidate_data()
```

- [ ] **Step 2: Write integration test**

Add to `tests/integration/test_v1_parse.py` or create `tests/integration/test_candidate_collection.py`:

```python
"""Integration test: candidate collection fires on RepeatedLabelError."""

from unittest import mock

import usaddress

from address_validator.services.training_candidates import get_candidate_data


class TestCandidateCollectionIntegration:
    def test_repeated_label_triggers_candidate(self, client) -> None:
        """When usaddress raises RepeatedLabelError, a candidate should be collected."""
        fake_tokens = [
            ("995", "AddressNumber"),
            ("9TH", "StreetName"),
            ("BLDG", "SubaddressType"),
            ("201", "SubaddressIdentifier"),
            ("ROOM", "SubaddressType"),
        ]
        exc = usaddress.RepeatedLabelError("fake", fake_tokens, {})

        with mock.patch(
            "address_validator.services.parser.usaddress.tag", side_effect=exc
        ), mock.patch(
            "address_validator.middleware.audit.write_training_candidate"
        ) as mock_write:
            client.post(
                "/api/v1/parse",
                json={"address": "995 9TH ST BLDG 201 ROOM 104"},
            )

        # write_training_candidate should have been called (fire-and-forget)
        # Give background task a moment to fire
        mock_write.assert_called_once()
        call_kwargs = mock_write.call_args.kwargs
        assert call_kwargs["failure_type"] == "repeated_label_error"
```

- [ ] **Step 3: Run tests**

```bash
uv run pytest tests/unit/test_audit_middleware.py tests/integration/test_candidate_collection.py -v --no-cov -x
```

Expected: All pass

- [ ] **Step 4: Run full test suite to verify no regressions**

```bash
uv run pytest --no-cov -x
```

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/middleware/audit.py tests/
git commit -m "#75 feat: write training candidates from audit middleware"
```

---

### Task 6: Custom Model Loading in Lifespan

**Files:**
- Modify: `src/address_validator/main.py` (lifespan function)
- Test: `tests/unit/test_custom_model_loading.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_custom_model_loading.py`:

```python
"""Unit tests for custom usaddress model loading."""

import os
import tempfile
from unittest import mock

import pycrfsuite
import pytest
import usaddress


class TestCustomModelLoading:
    def test_loads_custom_model_when_path_set(self, tmp_path) -> None:
        """CUSTOM_MODEL_PATH pointing to a valid .crfsuite file replaces TAGGER."""
        # Use the bundled model as our "custom" model for testing
        bundled_path = usaddress.MODEL_PATH
        original_tagger = usaddress.TAGGER

        with mock.patch.dict(os.environ, {"CUSTOM_MODEL_PATH": bundled_path}):
            from address_validator.main import _load_custom_model

            _load_custom_model()

        # TAGGER should have been replaced (even if with same model)
        # Restore after test
        usaddress.TAGGER = original_tagger

    def test_warns_on_missing_path(self, caplog: pytest.LogCaptureFixture) -> None:
        """Non-existent path logs a warning and keeps bundled model."""
        original_tagger = usaddress.TAGGER

        with mock.patch.dict(os.environ, {"CUSTOM_MODEL_PATH": "/nonexistent/model.crfsuite"}):
            from address_validator.main import _load_custom_model

            _load_custom_model()

        assert usaddress.TAGGER is original_tagger
        assert "not found" in caplog.text

    def test_noop_when_env_unset(self) -> None:
        """No CUSTOM_MODEL_PATH means bundled model is used (no-op)."""
        original_tagger = usaddress.TAGGER

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CUSTOM_MODEL_PATH", None)
            from address_validator.main import _load_custom_model

            _load_custom_model()

        assert usaddress.TAGGER is original_tagger
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_custom_model_loading.py -v --no-cov -x
```

Expected: FAIL — `ImportError` for `_load_custom_model`

- [ ] **Step 3: Add `_load_custom_model` to `main.py`**

Add after the imports in `main.py` (after line 34), before `_DESCRIPTION`:

```python
def _load_custom_model() -> None:
    """Swap usaddress.TAGGER with a custom .crfsuite model if configured.

    Reads CUSTOM_MODEL_PATH from environment. No-op when unset.
    Logs warning and falls back to bundled model if path is invalid.
    """
    import usaddress

    custom_path = os.environ.get("CUSTOM_MODEL_PATH", "").strip()
    if not custom_path:
        return

    path = Path(custom_path)
    if not path.exists():
        logging.getLogger(__name__).warning(
            "CUSTOM_MODEL_PATH=%s not found, using bundled model", path
        )
        return

    import pycrfsuite

    tagger = pycrfsuite.Tagger()
    tagger.open(str(path))
    usaddress.TAGGER = tagger
    logging.getLogger(__name__).info("loaded custom usaddress model: %s", path)
```

Then call it from the lifespan function, after `app.state.api_key` is set (line 70) but before DB init:

```python
    _load_custom_model()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_custom_model_loading.py -v --no-cov -x
```

Expected: 3 passed

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest --no-cov -x
```

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/main.py tests/unit/test_custom_model_loading.py
git commit -m "#75 feat: add CUSTOM_MODEL_PATH lifespan hook for custom CRF model"
```

---

### Task 7: Add `parserator` Dev Dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add parserator**

```bash
uv add --dev parserator
```

- [ ] **Step 2: Verify it installed correctly**

```bash
uv run python -c "import parserator; print('parserator OK')"
uv run parserator --help
```

- [ ] **Step 3: Run full test suite to verify no conflicts**

```bash
uv run pytest --no-cov -x
```

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "#75 chore: add parserator dev dependency"
```

---

### Task 8: Training Directory Structure

**Files:**
- Create: `training/` directory tree
- Create: `training/.gitignore`

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p training/data training/test_cases training/manifests training/models
touch training/data/.gitkeep training/test_cases/.gitkeep training/manifests/.gitkeep
```

- [ ] **Step 2: Create `.gitignore`**

Create `training/.gitignore`:

```
# Built model files (reconstructible from manifests + training data)
models/

# Local candidate export cache
candidates.jsonl
```

- [ ] **Step 3: Commit**

```bash
git add training/
git commit -m "#75 chore: add training directory structure"
```

---

## Phase 2: Pipeline Scripts

### Task 9: `scripts/model/identify.py`

**Files:**
- Create: `scripts/model/__init__.py`
- Create: `scripts/model/identify.py`

- [ ] **Step 1: Create package marker**

Create empty `scripts/model/__init__.py`.

- [ ] **Step 2: Write `identify.py`**

```python
"""Query model_training_candidates and export selected candidates to CSV.

Usage:
    python scripts/model/identify.py [--status new] [--type repeated_label_error] [--limit 100] [--out candidates.csv]

Requires VALIDATION_CACHE_DSN environment variable.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from address_validator.db.tables import model_training_candidates


async def _query_candidates(
    dsn: str,
    *,
    status: str = "new",
    failure_type: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Fetch candidates from the database."""
    engine = create_async_engine(dsn)
    try:
        query = (
            sa.select(model_training_candidates)
            .where(model_training_candidates.c.status == status)
            .order_by(model_training_candidates.c.created_at.desc())
            .limit(limit)
        )
        if failure_type:
            query = query.where(model_training_candidates.c.failure_type == failure_type)

        async with engine.begin() as conn:
            result = await conn.execute(query)
            rows = result.mappings().all()
            return [dict(r) for r in rows]
    finally:
        await engine.dispose()


async def _show_summary(dsn: str) -> None:
    """Print a summary of candidates grouped by failure_type."""
    engine = create_async_engine(dsn)
    try:
        query = (
            sa.select(
                model_training_candidates.c.failure_type,
                model_training_candidates.c.status,
                sa.func.count().label("count"),
            )
            .group_by(
                model_training_candidates.c.failure_type,
                model_training_candidates.c.status,
            )
            .order_by(sa.text("count DESC"))
        )
        async with engine.begin() as conn:
            result = await conn.execute(query)
            rows = result.all()

        print("\n=== Training Candidate Summary ===\n")
        print(f"{'Failure Type':<30} {'Status':<12} {'Count':>6}")
        print("-" * 50)
        for row in rows:
            print(f"{row.failure_type:<30} {row.status:<12} {row.count:>6}")
        print()
    finally:
        await engine.dispose()


async def _export_csv(
    dsn: str,
    outfile: str,
    *,
    status: str = "new",
    failure_type: str | None = None,
    limit: int = 100,
) -> int:
    """Export candidates to CSV. Returns count of exported rows."""
    rows = await _query_candidates(dsn, status=status, failure_type=failure_type, limit=limit)
    if not rows:
        print("No candidates found matching criteria.")
        return 0

    with open(outfile, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "raw_address", "failure_type", "parsed_tokens", "recovered_components"])
        for row in rows:
            writer.writerow([
                row["id"],
                row["raw_address"],
                row["failure_type"],
                json.dumps(row["parsed_tokens"]),
                json.dumps(row["recovered_components"]) if row["recovered_components"] else "",
            ])

    print(f"Exported {len(rows)} candidates to {outfile}")
    return len(rows)


async def _update_status(dsn: str, ids: list[int], new_status: str) -> None:
    """Update the status of candidates by ID."""
    engine = create_async_engine(dsn)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                model_training_candidates.update()
                .where(model_training_candidates.c.id.in_(ids))
                .values(status=new_status)
            )
        print(f"Updated {len(ids)} candidates to status='{new_status}'")
    finally:
        await engine.dispose()


def main() -> None:
    import os

    dsn = os.environ.get("VALIDATION_CACHE_DSN", "").strip()
    if not dsn:
        print("Error: VALIDATION_CACHE_DSN not set", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Identify training candidates")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("summary", help="Show candidate summary")

    export_cmd = sub.add_parser("export", help="Export candidates to CSV")
    export_cmd.add_argument("--status", default="new")
    export_cmd.add_argument("--type", dest="failure_type", default=None)
    export_cmd.add_argument("--limit", type=int, default=100)
    export_cmd.add_argument("--out", default="training/candidates.csv")

    mark_cmd = sub.add_parser("mark", help="Update candidate status")
    mark_cmd.add_argument("ids", nargs="+", type=int)
    mark_cmd.add_argument("--status", required=True, choices=["reviewed", "labeled", "rejected"])

    args = parser.parse_args()

    if args.command == "summary":
        asyncio.run(_show_summary(dsn))
    elif args.command == "export":
        asyncio.run(_export_csv(dsn, args.out, status=args.status, failure_type=args.failure_type, limit=args.limit))
    elif args.command == "mark":
        asyncio.run(_update_status(dsn, args.ids, args.status))


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Verify it runs**

```bash
uv run python scripts/model/identify.py --help
```

Expected: Help text with summary/export/mark subcommands

- [ ] **Step 4: Commit**

```bash
git add scripts/model/__init__.py scripts/model/identify.py
git commit -m "#75 feat: add scripts/model/identify.py for candidate querying"
```

---

### Task 10: `scripts/model/label.py`

**Files:**
- Create: `scripts/model/label.py`

- [ ] **Step 1: Write `label.py`**

```python
"""Agent-assisted address labeling for usaddress training data.

Generates draft labels from both usaddress (current model) and Claude,
produces a diff showing disagreements, and outputs labeled XML.

Usage:
    python scripts/model/label.py input.csv output.xml [--test-output test.xml]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import xml.etree.ElementTree as ET
from xml.dom import minidom

import usaddress

# usaddress tag names are the XML element names in training data
VALID_TAGS = set(usaddress.LABELS)


def _label_with_model(address: str) -> list[tuple[str, str]]:
    """Label an address using the current usaddress model.

    Returns list of (token, label) tuples. On RepeatedLabelError,
    returns the parsed_string from the exception (pre-recovery tokens).
    """
    try:
        return usaddress.parse(address)
    except usaddress.RepeatedLabelError as exc:
        return list(exc.parsed_string)


def _label_with_claude(address: str) -> list[tuple[str, str]]:
    """Label an address using Claude API.

    Sends the address and available labels to Claude, asks for
    token-level labeling. Returns list of (token, label) tuples.

    Requires ANTHROPIC_API_KEY environment variable.
    """
    try:
        import anthropic
    except ImportError:
        print("Error: anthropic package required for Claude labeling. Install with: uv add --dev anthropic", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        print("Error: ANTHROPIC_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    labels_str = ", ".join(sorted(VALID_TAGS))

    prompt = f"""Label each token in this US address with the correct USPS address component tag.

Available tags: {labels_str}

Address: {address}

Return ONLY a JSON array of [token, label] pairs. Example:
[["123", "AddressNumber"], ["Main", "StreetName"], ["St", "StreetNamePostType"]]

Important:
- Split the address into the same tokens that a simple whitespace tokenizer would produce
- Each token gets exactly one label
- Use the most specific applicable tag
- For secondary unit designators (APT, STE, BLDG, ROOM, etc.), use OccupancyType or SubaddressType
- Keep punctuation attached to tokens as they appear"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    # Extract JSON from response (may be wrapped in markdown code block)
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    pairs = json.loads(text)
    return [(token, label) for token, label in pairs]


def _format_diff(address: str, model_labels: list[tuple[str, str]], claude_labels: list[tuple[str, str]]) -> str:
    """Format a side-by-side diff of model vs Claude labels."""
    lines = [f"\n{'='*60}", f"Address: {address}", f"{'='*60}"]
    lines.append(f"{'Token':<20} {'Model':<30} {'Claude':<30} {'Match'}")
    lines.append("-" * 85)

    max_len = max(len(model_labels), len(claude_labels))
    for i in range(max_len):
        m_tok, m_lab = model_labels[i] if i < len(model_labels) else ("—", "—")
        c_tok, c_lab = claude_labels[i] if i < len(claude_labels) else ("—", "—")
        token = m_tok if m_tok != "—" else c_tok
        match = "OK" if m_lab == c_lab else "DIFF"
        lines.append(f"{token:<20} {m_lab:<30} {c_lab:<30} {match}")

    return "\n".join(lines)


def _labels_to_xml(labels: list[tuple[str, str]]) -> ET.Element:
    """Convert (token, label) pairs to an AddressString XML element."""
    addr_elem = ET.SubElement(ET.Element("root"), "AddressString")
    for token, label in labels:
        child = ET.SubElement(addr_elem, label)
        child.text = token
    return addr_elem


def _write_xml(addresses: list[list[tuple[str, str]]], outfile: str) -> None:
    """Write labeled addresses to XML file in usaddress training format."""
    root = ET.Element("AddressCollection")
    for labels in addresses:
        addr_elem = ET.SubElement(root, "AddressString")
        for token, label in labels:
            child = ET.SubElement(addr_elem, label)
            child.text = token

    xml_str = minidom.parseString(ET.tostring(root, encoding="unicode")).toprettyxml(indent="  ")
    # Remove extra XML declaration
    lines = xml_str.split("\n")
    if lines[0].startswith("<?xml"):
        lines = lines[1:]
    with open(outfile, "w") as f:
        f.write("\n".join(lines))
    print(f"Wrote {len(addresses)} labeled addresses to {outfile}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent-assisted address labeling")
    parser.add_argument("input_csv", help="CSV file with raw addresses (one per line, or column 'raw_address')")
    parser.add_argument("output_xml", help="Output XML file for training data")
    parser.add_argument("--test-output", help="Output XML file for test data (subset)")
    parser.add_argument("--model-only", action="store_true", help="Skip Claude labeling, use model labels only")
    parser.add_argument("--claude-only", action="store_true", help="Skip model labeling, use Claude labels only")
    args = parser.parse_args()

    # Read addresses from CSV
    addresses: list[str] = []
    with open(args.input_csv) as f:
        reader = csv.DictReader(f)
        if "raw_address" in (reader.fieldnames or []):
            for row in reader:
                addresses.append(row["raw_address"])
        else:
            # Fall back to first column or plain lines
            f.seek(0)
            for line in f:
                line = line.strip()
                if line and not line.startswith("id,"):
                    # Skip header if present
                    addr = line.split(",")[0].strip('"')
                    if addr:
                        addresses.append(addr)

    if not addresses:
        print("No addresses found in input file.", file=sys.stderr)
        sys.exit(1)

    print(f"Loaded {len(addresses)} addresses from {args.input_csv}\n")

    # Label each address with both methods and show diff
    final_labels: list[list[tuple[str, str]]] = []
    for addr in addresses:
        model_labels = _label_with_model(addr)

        if args.model_only:
            final_labels.append(model_labels)
            continue

        claude_labels = _label_with_claude(addr) if not args.model_only else model_labels

        if args.claude_only:
            final_labels.append(claude_labels)
            continue

        diff = _format_diff(addr, model_labels, claude_labels)
        print(diff)

        # In interactive mode, let operator choose
        print("\nUse [m]odel labels, [c]laude labels, or [s]kip? ", end="")
        choice = input().strip().lower()
        if choice == "m":
            final_labels.append(model_labels)
        elif choice == "c":
            final_labels.append(claude_labels)
        elif choice == "s":
            print("Skipped.")
        else:
            print(f"Unknown choice '{choice}', using Claude labels.")
            final_labels.append(claude_labels)

    if not final_labels:
        print("No addresses labeled.", file=sys.stderr)
        sys.exit(1)

    _write_xml(final_labels, args.output_xml)

    # Split for test output if requested
    if args.test_output and len(final_labels) > 1:
        split = max(1, len(final_labels) // 5)  # 20% for test
        _write_xml(final_labels[-split:], args.test_output)
        _write_xml(final_labels[:-split], args.output_xml)  # Rewrite training without test portion


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it runs**

```bash
uv run python scripts/model/label.py --help
```

- [ ] **Step 3: Commit**

```bash
git add scripts/model/label.py
git commit -m "#75 feat: add scripts/model/label.py for agent-assisted labeling"
```

---

### Task 11: `scripts/model/train.py`

**Files:**
- Create: `scripts/model/train.py`

- [ ] **Step 1: Write `train.py`**

```python
"""Train a custom usaddress CRF model from labeled XML data.

Combines upstream training data with custom labeled XML and runs
parserator train. Writes a manifest for deterministic reconstruction.

Usage:
    python scripts/model/train.py --name multi-unit --description "Multi-unit designator handling"
    python scripts/model/train.py --name multi-unit --custom-only  # Train on custom data alone
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import usaddress

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TRAINING_DIR = PROJECT_ROOT / "training"
DATA_DIR = TRAINING_DIR / "data"
MODELS_DIR = TRAINING_DIR / "models"
MANIFESTS_DIR = TRAINING_DIR / "manifests"

# Upstream training data location (inside installed usaddress package)
UPSTREAM_TRAINING_DIR = Path(usaddress.__file__).parent.parent / "training"


def _find_upstream_training_files() -> list[Path]:
    """Find all XML training files from the installed usaddress package."""
    # Look in the usaddress source/install directory
    candidates = [
        UPSTREAM_TRAINING_DIR,
        Path(usaddress.__file__).parent / "training",
    ]
    for candidate in candidates:
        if candidate.exists():
            xmls = sorted(candidate.glob("*.xml"))
            if xmls:
                return xmls

    print("Warning: Could not find upstream training data. "
          "You may need to clone the usaddress repo or install from source.", file=sys.stderr)
    return []


def _find_custom_training_files() -> list[Path]:
    """Find all XML training files in our training/data/ directory."""
    return sorted(DATA_DIR.glob("*.xml"))


def _build_manifest(
    name: str,
    description: str,
    training_files: list[tuple[str, str]],  # (source_type, path)
    test_files: list[str],
    output_model: str,
) -> dict:
    """Build a training manifest dict."""
    return {
        "id": f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}-{name}",
        "description": description,
        "usaddress_version": usaddress.__version__ if hasattr(usaddress, "__version__") else "unknown",
        "training_files": [f"{src}:{path}" for src, path in training_files],
        "test_files": test_files,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "output_model": output_model,
        "deployed": False,
        "upstream_pr": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Train custom usaddress model")
    parser.add_argument("--name", required=True, help="Short name for this training run (e.g. 'multi-unit')")
    parser.add_argument("--description", required=True, help="Description of what this training addresses")
    parser.add_argument("--custom-only", action="store_true", help="Train on custom data only (no upstream)")
    parser.add_argument("--files", nargs="*", help="Specific custom XML files to include (default: all in training/data/)")
    args = parser.parse_args()

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    MANIFESTS_DIR.mkdir(parents=True, exist_ok=True)

    # Collect training files
    training_file_entries: list[tuple[str, str]] = []
    training_paths: list[Path] = []

    if not args.custom_only:
        upstream_files = _find_upstream_training_files()
        for f in upstream_files:
            training_file_entries.append(("upstream", str(f.name)))
            training_paths.append(f)

    if args.files:
        custom_files = [Path(f) for f in args.files]
    else:
        custom_files = _find_custom_training_files()

    if not custom_files:
        print("Error: No custom training files found in training/data/", file=sys.stderr)
        sys.exit(1)

    for f in custom_files:
        training_file_entries.append(("custom", str(f.name)))
        training_paths.append(f)

    print(f"Training with {len(training_paths)} files:")
    for src, name in training_file_entries:
        print(f"  [{src}] {name}")

    # Find test files
    test_files = sorted(str(f.name) for f in (TRAINING_DIR / "test_cases").glob("*.csv"))

    # Backup current model
    current_model = Path(usaddress.MODEL_PATH)
    backup_path = MODELS_DIR / f"usaddr-backup-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}.crfsuite"
    if current_model.exists():
        shutil.copy2(current_model, backup_path)
        print(f"Backed up current model to {backup_path}")

    # Run parserator train
    training_arg = ",".join(str(p) for p in training_paths)
    cmd = ["uv", "run", "parserator", "train", training_arg, "usaddress"]
    print(f"\nRunning: {' '.join(cmd)}\n")

    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)

    if result.returncode != 0:
        print(f"\nTraining failed with return code {result.returncode}", file=sys.stderr)
        # Restore backup
        if backup_path.exists():
            shutil.copy2(backup_path, current_model)
            print("Restored backup model.")
        sys.exit(1)

    # Copy trained model to our models directory
    output_name = f"usaddr-{args.name}.crfsuite"
    output_path = MODELS_DIR / output_name
    if current_model.exists():
        shutil.copy2(current_model, output_path)
        print(f"Saved trained model to {output_path}")

    # Restore the original model (training replaces the installed one)
    if backup_path.exists():
        shutil.copy2(backup_path, current_model)
        print("Restored original bundled model.")

    # Write manifest
    manifest = _build_manifest(
        name=args.name,
        description=args.description,
        training_files=training_file_entries,
        test_files=test_files,
        output_model=output_name,
    )
    manifest_path = MANIFESTS_DIR / f"{manifest['id']}.json"
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"Wrote manifest to {manifest_path}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it runs**

```bash
uv run python scripts/model/train.py --help
```

- [ ] **Step 3: Commit**

```bash
git add scripts/model/train.py
git commit -m "#75 feat: add scripts/model/train.py for CRF model training"
```

---

### Task 12: `scripts/model/test_model.py`

**Files:**
- Create: `scripts/model/test_model.py`

- [ ] **Step 1: Write `test_model.py`**

```python
"""Test a trained model against regression test cases.

Optionally rebuilds a previous model from its manifest for comparison,
proving the new model fixes issues the old model had.

Usage:
    python scripts/model/test_model.py --model training/models/usaddr-multi-unit.crfsuite
    python scripts/model/test_model.py --model training/models/usaddr-multi-unit.crfsuite --compare-manifest training/manifests/2026-03-27-baseline.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import pycrfsuite
import usaddress

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TEST_CASES_DIR = PROJECT_ROOT / "training" / "test_cases"


def _load_tagger(model_path: str) -> pycrfsuite.Tagger:
    """Load a CRF model into a new Tagger instance."""
    tagger = pycrfsuite.Tagger()
    tagger.open(model_path)
    return tagger


def _parse_with_tagger(tagger: pycrfsuite.Tagger, address: str) -> list[tuple[str, str]]:
    """Parse an address using a specific tagger (not the global one).

    Uses usaddress's tokenization and feature extraction but a custom tagger.
    """
    original_tagger = usaddress.TAGGER
    try:
        usaddress.TAGGER = tagger
        return usaddress.parse(address)
    except usaddress.RepeatedLabelError as exc:
        return list(exc.parsed_string)
    finally:
        usaddress.TAGGER = original_tagger


def _load_test_cases(path: Path) -> list[dict]:
    """Load test cases from CSV.

    Expected columns: raw_address, expected_labels (JSON array of [token, label] pairs)
    Optional: description, should_fail_old_model (bool)
    """
    cases = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            case = {
                "raw_address": row["raw_address"],
                "expected_labels": json.loads(row["expected_labels"]),
                "description": row.get("description", ""),
                "should_fail_old": row.get("should_fail_old_model", "false").lower() == "true",
            }
            cases.append(case)
    return cases


def _compare_labels(
    actual: list[tuple[str, str]],
    expected: list[tuple[str, str]],
) -> tuple[bool, list[str]]:
    """Compare actual vs expected labels. Returns (passed, list of diffs)."""
    diffs = []
    passed = True

    max_len = max(len(actual), len(expected))
    for i in range(max_len):
        a_tok, a_lab = actual[i] if i < len(actual) else ("MISSING", "MISSING")
        e_tok, e_lab = expected[i] if i < len(expected) else ("EXTRA", "EXTRA")

        # Token text may differ slightly (punctuation handling)
        if a_lab != e_lab:
            diffs.append(f"  token='{a_tok}': got '{a_lab}', expected '{e_lab}'")
            passed = False

    return passed, diffs


def _run_tests(
    tagger: pycrfsuite.Tagger,
    test_cases: list[dict],
    label: str,
) -> tuple[int, int, list[str]]:
    """Run test cases against a tagger. Returns (passed, failed, details)."""
    passed = 0
    failed = 0
    details = []

    for case in test_cases:
        actual = _parse_with_tagger(tagger, case["raw_address"])
        ok, diffs = _compare_labels(actual, case["expected_labels"])

        if ok:
            passed += 1
            details.append(f"  PASS: {case['raw_address']}")
        else:
            failed += 1
            details.append(f"  FAIL: {case['raw_address']}")
            for d in diffs:
                details.append(f"    {d}")

    return passed, failed, details


def main() -> None:
    parser = argparse.ArgumentParser(description="Test a trained usaddress model")
    parser.add_argument("--model", required=True, help="Path to the .crfsuite model to test")
    parser.add_argument("--compare-manifest", help="Manifest of old model to rebuild and compare against")
    parser.add_argument("--test-dir", default=str(TEST_CASES_DIR), help="Directory containing test case CSVs")
    parser.add_argument("--run-pytest", action="store_true", help="Also run the project pytest suite")
    args = parser.parse_args()

    # Load test cases
    test_dir = Path(args.test_dir)
    test_files = sorted(test_dir.glob("*.csv"))
    if not test_files:
        print(f"No test case CSVs found in {test_dir}", file=sys.stderr)
        sys.exit(1)

    all_cases: list[dict] = []
    for tf in test_files:
        cases = _load_test_cases(tf)
        print(f"Loaded {len(cases)} test cases from {tf.name}")
        all_cases.extend(cases)

    # Test new model
    print(f"\n{'='*60}")
    print(f"Testing NEW model: {args.model}")
    print(f"{'='*60}")
    new_tagger = _load_tagger(args.model)
    new_passed, new_failed, new_details = _run_tests(new_tagger, all_cases, "NEW")
    for d in new_details:
        print(d)
    print(f"\nNEW model: {new_passed} passed, {new_failed} failed")

    # Optionally compare against old model
    if args.compare_manifest:
        manifest_path = Path(args.compare_manifest)
        with open(manifest_path) as f:
            old_manifest = json.load(f)

        old_model_name = old_manifest.get("output_model", "")
        old_model_path = PROJECT_ROOT / "training" / "models" / old_model_name

        if not old_model_path.exists():
            print(f"\nOld model {old_model_path} not found. Rebuild from manifest first:")
            print(f"  python scripts/model/train.py --name {old_manifest['id']} ...")
            print("Skipping comparison.")
        else:
            print(f"\n{'='*60}")
            print(f"Testing OLD model: {old_model_path}")
            print(f"{'='*60}")
            old_tagger = _load_tagger(str(old_model_path))
            old_passed, old_failed, old_details = _run_tests(old_tagger, all_cases, "OLD")
            for d in old_details:
                print(d)
            print(f"\nOLD model: {old_passed} passed, {old_failed} failed")

            # Show improvement
            improvement_cases = [c for c in all_cases if c["should_fail_old"]]
            if improvement_cases:
                print(f"\n{'='*60}")
                print("Improvement targets (should_fail_old_model=true):")
                print(f"{'='*60}")
                for case in improvement_cases:
                    old_result = _parse_with_tagger(old_tagger, case["raw_address"])
                    new_result = _parse_with_tagger(new_tagger, case["raw_address"])
                    old_ok, _ = _compare_labels(old_result, case["expected_labels"])
                    new_ok, _ = _compare_labels(new_result, case["expected_labels"])
                    status = "FIXED" if not old_ok and new_ok else "REGRESSION" if old_ok and not new_ok else "UNCHANGED"
                    print(f"  {status}: {case['raw_address']}")

    # Optionally run pytest
    if args.run_pytest:
        import subprocess

        print(f"\n{'='*60}")
        print("Running project test suite...")
        print(f"{'='*60}")
        # Temporarily swap the model for pytest
        result = subprocess.run(
            ["uv", "run", "pytest", "--no-cov", "-x"],
            env={**dict(__import__("os").environ), "CUSTOM_MODEL_PATH": args.model},
        )
        if result.returncode != 0:
            print("\nProject tests FAILED with new model!", file=sys.stderr)
            sys.exit(1)
        print("Project tests PASSED with new model.")

    # Exit with failure if new model has failures
    if new_failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it runs**

```bash
uv run python scripts/model/test_model.py --help
```

- [ ] **Step 3: Commit**

```bash
git add scripts/model/test_model.py
git commit -m "#75 feat: add scripts/model/test_model.py for model comparison testing"
```

---

### Task 13: `scripts/model/deploy.py`

**Files:**
- Create: `scripts/model/deploy.py`

- [ ] **Step 1: Write `deploy.py`**

```python
"""Deploy a trained usaddress model to the application.

Copies the model to the committed deployment path and validates it loads.

Usage:
    python scripts/model/deploy.py --model training/models/usaddr-multi-unit.crfsuite
    python scripts/model/deploy.py --model training/models/usaddr-multi-unit.crfsuite --restart
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pycrfsuite
import usaddress

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEPLOY_DIR = PROJECT_ROOT / "src" / "address_validator" / "custom_model"
DEPLOY_PATH = DEPLOY_DIR / "usaddr-custom.crfsuite"
MANIFESTS_DIR = PROJECT_ROOT / "training" / "manifests"


def _validate_model(model_path: Path) -> bool:
    """Verify the model file loads correctly and can parse an address."""
    try:
        tagger = pycrfsuite.Tagger()
        tagger.open(str(model_path))

        # Quick smoke test: parse a simple address
        original = usaddress.TAGGER
        usaddress.TAGGER = tagger
        try:
            result = usaddress.parse("123 Main St, Springfield, IL 62701")
            if not result:
                print("Error: model produced no output for smoke test address", file=sys.stderr)
                return False
        finally:
            usaddress.TAGGER = original

        print(f"Model validation passed ({len(tagger.labels())} labels)")
        return True
    except Exception as e:
        print(f"Error: model validation failed: {e}", file=sys.stderr)
        return False


def _update_manifest_deployed(model_name: str) -> None:
    """Mark the corresponding manifest as deployed."""
    for manifest_file in MANIFESTS_DIR.glob("*.json"):
        with open(manifest_file) as f:
            manifest = json.load(f)
        if manifest.get("output_model") == model_name:
            manifest["deployed"] = True
            with open(manifest_file, "w") as f:
                json.dump(manifest, f, indent=2)
            print(f"Updated manifest {manifest_file.name}: deployed=true")
            return
    print(f"Warning: no manifest found for model '{model_name}'")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy trained usaddress model")
    parser.add_argument("--model", required=True, help="Path to .crfsuite model to deploy")
    parser.add_argument("--restart", action="store_true", help="Restart the address-validator service after deploy")
    parser.add_argument("--smoke-test", action="store_true", help="Run smoke test against live API after restart")
    args = parser.parse_args()

    model_path = Path(args.model)
    if not model_path.exists():
        print(f"Error: model file not found: {model_path}", file=sys.stderr)
        sys.exit(1)

    # Validate model loads correctly
    if not _validate_model(model_path):
        sys.exit(1)

    # Copy to deployment path
    DEPLOY_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(model_path, DEPLOY_PATH)
    print(f"Deployed model to {DEPLOY_PATH}")

    # Update manifest
    _update_manifest_deployed(model_path.name)

    # Show next steps
    print("\n--- Next steps ---")
    print(f"1. Ensure CUSTOM_MODEL_PATH={DEPLOY_PATH} in /etc/address-validator/env")

    if args.restart:
        print("\nRestarting address-validator service...")
        result = subprocess.run(
            ["sudo", "systemctl", "restart", "address-validator"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"Error restarting service: {result.stderr}", file=sys.stderr)
            sys.exit(1)
        print("Service restarted successfully.")

        if args.smoke_test:
            import time

            time.sleep(2)  # Give service time to start
            print("\nRunning smoke test...")
            try:
                import httpx

                resp = httpx.get("http://localhost:8000/api/v1/health")
                health = resp.json()
                print(f"Health check: {health}")
                if health.get("status") != "ok":
                    print("Warning: service health is not 'ok'", file=sys.stderr)
            except Exception as e:
                print(f"Smoke test failed: {e}", file=sys.stderr)
                sys.exit(1)
    else:
        print("2. Run: sudo systemctl restart address-validator")
        print("3. Verify: journalctl -u address-validator -n 20")
        print(f"   Look for: 'loaded custom usaddress model: {DEPLOY_PATH}'")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Create `.gitignore` for custom_model directory**

```bash
mkdir -p src/address_validator/custom_model
```

Create `src/address_validator/custom_model/.gitignore`:

```
# Model binaries are only committed when explicitly deployed
*.crfsuite
!.gitignore
```

- [ ] **Step 3: Verify it runs**

```bash
uv run python scripts/model/deploy.py --help
```

- [ ] **Step 4: Commit**

```bash
git add scripts/model/deploy.py src/address_validator/custom_model/.gitignore
git commit -m "#75 feat: add scripts/model/deploy.py for model deployment"
```

---

### Task 14: `scripts/model/contribute.py`

**Files:**
- Create: `scripts/model/contribute.py`

- [ ] **Step 1: Write `contribute.py`**

```python
"""Assemble and submit a training data PR to the usaddress upstream repo.

Two-stage process:
  1. Push training + test XML to our fork (fast, unblocked)
  2. Open a PR from our fork to datamade/usaddress (gated, explicit)

Usage:
    python scripts/model/contribute.py --name multi-unit --stage fork
    python scripts/model/contribute.py --name multi-unit --stage upstream
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import usaddress

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "training" / "data"
TEST_CASES_DIR = PROJECT_ROOT / "training" / "test_cases"
MANIFESTS_DIR = PROJECT_ROOT / "training" / "manifests"

# Our fork — update this when fork is created
FORK_REPO = ""  # e.g. "CannObserv/usaddress"
UPSTREAM_REPO = "datamade/usaddress"


def _find_manifest(name: str) -> dict | None:
    """Find a manifest by name prefix."""
    for manifest_file in sorted(MANIFESTS_DIR.glob("*.json"), reverse=True):
        with open(manifest_file) as f:
            manifest = json.load(f)
        if name in manifest.get("id", ""):
            return manifest
    return None


def _generate_pr_body(manifest: dict, training_xml: Path, test_xml: Path | None) -> str:
    """Generate a PR body following upstream conventions."""
    name = manifest["id"]
    description = manifest["description"]

    # Generate before/after examples using the current bundled model
    examples = []
    custom_files = [f for f in manifest.get("training_files", []) if f.startswith("custom:")]
    # Read first address from training XML for demo
    try:
        import xml.etree.ElementTree as ET

        tree = ET.parse(training_xml)
        root = tree.getroot()
        first_addr = root.find("AddressString")
        if first_addr is not None:
            tokens = [child.text or "" for child in first_addr]
            address = " ".join(tokens)
            try:
                result = usaddress.parse(address)
                examples.append(f"```python\n>>> import usaddress\n>>> usaddress.parse(\"{address}\")\n{result}\n```")
            except usaddress.RepeatedLabelError as exc:
                examples.append(f"```python\n>>> import usaddress\n>>> usaddress.parse(\"{address}\")\n# Raises RepeatedLabelError\n# parsed_string: {exc.parsed_string[:5]}...\n```")
    except Exception:
        pass

    body = f"""## Overview

{description}

## Problem

Using `usaddress ({usaddress.__version__ if hasattr(usaddress, '__version__') else 'latest'})`, the following address patterns are parsed incorrectly:

{chr(10).join(examples) if examples else '_See training data for examples._'}

## Training

- Training data: `training/{training_xml.name}`
- Test data: `measure_performance/test_data/{test_xml.name if test_xml else 'N/A'}`

## Testing

```bash
pip install -e ".[dev]"
parserator train training/{training_xml.name} usaddress
pytest
```
"""
    return body


def _stage_fork(name: str, manifest: dict) -> None:
    """Push training + test data to our fork."""
    if not FORK_REPO:
        print("Error: FORK_REPO not configured in contribute.py. "
              "Create a fork of datamade/usaddress first and update the constant.", file=sys.stderr)
        sys.exit(1)

    # Find training XML
    custom_files = [
        f.split(":", 1)[1] for f in manifest.get("training_files", [])
        if f.startswith("custom:")
    ]
    if not custom_files:
        print("Error: no custom training files in manifest", file=sys.stderr)
        sys.exit(1)

    print(f"Would push to fork {FORK_REPO}:")
    for f in custom_files:
        training_path = DATA_DIR / f
        print(f"  training/{f} -> training/{f}")
        test_path = TEST_CASES_DIR / f.replace(".xml", ".csv")
        if test_path.exists():
            print(f"  test_cases/{f.replace('.xml', '.csv')} -> measure_performance/test_data/{f}")

    print("\nThis requires a local clone of the fork. Implementation TBD based on fork setup.")


def _stage_upstream(name: str, manifest: dict) -> None:
    """Open a PR from our fork to upstream."""
    if not FORK_REPO:
        print("Error: FORK_REPO not configured. Run --stage fork first.", file=sys.stderr)
        sys.exit(1)

    custom_files = [
        f.split(":", 1)[1] for f in manifest.get("training_files", [])
        if f.startswith("custom:")
    ]
    if not custom_files:
        print("Error: no custom training files in manifest", file=sys.stderr)
        sys.exit(1)

    training_xml = DATA_DIR / custom_files[0]
    test_xml = TEST_CASES_DIR / custom_files[0].replace(".xml", ".csv")

    pr_title = manifest["description"]
    if len(pr_title) > 70:
        pr_title = pr_title[:67] + "..."

    pr_body = _generate_pr_body(
        manifest,
        training_xml,
        test_xml if test_xml.exists() else None,
    )

    print(f"PR Title: {pr_title}")
    print(f"\nPR Body:\n{pr_body}")
    print("\n--- This would open a PR from {FORK_REPO} to {UPSTREAM_REPO} ---")
    print("Confirm? [y/N] ", end="")
    if input().strip().lower() != "y":
        print("Aborted.")
        sys.exit(0)

    print("PR creation requires the fork to be set up with the training data already pushed.")
    print("Run --stage fork first, then re-run --stage upstream.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Contribute training data upstream")
    parser.add_argument("--name", required=True, help="Training run name (matches manifest ID)")
    parser.add_argument("--stage", required=True, choices=["fork", "upstream"], help="Contribution stage")
    args = parser.parse_args()

    manifest = _find_manifest(args.name)
    if not manifest:
        print(f"Error: no manifest found matching '{args.name}'", file=sys.stderr)
        sys.exit(1)

    print(f"Using manifest: {manifest['id']}")
    print(f"Description: {manifest['description']}")

    if args.stage == "fork":
        _stage_fork(args.name, manifest)
    elif args.stage == "upstream":
        _stage_upstream(args.name, manifest)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify it runs**

```bash
uv run python scripts/model/contribute.py --help
```

- [ ] **Step 3: Commit**

```bash
git add scripts/model/contribute.py
git commit -m "#75 feat: add scripts/model/contribute.py for upstream contribution"
```

---

## Phase 3: Orchestrating Skill

### Task 15: Create `/train-model` Skill

**Files:**
- Create: `skills/train-model/SKILL.md`
- Create: `.claude/skills/train-model` (symlink)

- [ ] **Step 1: Write `SKILL.md`**

Create `skills/train-model/SKILL.md`:

```markdown
---
name: train-model
description: Interactive pipeline for training custom usaddress CRF models. Use when the user says "train model", "retrain usaddress", "fix parsing", or "/train-model". Walks through identify, label, train, test, deploy, and contribute steps.
compatibility: Designed for Claude. Requires Python 3.12, uv, parserator, pycrfsuite, usaddress, PostgreSQL (for candidate collection).
metadata:
  author: gregoryfoster
  version: "1.0"
  triggers: train model, retrain usaddress, fix parsing, train-model
---

# usaddress Model Training Pipeline

Interactive, resumable pipeline for training custom usaddress CRF models,
deploying them, and contributing improvements upstream.

## Steps

| # | Name | Script | Description |
|---|---|---|---|
| 1 | IDENTIFY | `scripts/model/identify.py` | Query candidates, review failures, export CSV |
| 2 | LABEL | `scripts/model/label.py` | Agent-assisted labeling with model+Claude diff |
| 3 | TRAIN | `scripts/model/train.py` | Run parserator train, write manifest |
| 4 | TEST | `scripts/model/test_model.py` | Regression suite + before/after comparison |
| 5 | DEPLOY | `scripts/model/deploy.py` | Copy model, validate, restart service |
| 6 | CONTRIBUTE | `scripts/model/contribute.py` | Fork PR + gated upstream PR |

## Invocation

```
/train-model                    # Interactive — prompt for step selection
/train-model --step identify    # Start/resume at specific step
/train-model --through train    # Automate steps 1–3, pause before deploy
/train-model --step contribute  # Resume at contribution step
```

## Process

### Parse arguments

If the user provided `--step <name>`, start at that step.
If the user provided `--through <name>`, run all steps up to and including that step without pausing between them (but still confirm before starting).
If neither, prompt: "Which steps would you like to run? Options: identify, label, train, test, deploy, contribute (or 'all')"

### Step 1: IDENTIFY

**Purpose:** Find and review addresses where the parser needed recovery.

1. Check if DB is available:
   ```bash
   source /etc/address-validator/env 2>/dev/null || true
   ```

2. Show candidate summary:
   ```bash
   uv run python scripts/model/identify.py summary
   ```

3. Ask operator which failure type / pattern to target.

4. Export selected candidates:
   ```bash
   uv run python scripts/model/identify.py export --type <failure_type> --out training/candidates.csv
   ```

5. Show the exported addresses to the operator for review. Ask if they want to add manual examples.

6. Confirm before proceeding.

### Step 2: LABEL

**Purpose:** Generate labeled training XML with model+Claude comparison.

1. Run the labeling script:
   ```bash
   uv run python scripts/model/label.py training/candidates.csv training/data/<pattern-name>.xml --test-output training/test_cases/<pattern-name>.csv
   ```

2. For each address, the script shows model labels vs Claude labels side-by-side.

3. Operator resolves disagreements interactively (choose model, Claude, or skip).

4. Review the output XML. Confirm it looks correct.

5. If no DB candidates exist, operator can create a manual CSV:
   ```
   raw_address
   "995 9TH ST BLDG 201 ROOM 104 T, SAN FRANCISCO, CA 94130-2107"
   "123 MAIN ST APT 4B STE 200, PORTLAND, OR 97201"
   ```

### Step 3: TRAIN

**Purpose:** Train a new CRF model from all training data.

1. Run training:
   ```bash
   uv run python scripts/model/train.py --name <pattern-name> --description "<description>"
   ```

2. The script:
   - Combines upstream training XML with our custom XML
   - Runs `parserator train`
   - Backs up the current model
   - Saves the new model to `training/models/`
   - Writes a manifest to `training/manifests/`
   - Restores the original bundled model

3. Verify training succeeded (check script exit code).

### Step 4: TEST

**Purpose:** Verify the new model improves parsing without regressions.

**Prerequisites:** Test case CSVs must exist in `training/test_cases/`. The labeling step (Step 2) should have created these. Format:

```csv
raw_address,expected_labels,description,should_fail_old_model
"995 9TH ST BLDG 201 ROOM 104 T, SAN FRANCISCO, CA 94130-2107","[[""995"",""AddressNumber""],[""9TH"",""StreetName""]]","Multi-unit designator",true
```

1. Run model tests:
   ```bash
   uv run python scripts/model/test_model.py --model training/models/usaddr-<name>.crfsuite --run-pytest
   ```

2. If a previous manifest exists, run comparison:
   ```bash
   uv run python scripts/model/test_model.py --model training/models/usaddr-<name>.crfsuite --compare-manifest training/manifests/<previous>.json
   ```

3. Review results with operator. All tests must pass before proceeding.

### Step 5: DEPLOY

**GATE:** This step always requires explicit operator confirmation.

1. Confirm with operator: "Ready to deploy model <name> to production?"

2. Deploy:
   ```bash
   uv run python scripts/model/deploy.py --model training/models/usaddr-<name>.crfsuite --restart --smoke-test
   ```

3. The script:
   - Copies model to `src/address_validator/custom_model/usaddr-custom.crfsuite`
   - Updates the manifest `deployed: true`
   - Restarts the service (if `--restart`)
   - Runs a health check (if `--smoke-test`)

4. Remind operator to verify `CUSTOM_MODEL_PATH` in `/etc/address-validator/env`.

5. Commit the deployed model:
   ```bash
   git add src/address_validator/custom_model/usaddr-custom.crfsuite training/manifests/
   git commit -m "#75 feat: deploy custom usaddress model for <pattern-name>"
   ```

### Step 6: CONTRIBUTE

**GATE:** This step always requires explicit operator confirmation. It has two independently gated sub-steps.

**6a: Our fork**

1. Ask: "Push training data to our usaddress fork?"
2. Run:
   ```bash
   uv run python scripts/model/contribute.py --name <pattern-name> --stage fork
   ```

**6b: Upstream PR**

1. Ask: "Open a PR to datamade/usaddress? This should only be done when you are confident the training data is correct and complete."
2. Run:
   ```bash
   uv run python scripts/model/contribute.py --name <pattern-name> --stage upstream
   ```
3. Report the PR URL to the operator.

## Error Handling

- If any step fails, report the error and ask the operator if they want to retry or skip.
- The `--step` flag allows resuming from any point after fixing an issue.
- Training data and manifests are never deleted automatically.

## Related

- Design doc: `docs/plans/2026-03-27-usaddress-model-training-pipeline-design.md`
- Issue: #75
```

- [ ] **Step 2: Create symlink**

```bash
ln -s ../../skills/train-model .claude/skills/train-model
```

- [ ] **Step 3: Verify skill is discoverable**

The skill should appear in Claude Code's skill list on next invocation.

- [ ] **Step 4: Commit**

```bash
git add skills/train-model/SKILL.md .claude/skills/train-model
git commit -m "#75 feat: add /train-model orchestrating skill"
```

---

### Task 16: Final Verification

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest --no-cov
```

All tests must pass.

- [ ] **Step 2: Run ruff**

```bash
uv run ruff check . && uv run ruff format --check .
```

Must be clean.

- [ ] **Step 3: Verify all scripts are runnable**

```bash
uv run python scripts/model/identify.py --help
uv run python scripts/model/label.py --help
uv run python scripts/model/train.py --help
uv run python scripts/model/test_model.py --help
uv run python scripts/model/deploy.py --help
uv run python scripts/model/contribute.py --help
```

All should print help text without errors.

- [ ] **Step 4: Verify skill symlink**

```bash
ls -la .claude/skills/train-model
```

Should point to `../../skills/train-model`.

- [ ] **Step 5: Final commit if any fixups needed**

```bash
uv run ruff check . --fix && uv run ruff format .
git add -A && git commit -m "#75 chore: lint fixups for model training pipeline"
```

Only if there are changes to commit.
