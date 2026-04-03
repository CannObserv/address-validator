# Admin Detail Screen Filter Redesign

**Date:** 2026-04-03

## Summary

Split reporting and filtering responsibilities on endpoint and provider detail screens. Status code / validation status pills currently serve double duty (aggregate reporting + implied drill-down). Redesign: absorb counts into stats cards (reporting), replace pills with toggleable filter controls (interaction).

## Current State

- Endpoint detail: status code pills (`200: 1234`, `404: 5`) rendered as colored badges
- Provider detail: validation status pills (`confirmed: 5432`, etc.) same treatment
- Recent Requests table filtered only by Client IP
- `get_audit_rows()` already supports `status_min` — not exposed on detail screens
- Audit list view (`/admin/audit/`) has richer filtering (client_ip, endpoint, status_min, raw_input)

## Design

### 1. Stats Cards — absorb reporting from pills

**Endpoint detail screen:**

| Card | Current | Change |
|------|---------|--------|
| Last 24h | Request count | Add non-200 status code breakdown |
| Last 7d | Request count | Add non-200 status code breakdown |
| Avg Latency | Milliseconds | No change |
| Error Rate | Percentage (24h) | No change |
| **Requests (All Time)** | *New* | Total count + non-200 breakdown |

**Provider detail screen:**

| Card | Current | Change |
|------|---------|--------|
| Last 24h | Request count | Add non-200 status code breakdown + validation status breakdown |
| Total | Request count | Add non-200 status code breakdown + validation status breakdown |
| Cache Hit Rate | Percentage | No change |
| **Requests (All Time)** | *New* | Total count + non-200 breakdown + validation status breakdown |

**Status code breakdown format:** Compact inline text below the main count. Only non-200 codes shown (200 count implied by total). Semantic colors on code numbers (green 2xx–3xx, yellow 4xx, red 5xx). Example:

```
1,247
422: 3 · 429: 1 · 500: 2
```

If all requests are 200, no breakdown line shown.

**Validation status breakdown format** (provider only): Same compact inline treatment.

```
confirmed: 412 · not_confirmed: 8
```

### 2. Filter Bar — replace pills with toggleable controls

Remove the Status Codes pills section (endpoint) and Validation Statuses pills section (provider). Replace with a filter bar between stats cards and the Recent Requests table.

**Endpoint detail layout:**

```
[200] [400] [422] [500]                          ← status code toggles
[Client IP ________]  [Filter] [Clear]
```

**Provider detail layout:**

```
[200] [400] [422] [500]                          ← status code toggles
[confirmed] [not_confirmed] [confirmed_bad_...]   ← validation status toggles
[Client IP ________]  [Filter] [Clear]
```

**Toggle behavior:**

- Pills keep semantic colors (green/yellow/red/gray) from current design
- Active state: `ring-2 ring-offset-1` to indicate selection
- No counts displayed — purely filter controls
- Multiple toggles active simultaneously = OR within category (e.g. selecting `422` + `500` shows both)
- Cross-category = AND (e.g. status `500` + validation `not_confirmed`)
- Populated dynamically — query distinct values from dataset, render only codes/statuses that exist
- All filters (toggles + client IP) submitted together via existing HTMX form pattern
- Filter + Clear buttons apply/reset all filters
- Pagination preserves active filter state

**Toggle implementation:** Hidden checkbox inputs with `<label>` styled as pills. Form serializes checked values as `status_code=422&status_code=500&validation_status=confirmed`. Pure HTML/CSS — no JavaScript needed.

### 3. Query Layer Changes

**`get_audit_rows()` — extend filter parameters:**

```python
async def get_audit_rows(
    engine,
    page: int = 1,
    endpoint: str | None = None,
    provider: str | None = None,
    client_ip: str | None = None,
    status_codes: list[int] | None = None,       # NEW — replaces status_min
    validation_status: str | None = None,          # NEW
    raw_input: str | None = None,
) -> tuple[list[dict], int]:
```

- `status_codes`: `WHERE status_code IN (...)` when non-empty
- `validation_status`: `WHERE validation_status = ...`
- Keep `status_min` for backward compat with audit list view, or migrate it to `status_codes` too

**`get_endpoint_stats()` / `get_provider_stats()` — extend for card breakdowns:**

- Return `status_codes_24h: dict[int, int]`, `status_codes_7d: dict[int, int]`, `status_codes_all: dict[int, int]`
- Provider stats additionally return `validation_statuses_24h`, `validation_statuses_7d`, `validation_statuses_all`
- Return `total_all_time: int` for the new all-time card

**New query — distinct filter values:**

```python
async def get_distinct_status_codes(engine, endpoint: str | None, provider: str | None) -> list[int]
async def get_distinct_validation_statuses(engine, provider: str) -> list[str]
```

Alternatively, derive from the all-time status code dict keys (already queried for cards) — avoids extra query.

### 4. Template Changes

**Remove:**
- Status Codes pills block in `endpoints/detail.html`
- Validation Statuses pills block in `providers/detail.html`

**Add to both detail templates:**
- All-time card in stats card row
- Status code breakdown text on each count card
- Filter bar with toggle pills + existing client IP input

**Shared partial candidate:** Toggle pill rendering could be a `_filter_toggles.html` partial if the pattern is identical across both screens.

### 5. What Stays the Same

- `_thead.html` and `_rows.html` table partials — no changes
- HTMX partial response pattern (HX-Request check)
- Client IP filter input and behavior
- Pagination with filter preservation
- Card visual design (just adding content within)

## Scope Boundary

**In scope:**
- Card redesign with status breakdowns
- All-time card
- Toggle filter controls
- Query layer extensions

**Out of scope:**
- Backoff/retry correlation tracking
- Changes to audit list view (`/admin/audit/`)
- New database indices (evaluate after implementation if needed)
- Changes to audit middleware or data collection
