# Admin Dashboard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an admin dashboard at `/admin/` with audit logging, journal backfill, and per-endpoint/per-provider detail views.

**Architecture:** ASGI audit middleware writes every API request to a new `audit_log` PostgreSQL table via fire-and-forget `asyncio.create_task`. Validation-specific fields (provider, status, cache hit) are passed through ContextVars set in the cache provider. The dashboard is server-side rendered with Jinja2 + HTMX, styled with Tailwind CSS (standalone CLI binary, pre-commit hook). Authentication uses exe.dev proxy headers (`X-ExeDev-UserID`, `X-ExeDev-Email`). A one-shot backfill script reconstructs ~15 days of history from journalctl.

**Tech Stack:** FastAPI, Jinja2, HTMX 1.9 (CDN), Tailwind CSS standalone CLI, pre-commit, asyncpg/SQLAlchemy (existing), Alembic (existing)

**Design doc:** `docs/plans/2026-03-20-admin-dashboard-design.md`

---

## Scope Check

This plan covers 6 independent subsystems that build on each other in order:

1. **Tooling** — pre-commit setup, Tailwind build pipeline
2. **Audit log schema** — Alembic migration + DB table
3. **Audit middleware** — ContextVars + ASGI middleware
4. **Dashboard foundation** — auth, templates, static files, base layout
5. **Dashboard views** — landing, audit log, per-endpoint, per-provider
6. **Journal backfill** — one-shot script

Each task produces working, testable software on its own.

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `.pre-commit-config.yaml` | Ruff lint/format + Tailwind build hooks |
| Create | `tailwind.config.js` | Tailwind content paths + custom theme |
| Create | `scripts/download-tailwind.sh` | Download platform-specific Tailwind CLI binary |
| Create | `scripts/build-css.sh` | Run Tailwind CLI build |
| Create | `scripts/pre-commit-tailwind.sh` | Pre-commit wrapper: build + auto-stage |
| Create | `alembic/versions/004_audit_log.py` | `audit_log` table + indices |
| Create | `src/address_validator/services/audit.py` | `_audit_*` ContextVars, `write_audit_row()`, `get_engine` import |
| Create | `src/address_validator/middleware/audit.py` | ASGI middleware: timing, IP, fire-and-forget write |
| Create | `src/address_validator/routers/admin/__init__.py` | Empty package init |
| Create | `src/address_validator/routers/admin/router.py` | Top-level admin router, includes sub-routers |
| Create | `src/address_validator/routers/admin/deps.py` | `AdminUser` dataclass, `get_admin_user()` dependency |
| Create | `src/address_validator/routers/admin/queries.py` | Shared SQL query helpers for dashboard views |
| Create | `src/address_validator/routers/admin/dashboard.py` | `GET /admin/` — landing page with stat cards |
| Create | `src/address_validator/routers/admin/audit_views.py` | `GET /admin/audit/` — full audit log with filters |
| Create | `src/address_validator/routers/admin/endpoints.py` | `GET /admin/endpoints/{name}` — per-endpoint detail |
| Create | `src/address_validator/routers/admin/providers.py` | `GET /admin/providers/{name}` — per-provider detail |
| Create | `src/address_validator/templates/admin/base.html` | Layout shell: topbar + sidebar + main |
| Create | `src/address_validator/templates/admin/dashboard.html` | Landing page stat cards |
| Create | `src/address_validator/templates/admin/audit/list.html` | Full audit log table |
| Create | `src/address_validator/templates/admin/audit/_rows.html` | HTMX partial: audit table rows |
| Create | `src/address_validator/templates/admin/endpoints/detail.html` | Per-endpoint stats + filtered audit |
| Create | `src/address_validator/templates/admin/endpoints/_rows.html` | HTMX partial: endpoint audit rows |
| Create | `src/address_validator/templates/admin/providers/detail.html` | Per-provider stats + filtered audit |
| Create | `src/address_validator/templates/admin/providers/_rows.html` | HTMX partial: provider audit rows |
| Create | `src/address_validator/static/admin/css/input.css` | Tailwind directives + HTMX loading styles |
| Create | `src/address_validator/static/admin/css/tailwind.css` | Committed build output (auto-generated) |
| Create | `scripts/backfill_audit_log.py` | One-shot journal → audit_log backfill |
| Create | `tests/unit/test_audit_service.py` | Audit service ContextVar + write tests |
| Create | `tests/unit/test_audit_middleware.py` | Audit middleware unit tests |
| Create | `tests/unit/test_admin_deps.py` | Admin auth dependency tests |
| Create | `tests/unit/test_admin_views.py` | Dashboard view integration tests |
| Create | `tests/unit/test_admin_queries.py` | SQL query helper tests |
| Create | `tests/unit/conftest.py` | Re-exports `db` fixture for all unit tests |
| Modify | `src/address_validator/main.py` | Add audit middleware, mount admin router + static files |
| Modify | `src/address_validator/services/validation/cache_provider.py` | Set audit ContextVars (provider, status, cache_hit) |
| Modify | `pyproject.toml` | Add `jinja2`, `pre-commit` dev deps |
| Modify | `.gitignore` | Add `scripts/bin/` |
| Modify | `tests/conftest.py` | Add admin auth fixtures |
| Modify | `tests/unit/validation/conftest.py` | Add `audit_log` to TRUNCATE statement |

---

### Task 1: Pre-commit Setup

**Files:**
- Create: `.pre-commit-config.yaml`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add pre-commit as dev dependency**

```bash
uv add --dev pre-commit
```

- [ ] **Step 2: Create `.pre-commit-config.yaml`**

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.4
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
```

Note: The Tailwind hook will be added in Task 2. Start with ruff only.

- [ ] **Step 3: Install pre-commit hooks**

Run: `uv run pre-commit install`
Expected: `pre-commit installed at .git/hooks/pre-commit`

- [ ] **Step 4: Verify ruff hooks work**

Run: `uv run pre-commit run --all-files`
Expected: `ruff.....Passed` and `ruff-format.....Passed` (or auto-fixes applied)

- [ ] **Step 5: Commit**

```bash
git add .pre-commit-config.yaml pyproject.toml uv.lock
git commit -m "#43 chore: add pre-commit with ruff lint and format hooks"
```

---

### Task 2: Tailwind Build Pipeline

**Files:**
- Create: `scripts/download-tailwind.sh`
- Create: `scripts/build-css.sh`
- Create: `scripts/pre-commit-tailwind.sh`
- Create: `tailwind.config.js`
- Create: `src/address_validator/static/admin/css/input.css`
- Create: `src/address_validator/static/admin/css/tailwind.css`
- Modify: `.pre-commit-config.yaml`
- Modify: `.gitignore`

- [ ] **Step 1: Create `scripts/download-tailwind.sh`**

```bash
#!/usr/bin/env bash
# Download the Tailwind CSS standalone CLI binary for the current platform.
set -euo pipefail

TAILWIND_VERSION="v3.4.17"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="$SCRIPT_DIR/bin"
BINARY="$BIN_DIR/tailwindcss"

if [ -f "$BINARY" ]; then
    exit 0
fi

OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
ARCH="$(uname -m)"
case "$ARCH" in
    x86_64) ARCH="x64" ;;
    aarch64|arm64) ARCH="arm64" ;;
    *) echo "Unsupported architecture: $ARCH" >&2; exit 1 ;;
esac

URL="https://github.com/tailwindlabs/tailwindcss/releases/download/${TAILWIND_VERSION}/tailwindcss-${OS}-${ARCH}"

mkdir -p "$BIN_DIR"
echo "Downloading tailwindcss ${TAILWIND_VERSION} (${OS}-${ARCH})..."
curl -sL "$URL" -o "$BINARY"
chmod +x "$BINARY"
echo "Installed: $BINARY"
```

- [ ] **Step 2: Create `scripts/build-css.sh`**

```bash
#!/usr/bin/env bash
# Build the minified Tailwind CSS output from the input file.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Ensure binary is available
"$SCRIPT_DIR/download-tailwind.sh"

"$SCRIPT_DIR/bin/tailwindcss" \
    -c "$PROJECT_ROOT/tailwind.config.js" \
    -i "$PROJECT_ROOT/src/address_validator/static/admin/css/input.css" \
    -o "$PROJECT_ROOT/src/address_validator/static/admin/css/tailwind.css" \
    --minify
```

- [ ] **Step 3: Create `scripts/pre-commit-tailwind.sh`**

```bash
#!/usr/bin/env bash
# Pre-commit hook: rebuild Tailwind CSS and stage the output if changed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT="src/address_validator/static/admin/css/tailwind.css"

"$SCRIPT_DIR/build-css.sh"

if ! git diff --quiet -- "$OUTPUT" 2>/dev/null; then
    git add "$OUTPUT"
fi
```

- [ ] **Step 4: Make scripts executable**

```bash
chmod +x scripts/download-tailwind.sh scripts/build-css.sh scripts/pre-commit-tailwind.sh
```

- [ ] **Step 5: Create `tailwind.config.js`**

```js
/** @type {import('tailwindcss').Config} */
module.exports = {
    content: [
        "./src/address_validator/templates/**/*.html",
    ],
    theme: {
        extend: {},
    },
}
```

- [ ] **Step 6: Create `src/address_validator/static/admin/css/input.css`**

```css
@tailwind base;
@tailwind components;
@tailwind utilities;

/* HTMX loading indicator */
.htmx-request .htmx-indicator {
    display: inline-block;
}
.htmx-indicator {
    display: none;
}
```

- [ ] **Step 7: Add `scripts/bin/` to `.gitignore`**

Append to `.gitignore`:
```
scripts/bin/
```

- [ ] **Step 8: Run the build to generate initial `tailwind.css`**

Run: `bash scripts/build-css.sh`
Expected: `src/address_validator/static/admin/css/tailwind.css` created (minified)

- [ ] **Step 9: Add Tailwind hook to `.pre-commit-config.yaml`**

Append after the ruff hooks:

```yaml
  - repo: local
    hooks:
      - id: build-tailwind-css
        name: Build Tailwind CSS
        language: system
        entry: scripts/pre-commit-tailwind.sh
        files: '^(src/address_validator/templates/.*\.html|src/address_validator/static/admin/css/input\.css|tailwind\.config\.js)$'
        pass_filenames: false
```

- [ ] **Step 10: Verify pre-commit with Tailwind hook**

Run: `uv run pre-commit run --all-files`
Expected: All hooks pass including `Build Tailwind CSS`

- [ ] **Step 11: Commit**

```bash
git add scripts/download-tailwind.sh scripts/build-css.sh scripts/pre-commit-tailwind.sh \
    tailwind.config.js \
    src/address_validator/static/admin/css/input.css \
    src/address_validator/static/admin/css/tailwind.css \
    .pre-commit-config.yaml .gitignore
git commit -m "#43 chore: add Tailwind CSS standalone build pipeline with pre-commit hook"
```

---

### Task 3: Alembic Migration — `audit_log` Table

**Files:**
- Create: `alembic/versions/004_audit_log.py`

- [ ] **Step 1: Write the migration**

```python
"""Add audit_log table for request tracking.

Revision ID: 004
Revises: 003
Create Date: 2026-03-20
"""

