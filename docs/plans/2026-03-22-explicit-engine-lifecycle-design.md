# Explicit Engine Lifecycle in `cache_db`

**Issue:** #52
**Date:** 2026-03-22

## Problem

`get_engine()` lazily creates the async engine **and** runs Alembic migrations
on first call. Any code path that touches the DB — including the audit
middleware on every request — can trigger a full `alembic upgrade head` if the
engine singleton was disposed and re-created.

Migration runs via `run_in_executor`, blocking a thread-pool slot if the DB is
slow. Under blue-green deployments or with locking DDL this becomes
problematic.

## Decision

Replace the lazy-factory pattern with explicit init + accessor.

### New `cache_db` API

| Function | Purpose |
|---|---|
| `init_engine()` | Creates engine + runs migrations. Called once in lifespan startup. Raises `RuntimeError` on failure (fail-fast). |
| `get_engine()` | Pure accessor — returns the initialized engine. Raises `RuntimeError` if `init_engine()` was not called. |
| `close_engine()` | Unchanged — disposes engine, sets singleton to `None`. |
| `_run_migrations()` | Private. Called from `init_engine()` only. |

### Lifespan change (`main.py`)

`await init_engine()` is added before registry construction, ensuring:

- Migrations run exactly once, before any request handling
- DB connectivity is validated at boot (fail-fast)
- `close_engine()` on shutdown cannot re-trigger migrations

### Audit middleware

No changes. Keeps calling `get_engine()` with existing fail-open
`except Exception` guard. The function now returns the pre-initialized
singleton instead of potentially creating + migrating.

### Admin views

No changes. Same `get_engine()` import pattern.

### Tests

- `test_cache_db.py` — updated for new API: test `init_engine()` for creation,
  test `get_engine()` raises before init
- Other test files — `CachingProvider` mocks `get_engine`, unaffected
- `conftest.py` — no changes (sync TestClient doesn't run lifespan)

### Files touched

- `src/address_validator/services/validation/cache_db.py`
- `src/address_validator/main.py`
- `tests/unit/validation/test_cache_db.py`
