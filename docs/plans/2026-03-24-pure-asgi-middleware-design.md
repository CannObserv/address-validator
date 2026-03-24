# Pure ASGI Middleware Conversion

**Date:** 2026-03-24
**Status:** Approved

## Problem

All three HTTP middlewares use `app.middleware("http")`, which delegates to
Starlette's `BaseHTTPMiddleware`. That class runs `call_next()` in a **child
asyncio task**. Python's `contextvars` propagate parent→child but not
child→parent.

Result: `set_audit_context()` calls inside the endpoint handler (child task)
are invisible to `audit_middleware` after `call_next` returns (parent task).
**117,520 validate audit rows have NULL provider/cache_hit/validation_status.**

## Scope

Convert all three `app.middleware("http")` registrations to pure ASGI classes:

| Current | New |
|---|---|
| `request_id_middleware` function | `RequestIdMiddleware` class |
| `audit_middleware` function | `AuditMiddleware` class |
| `add_api_version_header` function (inline in main.py) | `ApiVersionHeaderMiddleware` class |

## Approach

Each middleware becomes an ASGI class:

```python
class SomeMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # middleware logic — same asyncio task as endpoint
        await self.app(scope, receive, send)
```

No child task — ContextVars propagate naturally.

### Response interception

Pure ASGI has no `Response` object. To read status codes or append headers,
wrap the `send` callable to intercept `http.response.start` messages.

### Middleware ordering

`app.add_middleware` is LIFO: last-registered wraps outermost, so it
executes first.  Execution order (outermost → innermost):

1. `ApiVersionHeaderMiddleware` (outermost — executes first)
2. `RequestIdMiddleware` (sets ContextVar before inner middlewares run)
3. `AuditMiddleware` (reads ContextVars after endpoint returns)
4. `CORSMiddleware` (innermost)

## File changes

| File | Change |
|---|---|
| `middleware/request_id.py` | Replace function → `RequestIdMiddleware` class; `get_request_id()` + ContextVar unchanged |
| `middleware/audit.py` | Replace function → `AuditMiddleware` class; helper functions stay |
| `middleware/api_version.py` | **New file** — `ApiVersionHeaderMiddleware` |
| `main.py` | Replace `app.middleware("http")(fn)` → `app.add_middleware(Class)` |
| `tests/unit/test_audit_middleware.py` | Update imports; add ContextVar propagation regression test |
| `tests/unit/test_request_id.py` | Update imports if needed |

## Regression test

Dedicated test proving ContextVars set inside an endpoint handler are visible
in the audit row written by the middleware — the exact bug being fixed.

## What stays the same

- ContextVar API in `services/audit.py`
- Helper functions: `_should_audit`, `_get_client_ip`, `_error_detail_from_status`
- Public interfaces: `get_request_id()`, audit ContextVar getters
- Middleware ordering semantics
