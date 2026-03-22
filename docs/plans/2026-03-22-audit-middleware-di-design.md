# Audit middleware DI — inject engine via `app.state`

**Issue:** #55
**Date:** 2026-03-22

## Problem

`middleware/audit.py` imported `get_engine` directly from `cache_db`, while
`CachingProvider` receives it as a constructor parameter. The inconsistency
made audit middleware harder to test (required module-level patching).

## Decision

Store the `AsyncEngine` on `app.state.engine` during lifespan startup.
Audit middleware reads it via `getattr(request.app.state, "engine", None)`.

- No direct `cache_db` import in the middleware
- Tests set `client.app.state.engine` directly — no module-level mocking
- Fail-open behavior preserved: `None` engine → skip audit write silently

## Files changed

| File | Change |
|---|---|
| `main.py` | Store engine on `app.state.engine` after `init_engine()` |
| `middleware/audit.py` | Read engine from `request.app.state` instead of importing `get_engine` |
| `tests/unit/test_audit_middleware.py` | Set `app.state.engine` directly, remove `get_engine` patch |
| `AGENTS.md` | Update sensitive-areas entry for audit middleware |
