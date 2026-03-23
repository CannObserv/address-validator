# Admin Route DI Refactor

**Issue:** #63
**Date:** 2026-03-23
**Status:** Approved

## Problem

Admin route handlers (`dashboard.py`, `audit_views.py`, `endpoints.py`, `providers.py`) import `cache_db.get_engine()` directly and wrap every call in try/except. This is inconsistent with the `app.state.engine` DI pattern established in #55 for audit middleware, and `get_admin_user` is called manually with `isinstance` branching at every call site.

## Design

### `AdminContext` dataclass

```python
@dataclass(frozen=True)
class AdminContext:
    user: AdminUser
    engine: AsyncEngine
    request: Request
```

Single `Depends(get_admin_context)` per handler replaces both manual `get_admin_user()` calls and `get_engine()` try/except blocks.

### Dependency functions in `deps.py`

**`get_admin_user(request: Request) -> AdminUser`**
- Raises `RedirectResponse` directly when unauthenticated (no union return)
- Handlers receive guaranteed `AdminUser`

**`get_engine_dep(request: Request) -> AsyncEngine`**
- Reads `request.app.state.engine`
- If `None`: returns 503 HTML error page (rendered admin template)

**`get_admin_context(request: Request) -> AdminContext`**
- Calls `get_admin_user` first (auth before engine access)
- Then `get_engine_dep`
- Returns `AdminContext`

### 503 error template

New `templates/admin/error_503.html` — extends `base.html`, shows "Database not configured" message with admin nav/styling intact.

### Route handler changes

All 4 admin route handlers:
- Remove `from address_validator.db import engine as db_engine`
- Remove try/except blocks around `get_engine()`
- Remove manual `get_admin_user()` + `isinstance` check
- Add `ctx: AdminContext = Depends(get_admin_context)`
- Use `ctx.engine`, `ctx.user`, `ctx.request`

### Test changes

- Use `app.dependency_overrides[get_admin_context]` to inject test fixtures
- Remove `app.state.engine` manipulation in admin tests
- Add test for 503 template when engine is `None`

## Files touched

| File | Change |
|---|---|
| `routers/admin/deps.py` | Add `AdminContext`, `get_engine_dep`, `get_admin_context`; refactor `get_admin_user` |
| `templates/admin/error_503.html` | New — 503 error page |
| `routers/admin/dashboard.py` | Use `Depends(get_admin_context)` |
| `routers/admin/audit_views.py` | Use `Depends(get_admin_context)` |
| `routers/admin/endpoints.py` | Use `Depends(get_admin_context)` |
| `routers/admin/providers.py` | Use `Depends(get_admin_context)` |
| `tests/unit/test_admin_views.py` | Use `dependency_overrides` |
| `AGENTS.md` | Update sensitive-areas for `deps.py` |

## What stays unchanged

- `app.state.engine` set in lifespan — still the single source of truth
- Audit middleware reads `app.state.engine` directly — no DI available in middleware
- Query helper functions — already accept engine as a parameter
