# Raw Input Audit Traceability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Store the caller's original address input in `query_patterns.raw_input` and link each audit row to its cache entry via `audit_log.pattern_key`, enabling definitive JOIN-based traceability from the audit UI.

**Architecture:** Migration 007 adds two nullable TEXT columns. `raw_input` flows router → protocol kwarg → cache_provider → DB. `pattern_key` flows cache_provider → ContextVar → audit middleware → DB. The admin audit page gains a LEFT JOIN and a search filter.

**Tech Stack:** SQLAlchemy Core, Alembic, FastAPI, Jinja2/HTMX, pytest-asyncio

---

### Task 1: Migration 007 — add schema columns

**Files:**
- Create: `alembic/versions/007_raw_input_pattern_key.py`

- [ ] **Step 1: Create the migration file**

```python
"""Add raw_input to query_patterns; add pattern_key to audit_log.

Revision ID: 007
Revises: 006
Create Date: 2026-03-27

Two nullable columns — no data migration required:
- query_patterns.raw_input  TEXT — original caller input at first cache-entry time
- audit_log.pattern_key     TEXT — soft FK to query_patterns.pattern_key
"""

revision: str = "007"
down_revision: str = "006"
branch_labels = None
depends_on = None

import sqlalchemy as sa  # noqa: E402

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.add_column("query_patterns", sa.Column("raw_input", sa.Text(), nullable=True))
    op.add_column("audit_log", sa.Column("pattern_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_log", "pattern_key")
    op.drop_column("query_patterns", "raw_input")
```

- [ ] **Step 2: Run the migration and verify it succeeds**

```bash
uv run alembic upgrade head
```

Expected: `Running upgrade 006 -> 007` with no errors.

- [ ] **Step 3: Verify columns exist**

```bash
uv run python -c "
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text, inspect
async def check():
    dsn = open('/etc/address-validator/env').read()
    # or use test DSN:
    dsn = 'postgresql+asyncpg://address_validator:address_validator_dev@localhost/address_validator_test'
    e = create_async_engine(dsn)
    async with e.connect() as c:
        r = await c.execute(text(\"SELECT column_name FROM information_schema.columns WHERE table_name IN ('query_patterns','audit_log') AND column_name IN ('raw_input','pattern_key') ORDER BY table_name, column_name\"))
        print(r.fetchall())
    await e.dispose()
asyncio.run(check())
"
```

Expected: `[('audit_log', 'pattern_key'), ('query_patterns', 'raw_input')]` (or similar).

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/007_raw_input_pattern_key.py
git commit -m "#73 feat: migration 007 — add raw_input + pattern_key columns"
```

---

### Task 2: SQLAlchemy table definitions

**Files:**
- Modify: `src/address_validator/db/tables.py`

- [ ] **Step 1: Add `raw_input` to `query_patterns` and `pattern_key` to `audit_log`**

In `src/address_validator/db/tables.py`, add to `audit_log` after `error_detail`:

```python
    sa.Column("error_detail", sa.Text(), nullable=True),
    sa.Column("pattern_key", sa.Text(), nullable=True),
```

Add to `query_patterns` after `created_at`:

```python
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("raw_input", sa.Text(), nullable=True),
```

- [ ] **Step 2: Verify existing tests still pass**

```bash
uv run pytest tests/unit/test_audit_service.py tests/unit/test_admin_queries.py -x --no-cov -q
```

Expected: all pass (new nullable columns don't break existing inserts).

- [ ] **Step 3: Commit**

```bash
git add src/address_validator/db/tables.py
git commit -m "#73 feat: add pattern_key + raw_input to SQLAlchemy table defs"
```

---

### Task 3: Audit ContextVar — `pattern_key`

**Files:**
- Modify: `src/address_validator/services/audit.py`
- Test: `tests/unit/test_audit_service.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_audit_service.py` (after the existing imports, add `get_audit_pattern_key` to the import list, then add tests at the bottom):

```python
# Add get_audit_pattern_key to the existing import block:
from address_validator.services.audit import (
    get_audit_cache_hit,
    get_audit_pattern_key,
    get_audit_provider,
    get_audit_validation_status,
    reset_audit_context,
    set_audit_context,
    write_audit_row,
)
```

```python
def test_pattern_key_defaults_to_none() -> None:
    reset_audit_context()
    assert get_audit_pattern_key() is None


def test_set_audit_context_sets_pattern_key() -> None:
    set_audit_context(pattern_key="abc123def456")
    assert get_audit_pattern_key() == "abc123def456"
    reset_audit_context()


def test_reset_clears_pattern_key() -> None:
    set_audit_context(pattern_key="abc123def456")
    reset_audit_context()
    assert get_audit_pattern_key() is None