revision: str = "004"
down_revision: str = "003"
branch_labels = None
depends_on = None

import sqlalchemy as sa  # noqa: E402

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("client_ip", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("status_code", sa.SmallInteger(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("validation_status", sa.Text(), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_audit_ts", "audit_log", [sa.text("timestamp DESC")])
    op.create_index("idx_audit_ip", "audit_log", ["client_ip", sa.text("timestamp DESC")])
    op.create_index("idx_audit_endpoint", "audit_log", ["endpoint", sa.text("timestamp DESC")])
    op.create_index(
        "idx_audit_provider",
        "audit_log",
        ["provider", sa.text("timestamp DESC")],
        postgresql_where=sa.text("provider IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_audit_provider", table_name="audit_log")
    op.drop_index("idx_audit_endpoint", table_name="audit_log")
    op.drop_index("idx_audit_ip", table_name="audit_log")
    op.drop_index("idx_audit_ts", table_name="audit_log")
    op.drop_table("audit_log")
```

- [ ] **Step 2: Verify migration applies against test DB**

Run: `VALIDATION_CACHE_DSN="postgresql+asyncpg://address_validator:address_validator_dev@localhost/address_validator_test" uv run alembic upgrade head`
Expected: `Running upgrade 003 -> 004, Add audit_log table`

- [ ] **Step 3: Verify downgrade works**

Run: `VALIDATION_CACHE_DSN="postgresql+asyncpg://address_validator:address_validator_dev@localhost/address_validator_test" uv run alembic downgrade 003`
Expected: `Running downgrade 004 -> 003`

Then re-apply: `VALIDATION_CACHE_DSN="postgresql+asyncpg://address_validator:address_validator_dev@localhost/address_validator_test" uv run alembic upgrade head`

- [ ] **Step 4: Update test DB fixture to truncate audit_log**

In `tests/unit/validation/conftest.py`, update the TRUNCATE statement to include `audit_log`:

```python
await conn.execute(
    text("TRUNCATE validated_addresses, query_patterns, audit_log RESTART IDENTITY CASCADE")
)
```

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/004_audit_log.py tests/unit/validation/conftest.py
git commit -m "#43 feat: add audit_log table migration"
```

---

### Task 4: Audit ContextVars + Service Layer

**Files:**
- Create: `src/address_validator/services/audit.py`
- Modify: `src/address_validator/services/validation/cache_provider.py`
- Create: `tests/unit/test_audit_service.py`

- [ ] **Step 1: Write failing test for audit ContextVars**

Create `tests/unit/test_audit_service.py`:

```python
"""Tests for the audit service ContextVars and write helper."""

from address_validator.services.audit import (
    get_audit_cache_hit,
    get_audit_provider,
    get_audit_validation_status,
    reset_audit_context,
    set_audit_context,
)


def test_context_vars_default_to_none() -> None:
    assert get_audit_provider() is None
    assert get_audit_validation_status() is None
    assert get_audit_cache_hit() is None


def test_set_audit_context_sets_values() -> None:
    set_audit_context(provider="usps", validation_status="confirmed", cache_hit=False)
    assert get_audit_provider() == "usps"
    assert get_audit_validation_status() == "confirmed"
    assert get_audit_cache_hit() is False
    # Clean up
    reset_audit_context()


def test_reset_audit_context_clears_values() -> None:
    set_audit_context(provider="google", validation_status="not_confirmed", cache_hit=True)
    reset_audit_context()
    assert get_audit_provider() is None
    assert get_audit_validation_status() is None
    assert get_audit_cache_hit() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_audit_service.py -v --no-cov -x`
Expected: FAIL — `ModuleNotFoundError: No module named 'address_validator.services.audit'`

- [ ] **Step 3: Create `src/address_validator/services/audit.py`**

```python
"""Audit logging — ContextVars for passing validation metadata to middleware.

The audit middleware (middleware/audit.py) reads these ContextVars after the
request completes to enrich audit_log rows with validation-specific fields.
The cache provider sets them during validate() so the middleware doesn't need
to understand the validation pipeline.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_audit_provider: ContextVar[str | None] = ContextVar("audit_provider", default=None)
_audit_validation_status: ContextVar[str | None] = ContextVar(
    "audit_validation_status", default=None
)
_audit_cache_hit: ContextVar[bool | None] = ContextVar("audit_cache_hit", default=None)


def get_audit_provider() -> str | None:
    return _audit_provider.get()


def get_audit_validation_status() -> str | None:
    return _audit_validation_status.get()


def get_audit_cache_hit() -> bool | None:
    return _audit_cache_hit.get()


def reset_audit_context() -> None:
    """Reset all audit ContextVars to their defaults (None).

    Called at the start of each audited request to prevent stale values
    from a previous request leaking through on the same asyncio task.
    """
    _audit_provider.set(None)
    _audit_validation_status.set(None)
    _audit_cache_hit.set(None)


def set_audit_context(
    *,
    provider: str | None = None,
    validation_status: str | None = None,
    cache_hit: bool | None = None,
) -> None:
    """Set audit ContextVars for the current request."""
    if provider is not None:
        _audit_provider.set(provider)
    if validation_status is not None:
        _audit_validation_status.set(validation_status)
    if cache_hit is not None:
        _audit_cache_hit.set(cache_hit)


_INSERT_SQL = text("""
    INSERT INTO audit_log (
        timestamp, request_id, client_ip, method, endpoint,
        status_code, latency_ms, provider, validation_status,
        cache_hit, error_detail
    ) VALUES (
        :timestamp, :request_id, :client_ip, :method, :endpoint,
        :status_code, :latency_ms, :provider, :validation_status,
        :cache_hit, :error_detail
    )
""")


async def write_audit_row(
    engine: AsyncEngine,
    *,
    timestamp: datetime,
    request_id: str | None,
    client_ip: str,
    method: str,
    endpoint: str,
    status_code: int,
    latency_ms: int | None,
    provider: str | None,
    validation_status: str | None,
    cache_hit: bool | None,
    error_detail: str | None,
) -> None:
    """Insert a single audit_log row. Logs and swallows all errors (fail-open)."""
    try:
        async with engine.begin() as conn:
            await conn.execute(
                _INSERT_SQL,
                {
                    "timestamp": timestamp,
                    "request_id": request_id,
                    "client_ip": client_ip,
                    "method": method,
                    "endpoint": endpoint,
                    "status_code": status_code,
                    "latency_ms": latency_ms,
                    "provider": provider,
                    "validation_status": validation_status,
                    "cache_hit": cache_hit,
                    "error_detail": error_detail,
                },
            )
    except Exception:
        logger.warning("audit: failed to write audit row", exc_info=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_audit_service.py -v --no-cov -x`
Expected: PASS

- [ ] **Step 5: Write test for write_audit_row**

Add to `tests/unit/test_audit_service.py`:

```python
import pytest
from datetime import datetime, timezone
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from address_validator.services.audit import write_audit_row


@pytest.mark.asyncio
async def test_write_audit_row(db: AsyncEngine) -> None:
    """Verify write_audit_row inserts a row into audit_log."""
    await write_audit_row(
        db,
        timestamp=datetime.now(timezone.utc),
        request_id="01TESTULID",
        client_ip="127.0.0.1",
        method="POST",
        endpoint="/api/v1/validate",
        status_code=200,
        latency_ms=42,
        provider="usps",
        validation_status="confirmed",
        cache_hit=False,
        error_detail=None,
    )
    async with db.connect() as conn:
        result = await conn.execute(text("SELECT * FROM audit_log"))
        rows = result.fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row.client_ip == "127.0.0.1"
    assert row.endpoint == "/api/v1/validate"
    assert row.status_code == 200
    assert row.provider == "usps"


@pytest.mark.asyncio
async def test_write_audit_row_fail_open(db: AsyncEngine) -> None:
    """Verify write_audit_row swallows errors."""
    await db.dispose()  # break the engine
    # Should not raise
    await write_audit_row(
        db,
        timestamp=datetime.now(timezone.utc),
        request_id=None,
        client_ip="1.2.3.4",
        method="GET",
        endpoint="/api/v1/health",
        status_code=200,
        latency_ms=1,
        provider=None,
        validation_status=None,
        cache_hit=None,
        error_detail=None,
    )
```

Note: This test requires the `db` fixture from `tests/unit/validation/conftest.py`. Either move that fixture to the root conftest, or create a conftest in `tests/unit/` that imports it. The simplest approach: create `tests/unit/conftest.py` that re-exports the `db` and `run_cache_migrations` fixtures:

```python
"""Shared fixtures for all unit tests that need a database."""

from tests.unit.validation.conftest import db, run_cache_migrations  # noqa: F401
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_audit_service.py -v --no-cov -x`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/address_validator/services/audit.py tests/unit/test_audit_service.py tests/unit/conftest.py
git commit -m "#43 feat: add audit service with ContextVars and write helper"
```

---

### Task 5: Wire Audit ContextVars into Cache Provider

**Files:**
- Modify: `src/address_validator/services/validation/cache_provider.py`

The cache provider's `validate()` method is the nexus where we know: (a) whether the cache was hit, (b) the provider name, (c) the validation status. We set the audit ContextVars here so the middleware can read them after the response.

- [ ] **Step 1: Write failing test**

Add to `tests/unit/test_audit_service.py`:

```python
from unittest.mock import AsyncMock, patch

from address_validator.models import StandardizeResponseV1, ValidateResponseV1, ValidationResult
from address_validator.services.audit import get_audit_cache_hit, get_audit_provider, get_audit_validation_status


@pytest.mark.asyncio
async def test_cache_provider_sets_audit_context_on_hit(db: AsyncEngine) -> None:
    """CachingProvider sets audit ContextVars on cache hit."""
    from address_validator.services.validation.cache_provider import CachingProvider

    inner = AsyncMock()
    provider = CachingProvider(inner=inner, get_engine=AsyncMock(return_value=db), ttl_days=30)

    # First call: miss, delegates to inner
    result = ValidateResponseV1(
        country="US",
        validation=ValidationResult(status="confirmed", provider="usps"),
    )
    inner.validate.return_value = result

    std = StandardizeResponseV1(
        address_line_1="123 MAIN ST",
        city="ANYTOWN",
        region="WA",
        postal_code="98101",
        country="US",
        standardized="123 MAIN ST  ANYTOWN WA 98101",
        components=None,
        warnings=[],
        api_version="1",
    )

    await provider.validate(std)
    # After miss: provider and status should be set from the result
    assert get_audit_provider() == "usps"
    assert get_audit_validation_status() == "confirmed"
    assert get_audit_cache_hit() is False

    # Second call: cache hit
    result2 = await provider.validate(std)
    assert get_audit_cache_hit() is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_audit_service.py::test_cache_provider_sets_audit_context_on_hit -v --no-cov -x`
Expected: FAIL — audit ContextVars are not set by cache_provider yet

- [ ] **Step 3: Modify `cache_provider.py` to set audit ContextVars**

In `src/address_validator/services/validation/cache_provider.py`, add import at top:

```python
from address_validator.services.audit import set_audit_context
```

Then in the `validate()` method, after the cache lookup:

- On cache hit (after `if cached is not None:`): call `set_audit_context(provider=cached.validation.provider, validation_status=cached.validation.status, cache_hit=True)`
- On cache miss (after `result = await self._inner.validate(std)`): call `set_audit_context(provider=result.validation.provider, validation_status=result.validation.status, cache_hit=False)`

The exact edit locations depend on the current line numbers. The key insertions:

After `if cached is not None:` and before `return cached`:
```python
            set_audit_context(
                provider=cached.validation.provider,
                validation_status=cached.validation.status,
                cache_hit=True,
            )
```

After `result: ValidateResponseV1 = await self._inner.validate(std)`:
```python
        set_audit_context(
            provider=result.validation.provider,
            validation_status=result.validation.status,
            cache_hit=False,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_audit_service.py::test_cache_provider_sets_audit_context_on_hit -v --no-cov -x`
Expected: PASS

- [ ] **Step 5: Run all existing tests to verify no regressions**

Run: `uv run pytest --no-cov -x`
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/services/validation/cache_provider.py tests/unit/test_audit_service.py
git commit -m "#43 feat: wire audit ContextVars into cache provider"
```

---

### Task 6: Audit Middleware

**Files:**
- Create: `src/address_validator/middleware/audit.py`
- Create: `tests/unit/test_audit_middleware.py`
- Modify: `src/address_validator/main.py`

- [ ] **Step 1: Write failing test for audit middleware**

Create `tests/unit/test_audit_middleware.py`:

```python
"""Tests for the audit logging middleware."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from address_validator.middleware.audit import _should_audit


def test_should_audit_api_routes() -> None:
    assert _should_audit("/api/v1/parse") is True
    assert _should_audit("/api/v1/validate") is True
    assert _should_audit("/api/v1/standardize") is True
    assert _should_audit("/api/v1/health") is True


def test_should_not_audit_admin_routes() -> None:
    assert _should_audit("/admin/") is False
    assert _should_audit("/admin/audit/") is False


def test_should_not_audit_static_routes() -> None:
    assert _should_audit("/static/admin/css/tailwind.css") is False


def test_should_not_audit_docs() -> None:
    assert _should_audit("/") is False
    assert _should_audit("/docs") is False
    assert _should_audit("/redoc") is False
    assert _should_audit("/openapi.json") is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_audit_middleware.py -v --no-cov -x`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create `src/address_validator/middleware/audit.py`**

```python
"""Audit logging middleware — records every API request to the audit_log table.

Runs after the request_id middleware so the ULID is available. Captures timing,
client IP, status code, and validation-specific ContextVars (provider, status,
cache_hit). Writes are fire-and-forget via asyncio.create_task to avoid adding
latency to the response path.

Skips non-API routes: /, /docs, /redoc, /openapi.json, /admin/*, /static/*.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from fastapi import Request, Response

from address_validator.middleware.request_id import get_request_id
from address_validator.services.audit import (
    get_audit_cache_hit,
    get_audit_provider,
    get_audit_validation_status,
    reset_audit_context,
    write_audit_row,
)
from address_validator.services.validation.cache_db import get_engine

logger = logging.getLogger(__name__)

_SKIP_PREFIXES = ("/admin", "/static", "/docs", "/redoc")
_SKIP_EXACT = {"/", "/openapi.json"}


def _should_audit(path: str) -> bool:
    """Return True if the request path should be recorded in the audit log."""
    if path in _SKIP_EXACT:
        return False
    return not any(path.startswith(p) for p in _SKIP_PREFIXES)


def _get_client_ip(request: Request) -> str:
    """Extract client IP from X-Forwarded-For or fall back to request.client."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _error_detail_from_status(status_code: int, response: Response) -> str | None:
    """Extract a short error description for 4xx/5xx responses."""
    if status_code < 400:
        return None
    # Keep it short — just the status phrase
    phrases = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        422: "validation_error",
        429: "rate_limited",
        500: "internal_error",
        503: "service_unavailable",
    }
    return phrases.get(status_code, f"http_{status_code}")


async def audit_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Record API requests to the audit_log table after the response is sent."""
    path = request.url.path

    if not _should_audit(path):
        return await call_next(request)

    # Reset audit ContextVars so non-validate requests don't inherit
    # stale values from a previous request on the same asyncio task.
    reset_audit_context()

    start = time.monotonic()
    response = await call_next(request)
    elapsed_ms = int((time.monotonic() - start) * 1000)

    # Fire-and-forget: write audit row without blocking the response
    try:
        engine = await get_engine()
    except Exception:
        return response

    asyncio.create_task(
        write_audit_row(
            engine,
            timestamp=datetime.now(timezone.utc),
            request_id=get_request_id() or None,
            client_ip=_get_client_ip(request),
            method=request.method,
            endpoint=path,
            status_code=response.status_code,
            latency_ms=elapsed_ms,
            provider=get_audit_provider(),
            validation_status=get_audit_validation_status(),
            cache_hit=get_audit_cache_hit(),
            error_detail=_error_detail_from_status(response.status_code, response),
        )
    )

    return response
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_audit_middleware.py -v --no-cov -x`
Expected: PASS

- [ ] **Step 5: Write integration test for middleware**

Add to `tests/unit/test_audit_middleware.py`:

```python
def test_audit_middleware_records_request(client: TestClient) -> None:
    """Verify the middleware records an API request."""
    # This test requires a running DB; use the client fixture which hits the app
    # The middleware will fire-and-forget to the DB
    response = client.post(
        "/api/v1/parse",
        json={"address": "123 Main St, Anytown, WA 98101", "country": "US"},
    )
    assert response.status_code == 200
```

Note: Full integration testing of the audit row requires the DB. For now, the unit tests of `_should_audit`, `_get_client_ip`, and `_error_detail_from_status` provide good coverage. Integration tests for the full flow will be added in Task 10.

- [ ] **Step 6: Mount audit middleware in `main.py`**

Add import at top of `main.py`:
```python
from address_validator.middleware.audit import audit_middleware
```

Add **before** the request_id_middleware line. Starlette LIFO means the last-added middleware is outermost. We need request_id_middleware to be outermost (runs first, sets ContextVar) and audit_middleware to be innermost (runs inside request_id's `call_next`, can read the ContextVar):

```python
app.middleware("http")(audit_middleware)     # added first → innermost
app.middleware("http")(request_id_middleware)  # added second → outermost
```

**IMPORTANT:** Starlette LIFO ordering means the last-added middleware wraps the earlier ones. So the request flows: request_id_middleware (outermost, sets ULID ContextVar) → audit_middleware (reads ContextVar after call_next returns) → route handler. The audit middleware's `get_request_id()` call works because request_id_middleware has already set the ContextVar and won't reset it until after audit_middleware returns.

- [ ] **Step 7: Run all tests**

Run: `uv run pytest --no-cov -x`
Expected: All pass. Note: tests that don't set `VALIDATION_CACHE_DSN` will skip the audit write silently (the `get_engine()` call in the middleware will raise RuntimeError, which is caught and the middleware returns the response without writing).

- [ ] **Step 8: Commit**

```bash
git add src/address_validator/middleware/audit.py tests/unit/test_audit_middleware.py src/address_validator/main.py
git commit -m "#43 feat: add audit logging middleware"
```

---

### Task 7: Admin Auth + Dependencies

**Files:**
- Create: `src/address_validator/routers/admin/__init__.py`
- Create: `src/address_validator/routers/admin/deps.py`
- Create: `tests/unit/test_admin_deps.py`

- [ ] **Step 1: Write failing test for admin auth**

Create `tests/unit/test_admin_deps.py`:

```python
"""Tests for admin authentication dependency."""

import pytest
from starlette.testclient import TestClient
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from address_validator.routers.admin.deps import AdminUser, get_admin_user


@pytest.fixture()
def admin_app() -> FastAPI:
    """Minimal app with a route that requires admin auth."""
    app = FastAPI()

    @app.get("/test")
    async def test_route(user: AdminUser = get_admin_user) -> HTMLResponse:
        return HTMLResponse(f"Hello {user.email}")

    return app


def test_get_admin_user_with_headers() -> None:
    """Authenticated request returns AdminUser."""
    app = FastAPI()

    @app.get("/test")
    async def test_route(user = None) -> HTMLResponse:
        from fastapi import Request
        return HTMLResponse("ok")

    # Better approach: test the dependency function directly
    from starlette.requests import Request as StarletteRequest
    from starlette.datastructures import Headers

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/admin/",
        "query_string": b"",
        "headers": [
            (b"x-exedev-userid", b"user123"),
            (b"x-exedev-email", b"admin@example.com"),
        ],
    }
    request = StarletteRequest(scope)
    result = get_admin_user(request)
    assert isinstance(result, AdminUser)
    assert result.user_id == "user123"
    assert result.email == "admin@example.com"


def test_get_admin_user_missing_headers_redirects() -> None:
    """Unauthenticated request returns RedirectResponse."""
    from starlette.requests import Request as StarletteRequest
    from starlette.responses import RedirectResponse

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/admin/",
        "query_string": b"",
        "headers": [],
    }
    request = StarletteRequest(scope)
    result = get_admin_user(request)
    assert isinstance(result, RedirectResponse)
    assert "/__exe.dev/login" in result.headers["location"]
    assert "redirect=" in result.headers["location"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_admin_deps.py -v --no-cov -x`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Create package init and deps module**

Create `src/address_validator/routers/admin/__init__.py`:
```python
```
(empty file)

Create `src/address_validator/routers/admin/deps.py`:

```python
"""Admin dashboard authentication via exe.dev proxy headers.

The exe.dev reverse proxy injects X-ExeDev-UserID and X-ExeDev-Email headers
when the user is authenticated. If absent, the user needs to log in via
the /__exe.dev/login endpoint.

Any authenticated exe.dev user is treated as an admin (no RBAC).
"""

from dataclasses import dataclass
from urllib.parse import quote

from fastapi import Request
from starlette.responses import RedirectResponse


@dataclass(frozen=True)
class AdminUser:
    """Authenticated admin user from exe.dev proxy headers."""

    user_id: str
    email: str


def get_admin_user(request: Request) -> AdminUser | RedirectResponse:
    """Read exe.dev proxy headers and return AdminUser or redirect to login."""
    user_id = request.headers.get("X-ExeDev-UserID")
    email = request.headers.get("X-ExeDev-Email")

    if not user_id or not email:
        next_url = str(request.url.path)
        if request.url.query:
            next_url = f"{next_url}?{request.url.query}"
        return RedirectResponse(
            url=f"/__exe.dev/login?redirect={quote(next_url)}",
            status_code=302,
        )

    return AdminUser(user_id=user_id, email=email)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_admin_deps.py -v --no-cov -x`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/routers/admin/__init__.py \
    src/address_validator/routers/admin/deps.py \
    tests/unit/test_admin_deps.py
git commit -m "#43 feat: add admin auth dependency with exe.dev proxy headers"
```

---

### Task 8: Dashboard Foundation — Base Template, Static Files, Router Shell

**Files:**
- Create: `src/address_validator/templates/admin/base.html`
- Create: `src/address_validator/routers/admin/router.py`
- Create: `src/address_validator/routers/admin/dashboard.py`
- Create: `src/address_validator/templates/admin/dashboard.html`
- Modify: `src/address_validator/main.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add jinja2 dependency**

```bash
uv add jinja2
```

- [ ] **Step 2: Create `src/address_validator/templates/admin/base.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{% block title %}Admin{% endblock %} — Address Validator</title>
    <link rel="stylesheet" href="/static/admin/css/tailwind.css?v={{ css_version }}">
    <script src="https://unpkg.com/htmx.org@1.9.12" defer></script>
</head>
<body class="min-h-screen bg-gray-50 text-gray-900" hx-boost="true">
    <!-- Top bar -->
    <header class="bg-white border-b border-gray-200 px-4 py-3 flex items-center justify-between">
        <a href="/admin/" class="text-lg font-semibold text-gray-800">Address Validator Admin</a>
        <div class="flex items-center gap-4 text-sm text-gray-600">
            <span>{{ user.email }}</span>
            <form method="POST" action="/__exe.dev/logout" class="inline">
                <button type="submit"
                        class="text-gray-500 hover:text-gray-700 underline focus:outline-none focus:ring-2 focus:ring-blue-500 focus:ring-offset-1 rounded min-h-[44px] min-w-[44px] inline-flex items-center">
                    Logout
                </button>
            </form>
        </div>
    </header>

    <div class="flex flex-col md:flex-row">
        <!-- Sidebar -->
        <nav class="w-full md:w-56 bg-white border-b md:border-b-0 md:border-r border-gray-200 md:min-h-[calc(100vh-57px)]"
             aria-label="Admin navigation">
            <ul class="p-2 md:p-4 flex md:flex-col gap-1 overflow-x-auto md:overflow-visible">
                <li>
                    <a href="/admin/"
                       class="block px-3 py-2 rounded text-sm font-medium min-h-[44px] flex items-center
                              {% if active_nav == 'dashboard' %}bg-blue-50 text-blue-700{% else %}text-gray-700 hover:bg-gray-100{% endif %}
                              focus:outline-none focus:ring-2 focus:ring-blue-500">
                        Dashboard
                    </a>
                </li>
                <li>
                    <a href="/admin/audit/"
                       class="block px-3 py-2 rounded text-sm font-medium min-h-[44px] flex items-center
                              {% if active_nav == 'audit' %}bg-blue-50 text-blue-700{% else %}text-gray-700 hover:bg-gray-100{% endif %}
                              focus:outline-none focus:ring-2 focus:ring-blue-500">
                        Audit Log
                    </a>
                </li>
                <li class="text-xs text-gray-400 uppercase tracking-wide pt-3 pb-1 px-3 hidden md:block">
                    Endpoints
                </li>
                {% for ep in ['parse', 'standardize', 'validate'] %}
                <li>
                    <a href="/admin/endpoints/{{ ep }}"
                       class="block px-3 py-2 rounded text-sm min-h-[44px] flex items-center
                              {% if active_nav == 'endpoint_' + ep %}bg-blue-50 text-blue-700{% else %}text-gray-700 hover:bg-gray-100{% endif %}
                              focus:outline-none focus:ring-2 focus:ring-blue-500">
                        /{{ ep }}
                    </a>
                </li>
                {% endfor %}
                <li class="text-xs text-gray-400 uppercase tracking-wide pt-3 pb-1 px-3 hidden md:block">
                    Providers
                </li>
                {% for prov in ['usps', 'google'] %}
                <li>
                    <a href="/admin/providers/{{ prov }}"
                       class="block px-3 py-2 rounded text-sm min-h-[44px] flex items-center
                              {% if active_nav == 'provider_' + prov %}bg-blue-50 text-blue-700{% else %}text-gray-700 hover:bg-gray-100{% endif %}
                              focus:outline-none focus:ring-2 focus:ring-blue-500">
                        {{ prov | upper }}
                    </a>
                </li>
                {% endfor %}
            </ul>
        </nav>

        <!-- Main content -->
        <main class="flex-1 p-4 md:p-6" aria-live="polite">
            {% block content %}{% endblock %}
        </main>
    </div>
</body>
</html>
```

- [ ] **Step 3: Create `src/address_validator/templates/admin/dashboard.html`**

```html
{% extends "admin/base.html" %}
{% block title %}Dashboard{% endblock %}

{% block content %}
<h1 class="text-2xl font-bold text-gray-800 mb-6">Dashboard</h1>

<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
    <!-- Requests Today -->
    <div class="bg-white rounded-lg border border-gray-200 p-4">
        <p class="text-sm text-gray-500 mb-1">Requests Today</p>
        <p class="text-2xl font-bold text-gray-900">{{ stats.requests_today | default(0) }}</p>
    </div>

    <!-- Requests This Week -->
    <div class="bg-white rounded-lg border border-gray-200 p-4">
        <p class="text-sm text-gray-500 mb-1">Requests This Week</p>
        <p class="text-2xl font-bold text-gray-900">{{ stats.requests_week | default(0) }}</p>
    </div>

    <!-- Cache Hit Rate -->
    <div class="bg-white rounded-lg border border-gray-200 p-4">
        <p class="text-sm text-gray-500 mb-1">Cache Hit Rate</p>
        <p class="text-2xl font-bold text-gray-900">
            {% if stats.cache_hit_rate is not none %}
                {{ "%.1f" | format(stats.cache_hit_rate) }}%
            {% else %}
                N/A
            {% endif %}
        </p>
    </div>

    <!-- Error Rate -->
    <div class="bg-white rounded-lg border border-gray-200 p-4">
        <p class="text-sm text-gray-500 mb-1">Error Rate (Today)</p>
        <p class="text-2xl font-bold {% if stats.error_rate and stats.error_rate > 5 %}text-red-600{% else %}text-gray-900{% endif %}">
            {% if stats.error_rate is not none %}
                {{ "%.1f" | format(stats.error_rate) }}%
            {% else %}
                N/A
            {% endif %}
        </p>
    </div>
</div>

<!-- Provider Quota -->
{% if quota %}
<h2 class="text-lg font-semibold text-gray-800 mb-3">Provider Quota</h2>
<div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-8">
    {% for q in quota %}
    <div class="bg-white rounded-lg border border-gray-200 p-4">
        <p class="text-sm text-gray-500 mb-1">{{ q.provider | upper }} Daily Quota</p>
        <div class="flex items-baseline gap-2">
            <p class="text-2xl font-bold text-gray-900">{{ q.remaining }}</p>
            <p class="text-sm text-gray-500">/ {{ q.limit }}</p>
        </div>
        <div class="mt-2 w-full bg-gray-200 rounded-full h-2" role="progressbar"
             aria-valuenow="{{ q.remaining }}" aria-valuemin="0" aria-valuemax="{{ q.limit }}"
             aria-label="{{ q.provider }} quota usage">
            <div class="bg-blue-600 h-2 rounded-full"
                 style="width: {{ ((q.remaining / q.limit) * 100) | int if q.limit > 0 else 0 }}%">
            </div>
        </div>
    </div>
    {% endfor %}
</div>
{% endif %}

<!-- Requests All Time -->
<div class="text-sm text-gray-500">
    Total requests (all time): <strong class="text-gray-700">{{ stats.requests_all | default(0) }}</strong>
</div>
{% endblock %}
```

- [ ] **Step 4: Create `src/address_validator/routers/admin/queries.py`**

```python
"""Shared SQL query helpers for admin dashboard views."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def get_dashboard_stats(engine: AsyncEngine) -> dict:
    """Fetch aggregate stats for the dashboard landing page."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())

    async with engine.connect() as conn:
        # Request counts
        row = (
            await conn.execute(
                text("""
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE timestamp >= :today) AS today,
                        COUNT(*) FILTER (WHERE timestamp >= :week) AS week,
                        COUNT(*) FILTER (WHERE status_code >= 400 AND timestamp >= :today) AS errors_today
                    FROM audit_log
                """),
                {"today": today_start, "week": week_start},
            )
        ).one()

        # Cache hit rate (validate endpoint only)
        cache_row = (
            await conn.execute(
                text("""
                    SELECT
                        COUNT(*) FILTER (WHERE cache_hit = true) AS hits,
                        COUNT(*) FILTER (WHERE cache_hit IS NOT NULL) AS total
                    FROM audit_log
                    WHERE endpoint = '/api/v1/validate'
                """),
            )
        ).one()

    requests_today = row.today
    error_rate = (row.errors_today / requests_today * 100) if requests_today > 0 else None
    cache_hit_rate = (
        (cache_row.hits / cache_row.total * 100) if cache_row.total > 0 else None
    )

    return {
        "requests_today": requests_today,
        "requests_week": row.week,
        "requests_all": row.total,
        "error_rate": error_rate,
        "cache_hit_rate": cache_hit_rate,
    }


async def get_audit_rows(
    engine: AsyncEngine,
    *,
    page: int = 1,
    per_page: int = 50,
    endpoint: str | None = None,
    provider: str | None = None,
    client_ip: str | None = None,
    status_min: int | None = None,
) -> tuple[list[dict], int]:
    """Fetch paginated, filtered audit_log rows. Returns (rows, total_count)."""
    conditions = []
    params: dict = {}

    if endpoint:
        conditions.append("endpoint = :endpoint")
        params["endpoint"] = f"/api/v1/{endpoint}"
    if provider:
        conditions.append("provider = :provider")
        params["provider"] = provider
    if client_ip:
        conditions.append("client_ip = :client_ip")
        params["client_ip"] = client_ip
    if status_min:
        conditions.append("status_code >= :status_min")
        params["status_min"] = status_min

    # SAFETY: conditions list contains only hardcoded column/operator literals;
    # all user-supplied values go through :parameterized placeholders in params dict.
    where = " AND ".join(conditions) if conditions else "1=1"

    async with engine.connect() as conn:
        count_row = (
            await conn.execute(text(f"SELECT COUNT(*) FROM audit_log WHERE {where}"), params)
        ).one()
        total = count_row[0]

        params["limit"] = per_page
        params["offset"] = (page - 1) * per_page
        result = await conn.execute(
            text(f"""
                SELECT id, timestamp, request_id, client_ip, method, endpoint,
                       status_code, latency_ms, provider, validation_status,
                       cache_hit, error_detail
                FROM audit_log
                WHERE {where}
                ORDER BY timestamp DESC
                LIMIT :limit OFFSET :offset
            """),
            params,
        )
        rows = [dict(r._mapping) for r in result]

    return rows, total


async def get_endpoint_stats(engine: AsyncEngine, endpoint_name: str) -> dict:
    """Fetch stats for a specific endpoint."""
    endpoint_path = f"/api/v1/{endpoint_name}"
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("""
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE timestamp >= :today) AS today,
                        COUNT(*) FILTER (WHERE timestamp >= :week) AS week,
                        COUNT(*) FILTER (WHERE status_code >= 400) AS errors,
                        AVG(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS avg_latency
                    FROM audit_log
                    WHERE endpoint = :endpoint
                """),
                {"today": today_start, "week": week_start, "endpoint": endpoint_path},
            )
        ).one()

        # Status code breakdown
        status_rows = (
            await conn.execute(
                text("""
                    SELECT status_code, COUNT(*) AS count
                    FROM audit_log
                    WHERE endpoint = :endpoint
                    GROUP BY status_code
                    ORDER BY status_code
                """),
                {"endpoint": endpoint_path},
            )
        ).fetchall()

    error_rate = (row.errors / row.total * 100) if row.total > 0 else None
    return {
        "total": row.total,
        "today": row.today,
        "week": row.week,
        "error_rate": error_rate,
        "avg_latency_ms": round(row.avg_latency) if row.avg_latency else None,
        "status_codes": {r.status_code: r.count for r in status_rows},
    }


async def get_provider_stats(engine: AsyncEngine, provider_name: str) -> dict:
    """Fetch stats for a specific validation provider."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("""
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE timestamp >= :today) AS today,
                        COUNT(*) FILTER (WHERE cache_hit = true) AS cache_hits,
                        COUNT(*) FILTER (WHERE cache_hit IS NOT NULL) AS cache_total
                    FROM audit_log
                    WHERE provider = :provider
                """),
                {"today": today_start, "provider": provider_name},
            )
        ).one()

        # Validation status breakdown
        status_rows = (
            await conn.execute(
                text("""
                    SELECT validation_status, COUNT(*) AS count
                    FROM audit_log
                    WHERE provider = :provider AND validation_status IS NOT NULL
                    GROUP BY validation_status
                    ORDER BY count DESC
                """),
                {"provider": provider_name},
            )
        ).fetchall()

    cache_hit_rate = (
        (row.cache_hits / row.cache_total * 100) if row.cache_total > 0 else None
    )
    return {
        "total": row.total,
        "today": row.today,
        "cache_hit_rate": cache_hit_rate,
        "validation_statuses": {r.validation_status: r.count for r in status_rows},
    }
```

- [ ] **Step 5: Create `src/address_validator/routers/admin/dashboard.py`**

```python
"""Admin dashboard landing page."""

import subprocess

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from address_validator.routers.admin.deps import AdminUser, get_admin_user
from address_validator.routers.admin.queries import get_dashboard_stats
from address_validator.services.validation import cache_db, factory

templates = Jinja2Templates(directory="src/address_validator/templates")

router = APIRouter()

# Cache-bust CSS with git SHA
try:
    _css_version = (
        subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    )
except Exception:
    _css_version = "dev"


def _get_quota_info() -> list[dict]:
    """Read current quota state from provider singletons."""
    quota = []

    usps = factory._usps_provider
    if usps and hasattr(usps, "_client") and hasattr(usps._client, "_rate_limiter"):
        guard = usps._client._rate_limiter
        # Daily window is index 1
        if len(guard._windows) > 1:
            quota.append({
                "provider": "usps",
                "remaining": int(guard._tokens[1]),
                "limit": guard._windows[1].limit,
            })

    google = factory._google_provider
    if google and hasattr(google, "_client") and hasattr(google._client, "_rate_limiter"):
        guard = google._client._rate_limiter
        if len(guard._windows) > 1:
            quota.append({
                "provider": "google",
                "remaining": int(guard._tokens[1]),
                "limit": guard._windows[1].limit,
            })

    return quota


@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request) -> HTMLResponse | RedirectResponse:
    user = get_admin_user(request)
    if isinstance(user, RedirectResponse):
        return user

    try:
        engine = await cache_db.get_engine()
        stats = await get_dashboard_stats(engine)
    except Exception:
        stats = {}

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "user": user,
            "active_nav": "dashboard",
            "css_version": _css_version,
            "stats": stats,
            "quota": _get_quota_info(),
        },
    )
```

- [ ] **Step 6: Create `src/address_validator/routers/admin/router.py`**

```python
"""Top-level admin router — mounts all dashboard sub-routers."""

from fastapi import APIRouter

from address_validator.routers.admin.dashboard import router as dashboard_router

admin_router = APIRouter(prefix="/admin")
admin_router.include_router(dashboard_router)
```

- [ ] **Step 7: Mount admin router and static files in `main.py`**

Add imports at top of `main.py`:
```python
from fastapi.staticfiles import StaticFiles
from address_validator.routers.admin.router import admin_router
```

After the existing `app.include_router(v1_validate.router)` line, add:
```python
app.include_router(admin_router)
app.mount("/static/admin", StaticFiles(directory="src/address_validator/static/admin"), name="admin-static")
```

- [ ] **Step 8: Build Tailwind CSS (will now include classes from templates)**

Run: `bash scripts/build-css.sh`

- [ ] **Step 9: Verify the dashboard loads**

Run: `uv run pytest --no-cov -x` (all existing tests still pass)

Then manually test (if service is running):
```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/admin/
```
Expected: `302` (redirect to exe.dev login since no auth headers)

- [ ] **Step 10: Commit**

```bash
git add src/address_validator/templates/admin/base.html \
    src/address_validator/templates/admin/dashboard.html \
    src/address_validator/routers/admin/queries.py \
    src/address_validator/routers/admin/dashboard.py \
    src/address_validator/routers/admin/router.py \
    src/address_validator/static/admin/css/tailwind.css \
    src/address_validator/main.py \
    pyproject.toml uv.lock
git commit -m "#43 feat: add admin dashboard foundation with landing page"
```

---

### Task 9: Audit Log View

**Files:**
- Create: `src/address_validator/routers/admin/audit_views.py`
- Create: `src/address_validator/templates/admin/audit/list.html`
- Create: `src/address_validator/templates/admin/audit/_rows.html`
- Modify: `src/address_validator/routers/admin/router.py`

- [ ] **Step 1: Create `src/address_validator/templates/admin/audit/_rows.html`**

This is the HTMX partial — renders just the `<tr>` elements, never extends `base.html`.

```html
{% for row in rows %}
<tr class="border-b border-gray-100 hover:bg-gray-50">
    <td class="px-3 py-2 text-sm text-gray-500 whitespace-nowrap">
        {{ row.timestamp.strftime('%Y-%m-%d %H:%M:%S') if row.timestamp else '' }}
    </td>
    <td class="px-3 py-2 text-sm font-mono text-gray-700">{{ row.client_ip }}</td>
    <td class="px-3 py-2 text-sm text-gray-700">{{ row.method }}</td>
    <td class="px-3 py-2 text-sm text-gray-700">{{ row.endpoint }}</td>
    <td class="px-3 py-2 text-sm">
        <span class="inline-flex items-center gap-1
            {% if row.status_code >= 500 %}text-red-700
            {% elif row.status_code >= 400 %}text-amber-700
            {% else %}text-green-700{% endif %}">
            <span aria-hidden="true">
                {% if row.status_code >= 500 %}&#x2715;
                {% elif row.status_code >= 400 %}&#x25B3;
                {% else %}&#x2713;{% endif %}
            </span>
            {{ row.status_code }}
        </span>
    </td>
    <td class="px-3 py-2 text-sm text-gray-500">
        {{ row.latency_ms if row.latency_ms is not none else '—' }}
    </td>
    <td class="px-3 py-2 text-sm text-gray-500">{{ row.provider or '—' }}</td>
    <td class="px-3 py-2 text-sm text-gray-500">
        {% if row.cache_hit is true %}Hit{% elif row.cache_hit is false %}Miss{% else %}—{% endif %}
    </td>
</tr>
{% endfor %}
{% if not rows %}
<tr>
    <td colspan="8" class="px-3 py-8 text-center text-sm text-gray-400">No audit log entries found.</td>
</tr>
{% endif %}
```

- [ ] **Step 2: Create `src/address_validator/templates/admin/audit/list.html`**

```html
{% extends "admin/base.html" %}
{% block title %}Audit Log{% endblock %}

{% block content %}
<h1 class="text-2xl font-bold text-gray-800 mb-6">Audit Log</h1>

<!-- Filters -->
<form class="flex flex-wrap gap-3 mb-4 items-end"
      hx-get="/admin/audit/"
      hx-target="#audit-rows"
      hx-push-url="true"
      hx-trigger="change from:select, input delay:300ms from:input">

    <div>
        <label for="filter-ip" class="block text-xs text-gray-500 mb-1">Client IP</label>
        <input type="text" id="filter-ip" name="client_ip" value="{{ filters.client_ip or '' }}"
               placeholder="e.g. 67.213.124.9"
               class="border border-gray-300 rounded px-3 py-1.5 text-sm min-h-[44px]
                      focus:border-blue-500 focus:ring-1 focus:ring-blue-500 focus:outline-none">
    </div>

    <div>
        <label for="filter-endpoint" class="block text-xs text-gray-500 mb-1">Endpoint</label>
        <select id="filter-endpoint" name="endpoint"
                class="border border-gray-300 rounded px-3 py-1.5 text-sm min-h-[44px]
                       focus:border-blue-500 focus:ring-1 focus:ring-blue-500 focus:outline-none">
            <option value="">All</option>
            {% for ep in ['parse', 'standardize', 'validate', 'health'] %}
            <option value="{{ ep }}" {% if filters.endpoint == ep %}selected{% endif %}>
                /{{ ep }}
            </option>
            {% endfor %}
        </select>
    </div>

    <div>
        <label for="filter-status" class="block text-xs text-gray-500 mb-1">Status</label>
        <select id="filter-status" name="status_min"
                class="border border-gray-300 rounded px-3 py-1.5 text-sm min-h-[44px]
                       focus:border-blue-500 focus:ring-1 focus:ring-blue-500 focus:outline-none">
            <option value="">All</option>
            <option value="400" {% if filters.status_min == 400 %}selected{% endif %}>4xx+</option>
            <option value="500" {% if filters.status_min == 500 %}selected{% endif %}>5xx</option>
        </select>
    </div>
</form>

<!-- Table -->
<div class="overflow-x-auto bg-white rounded-lg border border-gray-200">
    <table class="w-full text-left">
        <thead class="sticky top-0 bg-gray-50 border-b border-gray-200 z-10 shadow-sm">
            <tr>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Time</th>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">IP</th>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Method</th>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Endpoint</th>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Status</th>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Latency</th>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Provider</th>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Cache</th>
            </tr>
        </thead>
        <tbody id="audit-rows" aria-live="polite" aria-atomic="false">
            {% include "admin/audit/_rows.html" %}
        </tbody>
    </table>
</div>

<!-- Pagination -->
{% if total_pages > 1 %}
<nav class="flex items-center gap-2 mt-4" aria-label="Audit log pagination">
    {% if page > 1 %}
    <a href="/admin/audit/?page={{ page - 1 }}{% if filters.client_ip %}&client_ip={{ filters.client_ip }}{% endif %}{% if filters.endpoint %}&endpoint={{ filters.endpoint }}{% endif %}{% if filters.status_min %}&status_min={{ filters.status_min }}{% endif %}"
       class="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 min-h-[44px] inline-flex items-center
              focus:outline-none focus:ring-2 focus:ring-blue-500">
        Previous
    </a>
    {% endif %}
    <span class="text-sm text-gray-500">Page {{ page }} of {{ total_pages }}</span>
    {% if page < total_pages %}
    <a href="/admin/audit/?page={{ page + 1 }}{% if filters.client_ip %}&client_ip={{ filters.client_ip }}{% endif %}{% if filters.endpoint %}&endpoint={{ filters.endpoint }}{% endif %}{% if filters.status_min %}&status_min={{ filters.status_min }}{% endif %}"
       class="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 min-h-[44px] inline-flex items-center
              focus:outline-none focus:ring-2 focus:ring-blue-500">
        Next
    </a>
    {% endif %}
</nav>
{% endif %}
{% endblock %}
```

- [ ] **Step 3: Create `src/address_validator/routers/admin/audit_views.py`**

```python
"""Admin audit log view — full audit log with filters and pagination."""

import math

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from address_validator.routers.admin.dashboard import _css_version
from address_validator.routers.admin.deps import AdminUser, get_admin_user
from address_validator.routers.admin.queries import get_audit_rows
from address_validator.services.validation import cache_db

templates = Jinja2Templates(directory="src/address_validator/templates")

router = APIRouter(prefix="/audit")

_PER_PAGE = 50


@router.get("/", response_class=HTMLResponse)
async def audit_log_list(
    request: Request,
    page: int = Query(1, ge=1),
    client_ip: str | None = Query(None),
    endpoint: str | None = Query(None),
    status_min: int | None = Query(None),
) -> HTMLResponse | RedirectResponse:
    user = get_admin_user(request)
    if isinstance(user, RedirectResponse):
        return user

    engine = await cache_db.get_engine()
    rows, total = await get_audit_rows(
        engine,
        page=page,
        per_page=_PER_PAGE,
        endpoint=endpoint,
        client_ip=client_ip,
        status_min=status_min,
    )
    total_pages = max(1, math.ceil(total / _PER_PAGE))

    filters = {"client_ip": client_ip, "endpoint": endpoint, "status_min": status_min}

    # HTMX partial: return just the rows
    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "admin/audit/_rows.html",
            {"request": request, "rows": rows},
        )

    return templates.TemplateResponse(
        "admin/audit/list.html",
        {
            "request": request,
            "user": user,
            "active_nav": "audit",
            "css_version": _css_version,
            "rows": rows,
            "page": page,
            "total_pages": total_pages,
            "filters": filters,
        },
    )
```

- [ ] **Step 4: Add audit views to admin router**

Update `src/address_validator/routers/admin/router.py`:

```python
"""Top-level admin router — mounts all dashboard sub-routers."""

from fastapi import APIRouter

from address_validator.routers.admin.audit_views import router as audit_router
from address_validator.routers.admin.dashboard import router as dashboard_router

admin_router = APIRouter(prefix="/admin")
admin_router.include_router(dashboard_router)
admin_router.include_router(audit_router)
```

- [ ] **Step 5: Build Tailwind CSS**

Run: `bash scripts/build-css.sh`

- [ ] **Step 6: Run all tests**

Run: `uv run pytest --no-cov -x`
Expected: All pass

- [ ] **Step 7: Commit**

```bash
git add src/address_validator/routers/admin/audit_views.py \
    src/address_validator/routers/admin/router.py \
    src/address_validator/templates/admin/audit/list.html \
    src/address_validator/templates/admin/audit/_rows.html \
    src/address_validator/static/admin/css/tailwind.css
git commit -m "#43 feat: add audit log view with filters and pagination"
```

---

### Task 10: Per-Endpoint Detail View

**Files:**
- Create: `src/address_validator/routers/admin/endpoints.py`
- Create: `src/address_validator/templates/admin/endpoints/detail.html`
- Create: `src/address_validator/templates/admin/endpoints/_rows.html`
- Modify: `src/address_validator/routers/admin/router.py`

- [ ] **Step 1: Create `src/address_validator/templates/admin/endpoints/_rows.html`**

Symlink or copy `audit/_rows.html` — identical format:

```html
{% include "admin/audit/_rows.html" %}
```

- [ ] **Step 2: Create `src/address_validator/templates/admin/endpoints/detail.html`**

```html
{% extends "admin/base.html" %}
{% block title %}/{{ endpoint_name }} Endpoint{% endblock %}

{% block content %}
<h1 class="text-2xl font-bold text-gray-800 mb-6">/api/v1/{{ endpoint_name }}</h1>

<!-- Stats cards -->
<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
    <div class="bg-white rounded-lg border border-gray-200 p-4">
        <p class="text-sm text-gray-500 mb-1">Today</p>
        <p class="text-2xl font-bold text-gray-900">{{ stats.today | default(0) }}</p>
    </div>
    <div class="bg-white rounded-lg border border-gray-200 p-4">
        <p class="text-sm text-gray-500 mb-1">This Week</p>
        <p class="text-2xl font-bold text-gray-900">{{ stats.week | default(0) }}</p>
    </div>
    <div class="bg-white rounded-lg border border-gray-200 p-4">
        <p class="text-sm text-gray-500 mb-1">Avg Latency</p>
        <p class="text-2xl font-bold text-gray-900">
            {{ stats.avg_latency_ms if stats.avg_latency_ms is not none else '—' }}
            {% if stats.avg_latency_ms is not none %}<span class="text-sm text-gray-500">ms</span>{% endif %}
        </p>
    </div>
    <div class="bg-white rounded-lg border border-gray-200 p-4">
        <p class="text-sm text-gray-500 mb-1">Error Rate</p>
        <p class="text-2xl font-bold {% if stats.error_rate and stats.error_rate > 5 %}text-red-600{% else %}text-gray-900{% endif %}">
            {% if stats.error_rate is not none %}
                {{ "%.1f" | format(stats.error_rate) }}%
            {% else %}N/A{% endif %}
        </p>
    </div>
</div>

<!-- Status code breakdown -->
{% if stats.status_codes %}
<h2 class="text-lg font-semibold text-gray-800 mb-3">Status Codes</h2>
<div class="flex flex-wrap gap-2 mb-6">
    {% for code, count in stats.status_codes.items() %}
    <span class="inline-flex items-center gap-1 px-3 py-1 rounded-full text-sm
        {% if code >= 500 %}bg-red-100 text-red-800
        {% elif code >= 400 %}bg-amber-100 text-amber-800
        {% else %}bg-green-100 text-green-800{% endif %}">
        {{ code }}: {{ count }}
    </span>
    {% endfor %}
</div>
{% endif %}

<!-- Filtered audit log -->
<h2 class="text-lg font-semibold text-gray-800 mb-3">Recent Requests</h2>

<div class="flex flex-wrap gap-3 mb-4 items-end"
     hx-get="/admin/endpoints/{{ endpoint_name }}"
     hx-target="#endpoint-rows"
     hx-push-url="true"
     hx-trigger="change from:select, input delay:300ms from:input">
    <div>
        <label for="filter-ip" class="block text-xs text-gray-500 mb-1">Client IP</label>
        <input type="text" id="filter-ip" name="client_ip" value="{{ filters.client_ip or '' }}"
               placeholder="Filter by IP"
               class="border border-gray-300 rounded px-3 py-1.5 text-sm min-h-[44px]
                      focus:border-blue-500 focus:ring-1 focus:ring-blue-500 focus:outline-none">
    </div>
</div>

<div class="overflow-x-auto bg-white rounded-lg border border-gray-200">
    <table class="w-full text-left">
        <thead class="sticky top-0 bg-gray-50 border-b border-gray-200 z-10 shadow-sm">
            <tr>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Time</th>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">IP</th>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Method</th>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Endpoint</th>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Status</th>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Latency</th>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Provider</th>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Cache</th>
            </tr>
        </thead>
        <tbody id="endpoint-rows" aria-live="polite" aria-atomic="false">
            {% include "admin/audit/_rows.html" %}
        </tbody>
    </table>
</div>

{% if total_pages > 1 %}
<nav class="flex items-center gap-2 mt-4" aria-label="Endpoint audit pagination">
    {% if page > 1 %}
    <a href="/admin/endpoints/{{ endpoint_name }}?page={{ page - 1 }}{% if filters.client_ip %}&client_ip={{ filters.client_ip }}{% endif %}"
       class="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 min-h-[44px] inline-flex items-center
              focus:outline-none focus:ring-2 focus:ring-blue-500">Previous</a>
    {% endif %}
    <span class="text-sm text-gray-500">Page {{ page }} of {{ total_pages }}</span>
    {% if page < total_pages %}
    <a href="/admin/endpoints/{{ endpoint_name }}?page={{ page + 1 }}{% if filters.client_ip %}&client_ip={{ filters.client_ip }}{% endif %}"
       class="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 min-h-[44px] inline-flex items-center
              focus:outline-none focus:ring-2 focus:ring-blue-500">Next</a>
    {% endif %}
</nav>
{% endif %}
{% endblock %}
```

- [ ] **Step 3: Create `src/address_validator/routers/admin/endpoints.py`**

```python
"""Per-endpoint detail view — stats and filtered audit log."""

import math

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from address_validator.routers.admin.dashboard import _css_version
from address_validator.routers.admin.deps import AdminUser, get_admin_user
from address_validator.routers.admin.queries import get_audit_rows, get_endpoint_stats
from address_validator.services.validation import cache_db

templates = Jinja2Templates(directory="src/address_validator/templates")

router = APIRouter(prefix="/endpoints")

_VALID_ENDPOINTS = {"parse", "standardize", "validate", "health"}
_PER_PAGE = 50


@router.get("/{name}", response_class=HTMLResponse)
async def endpoint_detail(
    request: Request,
    name: str,
    page: int = Query(1, ge=1),
    client_ip: str | None = Query(None),
) -> HTMLResponse | RedirectResponse:
    if name not in _VALID_ENDPOINTS:
        raise HTTPException(status_code=404, detail="Unknown endpoint")

    user = get_admin_user(request)
    if isinstance(user, RedirectResponse):
        return user

    engine = await cache_db.get_engine()
    stats = await get_endpoint_stats(engine, name)
    rows, total = await get_audit_rows(
        engine, page=page, per_page=_PER_PAGE, endpoint=name, client_ip=client_ip
    )
    total_pages = max(1, math.ceil(total / _PER_PAGE))
    filters = {"client_ip": client_ip}

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "admin/audit/_rows.html",
            {"request": request, "rows": rows},
        )

    return templates.TemplateResponse(
        "admin/endpoints/detail.html",
        {
            "request": request,
            "user": user,
            "active_nav": f"endpoint_{name}",
            "css_version": _css_version,
            "endpoint_name": name,
            "stats": stats,
            "rows": rows,
            "page": page,
            "total_pages": total_pages,
            "filters": filters,
        },
    )
```

- [ ] **Step 4: Add endpoints router**

Update `src/address_validator/routers/admin/router.py` — add import and include:

```python
from address_validator.routers.admin.endpoints import router as endpoints_router
admin_router.include_router(endpoints_router)
```

- [ ] **Step 5: Build Tailwind CSS, run tests**

```bash
bash scripts/build-css.sh
uv run pytest --no-cov -x
```

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/routers/admin/endpoints.py \
    src/address_validator/routers/admin/router.py \
    src/address_validator/templates/admin/endpoints/detail.html \
    src/address_validator/templates/admin/endpoints/_rows.html \
    src/address_validator/static/admin/css/tailwind.css
git commit -m "#43 feat: add per-endpoint detail views"
```

---

### Task 11: Per-Provider Detail View

**Files:**
- Create: `src/address_validator/routers/admin/providers.py`
- Create: `src/address_validator/templates/admin/providers/detail.html`
- Create: `src/address_validator/templates/admin/providers/_rows.html`
- Modify: `src/address_validator/routers/admin/router.py`

- [ ] **Step 1: Create `src/address_validator/templates/admin/providers/_rows.html`**

```html
{% include "admin/audit/_rows.html" %}
```

- [ ] **Step 2: Create `src/address_validator/templates/admin/providers/detail.html`**

```html
{% extends "admin/base.html" %}
{% block title %}{{ provider_name | upper }} Provider{% endblock %}

{% block content %}
<h1 class="text-2xl font-bold text-gray-800 mb-6">{{ provider_name | upper }} Provider</h1>

<!-- Stats cards -->
<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
    <div class="bg-white rounded-lg border border-gray-200 p-4">
        <p class="text-sm text-gray-500 mb-1">Requests Today</p>
        <p class="text-2xl font-bold text-gray-900">{{ stats.today | default(0) }}</p>
    </div>
    <div class="bg-white rounded-lg border border-gray-200 p-4">
        <p class="text-sm text-gray-500 mb-1">Total Requests</p>
        <p class="text-2xl font-bold text-gray-900">{{ stats.total | default(0) }}</p>
    </div>
    <div class="bg-white rounded-lg border border-gray-200 p-4">
        <p class="text-sm text-gray-500 mb-1">Cache Hit Rate</p>
        <p class="text-2xl font-bold text-gray-900">
            {% if stats.cache_hit_rate is not none %}
                {{ "%.1f" | format(stats.cache_hit_rate) }}%
            {% else %}N/A{% endif %}
        </p>
    </div>
    {% if quota %}
    <div class="bg-white rounded-lg border border-gray-200 p-4">
        <p class="text-sm text-gray-500 mb-1">Daily Quota</p>
        <div class="flex items-baseline gap-2">
            <p class="text-2xl font-bold text-gray-900">{{ quota.remaining }}</p>
            <p class="text-sm text-gray-500">/ {{ quota.limit }}</p>
        </div>
        <div class="mt-2 w-full bg-gray-200 rounded-full h-2" role="progressbar"
             aria-valuenow="{{ quota.remaining }}" aria-valuemin="0" aria-valuemax="{{ quota.limit }}"
             aria-label="Daily quota usage">
            <div class="bg-blue-600 h-2 rounded-full"
                 style="width: {{ ((quota.remaining / quota.limit) * 100) | int if quota.limit > 0 else 0 }}%">
            </div>
        </div>
    </div>
    {% endif %}
</div>

<!-- Validation status breakdown -->
{% if stats.validation_statuses %}
<h2 class="text-lg font-semibold text-gray-800 mb-3">Validation Results</h2>
<div class="flex flex-wrap gap-2 mb-6">
    {% for vs, count in stats.validation_statuses.items() %}
    <span class="inline-flex items-center gap-1 px-3 py-1 rounded-full text-sm
        {% if vs == 'confirmed' %}bg-green-100 text-green-800
        {% elif vs == 'not_confirmed' %}bg-red-100 text-red-800
        {% else %}bg-gray-100 text-gray-800{% endif %}">
        <span aria-hidden="true">
            {% if vs == 'confirmed' %}&#x2713;
            {% elif vs == 'not_confirmed' %}&#x2715;
            {% else %}&#x25CB;{% endif %}
        </span>
        {{ vs }}: {{ count }}
    </span>
    {% endfor %}
</div>
{% endif %}

<!-- Filtered audit log -->
<h2 class="text-lg font-semibold text-gray-800 mb-3">Recent Requests</h2>

<div class="flex flex-wrap gap-3 mb-4 items-end"
     hx-get="/admin/providers/{{ provider_name }}"
     hx-target="#provider-rows"
     hx-push-url="true"
     hx-trigger="change from:select, input delay:300ms from:input">
    <div>
        <label for="filter-ip" class="block text-xs text-gray-500 mb-1">Client IP</label>
        <input type="text" id="filter-ip" name="client_ip" value="{{ filters.client_ip or '' }}"
               placeholder="Filter by IP"
               class="border border-gray-300 rounded px-3 py-1.5 text-sm min-h-[44px]
                      focus:border-blue-500 focus:ring-1 focus:ring-blue-500 focus:outline-none">
    </div>
</div>

<div class="overflow-x-auto bg-white rounded-lg border border-gray-200">
    <table class="w-full text-left">
        <thead class="sticky top-0 bg-gray-50 border-b border-gray-200 z-10 shadow-sm">
            <tr>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Time</th>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">IP</th>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Method</th>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Endpoint</th>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Status</th>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Latency</th>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Provider</th>
                <th class="px-3 py-2 text-xs font-medium text-gray-500 uppercase">Cache</th>
            </tr>
        </thead>
        <tbody id="provider-rows" aria-live="polite" aria-atomic="false">
            {% include "admin/audit/_rows.html" %}
        </tbody>
    </table>
</div>

{% if total_pages > 1 %}
<nav class="flex items-center gap-2 mt-4" aria-label="Provider audit pagination">
    {% if page > 1 %}
    <a href="/admin/providers/{{ provider_name }}?page={{ page - 1 }}{% if filters.client_ip %}&client_ip={{ filters.client_ip }}{% endif %}"
       class="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 min-h-[44px] inline-flex items-center
              focus:outline-none focus:ring-2 focus:ring-blue-500">Previous</a>
    {% endif %}
    <span class="text-sm text-gray-500">Page {{ page }} of {{ total_pages }}</span>
    {% if page < total_pages %}
    <a href="/admin/providers/{{ provider_name }}?page={{ page + 1 }}{% if filters.client_ip %}&client_ip={{ filters.client_ip }}{% endif %}"
       class="px-3 py-1.5 text-sm border border-gray-300 rounded hover:bg-gray-50 min-h-[44px] inline-flex items-center
              focus:outline-none focus:ring-2 focus:ring-blue-500">Next</a>
    {% endif %}
</nav>
{% endif %}
{% endblock %}
```

- [ ] **Step 3: Create `src/address_validator/routers/admin/providers.py`**

```python
"""Per-provider detail view — stats, quota, and filtered audit log."""

import math

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from address_validator.routers.admin.dashboard import _css_version, _get_quota_info
from address_validator.routers.admin.deps import AdminUser, get_admin_user
from address_validator.routers.admin.queries import get_audit_rows, get_provider_stats
from address_validator.services.validation import cache_db

templates = Jinja2Templates(directory="src/address_validator/templates")

router = APIRouter(prefix="/providers")

_VALID_PROVIDERS = {"usps", "google"}
_PER_PAGE = 50


@router.get("/{name}", response_class=HTMLResponse)
async def provider_detail(
    request: Request,
    name: str,
    page: int = Query(1, ge=1),
    client_ip: str | None = Query(None),
) -> HTMLResponse | RedirectResponse:
    if name not in _VALID_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")

    user = get_admin_user(request)
    if isinstance(user, RedirectResponse):
        return user

    engine = await cache_db.get_engine()
    stats = await get_provider_stats(engine, name)
    rows, total = await get_audit_rows(
        engine, page=page, per_page=_PER_PAGE, provider=name, client_ip=client_ip
    )
    total_pages = max(1, math.ceil(total / _PER_PAGE))
    filters = {"client_ip": client_ip}

    # Find quota for this provider
    quota = next((q for q in _get_quota_info() if q["provider"] == name), None)

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "admin/audit/_rows.html",
            {"request": request, "rows": rows},
        )

    return templates.TemplateResponse(
        "admin/providers/detail.html",
        {
            "request": request,
            "user": user,
            "active_nav": f"provider_{name}",
            "css_version": _css_version,
            "provider_name": name,
            "stats": stats,
            "quota": quota,
            "rows": rows,
            "page": page,
            "total_pages": total_pages,
            "filters": filters,
        },
    )
```

- [ ] **Step 4: Add providers router**

Update `src/address_validator/routers/admin/router.py` — add import and include:

```python
from address_validator.routers.admin.providers import router as providers_router
admin_router.include_router(providers_router)
```

- [ ] **Step 5: Build Tailwind CSS, run tests**

```bash
bash scripts/build-css.sh
uv run pytest --no-cov -x
```

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/routers/admin/providers.py \
    src/address_validator/routers/admin/router.py \
    src/address_validator/templates/admin/providers/detail.html \
    src/address_validator/templates/admin/providers/_rows.html \
    src/address_validator/static/admin/css/tailwind.css
git commit -m "#43 feat: add per-provider detail views"
```

---

### Task 12: Query Helper Tests

**Files:**
- Create: `tests/unit/test_admin_queries.py`

- [ ] **Step 1: Write tests for query helpers**

Create `tests/unit/test_admin_queries.py`:

```python
"""Tests for admin dashboard SQL query helpers."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from address_validator.routers.admin.queries import (
    get_audit_rows,
    get_dashboard_stats,
    get_endpoint_stats,
    get_provider_stats,
)


async def _seed_rows(engine: AsyncEngine) -> None:
    """Insert sample audit_log rows for testing."""
    now = datetime.now(timezone.utc)
    rows = [
        {"ts": now, "ip": "1.2.3.4", "method": "POST", "ep": "/api/v1/validate",
         "status": 200, "provider": "usps", "vs": "confirmed", "cache": True},
        {"ts": now, "ip": "1.2.3.4", "method": "POST", "ep": "/api/v1/validate",
         "status": 200, "provider": "usps", "vs": "confirmed", "cache": False},
        {"ts": now, "ip": "5.6.7.8", "method": "POST", "ep": "/api/v1/parse",
         "status": 200, "provider": None, "vs": None, "cache": None},
        {"ts": now, "ip": "5.6.7.8", "method": "POST", "ep": "/api/v1/parse",
         "status": 400, "provider": None, "vs": None, "cache": None},
    ]
    async with engine.begin() as conn:
        for r in rows:
            await conn.execute(
                text("""
                    INSERT INTO audit_log (timestamp, client_ip, method, endpoint,
                        status_code, provider, validation_status, cache_hit)
                    VALUES (:ts, :ip, :method, :ep, :status, :provider, :vs, :cache)
                """),
                r,
            )


@pytest.mark.asyncio
async def test_get_dashboard_stats(db: AsyncEngine) -> None:
    await _seed_rows(db)
    stats = await get_dashboard_stats(db)
    assert stats["requests_today"] == 4
    assert stats["requests_all"] == 4
    assert stats["cache_hit_rate"] == 50.0  # 1 hit / 2 validate requests


@pytest.mark.asyncio
async def test_get_audit_rows_with_filter(db: AsyncEngine) -> None:
    await _seed_rows(db)
    rows, total = await get_audit_rows(db, endpoint="parse")
    assert total == 2
    assert all(r["endpoint"] == "/api/v1/parse" for r in rows)


@pytest.mark.asyncio
async def test_get_audit_rows_by_ip(db: AsyncEngine) -> None:
    await _seed_rows(db)
    rows, total = await get_audit_rows(db, client_ip="1.2.3.4")
    assert total == 2


@pytest.mark.asyncio
async def test_get_endpoint_stats(db: AsyncEngine) -> None:
    await _seed_rows(db)
    stats = await get_endpoint_stats(db, "parse")
    assert stats["total"] == 2
    assert 400 in stats["status_codes"]


@pytest.mark.asyncio
async def test_get_provider_stats(db: AsyncEngine) -> None:
    await _seed_rows(db)
    stats = await get_provider_stats(db, "usps")
    assert stats["total"] == 2
    assert "confirmed" in stats["validation_statuses"]
```

- [ ] **Step 2: Run tests**

Run: `uv run pytest tests/unit/test_admin_queries.py -v --no-cov -x`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/unit/test_admin_queries.py
git commit -m "#43 test: add query helper tests"
```

---

### Task 13: Admin Dashboard View Tests

**Files:**
- Create: `tests/unit/test_admin_views.py`
- Modify: `tests/conftest.py`

- [ ] **Step 1: Add admin auth fixtures to `tests/conftest.py`**

Add to the existing conftest:

```python
@pytest.fixture(scope="session")
def admin_headers() -> dict[str, str]:
    """Exe.dev proxy auth headers for admin dashboard tests."""
    return {
        "X-ExeDev-UserID": "test-user-123",
        "X-ExeDev-Email": "admin@test.example.com",
    }
```

- [ ] **Step 2: Create `tests/unit/test_admin_views.py`**

```python
"""Integration tests for admin dashboard views."""

import pytest
from starlette.testclient import TestClient


def test_admin_dashboard_requires_auth(client_no_auth: TestClient) -> None:
    """Unauthenticated request to /admin/ redirects to login."""
    response = client_no_auth.get("/admin/", follow_redirects=False)
    assert response.status_code == 302
    assert "/__exe.dev/login" in response.headers["location"]


def test_admin_dashboard_authenticated(client: TestClient, admin_headers: dict) -> None:
    """Authenticated request returns 200 with dashboard HTML."""
    response = client.get("/admin/", headers=admin_headers)
    assert response.status_code == 200
    assert "Dashboard" in response.text


def test_admin_audit_requires_auth(client_no_auth: TestClient) -> None:
    response = client_no_auth.get("/admin/audit/", follow_redirects=False)
    assert response.status_code == 302


def test_admin_endpoint_detail_404_for_unknown(client: TestClient, admin_headers: dict) -> None:
    response = client.get("/admin/endpoints/unknown", headers=admin_headers)
    assert response.status_code == 404


def test_admin_provider_detail_404_for_unknown(client: TestClient, admin_headers: dict) -> None:
    response = client.get("/admin/providers/unknown", headers=admin_headers)
    assert response.status_code == 404
```

Note: The `client` fixture from the root conftest includes the API key header. Admin views don't need API keys but they do need the app to be running. The admin views will fail gracefully when the DB is not available (no `VALIDATION_CACHE_DSN` in test env) — the dashboard catches exceptions and returns empty stats. If tests need a real DB, use the `db` fixture and set `VALIDATION_CACHE_DSN` in the test environment.

- [ ] **Step 3: Run tests**

Run: `uv run pytest tests/unit/test_admin_views.py -v --no-cov -x`
Expected: PASS (some views may return empty stats without a DB)

- [ ] **Step 4: Run full test suite**

Run: `uv run pytest --no-cov -x`
Expected: All pass

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_admin_views.py tests/conftest.py
git commit -m "#43 test: add admin dashboard view tests"
```

---

### Task 14: Journal Backfill Script

**Files:**
- Create: `scripts/backfill_audit_log.py`

- [ ] **Step 1: Create the backfill script**

```python
#!/usr/bin/env python3
"""One-shot backfill: parse journalctl logs into the audit_log table.

Extracts: timestamp, client IP, HTTP method, endpoint path, status code.
Fields left NULL: request_id, latency_ms, provider, validation_status, cache_hit.

Usage:
    uv run python scripts/backfill_audit_log.py

Idempotency: skips if any rows with NULL request_id already exist in the
journal's time range (indicating a previous backfill).
"""

import asyncio
import json
import re
import subprocess
import sys
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Matches uvicorn access log format:
# INFO:     1.2.3.4:0 - "POST /api/v1/validate HTTP/1.1" 200 OK
_ACCESS_RE = re.compile(
    r'INFO:\s+'
    r'(?P<ip>[\d.]+):\d+\s+-\s+'
    r'"(?P<method>\w+)\s+(?P<path>/\S+)\s+HTTP/[\d.]+"\s+'
    r'(?P<status>\d+)'
)

# Only backfill API routes
_API_PREFIX = "/api/"


def _parse_journal_line(line: str) -> dict | None:
    """Parse a single JSON journal entry into an audit_log row dict, or None."""
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return None

    message = entry.get("MESSAGE", "")
    match = _ACCESS_RE.search(message)
    if not match:
        return None

    path = match.group("path")
    if not path.startswith(_API_PREFIX):
        return None

    # __REALTIME_TIMESTAMP is microseconds since epoch
    ts_us = int(entry.get("__REALTIME_TIMESTAMP", 0))
    if ts_us == 0:
        return None

    return {
        "timestamp": datetime.fromtimestamp(ts_us / 1_000_000, tz=timezone.utc),
        "request_id": None,
        "client_ip": match.group("ip"),
        "method": match.group("method"),
        "endpoint": path,
        "status_code": int(match.group("status")),
        "latency_ms": None,
        "provider": None,
        "validation_status": None,
        "cache_hit": None,
        "error_detail": None,
    }


async def main() -> None:
    import os

    dsn = os.environ.get("VALIDATION_CACHE_DSN", "").strip()
    if not dsn:
        print("ERROR: VALIDATION_CACHE_DSN not set", file=sys.stderr)
        sys.exit(1)

    engine = create_async_engine(dsn)

    # Check for existing backfill
    async with engine.connect() as conn:
        existing = (
            await conn.execute(
                text("SELECT COUNT(*) FROM audit_log WHERE request_id IS NULL")
            )
        ).scalar()
        if existing and existing > 0:
            print(f"Skipping: {existing} backfilled rows already exist.")
            await engine.dispose()
            return

    # Read journal
    print("Reading journalctl output...")
    proc = subprocess.run(
        ["journalctl", "-u", "address-validator", "--output=json", "--no-pager"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        print(f"ERROR: journalctl failed: {proc.stderr}", file=sys.stderr)
        sys.exit(1)

    rows = []
    for line in proc.stdout.strip().splitlines():
        row = _parse_journal_line(line)
        if row:
            rows.append(row)

    if not rows:
        print("No API requests found in journal.")
        await engine.dispose()
        return

    print(f"Parsed {len(rows)} API requests from journal.")
    print(f"  Time range: {rows[0]['timestamp']} — {rows[-1]['timestamp']}")

    # Bulk insert
    insert_sql = text("""
        INSERT INTO audit_log (
            timestamp, request_id, client_ip, method, endpoint,
            status_code, latency_ms, provider, validation_status,
            cache_hit, error_detail
        ) VALUES (
            :timestamp, :request_id, :client_ip, :method, :endpoint,
            :status_code, :latency_ms, :provider, :validation_status,
            :cache_hit, :error_detail
        )
    """)

    batch_size = 1000
    inserted = 0
    async with engine.begin() as conn:
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            await conn.execute(insert_sql, batch)
            inserted += len(batch)
            print(f"  Inserted {inserted}/{len(rows)}...")

    print(f"Done. Backfilled {len(rows)} rows.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Make script executable**

```bash
chmod +x scripts/backfill_audit_log.py
```

- [ ] **Step 3: Test the parsing logic**

Create a quick test or run manually:
```bash
uv run python -c "
from scripts.backfill_audit_log import _parse_journal_line
import json
line = json.dumps({
    '__REALTIME_TIMESTAMP': '1774013422492820',
    'MESSAGE': 'INFO:     67.213.124.9:0 - \"POST /api/v1/standardize HTTP/1.1\" 200 OK'
})
result = _parse_journal_line(line)
print(result)
assert result['client_ip'] == '67.213.124.9'
assert result['endpoint'] == '/api/v1/standardize'
assert result['status_code'] == 200
print('OK')
"
```

- [ ] **Step 4: Commit**

```bash
git add scripts/backfill_audit_log.py
git commit -m "#43 feat: add journal backfill script for audit_log"
```

---

### Task 15: Final Integration — Restart Service, Run Backfill, Verify

This task is manual and ensures everything works end-to-end on the live service.

- [ ] **Step 1: Run full test suite with coverage**

Run: `uv run pytest`
Expected: All pass, coverage >= 80%

- [ ] **Step 2: Run ruff**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: Clean

- [ ] **Step 3: Restart the service to apply migration**

```bash
sudo systemctl restart address-validator
journalctl -u address-validator -n 20 --no-pager
```

Expected: Startup logs show `alembic upgrade head` applying migration 004 (audit_log table).

- [ ] **Step 4: Run the backfill script**

```bash
source /etc/address-validator/env
uv run python scripts/backfill_audit_log.py
```

Expected: `Parsed N API requests from journal. Done. Backfilled N rows.`

- [ ] **Step 5: Verify the dashboard loads**

Visit `https://<vm>.exe.xyz/admin/` in browser. Should see:
- Login redirect via exe.dev
- After auth: dashboard with stat cards showing backfilled data
- Audit log with ~15 days of historical entries
- Per-endpoint and per-provider views working

- [ ] **Step 6: Make a few API requests and verify live audit logging**

```bash
curl -H "X-API-Key: $(grep API_KEY /etc/address-validator/env | cut -d= -f2)" \
    -X POST http://localhost:8000/api/v1/parse \
    -H "Content-Type: application/json" \
    -d '{"address": "123 Main St, Anytown, WA 98101", "country": "US"}'
```

Then check `/admin/audit/` — should see the new request with non-null `request_id` and `latency_ms`.

- [ ] **Step 7: Final commit if any fixes needed**

```bash
git add -A
git commit -m "#43 fix: post-integration fixes"
```
