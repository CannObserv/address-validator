# Health endpoint DB connectivity check

**Issue:** #60
**Date:** 2026-03-22

## Problem

`GET /api/v1/health` returns `{"status": "ok"}` unconditionally. When the database is down, load balancers see the service as healthy while audit writes and cache lookups silently fail-open.

## Decision

Enhance the existing `/api/v1/health` response with a `database` field. No new endpoint.

## Response shape

```json
{ "status": "ok", "api_version": "1", "database": "ok" }
```

| `database` value | Meaning |
|---|---|
| `"ok"` | `SELECT 1` succeeded |
| `"error"` | Engine configured but query failed |
| `"unconfigured"` | No DSN / `app.state.engine` is `None` |

`status` widens to `"ok" | "degraded"`. Returns `"degraded"` when `database == "error"`.
HTTP status: **503** when degraded, **200** otherwise.

## Changes

| File | Change |
|---|---|
| `models.py` | Widen `status` literal; add `database` field |
| `routers/v1/health.py` | Make handler `async`; inject `Request` + `Response`; run `SELECT 1` |
| `tests/integration/test_health.py` | Update assertions; add degraded case via mock |
