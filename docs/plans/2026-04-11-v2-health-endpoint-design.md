# Design: GET /api/v2/health endpoint

**Date:** 2026-04-11
**Issue:** #91

## Problem

v2 has no health endpoint. Callers using v2 exclusively have no version-appropriate liveness probe. v2 also introduces a dependency v1 doesn't have — the libpostal sidecar — whose health is invisible to operators.

## Decision

Add `GET /api/v2/health` with a `libpostal` field that reflects sidecar reachability without gating the HTTP status code on it.

## API contract

```
GET /api/v2/health
Authorization: none
Tag: health
```

**Response (200 — healthy):**
```json
{
  "status": "ok",
  "api_version": "2",
  "database": "ok",
  "libpostal": "ok"
}
```

**Response (200 — libpostal down, service still usable for US):**
```json
{
  "status": "ok",
  "api_version": "2",
  "database": "ok",
  "libpostal": "unavailable"
}
```

**Response (503 — database unreachable):**
```json
{
  "status": "degraded",
  "api_version": "2",
  "database": "error",
  "libpostal": "ok"
}
```

**`database` values:** `"ok"` | `"error"` | `"unconfigured"` (consistent with v1)
**`libpostal` values:** `"ok"` | `"unavailable"`
**`status`:** driven by `database` only — libpostal unavailability is degraded capability, not a service failure; does not produce HTTP 503

**Rationale for not 503-ing on libpostal down:** The lifespan already starts the service when libpostal is unreachable (logs a warning). US parsing works fine. Triggering 503 would cause load balancers to pull healthy instances when only CA parsing is impaired.

## Model changes (`models.py`)

Add `HealthResponseV2` alongside `HealthResponse`:

```python
class HealthResponseV2(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    api_version: Literal["2"] = "2"
    database: Literal["ok", "error", "unconfigured"] = "unconfigured"
    libpostal: Literal["ok", "unavailable"] = "unavailable"
```

## Router (`routers/v2/health.py`)

Mirrors `routers/v1/health.py`. Reads:
- `app.state.engine` — same DB probe (`SELECT 1`)
- `app.state.libpostal_client` — calls `health_check()` (already exists, returns bool, never raises)

`status` and HTTP 503 driven by DB result only.

## Registration (`main.py`)

Import `v2_health` and call `app.include_router(v2_health.router)` alongside the other v2 routers.

## Test strategy

Integration tests covering:
1. Healthy — 200, `status: "ok"`, `database: "ok"`, `libpostal: "ok"`
2. libpostal down — 200, `status: "ok"`, `libpostal: "unavailable"` (DB healthy)
3. DB down — 503, `status: "degraded"`, `database: "error"`
4. No engine — 200, `database: "unconfigured"` (mirrors v1 test)

Libpostal state is simulated by replacing `app.state.libpostal_client` with a mock whose `health_check()` returns False.