@pytest.mark.asyncio
async def test_write_audit_row_stores_pattern_key(db: AsyncEngine) -> None:
    await write_audit_row(
        db,
        timestamp=datetime.now(UTC),
        request_id="01TESTULID",
        client_ip="127.0.0.1",
        method="POST",
        endpoint="/api/v1/validate",
        status_code=200,
        latency_ms=10,
        provider="usps",
        validation_status="confirmed",
        cache_hit=True,
        error_detail=None,
        pattern_key="deadbeef1234",
    )
    async with db.connect() as conn:
        result = await conn.execute(text("SELECT pattern_key FROM audit_log"))
        row = result.fetchone()
    assert row.pattern_key == "deadbeef1234"
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/unit/test_audit_service.py -x --no-cov -q
```

Expected: `ImportError` or `TypeError` — `get_audit_pattern_key` doesn't exist yet.

- [ ] **Step 3: Update `src/address_validator/services/audit.py`**

Replace the entire file with:

```python
"""Audit logging — ContextVars for passing validation metadata to middleware.

The audit middleware (middleware/audit.py) reads these ContextVars after the
request completes to enrich audit_log rows with validation-specific fields.
The cache provider sets them during validate() so the middleware doesn't need
to understand the validation pipeline.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import TYPE_CHECKING

from address_validator.db.tables import audit_log

if TYPE_CHECKING:
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_audit_provider: ContextVar[str | None] = ContextVar("audit_provider", default=None)
_audit_validation_status: ContextVar[str | None] = ContextVar(
    "audit_validation_status", default=None
)
_audit_cache_hit: ContextVar[bool | None] = ContextVar("audit_cache_hit", default=None)
_audit_pattern_key: ContextVar[str | None] = ContextVar("audit_pattern_key", default=None)


def get_audit_provider() -> str | None:
    return _audit_provider.get()


def get_audit_validation_status() -> str | None:
    return _audit_validation_status.get()


def get_audit_cache_hit() -> bool | None:
    return _audit_cache_hit.get()


def get_audit_pattern_key() -> str | None:
    return _audit_pattern_key.get()


def reset_audit_context() -> None:
    """Reset all audit ContextVars to their defaults (None).

    Called at the start of each audited request to prevent stale values
    from a previous request leaking through on the same asyncio task.
    """
    _audit_provider.set(None)
    _audit_validation_status.set(None)
    _audit_cache_hit.set(None)
    _audit_pattern_key.set(None)


def set_audit_context(
    *,
    provider: str | None = None,
    validation_status: str | None = None,
    cache_hit: bool | None = None,
    pattern_key: str | None = None,
) -> None:
    """Set audit ContextVars for the current request."""
    if provider is not None:
        _audit_provider.set(provider)
    if validation_status is not None:
        _audit_validation_status.set(validation_status)
    if cache_hit is not None:
        _audit_cache_hit.set(cache_hit)
    if pattern_key is not None:
        _audit_pattern_key.set(pattern_key)


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
    pattern_key: str | None = None,
) -> None:
    """Insert a single audit_log row. Logs and swallows all errors (fail-open)."""
    try:
        async with engine.begin() as conn:
            await conn.execute(
                audit_log.insert().values(
                    timestamp=timestamp,
                    request_id=request_id,
                    client_ip=client_ip,
                    method=method,
                    endpoint=endpoint,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    provider=provider,
                    validation_status=validation_status,
                    cache_hit=cache_hit,
                    error_detail=error_detail,
                    pattern_key=pattern_key,
                )
            )
    except Exception:
        logger.warning("audit: failed to write audit row", exc_info=True)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_audit_service.py -x --no-cov -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/services/audit.py tests/unit/test_audit_service.py
git commit -m "#73 feat: add pattern_key ContextVar to audit service"
```

---

### Task 4: Protocol + leaf providers — accept `raw_input` kwarg

**Files:**
- Modify: `src/address_validator/services/validation/protocol.py`
- Modify: `src/address_validator/services/validation/null_provider.py`
- Modify: `src/address_validator/services/validation/usps_provider.py`
- Modify: `src/address_validator/services/validation/google_provider.py`
- Test: `tests/unit/validation/test_null_provider.py`

- [ ] **Step 1: Write a failing test for null_provider**

Add to `tests/unit/validation/test_null_provider.py`:

```python
@pytest.mark.asyncio
async def test_validate_accepts_raw_input_kwarg(std_address) -> None:
    """NullProvider.validate must accept raw_input without raising."""
    from address_validator.services.validation.null_provider import NullProvider
    provider = NullProvider()
    result = await provider.validate(std_address, raw_input="123 Main St, Springfield IL")
    assert result.validation.status == "unavailable"
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/unit/validation/test_null_provider.py -x --no-cov -q
```

Expected: `TypeError: validate() got an unexpected keyword argument 'raw_input'`

- [ ] **Step 3: Update `protocol.py`**

In `src/address_validator/services/validation/protocol.py`, change the `validate` signature:

```python
    async def validate(self, std: StandardizeResponseV1, *, raw_input: str | None = None) -> ValidateResponseV1:
        """Validate the standardised address *std* and return an authoritative response."""
        ...
```

- [ ] **Step 4: Update `null_provider.py`**

In `src/address_validator/services/validation/null_provider.py`, change the `validate` signature:

```python
    async def validate(self, std: StandardizeResponseV1, *, raw_input: str | None = None) -> ValidateResponseV1:
```

- [ ] **Step 5: Update `usps_provider.py`**

In `src/address_validator/services/validation/usps_provider.py`, change line 38:

```python
    async def validate(self, std: StandardizeResponseV1, *, raw_input: str | None = None) -> ValidateResponseV1:
```

- [ ] **Step 6: Update `google_provider.py`**

In `src/address_validator/services/validation/google_provider.py`, change line 46:

```python
    async def validate(self, std: StandardizeResponseV1, *, raw_input: str | None = None) -> ValidateResponseV1:
```

- [ ] **Step 7: Run tests to verify all pass**

```bash
uv run pytest tests/unit/validation/test_null_provider.py tests/unit/validation/test_usps_provider.py tests/unit/validation/test_google_provider.py -x --no-cov -q
```

Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add src/address_validator/services/validation/protocol.py \
        src/address_validator/services/validation/null_provider.py \
        src/address_validator/services/validation/usps_provider.py \
        src/address_validator/services/validation/google_provider.py \
        tests/unit/validation/test_null_provider.py
git commit -m "#73 feat: add raw_input kwarg to ValidationProvider protocol + leaf providers"
```

---

### Task 5: ChainProvider — thread `raw_input`

**Files:**
- Modify: `src/address_validator/services/validation/chain_provider.py`
- Test: `tests/unit/validation/test_chain_provider.py`

- [ ] **Step 1: Write a failing test**

Add to `tests/unit/validation/test_chain_provider.py` inside `class TestChainProvider`:

```python
    @pytest.mark.asyncio
    async def test_raw_input_threaded_to_provider(self, std_address) -> None:
        """ChainProvider must forward raw_input to each sub-provider."""
        provider = _mock_provider(_CONFIRMED)
        chain = ChainProvider(providers=[provider])

        await chain.validate(std_address, raw_input="123 Main St, Springfield IL")

        provider.validate.assert_awaited_once_with(std_address, raw_input="123 Main St, Springfield IL")

    @pytest.mark.asyncio
    async def test_raw_input_threaded_on_fallback(self, std_address) -> None:
        """raw_input is passed to the fallback provider, not lost on retry."""
        first = _rate_limited_provider()
        second = _mock_provider(_GOOGLE_CONFIRMED)
        chain = ChainProvider(providers=[first, second])

        await chain.validate(std_address, raw_input="456 Elm Ave")

        second.validate.assert_awaited_once_with(std_address, raw_input="456 Elm Ave")
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/unit/validation/test_chain_provider.py::TestChainProvider::test_raw_input_threaded_to_provider -x --no-cov -q
```

Expected: `AssertionError` — called without `raw_input` kwarg.

- [ ] **Step 3: Update `chain_provider.py`**

In `src/address_validator/services/validation/chain_provider.py`, replace the `validate` method:

```python
    async def validate(self, std: StandardizeResponseV1, *, raw_input: str | None = None) -> ValidateResponseV1:
        last_exc: ProviderRateLimitedError | ProviderAtCapacityError | None = None
        for provider in self._providers:
            name = type(provider).__name__
            try:
                return await provider.validate(std, raw_input=raw_input)
            except (ProviderRateLimitedError, ProviderAtCapacityError) as exc:
                last_exc = exc
                logger.warning(
                    "ChainProvider: %s at capacity or rate-limited, trying next provider", name
                )
        retry_after = last_exc.retry_after_seconds if last_exc is not None else 0.0
        raise ProviderRateLimitedError("all", retry_after_seconds=retry_after)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/validation/test_chain_provider.py -x --no-cov -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/services/validation/chain_provider.py \
        tests/unit/validation/test_chain_provider.py
git commit -m "#73 feat: thread raw_input through ChainProvider"
```

---

### Task 6: CachingProvider — store `raw_input`, set `pattern_key` ContextVar

**Files:**
- Modify: `src/address_validator/services/validation/cache_provider.py`
- Test: `tests/unit/validation/test_cache_provider.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/validation/test_cache_provider.py`:

```python
class TestRawInput:
    async def test_raw_input_stored_on_cache_miss(self, db: AsyncEngine) -> None:
        """raw_input is written to query_patterns on the first (miss) call."""
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))
        std = _make_std()

        await provider.validate(std, raw_input="123 Main St, Springfield IL 62701")

        pattern_key = _make_pattern_key(std)
        row = await _fetch_one(db, query_patterns, query_patterns.c.pattern_key == pattern_key)
        assert row is not None
        assert row["raw_input"] == "123 Main St, Springfield IL 62701"

    async def test_raw_input_none_stored_when_not_provided(self, db: AsyncEngine) -> None:
        """raw_input is NULL when not supplied (e.g. called without the kwarg)."""
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))

        await provider.validate(_make_std())

        row = await _fetch_one(db, query_patterns)
        assert row["raw_input"] is None


class TestPatternKeyContextVar:
    async def test_pattern_key_set_on_cache_miss(self, db: AsyncEngine) -> None:
        """pattern_key ContextVar is set after a successful cache store."""
        from address_validator.services.audit import get_audit_pattern_key, reset_audit_context
        reset_audit_context()

        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))
        std = _make_std()

        await provider.validate(std)

        expected = _make_pattern_key(std)
        assert get_audit_pattern_key() == expected
        reset_audit_context()

    async def test_pattern_key_set_on_cache_hit(self, db: AsyncEngine) -> None:
        """pattern_key ContextVar is set on a cache hit."""
        from address_validator.services.audit import get_audit_pattern_key, reset_audit_context
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))
        std = _make_std()

        await provider.validate(std)  # miss — stores
        reset_audit_context()

        await provider.validate(std)  # hit

        expected = _make_pattern_key(std)
        assert get_audit_pattern_key() == expected
        reset_audit_context()

    async def test_pattern_key_not_set_on_store_failure(self, db: AsyncEngine) -> None:
        """pattern_key ContextVar is NOT set when _store raises (fail-open)."""
        from unittest.mock import patch
        from address_validator.services.audit import get_audit_pattern_key, reset_audit_context
        reset_audit_context()

        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))

        with patch(
            "address_validator.services.validation.cache_provider._store",
            side_effect=RuntimeError("disk full"),
        ):
            await provider.validate(_make_std())

        assert get_audit_pattern_key() is None
        reset_audit_context()

    async def test_pattern_key_not_set_for_unavailable(self, db: AsyncEngine) -> None:
        """pattern_key is not set when status is unavailable (nothing stored)."""
        from address_validator.services.audit import get_audit_pattern_key, reset_audit_context
        reset_audit_context()

        response = _make_unavailable_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))

        await provider.validate(_make_std())

        assert get_audit_pattern_key() is None
        reset_audit_context()
```

Also update the **existing** broken test in `TestCacheMiss` (it asserts `inner.validate.assert_awaited_once_with(std)` but now the call includes `raw_input=None`):

```python
    async def test_cache_miss_calls_inner(self, db: AsyncEngine) -> None:
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))
        std = _make_std()

        result = await provider.validate(std)

        inner.validate.assert_awaited_once_with(std, raw_input=None)
        assert result.validation.status == "confirmed"
```

- [ ] **Step 2: Run to verify failures**

```bash
uv run pytest tests/unit/validation/test_cache_provider.py -x --no-cov -q 2>&1 | head -30
```

Expected: failures on `TestRawInput` and `TestPatternKeyContextVar`, plus `test_cache_miss_calls_inner`.

- [ ] **Step 3: Update `cache_provider.py`**

Replace the `_store` function and `CachingProvider.validate` method:

```python
async def _store(
    engine: AsyncEngine,
    pattern_key: str,
    canonical_key: str,
    result: ValidateResponseV1,
    *,
    raw_input: str | None,
) -> None:
    now = _now_utc()
    components_json = result.components.model_dump(mode="python") if result.components else None
    warnings_json = result.warnings

    async with engine.begin() as conn:
        await conn.execute(
            pg_insert(validated_addresses)
            .values(
                canonical_key=canonical_key,
                provider=result.validation.provider,
                status=result.validation.status,
                dpv_match_code=result.validation.dpv_match_code,
                address_line_1=result.address_line_1,
                address_line_2=result.address_line_2,
                city=result.city,
                region=result.region,
                postal_code=result.postal_code,
                country=result.country,
                validated=result.validated,
                components_json=components_json,
                latitude=result.latitude,
                longitude=result.longitude,
                warnings_json=warnings_json,
                created_at=now,
                last_seen_at=now,
                validated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[validated_addresses.c.canonical_key],
                set_={"last_seen_at": now, "validated_at": now},
            ),
        )

        await conn.execute(
            pg_insert(query_patterns)
            .values(
                pattern_key=pattern_key,
                canonical_key=canonical_key,
                created_at=now,
                raw_input=raw_input,
            )
            .on_conflict_do_nothing(
                index_elements=[query_patterns.c.pattern_key],
            ),
        )

    logger.debug(
        "cache_store: pattern_key=%s canonical_key=%s status=%s",
        pattern_key,
        canonical_key,
        result.validation.status,
    )
```

Replace `CachingProvider.validate`:

```python
    async def validate(self, std: StandardizeResponseV1, *, raw_input: str | None = None) -> ValidateResponseV1:
        """Check the cache; delegate to inner provider on miss; store the result.

        Fail-open: any database error during lookup or store is logged as a
        warning and the request continues without the cache.
        """
        pattern_key = _make_pattern_key(std)
        engine: AsyncEngine | None = None

        try:
            engine = self._get_engine()
            cached = await _lookup(engine, pattern_key, self._ttl_days)
        except Exception:
            logger.warning("cache_lookup: storage error — failing open", exc_info=True)
            cached = None

        if cached is not None:
            set_audit_context(
                provider=cached.validation.provider,
                validation_status=cached.validation.status,
                cache_hit=True,
                pattern_key=pattern_key,
            )
            logger.info(
                "validate: provider=%s status=%s cache_hit=true",
                cached.validation.provider,
                cached.validation.status,
            )
            return cached

        result: ValidateResponseV1 = await self._inner.validate(std, raw_input=raw_input)

        set_audit_context(
            provider=result.validation.provider,
            validation_status=result.validation.status,
            cache_hit=False,
        )

        logger.info(
            "validate: provider=%s status=%s cache_hit=false",
            result.validation.provider,
            result.validation.status,
        )

        if result.validation.status == "unavailable":
            logger.debug(
                "cache_store: skip provider=%s status=unavailable",
                result.validation.provider,
            )
            return result

        if engine is not None:
            try:
                canonical_key = _make_canonical_key(result)
                await _store(engine, pattern_key, canonical_key, result, raw_input=raw_input)
                set_audit_context(pattern_key=pattern_key)
            except Exception:
                logger.warning("cache_store: storage error — result not cached", exc_info=True)

        return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/validation/test_cache_provider.py -x --no-cov -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/services/validation/cache_provider.py \
        tests/unit/validation/test_cache_provider.py
git commit -m "#73 feat: store raw_input in query_patterns; set pattern_key ContextVar in CachingProvider"
```

---

### Task 7: Audit middleware — read `pattern_key` ContextVar

**Files:**
- Modify: `src/address_validator/middleware/audit.py`
- Test: `tests/unit/test_audit_middleware.py`

- [ ] **Step 1: Write a failing test**

Add to `tests/unit/test_audit_middleware.py`:

```python
def test_audit_row_receives_pattern_key() -> None:
    """pattern_key ContextVar set during the endpoint must appear in the audit row."""
    from address_validator.services.audit import set_audit_context

    mini = FastAPI()
    mini.add_middleware(AuditMiddleware)
    mini.add_middleware(RequestIdMiddleware)
    mini.state.engine = MagicMock()

    @mini.get("/api/v1/fake")
    async def _fake_endpoint() -> dict[str, str]:
        set_audit_context(
            provider="usps",
            validation_status="confirmed",
            cache_hit=True,
            pattern_key="cafebabe1234",
        )
        return {"ok": "true"}

    mock_write = AsyncMock()
    with patch("address_validator.middleware.audit.write_audit_row", mock_write):
        tc = TestClient(mini)
        tc.get("/api/v1/fake")

    mock_write.assert_called_once()
    kwargs = mock_write.call_args.kwargs
    assert kwargs["pattern_key"] == "cafebabe1234", (
        f"pattern_key should be 'cafebabe1234', got {kwargs.get('pattern_key')!r}"
    )
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/unit/test_audit_middleware.py::test_audit_row_receives_pattern_key -x --no-cov -q
```

Expected: `AssertionError` — `pattern_key` not passed to `write_audit_row`.

- [ ] **Step 3: Update `middleware/audit.py`**

Add `get_audit_pattern_key` to the import from `services.audit`:

```python
from address_validator.services.audit import (
    get_audit_cache_hit,
    get_audit_pattern_key,
    get_audit_provider,
    get_audit_validation_status,
    reset_audit_context,
    write_audit_row,
)
```

In `AuditMiddleware.__call__`, after `cache_hit = get_audit_cache_hit()`, add:

```python
        pattern_key = get_audit_pattern_key()
```

Add `pattern_key=pattern_key` to the `write_audit_row(...)` call:

```python
        task = asyncio.create_task(
            write_audit_row(
                engine,
                timestamp=datetime.now(UTC),
                request_id=get_request_id() or None,
                client_ip=_get_client_ip(scope),
                method=method,
                endpoint=path,
                status_code=status_code,
                latency_ms=elapsed_ms,
                provider=provider,
                validation_status=validation_status,
                cache_hit=cache_hit,
                error_detail=error_detail,
                pattern_key=pattern_key,
            )
        )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_audit_middleware.py -x --no-cov -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/middleware/audit.py \
        tests/unit/test_audit_middleware.py
git commit -m "#73 feat: thread pattern_key from ContextVar through audit middleware"
```

---

### Task 8: Validate router — extract and pass `raw_input`

**Files:**
- Modify: `src/address_validator/routers/v1/validate.py`
- Test: `tests/unit/test_validate_router.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_validate_router.py` inside `class TestValidateEndpoint`:

```python
    def test_address_string_raw_input_passed_to_provider(self, client: TestClient) -> None:
        """The original address string is passed as raw_input to the provider."""
        provider = _make_null_provider(NULL_RESPONSE)
        with _mock_registry_with(provider):
            client.post(
                "/api/v1/validate",
                json={"address": "123 Main St, Springfield, IL 62701"},
            )
        kwargs = provider.validate.call_args.kwargs
        assert kwargs.get("raw_input") == "123 Main St, Springfield, IL 62701"

    def test_components_raw_input_is_json(self, client: TestClient) -> None:
        """Component dict input is JSON-serialised as raw_input."""
        import json as json_mod

        comps = {
            "address_number": "123",
            "street_name": "MAIN",
            "street_suffix": "ST",
            "city": "SPRINGFIELD",
            "region": "IL",
            "postal_code": "62701",
        }
        provider = _make_null_provider(NULL_RESPONSE)
        with _mock_registry_with(provider):
            client.post("/api/v1/validate", json={"components": comps})

        raw = provider.validate.call_args.kwargs.get("raw_input")
        assert raw is not None
        assert json_mod.loads(raw) == comps
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/unit/test_validate_router.py::TestValidateEndpoint::test_address_string_raw_input_passed_to_provider -x --no-cov -q
```

Expected: `AssertionError` — `raw_input` is `None` (not yet extracted).

- [ ] **Step 3: Update `routers/v1/validate.py`**

Add `import json` at the top of the imports section:

```python
import json
import logging
import math
```

Replace the `validate_address_v1` body with:

```python
async def validate_address_v1(req: ValidateRequestV1, request: Request) -> ValidateResponseV1:
    check_country(req.country)

    upstream_warnings: list[str] = []

    if req.components:
        comps = req.components
        raw_input: str | None = json.dumps(
            req.components, separators=(",", ":"), ensure_ascii=True
        )
    else:
        # model_validator guarantees address is non-blank when components is absent
        parse_result = parse_address(req.address.strip(), country=req.country)  # type: ignore[union-attr]
        comps = parse_result.components.values
        upstream_warnings = parse_result.warnings
        raw_input = req.address

    std = standardize(comps, country=req.country, upstream_warnings=upstream_warnings)

    provider = request.app.state.registry.get_provider()
    logger.debug("validate_address_v1: provider=%s", type(provider).__name__)
    try:
        result = await provider.validate(std, raw_input=raw_input)
    except ProviderRateLimitedError as exc:
        raise APIError(
            status_code=429,
            error="provider_rate_limited",
            message="All configured validation providers are currently rate-limited. Retry later.",
            headers={"Retry-After": str(math.ceil(exc.retry_after_seconds))},
        ) from None

    if std.warnings:
        result = result.model_copy(update={"warnings": std.warnings + result.warnings})

    return result
```

- [ ] **Step 4: Run all validate router tests**

```bash
uv run pytest tests/unit/test_validate_router.py -x --no-cov -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/routers/v1/validate.py \
        tests/unit/test_validate_router.py
git commit -m "#73 feat: extract raw_input in validate router and pass to provider"
```

---

### Task 9: Admin queries — LEFT JOIN + `raw_input` filter

**Files:**
- Modify: `src/address_validator/routers/admin/queries.py`
- Test: `tests/unit/test_admin_queries.py`

- [ ] **Step 1: Write a failing test**

Add to `tests/unit/test_admin_queries.py` (add `query_patterns` to the import at the top, from `address_validator.db.tables`):

```python
from address_validator.db.tables import ERROR_STATUS_MIN, audit_log, audit_daily_stats, query_patterns
```

Add helper and test:

```python
async def _seed_cache_row(
    engine: AsyncEngine,
    *,
    pattern_key: str,
    raw_input: str,
    audit_pattern_key: str | None = None,
) -> None:
    """Insert a query_patterns row and optionally link an audit_log row via pattern_key."""
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        # Insert a minimal validated_addresses row first (FK requirement)
        await conn.execute(
            text("""
                INSERT INTO validated_addresses
                    (canonical_key, status, country, created_at, last_seen_at, validated_at)
                VALUES (:ck, 'confirmed', 'US', :now, :now, :now)
                ON CONFLICT DO NOTHING
            """),
            {"ck": f"canonical_{pattern_key}", "now": now},
        )
        await conn.execute(
            text("""
                INSERT INTO query_patterns (pattern_key, canonical_key, created_at, raw_input)
                VALUES (:pk, :ck, :now, :raw)
            """),
            {"pk": pattern_key, "ck": f"canonical_{pattern_key}", "now": now, "raw": raw_input},
        )
        if audit_pattern_key is not None:
            await conn.execute(
                text("""
                    UPDATE audit_log SET pattern_key = :pk
                    WHERE pattern_key IS NULL
                    LIMIT 1
                """),
                {"pk": audit_pattern_key},
            )


@pytest.mark.asyncio
async def test_get_audit_rows_by_raw_input(db: AsyncEngine) -> None:
    """raw_input filter returns only rows joined to a matching query_patterns entry."""
    await _seed_rows(db)

    # Assign pattern_key to the first validate row, seed matching query_patterns entry
    pk = "aaaa1111"
    async with db.begin() as conn:
        await conn.execute(
            text("""
                UPDATE audit_log SET pattern_key = :pk
                WHERE endpoint = '/api/v1/validate'
                  AND pattern_key IS NULL
                ORDER BY id
                LIMIT 1
            """),
            {"pk": pk},
        )
    await _seed_cache_row(db, pattern_key=pk, raw_input="123 Main St, Springfield IL")

    rows, total = await get_audit_rows(db, raw_input="Springfield")
    assert total == 1
    assert rows[0]["raw_input"] == "123 Main St, Springfield IL"


@pytest.mark.asyncio
async def test_get_audit_rows_raw_input_not_set_excluded(db: AsyncEngine) -> None:
    """Rows without a linked query_patterns entry are excluded when filtering by raw_input."""
    await _seed_rows(db)

    rows, total = await get_audit_rows(db, raw_input="anything")
    assert total == 0


@pytest.mark.asyncio
async def test_get_audit_rows_includes_raw_input_column(db: AsyncEngine) -> None:
    """Each returned row dict contains a 'raw_input' key (NULL when no cache link)."""
    await _seed_rows(db)
    rows, _ = await get_audit_rows(db)
    assert all("raw_input" in r for r in rows)
```

- [ ] **Step 2: Run to verify failure**

```bash
uv run pytest tests/unit/test_admin_queries.py::test_get_audit_rows_by_raw_input -x --no-cov -q
```

Expected: failure — `get_audit_rows` doesn't accept `raw_input` kwarg yet.

- [ ] **Step 3: Update `routers/admin/queries.py`**

Add `query_patterns` to the import from `db.tables`:

```python
from address_validator.db.tables import ERROR_STATUS_MIN, audit_log, audit_daily_stats, query_patterns
```

Replace `get_audit_rows` with:

```python
async def get_audit_rows(
    engine: AsyncEngine,
    *,
    page: int = 1,
    per_page: int = 50,
    endpoint: str | None = None,
    provider: str | None = None,
    client_ip: str | None = None,
    status_min: int | None = None,
    raw_input: str | None = None,
) -> tuple[list[dict], int]:
    """Fetch paginated, filtered audit_log rows. Returns (rows, total_count)."""
    conditions: list[ColumnElement] = []

    if endpoint:
        conditions.append(audit_log.c.endpoint == f"/api/v1/{endpoint}")
    if provider:
        conditions.append(audit_log.c.provider == provider)
    if client_ip:
        conditions.append(audit_log.c.client_ip == client_ip)
    if status_min:
        conditions.append(audit_log.c.status_code >= status_min)
    if raw_input:
        conditions.append(query_patterns.c.raw_input.ilike(f"%{raw_input}%"))

    joined = audit_log.outerjoin(
        query_patterns,
        audit_log.c.pattern_key == query_patterns.c.pattern_key,
    )

    async with engine.connect() as conn:
        count_stmt = select(func.count()).select_from(joined)
        for cond in conditions:
            count_stmt = count_stmt.where(cond)
        total = (await conn.execute(count_stmt)).scalar()

        row_stmt = select(
            audit_log.c.id,
            audit_log.c.timestamp,
            audit_log.c.request_id,
            audit_log.c.client_ip,
            audit_log.c.method,
            audit_log.c.endpoint,
            audit_log.c.status_code,
            audit_log.c.latency_ms,
            audit_log.c.provider,
            audit_log.c.validation_status,
            audit_log.c.cache_hit,
            audit_log.c.error_detail,
            query_patterns.c.raw_input,
        ).select_from(joined)
        for cond in conditions:
            row_stmt = row_stmt.where(cond)
        row_stmt = (
            row_stmt.order_by(audit_log.c.timestamp.desc())
            .limit(per_page)
            .offset((page - 1) * per_page)
        )
        result = await conn.execute(row_stmt)
        rows = [dict(r._mapping) for r in result]  # noqa: SLF001

    return rows, total
```

- [ ] **Step 4: Run all admin query tests**

```bash
uv run pytest tests/unit/test_admin_queries.py -x --no-cov -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/routers/admin/queries.py \
        tests/unit/test_admin_queries.py
git commit -m "#73 feat: LEFT JOIN query_patterns in get_audit_rows; add raw_input filter"
```

---

### Task 10: Admin UI — `raw_input` filter input and table column

**Files:**
- Modify: `src/address_validator/routers/admin/audit_views.py`
- Modify: `src/address_validator/templates/admin/audit/list.html`
- Modify: `src/address_validator/templates/admin/audit/_rows.html`

- [ ] **Step 1: Update `audit_views.py`**

Replace the `audit_list` handler:

```python
@router.get("/", response_class=HTMLResponse, response_model=None)
async def audit_list(
    request: Request,
    page: int = Query(1, ge=1),
    client_ip: str | None = Query(None),
    endpoint: str | None = Query(None),
    status_min: int | None = Query(None, ge=100, le=599),
    raw_input: str | None = Query(None),
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    rows, total = await get_audit_rows(
        ctx.engine,
        page=page,
        per_page=_PER_PAGE,
        endpoint=endpoint,
        client_ip=client_ip,
        status_min=status_min,
        raw_input=raw_input,
    )

    total_pages = max(1, math.ceil(total / _PER_PAGE))
    filters = {
        "client_ip": client_ip,
        "endpoint": endpoint,
        "status_min": status_min,
        "raw_input": raw_input,
    }

    if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"):
        return templates.TemplateResponse(
            "admin/audit/_rows.html",
            {"request": request, "rows": rows},
        )

    return templates.TemplateResponse(
        "admin/audit/list.html",
        {
            "request": request,
            "user": ctx.user,
            "active_nav": "audit",
            "css_version": get_css_version(),
            "rows": rows,
            "page": page,
            "total_pages": total_pages,
            "filters": filters,
        },
    )
```

- [ ] **Step 2: Update `templates/admin/audit/list.html`**

Replace the entire file with:

```html
{% extends "admin/base.html" %}
{% block title %}Audit Log{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-6">Audit Log</h1>

<form class="flex flex-wrap gap-3 mb-6 items-end"
      hx-get="/admin/audit/"
      hx-target="#audit-rows"
      hx-push-url="true">
    <div>
        <label for="client_ip" class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Client IP</label>
        <input type="text" name="client_ip" id="client_ip" value="{{ filters.client_ip or '' }}"
               class="border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-2 py-1 text-sm w-40 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100"
               placeholder="e.g. 10.0.0.1">
    </div>
    <div>
        <label for="endpoint" class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Endpoint</label>
        <select name="endpoint" id="endpoint"
                class="border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100">
            <option value="">All</option>
            {% for ep in ['parse', 'standardize', 'validate', 'health'] %}
            <option value="{{ ep }}" {% if filters.endpoint == ep %}selected{% endif %}>{{ ep }}</option>
            {% endfor %}
        </select>
    </div>
    <div>
        <label for="status_min" class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Min Status</label>
        <input type="number" name="status_min" id="status_min" value="{{ filters.status_min or '' }}"
               class="border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-2 py-1 text-sm w-24 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100"
               placeholder="400" min="100" max="599">
    </div>
    <div>
        <label for="raw_input" class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Raw Input</label>
        <input type="text" name="raw_input" id="raw_input" value="{{ filters.raw_input or '' }}"
               class="border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-2 py-1 text-sm w-52 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100"
               placeholder="address substring…">
    </div>
    <button type="submit"
            class="bg-co-purple text-white px-4 py-1 rounded text-sm font-medium hover:bg-co-purple-700 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100 focus:ring-offset-1 dark:ring-offset-gray-800 min-h-[32px]">
        Filter
    </button>
    <a href="/admin/audit/"
       class="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 underline self-center">Clear</a>
</form>

<div class="overflow-x-auto bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
    <table class="w-full text-left">
        <thead class="sticky top-0 bg-gray-50 dark:bg-gray-700 border-b border-gray-200 dark:border-gray-600 text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wide">
            <tr>
                <th class="px-3 py-2">Time</th>
                <th class="px-3 py-2">IP</th>
                <th class="px-3 py-2">Method</th>
                <th class="px-3 py-2">Endpoint</th>
                <th class="px-3 py-2">Status</th>
                <th class="px-3 py-2 text-right">Latency</th>
                <th class="px-3 py-2">Provider</th>
                <th class="px-3 py-2">Cache</th>
                <th class="px-3 py-2">Raw Input</th>
            </tr>
        </thead>
        <tbody id="audit-rows">
            {% include "admin/audit/_rows.html" %}
        </tbody>
    </table>
</div>

{% if total_pages > 1 %}
<nav class="flex items-center gap-2 mt-4 text-sm" aria-label="Pagination">
    {% if page > 1 %}
    <a href="?page={{ page - 1 }}{% if filters.client_ip %}&client_ip={{ filters.client_ip }}{% endif %}{% if filters.endpoint %}&endpoint={{ filters.endpoint }}{% endif %}{% if filters.status_min %}&status_min={{ filters.status_min }}{% endif %}{% if filters.raw_input %}&raw_input={{ filters.raw_input }}{% endif %}"
       class="px-3 py-1 rounded border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100">&laquo; Prev</a>
    {% endif %}
    <span class="text-gray-500 dark:text-gray-400">Page {{ page }} of {{ total_pages }}</span>
    {% if page < total_pages %}
    <a href="?page={{ page + 1 }}{% if filters.client_ip %}&client_ip={{ filters.client_ip }}{% endif %}{% if filters.endpoint %}&endpoint={{ filters.endpoint }}{% endif %}{% if filters.status_min %}&status_min={{ filters.status_min }}{% endif %}{% if filters.raw_input %}&raw_input={{ filters.raw_input }}{% endif %}"
       class="px-3 py-1 rounded border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100">Next &raquo;</a>
    {% endif %}
</nav>
{% endif %}
{% endblock %}
```

- [ ] **Step 3: Update `templates/admin/audit/_rows.html`**

Replace the entire file with:

```html
{% for row in rows %}
<tr class="border-b border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 text-sm">
    <td class="px-3 py-2 whitespace-nowrap text-gray-500 dark:text-gray-400">{{ row["timestamp"].strftime('%Y-%m-%d %H:%M:%S') if row["timestamp"] else '' }}</td>
    <td class="px-3 py-2 whitespace-nowrap text-gray-700 dark:text-gray-300">{{ row["client_ip"] or '' }}</td>
    <td class="px-3 py-2 whitespace-nowrap text-gray-700 dark:text-gray-300">{{ row["method"] or '' }}</td>
    <td class="px-3 py-2 whitespace-nowrap text-gray-700 dark:text-gray-300">{{ row["endpoint"] or '' }}</td>
    <td class="px-3 py-2 whitespace-nowrap">
        {% if row["status_code"] and row["status_code"] < 400 %}
            <span class="inline-flex items-center gap-1 text-green-700 dark:text-green-400">&#10003; {{ row["status_code"] }}</span>
        {% elif row["status_code"] and row["status_code"] < 500 %}
            <span class="inline-flex items-center gap-1 text-yellow-600 dark:text-yellow-400">&#9650; {{ row["status_code"] }}</span>
        {% elif row["status_code"] %}
            <span class="inline-flex items-center gap-1 text-red-600 dark:text-red-400">&#10005; {{ row["status_code"] }}</span>
        {% endif %}
    </td>
    <td class="px-3 py-2 whitespace-nowrap text-gray-700 dark:text-gray-300 text-right">{% if row["latency_ms"] is not none %}{{ row["latency_ms"] }}ms{% endif %}</td>
    <td class="px-3 py-2 whitespace-nowrap text-gray-700 dark:text-gray-300">{{ row["provider"] or '' }}</td>
    <td class="px-3 py-2 whitespace-nowrap">
        {% if row["cache_hit"] is true %}
            <span class="text-green-600 dark:text-green-400 font-medium">HIT</span>
        {% elif row["cache_hit"] is false %}
            <span class="text-gray-400 dark:text-gray-500">MISS</span>
        {% endif %}
    </td>
    <td class="px-3 py-2 text-gray-700 dark:text-gray-300 max-w-xs">
        {% if row["raw_input"] %}
            <span title="{{ row['raw_input'] }}" class="block truncate text-xs font-mono">{{ row["raw_input"] }}</span>
        {% else %}
            <span class="text-gray-400 dark:text-gray-600">—</span>
        {% endif %}
    </td>
</tr>
{% else %}
<tr><td colspan="9" class="px-3 py-8 text-center text-gray-400 dark:text-gray-500">No audit log entries found.</td></tr>
{% endfor %}
```

- [ ] **Step 4: Run the full test suite**

```bash
uv run pytest --no-cov -x -q
```

Expected: all tests pass.

- [ ] **Step 5: Run linting**

```bash
uv run ruff check . && uv run ruff format --check .
```

Fix any issues found, then re-run until clean.

- [ ] **Step 6: Run coverage check**

```bash
uv run pytest -q
```

Expected: coverage ≥ 80% (baseline ~93% — verify no regression).

- [ ] **Step 7: Commit**

```bash
git add src/address_validator/routers/admin/audit_views.py \
        src/address_validator/templates/admin/audit/list.html \
        src/address_validator/templates/admin/audit/_rows.html
git commit -m "#73 feat: add raw_input filter and column to admin audit view"
```
