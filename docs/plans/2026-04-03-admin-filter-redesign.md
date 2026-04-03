# Admin Detail Screen Filter Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split reporting and filtering on endpoint/provider detail screens — status code breakdowns belong on stats cards, pills become filter toggles with no counts.

**Architecture:** Extend the query layer first (tests against real DB), then route handlers, then templates. No new DB tables or migrations required. The provider detail already has an all-time card; only the endpoint detail needs one added. The audit list view (`/admin/audit/`) is out of scope — `status_min` stays untouched there.

**Tech Stack:** SQLAlchemy Core, FastAPI, Jinja2, HTMX, Tailwind CSS (via peer modifier for toggle pills).

---

## File Map

| File | Change |
|------|--------|
| `src/address_validator/routers/admin/queries.py` | Extend `get_audit_rows`, `get_endpoint_stats`, `get_provider_stats` |
| `src/address_validator/routers/admin/endpoints.py` | Add `status_code` list query param |
| `src/address_validator/routers/admin/providers.py` | Add `status_code` + `validation_status` list query params |
| `src/address_validator/templates/admin/endpoints/detail.html` | New all-time card, status breakdowns on count cards, filter toggles, pagination update |
| `src/address_validator/templates/admin/providers/detail.html` | Status + validation breakdowns on count cards, filter toggles, pagination update |
| `tests/unit/test_admin_queries.py` | New tests for extended query params |
| `tests/unit/test_admin_views.py` | New tests for new query params, card HTML, toggle HTML |

---

### Task 1: Extend `get_audit_rows()` — status_codes and validation_statuses filters

**Files:**
- Modify: `src/address_validator/routers/admin/queries.py`
- Test: `tests/unit/test_admin_queries.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_admin_queries.py`:

```python
@pytest.mark.asyncio
async def test_get_audit_rows_by_status_codes_single(db: AsyncEngine) -> None:
    await _seed_rows(db)
    rows, total = await get_audit_rows(db, status_codes=[400])
    assert total == 2  # parse 400 + favicon 404 -- wait, 400 exactly
    # seed has: parse/400, favicon/404 — testing exact 400 only
    assert total == 1
    assert rows[0]["status_code"] == 400


@pytest.mark.asyncio
async def test_get_audit_rows_by_status_codes_multiple(db: AsyncEngine) -> None:
    """Multiple status_codes = OR: returns rows matching any of the given codes."""
    await _seed_rows(db)
    rows, total = await get_audit_rows(db, status_codes=[400, 404])
    assert total == 2
    assert {r["status_code"] for r in rows} == {400, 404}


@pytest.mark.asyncio
async def test_get_audit_rows_by_validation_statuses_single(db: AsyncEngine) -> None:
    await _seed_rows(db)
    rows, total = await get_audit_rows(db, validation_statuses=["confirmed"])
    assert total == 2
    assert all(r["validation_status"] == "confirmed" for r in rows)


@pytest.mark.asyncio
async def test_get_audit_rows_by_validation_statuses_multiple(db: AsyncEngine) -> None:
    """Multiple validation_statuses = OR behavior."""
    # seed_rows only has 'confirmed'; add a not_confirmed row
    from datetime import UTC, datetime
    from sqlalchemy import text
    async with db.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO audit_log (timestamp, client_ip, method, endpoint,
                    status_code, provider, validation_status, cache_hit)
                VALUES (:ts, '1.2.3.4', 'POST', '/api/v1/validate',
                    200, 'usps', 'not_confirmed', false)
            """),
            {"ts": datetime.now(UTC)},
        )
    rows, total = await get_audit_rows(db, validation_statuses=["confirmed", "not_confirmed"])
    assert total == 3  # 2 confirmed from seed + 1 not_confirmed
    statuses = {r["validation_status"] for r in rows}
    assert statuses == {"confirmed", "not_confirmed"}


@pytest.mark.asyncio
async def test_get_audit_rows_status_codes_and_validation_statuses_combined(db: AsyncEngine) -> None:
    """status_codes AND validation_statuses filters combine as AND across categories."""
    await _seed_rows(db)
    # seed has 2 validate/200/usps/confirmed rows — filter to both confirmed AND 200
    rows, total = await get_audit_rows(
        db, status_codes=[200], validation_statuses=["confirmed"]
    )
    assert total == 2
    assert all(r["status_code"] == 200 and r["validation_status"] == "confirmed" for r in rows)
```

- [ ] **Step 2: Run tests to confirm they fail**

```
uv run pytest tests/unit/test_admin_queries.py::test_get_audit_rows_by_status_codes_single tests/unit/test_admin_queries.py::test_get_audit_rows_by_status_codes_multiple tests/unit/test_admin_queries.py::test_get_audit_rows_by_validation_statuses_single tests/unit/test_admin_queries.py::test_get_audit_rows_by_validation_statuses_multiple tests/unit/test_admin_queries.py::test_get_audit_rows_status_codes_and_validation_statuses_combined --no-cov -x 2>&1 | tail -20
```

Expected: TypeError or AssertionError — `get_audit_rows` doesn't accept these params yet.

- [ ] **Step 3: Extend `get_audit_rows()` in queries.py**

In `src/address_validator/routers/admin/queries.py`, update the `get_audit_rows` signature and body. The new params go after `status_min` to preserve existing callers:

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
    status_codes: list[int] | None = None,
    validation_statuses: list[str] | None = None,
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
    if status_codes:
        conditions.append(audit_log.c.status_code.in_(status_codes))
    if validation_statuses:
        conditions.append(audit_log.c.validation_status.in_(validation_statuses))
    if raw_input:
        conditions.append(query_patterns.c.raw_input.ilike(f"%{raw_input}%"))
    # ... rest of function unchanged
```

- [ ] **Step 4: Run the new tests**

```
uv run pytest tests/unit/test_admin_queries.py::test_get_audit_rows_by_status_codes_single tests/unit/test_admin_queries.py::test_get_audit_rows_by_status_codes_multiple tests/unit/test_admin_queries.py::test_get_audit_rows_by_validation_statuses_single tests/unit/test_admin_queries.py::test_get_audit_rows_by_validation_statuses_multiple tests/unit/test_admin_queries.py::test_get_audit_rows_status_codes_and_validation_statuses_combined --no-cov -x 2>&1 | tail -20
```

Expected: All 5 PASS.

- [ ] **Step 5: Run full query test file to ensure no regressions**

```
uv run pytest tests/unit/test_admin_queries.py --no-cov -x 2>&1 | tail -10
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_admin_queries.py src/address_validator/routers/admin/queries.py
git commit -m "#83 feat: extend get_audit_rows with status_codes and validation_statuses filters"
```

---

### Task 2: Extend `get_endpoint_stats()` — per-window status code breakdowns

**Files:**
- Modify: `src/address_validator/routers/admin/queries.py`
- Test: `tests/unit/test_admin_queries.py`

The current function returns `status_codes: dict[int, int]` (all-time, live + archived). Rename to `status_codes_all` and add `status_codes_24h`, `status_codes_7d` (live only).

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_admin_queries.py`:

```python
@pytest.mark.asyncio
async def test_get_endpoint_stats_has_per_window_status_codes(db: AsyncEngine) -> None:
    """get_endpoint_stats returns status_codes_24h, status_codes_7d, status_codes_all."""
    await _seed_rows(db)
    stats = await get_endpoint_stats(db, "parse")
    # parse has: 1x 200, 1x 400
    assert "status_codes_all" in stats
    assert "status_codes_24h" in stats
    assert "status_codes_7d" in stats
    assert stats["status_codes_all"][400] == 1
    assert stats["status_codes_all"][200] == 1
    assert stats["status_codes_24h"][400] == 1
    assert stats["status_codes_7d"][400] == 1


@pytest.mark.asyncio
async def test_get_endpoint_stats_status_codes_key_removed(db: AsyncEngine) -> None:
    """Old 'status_codes' key is gone — callers must use status_codes_all."""
    await _seed_rows(db)
    stats = await get_endpoint_stats(db, "parse")
    assert "status_codes" not in stats


@pytest.mark.asyncio
async def test_get_endpoint_stats_all_time_includes_archived(db: AsyncEngine) -> None:
    """status_codes_all merges live + archived; 24h/7d are live only."""
    await _seed_rows(db)
    await _seed_stats_rows(db)  # adds archived parse/400 x10
    stats = await get_endpoint_stats(db, "parse")
    assert stats["status_codes_all"][400] == 11  # 1 live + 10 archived
    assert stats["status_codes_24h"][400] == 1   # live only
    assert stats["status_codes_7d"][400] == 1    # live only
```

- [ ] **Step 2: Run tests to confirm they fail**

```
uv run pytest tests/unit/test_admin_queries.py::test_get_endpoint_stats_has_per_window_status_codes tests/unit/test_admin_queries.py::test_get_endpoint_stats_status_codes_key_removed tests/unit/test_admin_queries.py::test_get_endpoint_stats_all_time_includes_archived --no-cov -x 2>&1 | tail -20
```

Expected: FAIL — key `status_codes_all` not found.

- [ ] **Step 3: Update `get_endpoint_stats()` in queries.py**

Inside the `async with engine.connect() as conn:` block, add two new queries for per-window status code distributions (after the existing `status_rows` query):

```python
        # Per-window status code distributions (live only)
        live_status_24h_rows = (
            await conn.execute(
                select(
                    audit_log.c.status_code,
                    sa.cast(func.count(), sa.Integer).label("cnt"),
                )
                .where(
                    audit_log.c.endpoint == endpoint_path,
                    audit_log.c.timestamp >= tb["last_24h"],
                )
                .group_by(audit_log.c.status_code)
            )
        ).fetchall()

        live_status_7d_rows = (
            await conn.execute(
                select(
                    audit_log.c.status_code,
                    sa.cast(func.count(), sa.Integer).label("cnt"),
                )
                .where(
                    audit_log.c.endpoint == endpoint_path,
                    audit_log.c.timestamp >= tb["last_7d"],
                )
                .group_by(audit_log.c.status_code)
            )
        ).fetchall()
```

Then update the return dict — rename `status_codes` to `status_codes_all` and add the two new keys:

```python
    return {
        "total": total,
        "last_24h": row.last_24h,
        "last_7d": row.last_7d,
        "error_rate": error_rate,
        "avg_latency_ms": round(row.avg_latency) if row.avg_latency else None,
        "status_codes_all": {r.status_code: r.count for r in status_rows},
        "status_codes_24h": {r.status_code: r.cnt for r in live_status_24h_rows},
        "status_codes_7d": {r.status_code: r.cnt for r in live_status_7d_rows},
    }
```

- [ ] **Step 4: Run the new tests**

```
uv run pytest tests/unit/test_admin_queries.py::test_get_endpoint_stats_has_per_window_status_codes tests/unit/test_admin_queries.py::test_get_endpoint_stats_status_codes_key_removed tests/unit/test_admin_queries.py::test_get_endpoint_stats_all_time_includes_archived --no-cov -x 2>&1 | tail -10
```

Expected: All 3 PASS.

- [ ] **Step 5: Run full query test file**

```
uv run pytest tests/unit/test_admin_queries.py --no-cov -x 2>&1 | tail -10
```

Expected: All pass. (Note: `test_endpoint_stats_includes_archived` checks `stats["status_codes"][200]` — that test must be updated to use `status_codes_all` in this step.)

Update `test_endpoint_stats_includes_archived` in `tests/unit/test_admin_queries.py`:

```python
@pytest.mark.asyncio
async def test_endpoint_stats_includes_archived(db: AsyncEngine) -> None:
    """Per-endpoint all-time stats include archived data."""
    await _seed_rows(db)
    await _seed_stats_rows(db)

    stats = await get_endpoint_stats(db, "validate")
    assert stats["total"] == 52  # 2 live + 50 archived
    assert stats["last_24h"] == 2  # Only live

    # Status codes should include archived
    assert stats["status_codes_all"][200] == 52
```

- [ ] **Step 6: Commit**

```bash
git add tests/unit/test_admin_queries.py src/address_validator/routers/admin/queries.py
git commit -m "#83 feat: get_endpoint_stats returns per-window status_codes_24h/7d/all"
```

---

### Task 3: Extend `get_provider_stats()` — per-window status code and validation status breakdowns

**Files:**
- Modify: `src/address_validator/routers/admin/queries.py`
- Test: `tests/unit/test_admin_queries.py`

Current function returns `validation_statuses: dict[str, int]` (live only, all-time). Rename to `validation_statuses_all` and add `validation_statuses_24h`. Add `status_codes_24h` and `status_codes_all` (live + archived union).

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_admin_queries.py`:

```python
@pytest.mark.asyncio
async def test_get_provider_stats_has_per_window_breakdowns(db: AsyncEngine) -> None:
    """get_provider_stats returns status_codes_24h/all and validation_statuses_24h/all."""
    await _seed_rows(db)
    stats = await get_provider_stats(db, "usps")
    # seed has 2 validate/200/usps/confirmed rows
    assert "status_codes_all" in stats
    assert "status_codes_24h" in stats
    assert "validation_statuses_all" in stats
    assert "validation_statuses_24h" in stats
    assert stats["status_codes_all"][200] == 2
    assert stats["status_codes_24h"][200] == 2
    assert stats["validation_statuses_all"]["confirmed"] == 2
    assert stats["validation_statuses_24h"]["confirmed"] == 2


@pytest.mark.asyncio
async def test_get_provider_stats_validation_statuses_key_removed(db: AsyncEngine) -> None:
    """Old 'validation_statuses' key is gone — callers must use validation_statuses_all."""
    await _seed_rows(db)
    stats = await get_provider_stats(db, "usps")
    assert "validation_statuses" not in stats


@pytest.mark.asyncio
async def test_get_provider_stats_status_codes_all_includes_archived(db: AsyncEngine) -> None:
    """status_codes_all merges live + archived; status_codes_24h is live only."""
    await _seed_rows(db)
    await _seed_stats_rows(db)  # adds archived validate/200/usps x50
    stats = await get_provider_stats(db, "usps")
    assert stats["status_codes_all"][200] == 52   # 2 live + 50 archived
    assert stats["status_codes_24h"][200] == 2    # live only
    # validation_statuses only from live data (no archive column)
    assert stats["validation_statuses_all"]["confirmed"] == 2
```

- [ ] **Step 2: Run tests to confirm they fail**

```
uv run pytest tests/unit/test_admin_queries.py::test_get_provider_stats_has_per_window_breakdowns tests/unit/test_admin_queries.py::test_get_provider_stats_validation_statuses_key_removed tests/unit/test_admin_queries.py::test_get_provider_stats_status_codes_all_includes_archived --no-cov -x 2>&1 | tail -20
```

Expected: FAIL.

- [ ] **Step 3: Update `get_provider_stats()` in queries.py**

Inside the `async with engine.connect() as conn:` block, add queries for per-window status codes and validation statuses. After the existing `archived_total` query:

```python
        # Status code distributions (live only, 24h)
        live_status_24h_rows = (
            await conn.execute(
                select(
                    audit_log.c.status_code,
                    sa.cast(func.count(), sa.Integer).label("cnt"),
                )
                .where(
                    audit_log.c.provider == provider_name,
                    audit_log.c.timestamp >= tb["last_24h"],
                )
                .group_by(audit_log.c.status_code)
            )
        ).fetchall()

        # Status code distributions (live + archived, all-time)
        live_status_all = (
            select(
                audit_log.c.status_code,
                sa.cast(func.count(), sa.Integer).label("cnt"),
            )
            .where(audit_log.c.provider == provider_name)
            .group_by(audit_log.c.status_code)
        )
        archived_status_all = (
            select(
                audit_daily_stats.c.status_code,
                sa.cast(
                    func.sum(audit_daily_stats.c.request_count), sa.Integer
                ).label("cnt"),
            )
            .where(
                audit_daily_stats.c.provider == provider_name,
                _ARCHIVED_DATE_GUARD,
            )
            .group_by(audit_daily_stats.c.status_code)
        )
        combined_status = union_all(live_status_all, archived_status_all).subquery(
            "combined_status"
        )
        status_all_rows = (
            await conn.execute(
                select(
                    combined_status.c.status_code,
                    sa.cast(func.sum(combined_status.c.cnt), sa.Integer).label("count"),
                )
                .group_by(combined_status.c.status_code)
                .order_by(combined_status.c.status_code)
            )
        ).fetchall()

        # Validation status distributions (live only — no archive column)
        vs_24h_rows = (
            await conn.execute(
                _from_live(
                    [
                        audit_log.c.validation_status,
                        func.count().label("count"),
                    ],
                    audit_log.c.provider == provider_name,
                    audit_log.c.validation_status.isnot(None),
                    audit_log.c.timestamp >= tb["last_24h"],
                )
                .group_by(audit_log.c.validation_status)
                .order_by(func.count().desc())
            )
        ).fetchall()
```

The existing `status_rows` query (which becomes `validation_statuses_all`) stays as-is. Update the return dict:

```python
    cache_hit_rate = (row.cache_hits / row.cache_total * 100) if row.cache_total > 0 else None
    return {
        "total": row.total + archived_total,
        "last_24h": row.last_24h,
        "cache_hit_rate": cache_hit_rate,
        "status_codes_24h": {r.status_code: r.cnt for r in live_status_24h_rows},
        "status_codes_all": {r.status_code: r.count for r in status_all_rows},
        "validation_statuses_all": {r.validation_status: r.count for r in status_rows},
        "validation_statuses_24h": {r.validation_status: r.count for r in vs_24h_rows},
    }
```

- [ ] **Step 4: Run the new tests**

```
uv run pytest tests/unit/test_admin_queries.py::test_get_provider_stats_has_per_window_breakdowns tests/unit/test_admin_queries.py::test_get_provider_stats_validation_statuses_key_removed tests/unit/test_admin_queries.py::test_get_provider_stats_status_codes_all_includes_archived --no-cov -x 2>&1 | tail -10
```

Expected: All 3 PASS.

- [ ] **Step 5: Update the old `test_get_provider_stats` and `test_provider_stats_includes_archived` tests**

They reference `stats["validation_statuses"]` — update to `validation_statuses_all`:

```python
@pytest.mark.asyncio
async def test_get_provider_stats(db: AsyncEngine) -> None:
    await _seed_rows(db)
    stats = await get_provider_stats(db, "usps")
    assert stats["total"] == 2
    assert stats["last_24h"] == 2
    assert "confirmed" in stats["validation_statuses_all"]


@pytest.mark.asyncio
async def test_provider_stats_includes_archived(db: AsyncEngine) -> None:
    """Per-provider all-time stats include archived data."""
    await _seed_rows(db)
    await _seed_stats_rows(db)

    stats = await get_provider_stats(db, "usps")
    assert stats["total"] == 52  # 2 live + 50 archived
    assert stats["last_24h"] == 2  # Only live
```

- [ ] **Step 6: Run full query test file**

```
uv run pytest tests/unit/test_admin_queries.py --no-cov -x 2>&1 | tail -10
```

Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add tests/unit/test_admin_queries.py src/address_validator/routers/admin/queries.py
git commit -m "#83 feat: get_provider_stats returns per-window status_codes and validation_statuses"
```

---

### Task 4: Update route handlers — accept filter params and pass to query

**Files:**
- Modify: `src/address_validator/routers/admin/endpoints.py`
- Modify: `src/address_validator/routers/admin/providers.py`
- Test: `tests/unit/test_admin_views.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_admin_views.py` (these use the existing `_mock_engine` fixture which patches `get_audit_rows`):

```python
def test_endpoint_detail_accepts_status_code_param(client: TestClient, admin_headers: dict) -> None:
    """status_code query params are accepted without 422."""
    response = client.get(
        "/admin/endpoints/parse?status_code=400&status_code=500",
        headers=admin_headers,
    )
    assert response.status_code == 200


def test_provider_detail_accepts_status_code_param(client: TestClient, admin_headers: dict) -> None:
    response = client.get(
        "/admin/providers/usps?status_code=200",
        headers=admin_headers,
    )
    assert response.status_code == 200


def test_provider_detail_accepts_validation_status_param(client: TestClient, admin_headers: dict) -> None:
    response = client.get(
        "/admin/providers/usps?validation_status=confirmed&validation_status=not_confirmed",
        headers=admin_headers,
    )
    assert response.status_code == 200
```

- [ ] **Step 2: Run tests to confirm they fail**

```
uv run pytest tests/unit/test_admin_views.py::test_endpoint_detail_accepts_status_code_param tests/unit/test_admin_views.py::test_provider_detail_accepts_status_code_param tests/unit/test_admin_views.py::test_provider_detail_accepts_validation_status_param --no-cov -x 2>&1 | tail -20
```

Expected: 422 Unprocessable Entity — unknown query params.

- [ ] **Step 3: Update `endpoints.py`**

Replace the entire file content of `src/address_validator/routers/admin/endpoints.py`:

```python
"""Per-endpoint detail view."""

import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from address_validator.routers.admin._config import get_css_version, templates
from address_validator.routers.admin.deps import AdminContext, get_admin_context
from address_validator.routers.admin.queries import get_audit_rows, get_endpoint_stats

router = APIRouter(prefix="/endpoints")

_VALID_ENDPOINTS = {"parse", "standardize", "validate", "health"}
_PER_PAGE = 50


@router.get("/{name}", response_class=HTMLResponse, response_model=None)
async def endpoint_detail(
    request: Request,
    name: str,
    page: int = Query(1, ge=1),
    client_ip: str | None = Query(None),
    status_code: Annotated[list[int], Query()] = [],
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    if name not in _VALID_ENDPOINTS:
        raise HTTPException(status_code=404, detail="Unknown endpoint")

    stats = await get_endpoint_stats(ctx.engine, name)
    rows, total = await get_audit_rows(
        ctx.engine,
        page=page,
        per_page=_PER_PAGE,
        endpoint=name,
        client_ip=client_ip,
        status_codes=status_code or None,
    )

    total_pages = max(1, math.ceil(total / _PER_PAGE))
    filters = {"client_ip": client_ip, "status_codes": status_code}

    # HTMX partial — return just the rows (skip for boosted nav)
    if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"):
        return templates.TemplateResponse(
            "admin/audit/_rows.html",
            {"request": request, "rows": rows},
        )

    return templates.TemplateResponse(
        "admin/endpoints/detail.html",
        {
            "request": request,
            "user": ctx.user,
            "active_nav": f"endpoint_{name}",
            "css_version": get_css_version(),
            "endpoint_name": name,
            "stats": stats,
            "rows": rows,
            "page": page,
            "total_pages": total_pages,
            "filters": filters,
        },
    )
```

- [ ] **Step 4: Update `providers.py`**

Replace the entire file content of `src/address_validator/routers/admin/providers.py`:

```python
"""Per-provider detail view."""

import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from address_validator.routers.admin._config import get_css_version, get_quota_info, templates
from address_validator.routers.admin.deps import AdminContext, get_admin_context
from address_validator.routers.admin.queries import get_audit_rows, get_provider_stats

router = APIRouter(prefix="/providers")

_VALID_PROVIDERS = {"usps", "google"}
_PER_PAGE = 50


@router.get("/{name}", response_class=HTMLResponse, response_model=None)
async def provider_detail(
    request: Request,
    name: str,
    page: int = Query(1, ge=1),
    client_ip: str | None = Query(None),
    status_code: Annotated[list[int], Query()] = [],
    validation_status: Annotated[list[str], Query()] = [],
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    if name not in _VALID_PROVIDERS:
        raise HTTPException(status_code=404, detail="Unknown provider")

    stats = await get_provider_stats(ctx.engine, name)
    rows, total = await get_audit_rows(
        ctx.engine,
        page=page,
        per_page=_PER_PAGE,
        provider=name,
        client_ip=client_ip,
        status_codes=status_code or None,
        validation_statuses=validation_status or None,
    )

    total_pages = max(1, math.ceil(total / _PER_PAGE))
    filters = {
        "client_ip": client_ip,
        "status_codes": status_code,
        "validation_statuses": validation_status,
    }

    # Find quota for this provider
    quota = None
    for q in get_quota_info(ctx.request):
        if q["provider"] == name:
            quota = q
            break

    # HTMX partial — return just the rows (skip for boosted nav)
    if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"):
        return templates.TemplateResponse(
            "admin/audit/_rows.html",
            {"request": request, "rows": rows},
        )

    return templates.TemplateResponse(
        "admin/providers/detail.html",
        {
            "request": request,
            "user": ctx.user,
            "active_nav": f"provider_{name}",
            "css_version": get_css_version(),
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

- [ ] **Step 5: Run the new tests**

```
uv run pytest tests/unit/test_admin_views.py::test_endpoint_detail_accepts_status_code_param tests/unit/test_admin_views.py::test_provider_detail_accepts_status_code_param tests/unit/test_admin_views.py::test_provider_detail_accepts_validation_status_param --no-cov -x 2>&1 | tail -10
```

Expected: All 3 PASS.

- [ ] **Step 6: Run full views test file**

```
uv run pytest tests/unit/test_admin_views.py --no-cov -x 2>&1 | tail -10
```

Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add src/address_validator/routers/admin/endpoints.py src/address_validator/routers/admin/providers.py tests/unit/test_admin_views.py
git commit -m "#83 feat: endpoint and provider detail routes accept status_code and validation_status filter params"
```

---

### Task 5: Update endpoint detail template — all-time card, status breakdowns, filter toggles

**Files:**
- Modify: `src/address_validator/templates/admin/endpoints/detail.html`
- Test: `tests/unit/test_admin_views.py`

- [ ] **Step 1: Write failing tests for card and filter HTML**

Add to `tests/unit/test_admin_views.py`. These require `get_endpoint_stats` to return real-ish data, so update the mock in `_mock_engine`:

The mock fixture's `_empty_endpoint_stats` returns `{}`. Some template references (like `stats.status_codes_all`) will be absent — that's fine for structural tests. But to test the filter toggle rendering, we need the mock to return status codes. Add a separate test that overrides the mock:

```python
def test_endpoint_detail_has_all_time_card(client: TestClient, admin_headers: dict) -> None:
    """Endpoint detail page has a Requests (All Time) card."""
    response = client.get("/admin/endpoints/parse", headers=admin_headers)
    assert response.status_code == 200
    assert "Requests (All Time)" in response.text


def test_endpoint_detail_no_status_code_pills_section(client: TestClient, admin_headers: dict) -> None:
    """Old Status Codes pills section is gone."""
    response = client.get("/admin/endpoints/parse", headers=admin_headers)
    assert response.status_code == 200
    assert "<h2" not in response.text or "Status Codes" not in response.text


def test_endpoint_detail_has_filter_toggle_section(client: TestClient, admin_headers: dict) -> None:
    """Filter bar renders (even if empty when no status codes exist in dataset)."""
    response = client.get("/admin/endpoints/parse", headers=admin_headers)
    assert response.status_code == 200
    # The form with hx-target=#audit-rows is present
    assert 'hx-target="#audit-rows"' in response.text


def test_endpoint_detail_filter_toggles_with_status_codes(
    client: TestClient, admin_headers: dict
) -> None:
    """Filter toggles render pills for each status code in stats.status_codes_all."""
    with patch(
        "address_validator.routers.admin.endpoints.get_endpoint_stats",
        side_effect=lambda _e, _n: {
            "status_codes_all": {200: 10, 422: 2, 500: 1},
            "status_codes_24h": {200: 3},
            "status_codes_7d": {200: 7, 422: 1},
        },
    ):
        response = client.get("/admin/endpoints/parse", headers=admin_headers)
    assert response.status_code == 200
    html = response.text
    # Three toggle pills for the three distinct codes
    assert 'value="200"' in html
    assert 'value="422"' in html
    assert 'value="500"' in html
    # No counts in the pills (just the code)
    assert "200: " not in html  # old pills format gone
```

- [ ] **Step 2: Run tests to confirm they fail**

```
uv run pytest tests/unit/test_admin_views.py::test_endpoint_detail_has_all_time_card tests/unit/test_admin_views.py::test_endpoint_detail_no_status_code_pills_section tests/unit/test_admin_views.py::test_endpoint_detail_has_filter_toggle_section tests/unit/test_admin_views.py::test_endpoint_detail_filter_toggles_with_status_codes --no-cov -x 2>&1 | tail -20
```

Expected: FAIL — `Requests (All Time)` missing, pills section still present, etc.

- [ ] **Step 3: Rewrite `endpoints/detail.html`**

Replace `src/address_validator/templates/admin/endpoints/detail.html` with:

```html
{% extends "admin/base.html" %}
{% block title %}/{{ endpoint_name }} — Endpoint{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-6">/api/v1/{{ endpoint_name }}</h1>

{# ── Stats cards ─────────────────────────────────────────────────── #}
<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4 mb-8">
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Requests (Last 24 Hours)</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ stats.last_24h | default(0) }}</p>
        {% if stats.get("status_codes_24h") %}
        <p class="text-xs mt-1 text-gray-500 dark:text-gray-400">
            {% for code, cnt in stats.status_codes_24h.items() if code != 200 %}
            <span class="{% if code < 400 %}text-green-700 dark:text-green-400{% elif code < 500 %}text-yellow-600 dark:text-yellow-400{% else %}text-red-600 dark:text-red-400{% endif %}">{{ code }}: {{ cnt }}</span>{% if not loop.last %} · {% endif %}
            {% endfor %}
        </p>
        {% endif %}
    </div>
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Requests (Last 7 Days)</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ stats.last_7d | default(0) }}</p>
        {% if stats.get("status_codes_7d") %}
        <p class="text-xs mt-1 text-gray-500 dark:text-gray-400">
            {% for code, cnt in stats.status_codes_7d.items() if code != 200 %}
            <span class="{% if code < 400 %}text-green-700 dark:text-green-400{% elif code < 500 %}text-yellow-600 dark:text-yellow-400{% else %}text-red-600 dark:text-red-400{% endif %}">{{ code }}: {{ cnt }}</span>{% if not loop.last %} · {% endif %}
            {% endfor %}
        </p>
        {% endif %}
    </div>
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Requests (All Time)</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ stats.total | default(0) }}</p>
        {% if stats.get("status_codes_all") %}
        <p class="text-xs mt-1 text-gray-500 dark:text-gray-400">
            {% for code, cnt in stats.status_codes_all.items() if code != 200 %}
            <span class="{% if code < 400 %}text-green-700 dark:text-green-400{% elif code < 500 %}text-yellow-600 dark:text-yellow-400{% else %}text-red-600 dark:text-red-400{% endif %}">{{ code }}: {{ cnt }}</span>{% if not loop.last %} · {% endif %}
            {% endfor %}
        </p>
        {% endif %}
    </div>
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Avg Latency</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">
            {% if stats.get("avg_latency_ms") is not none %}{{ stats.avg_latency_ms }}ms{% else %}N/A{% endif %}
        </p>
    </div>
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Error Rate (All Time)</p>
        <p class="text-2xl font-bold {% if stats.get("error_rate") and stats.error_rate > 5 %}text-red-600 dark:text-red-400{% else %}text-gray-900 dark:text-gray-100{% endif %}">
            {% if stats.get("error_rate") is not none %}{{ "%.1f" | format(stats.error_rate) }}%{% else %}N/A{% endif %}
        </p>
    </div>
</div>

{# ── Recent Requests + filters ────────────────────────────────────── #}
<h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-3">Recent Requests</h2>

<form class="flex flex-col gap-3 mb-4"
      hx-get="/admin/endpoints/{{ endpoint_name }}"
      hx-target="#audit-rows"
      hx-push-url="true">

    {# Status code toggles — only rendered when codes exist in dataset #}
    {% if stats.get("status_codes_all") %}
    <div class="flex flex-wrap items-center gap-2">
        <span class="text-xs text-gray-500 dark:text-gray-400 shrink-0">Status:</span>
        {% for code in stats.status_codes_all %}
        <label class="cursor-pointer">
            <input type="checkbox" name="status_code" value="{{ code }}"
                   {% if code in filters.status_codes %}checked{% endif %}
                   class="sr-only peer">
            <span class="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium
                peer-checked:ring-2 peer-checked:ring-offset-1 dark:peer-checked:ring-offset-gray-800
                {% if code < 400 %}bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300 peer-checked:ring-green-600 dark:peer-checked:ring-green-400
                {% elif code < 500 %}bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300 peer-checked:ring-yellow-600 dark:peer-checked:ring-yellow-400
                {% else %}bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300 peer-checked:ring-red-600 dark:peer-checked:ring-red-400{% endif %}">
                {{ code }}
            </span>
        </label>
        {% endfor %}
    </div>
    {% endif %}

    <div class="flex flex-wrap gap-3 items-end">
        <div>
            <label for="client_ip" class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Client IP</label>
            <input type="text" name="client_ip" id="client_ip" value="{{ filters.client_ip or '' }}"
                   class="border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-2 py-1 text-sm w-40 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100"
                   placeholder="e.g. 10.0.0.1">
        </div>
        <button type="submit"
                class="bg-co-purple text-white px-4 py-1 rounded text-sm font-medium hover:bg-co-purple-700 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100 focus:ring-offset-1 dark:ring-offset-gray-800 min-h-[32px]">
            Filter
        </button>
        <a href="/admin/endpoints/{{ endpoint_name }}"
           hx-target="body"
           class="{{ clear_btn_cls }}">Clear</a>
    </div>
</form>

<div class="overflow-x-auto bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
    <table class="w-full text-left">
        <thead class="sticky top-0 bg-gray-50 dark:bg-gray-700 border-b border-gray-200 dark:border-gray-600 text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wide">
            {% include "admin/audit/_thead.html" %}
        </thead>
        <tbody id="audit-rows">
            {% include "admin/audit/_rows.html" %}
        </tbody>
    </table>
</div>

{% if total_pages > 1 %}
<nav class="flex items-center gap-2 mt-4 text-sm" aria-label="Pagination">
    {% if page > 1 %}
    <a href="?page={{ page - 1 }}{% if filters.client_ip %}&client_ip={{ filters.client_ip }}{% endif %}{% for code in filters.status_codes %}&status_code={{ code }}{% endfor %}"
       class="px-3 py-1 rounded border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100">&laquo; Prev</a>
    {% endif %}
    <span class="text-gray-500 dark:text-gray-400">Page {{ page }} of {{ total_pages }}</span>
    {% if page < total_pages %}
    <a href="?page={{ page + 1 }}{% if filters.client_ip %}&client_ip={{ filters.client_ip }}{% endif %}{% for code in filters.status_codes %}&status_code={{ code }}{% endfor %}"
       class="px-3 py-1 rounded border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100">Next &raquo;</a>
    {% endif %}
</nav>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Run the new tests**

```
uv run pytest tests/unit/test_admin_views.py::test_endpoint_detail_has_all_time_card tests/unit/test_admin_views.py::test_endpoint_detail_no_status_code_pills_section tests/unit/test_admin_views.py::test_endpoint_detail_has_filter_toggle_section tests/unit/test_admin_views.py::test_endpoint_detail_filter_toggles_with_status_codes --no-cov -x 2>&1 | tail -10
```

Expected: All 4 PASS.

- [ ] **Step 5: Run full views test file**

```
uv run pytest tests/unit/test_admin_views.py --no-cov -x 2>&1 | tail -10
```

Expected: All pass. (The `test_endpoint_clear_link_overrides_hx_target` test should still pass — `hx-target="body"` is still on the Clear link.)

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/templates/admin/endpoints/detail.html tests/unit/test_admin_views.py
git commit -m "#83 feat: endpoint detail — all-time card, status code breakdowns, filter toggle pills"
```

---

### Task 6: Update provider detail template — status breakdowns, filter toggles

**Files:**
- Modify: `src/address_validator/templates/admin/providers/detail.html`
- Test: `tests/unit/test_admin_views.py`

The provider already has an all-time card (`stats.total`). No new card — just add status code + validation status breakdowns and replace pills section with filter toggles.

- [ ] **Step 1: Write failing tests**

Add to `tests/unit/test_admin_views.py`:

```python
def test_provider_detail_no_validation_statuses_pills_section(
    client: TestClient, admin_headers: dict
) -> None:
    """Old Validation Statuses pills section is gone."""
    response = client.get("/admin/providers/usps", headers=admin_headers)
    assert response.status_code == 200
    assert "Validation Statuses" not in response.text


def test_provider_detail_filter_toggles_with_codes_and_statuses(
    client: TestClient, admin_headers: dict
) -> None:
    """Provider detail renders status code and validation status toggle pills."""
    with patch(
        "address_validator.routers.admin.providers.get_provider_stats",
        side_effect=lambda _e, _n: {
            "total": 100,
            "last_24h": 10,
            "cache_hit_rate": 80.0,
            "status_codes_all": {200: 90, 422: 5, 500: 5},
            "status_codes_24h": {200: 10},
            "validation_statuses_all": {"confirmed": 85, "not_confirmed": 5},
            "validation_statuses_24h": {"confirmed": 9, "not_confirmed": 1},
        },
    ):
        response = client.get("/admin/providers/usps", headers=admin_headers)
    assert response.status_code == 200
    html = response.text
    assert 'value="200"' in html
    assert 'value="422"' in html
    assert 'value="confirmed"' in html
    assert 'value="not_confirmed"' in html
    # No old pill format with counts
    assert "confirmed: " not in html
```

- [ ] **Step 2: Run tests to confirm they fail**

```
uv run pytest tests/unit/test_admin_views.py::test_provider_detail_no_validation_statuses_pills_section tests/unit/test_admin_views.py::test_provider_detail_filter_toggles_with_codes_and_statuses --no-cov -x 2>&1 | tail -20
```

Expected: FAIL.

- [ ] **Step 3: Rewrite `providers/detail.html`**

Replace `src/address_validator/templates/admin/providers/detail.html` with:

```html
{% extends "admin/base.html" %}
{% block title %}{{ provider_name | upper }} — Provider{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-6">{{ provider_name | upper }} Provider</h1>

{# ── Stats cards ─────────────────────────────────────────────────── #}
<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Requests (Last 24 Hours)</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ stats.last_24h | default(0) }}</p>
        {% if stats.get("status_codes_24h") %}
        <p class="text-xs mt-1 text-gray-500 dark:text-gray-400">
            {% for code, cnt in stats.status_codes_24h.items() if code != 200 %}
            <span class="{% if code < 400 %}text-green-700 dark:text-green-400{% elif code < 500 %}text-yellow-600 dark:text-yellow-400{% else %}text-red-600 dark:text-red-400{% endif %}">{{ code }}: {{ cnt }}</span>{% if not loop.last %} · {% endif %}
            {% endfor %}
        </p>
        {% endif %}
        {% if stats.get("validation_statuses_24h") %}
        <p class="text-xs mt-1 text-gray-500 dark:text-gray-400">
            {% for vs, cnt in stats.validation_statuses_24h.items() %}
            <span class="{% if vs in ('confirmed', 'confirmed_missing_secondary') %}text-green-700 dark:text-green-400{% elif vs == 'confirmed_bad_secondary' %}text-yellow-600 dark:text-yellow-400{% elif vs == 'not_confirmed' %}text-red-600 dark:text-red-400{% endif %}">{{ vs }}: {{ cnt }}</span>{% if not loop.last %} · {% endif %}
            {% endfor %}
        </p>
        {% endif %}
    </div>
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Requests (All Time)</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ stats.total | default(0) }}</p>
        {% if stats.get("status_codes_all") %}
        <p class="text-xs mt-1 text-gray-500 dark:text-gray-400">
            {% for code, cnt in stats.status_codes_all.items() if code != 200 %}
            <span class="{% if code < 400 %}text-green-700 dark:text-green-400{% elif code < 500 %}text-yellow-600 dark:text-yellow-400{% else %}text-red-600 dark:text-red-400{% endif %}">{{ code }}: {{ cnt }}</span>{% if not loop.last %} · {% endif %}
            {% endfor %}
        </p>
        {% endif %}
        {% if stats.get("validation_statuses_all") %}
        <p class="text-xs mt-1 text-gray-500 dark:text-gray-400">
            {% for vs, cnt in stats.validation_statuses_all.items() %}
            <span class="{% if vs in ('confirmed', 'confirmed_missing_secondary') %}text-green-700 dark:text-green-400{% elif vs == 'confirmed_bad_secondary' %}text-yellow-600 dark:text-yellow-400{% elif vs == 'not_confirmed' %}text-red-600 dark:text-red-400{% endif %}">{{ vs }}: {{ cnt }}</span>{% if not loop.last %} · {% endif %}
            {% endfor %}
        </p>
        {% endif %}
    </div>
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Cache Hit Rate (Last 7 Days)</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">
            {% if stats.get("cache_hit_rate") is not none %}{{ "%.1f" | format(stats.cache_hit_rate) }}%{% else %}N/A{% endif %}
        </p>
    </div>
    {% if quota %}
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Daily Quota</p>
        <div class="flex items-baseline gap-2">
            <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ quota.remaining }}</p>
            <p class="text-sm text-gray-500 dark:text-gray-400">/ {{ quota.limit }}</p>
        </div>
        <div class="mt-2 w-full bg-gray-200 dark:bg-gray-600 rounded-full h-2" role="progressbar"
             aria-valuenow="{{ quota.remaining }}" aria-valuemin="0" aria-valuemax="{{ quota.limit }}"
             aria-label="{{ provider_name }} quota usage">
            <div class="bg-co-purple h-2 rounded-full"
                 style="width: {{ ((quota.remaining / quota.limit) * 100) | int if quota.limit > 0 else 0 }}%"></div>
        </div>
    </div>
    {% else %}
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Daily Quota</p>
        <p class="text-2xl font-bold text-gray-400 dark:text-gray-500">N/A</p>
    </div>
    {% endif %}
</div>

{# ── Recent Requests + filters ────────────────────────────────────── #}
<h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-3">Recent Requests</h2>

<form class="flex flex-col gap-3 mb-4"
      hx-get="/admin/providers/{{ provider_name }}"
      hx-target="#audit-rows"
      hx-push-url="true">

    {# Status code toggles #}
    {% if stats.get("status_codes_all") %}
    <div class="flex flex-wrap items-center gap-2">
        <span class="text-xs text-gray-500 dark:text-gray-400 shrink-0">Status:</span>
        {% for code in stats.status_codes_all %}
        <label class="cursor-pointer">
            <input type="checkbox" name="status_code" value="{{ code }}"
                   {% if code in filters.status_codes %}checked{% endif %}
                   class="sr-only peer">
            <span class="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium
                peer-checked:ring-2 peer-checked:ring-offset-1 dark:peer-checked:ring-offset-gray-800
                {% if code < 400 %}bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300 peer-checked:ring-green-600 dark:peer-checked:ring-green-400
                {% elif code < 500 %}bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300 peer-checked:ring-yellow-600 dark:peer-checked:ring-yellow-400
                {% else %}bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300 peer-checked:ring-red-600 dark:peer-checked:ring-red-400{% endif %}">
                {{ code }}
            </span>
        </label>
        {% endfor %}
    </div>
    {% endif %}

    {# Validation status toggles #}
    {% if stats.get("validation_statuses_all") %}
    <div class="flex flex-wrap items-center gap-2">
        <span class="text-xs text-gray-500 dark:text-gray-400 shrink-0">Result:</span>
        {% for vs in stats.validation_statuses_all %}
        <label class="cursor-pointer">
            <input type="checkbox" name="validation_status" value="{{ vs }}"
                   {% if vs in filters.validation_statuses %}checked{% endif %}
                   class="sr-only peer">
            <span class="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium
                peer-checked:ring-2 peer-checked:ring-offset-1 dark:peer-checked:ring-offset-gray-800
                {% if vs in ('confirmed', 'confirmed_missing_secondary') %}bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300 peer-checked:ring-green-600 dark:peer-checked:ring-green-400
                {% elif vs == 'confirmed_bad_secondary' %}bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300 peer-checked:ring-yellow-600 dark:peer-checked:ring-yellow-400
                {% elif vs == 'not_confirmed' %}bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300 peer-checked:ring-red-600 dark:peer-checked:ring-red-400
                {% else %}bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300 peer-checked:ring-gray-500{% endif %}">
                {{ vs }}
            </span>
        </label>
        {% endfor %}
    </div>
    {% endif %}

    <div class="flex flex-wrap gap-3 items-end">
        <div>
            <label for="client_ip" class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Client IP</label>
            <input type="text" name="client_ip" id="client_ip" value="{{ filters.client_ip or '' }}"
                   class="border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 rounded px-2 py-1 text-sm w-40 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100"
                   placeholder="e.g. 10.0.0.1">
        </div>
        <button type="submit"
                class="bg-co-purple text-white px-4 py-1 rounded text-sm font-medium hover:bg-co-purple-700 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100 focus:ring-offset-1 dark:ring-offset-gray-800 min-h-[32px]">
            Filter
        </button>
        <a href="/admin/providers/{{ provider_name }}"
           hx-target="body"
           class="{{ clear_btn_cls }}">Clear</a>
    </div>
</form>

<div class="overflow-x-auto bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700">
    <table class="w-full text-left">
        <thead class="sticky top-0 bg-gray-50 dark:bg-gray-700 border-b border-gray-200 dark:border-gray-600 text-xs text-gray-500 dark:text-gray-400 uppercase tracking-wide">
            {% include "admin/audit/_thead.html" %}
        </thead>
        <tbody id="audit-rows">
            {% include "admin/audit/_rows.html" %}
        </tbody>
    </table>
</div>

{% if total_pages > 1 %}
<nav class="flex items-center gap-2 mt-4 text-sm" aria-label="Pagination">
    {% if page > 1 %}
    <a href="?page={{ page - 1 }}{% if filters.client_ip %}&client_ip={{ filters.client_ip }}{% endif %}{% for code in filters.status_codes %}&status_code={{ code }}{% endfor %}{% for vs in filters.validation_statuses %}&validation_status={{ vs }}{% endfor %}"
       class="px-3 py-1 rounded border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100">&laquo; Prev</a>
    {% endif %}
    <span class="text-gray-500 dark:text-gray-400">Page {{ page }} of {{ total_pages }}</span>
    {% if page < total_pages %}
    <a href="?page={{ page + 1 }}{% if filters.client_ip %}&client_ip={{ filters.client_ip }}{% endif %}{% for code in filters.status_codes %}&status_code={{ code }}{% endfor %}{% for vs in filters.validation_statuses %}&validation_status={{ vs }}{% endfor %}"
       class="px-3 py-1 rounded border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 focus:outline-none focus:ring-2 focus:ring-co-purple-700 dark:focus:ring-co-purple-100">Next &raquo;</a>
    {% endif %}
</nav>
{% endif %}
{% endblock %}
```

- [ ] **Step 4: Run the new tests**

```
uv run pytest tests/unit/test_admin_views.py::test_provider_detail_no_validation_statuses_pills_section tests/unit/test_admin_views.py::test_provider_detail_filter_toggles_with_codes_and_statuses --no-cov -x 2>&1 | tail -10
```

Expected: Both PASS.

- [ ] **Step 5: Run full views test file**

```
uv run pytest tests/unit/test_admin_views.py --no-cov -x 2>&1 | tail -10
```

Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/templates/admin/providers/detail.html tests/unit/test_admin_views.py
git commit -m "#83 feat: provider detail — status code and validation status breakdowns on cards, filter toggle pills"
```

---

### Task 7: Full test suite and Tailwind build

- [ ] **Step 1: Run full test suite with coverage**

```
uv run pytest 2>&1 | tail -20
```

Expected: All pass, coverage ≥ 80%.

- [ ] **Step 2: Lint**

```
uv run ruff check . 2>&1 | tail -10
```

Expected: No errors. If any, fix and re-run.

- [ ] **Step 3: Verify Tailwind CSS picks up the new `peer-checked` classes**

The `peer-checked:ring-*` and `peer` classes must be present in the built CSS. Run the Tailwind build:

```
npx tailwindcss -i src/address_validator/static/admin/css/input.css -o src/address_validator/static/admin/css/tailwind.css --minify 2>&1 | tail -5
```

The pre-commit hook runs this automatically; but confirm the built CSS file was updated:

```bash
git diff --stat src/address_validator/static/admin/css/tailwind.css
```

Expected: Shows changes (new `peer` and `peer-checked` utilities added).

- [ ] **Step 4: Commit the built CSS**

```bash
git add src/address_validator/static/admin/css/tailwind.css
git commit -m "#83 chore: rebuild Tailwind CSS — add peer-checked toggle pill utilities"
```

---

## Self-Review Checklist

- **Spec coverage:**
  - ✅ All-time card added to endpoint detail (Task 5)
  - ✅ Non-200 status code breakdowns on each count card — endpoint (Task 5), provider (Task 6)
  - ✅ Validation status breakdowns on provider cards (Task 6)
  - ✅ Status pills replaced with toggle filters on endpoint (Task 5)
  - ✅ Status + validation status toggles on provider (Task 6)
  - ✅ Filter toggles use semantic colors (green/yellow/red) with no counts
  - ✅ Active state via `peer-checked:ring-*`
  - ✅ Toggles populated from `status_codes_all` / `validation_statuses_all` keys (dynamic)
  - ✅ Multi-value OR within category, AND across categories (Task 1 + Task 4)
  - ✅ Pagination preserves filter params (Tasks 5, 6)
  - ✅ `get_audit_rows` unchanged for `status_min` — audit list view unaffected
  - ✅ `hx-target="body"` on Clear links preserved in both templates

- **Key rename:** `stats.status_codes` → `stats.status_codes_all` in endpoint stats. The old key reference in `test_endpoint_stats_includes_archived` is updated in Task 2 Step 5.

- **`validation_statuses` rename:** `stats.validation_statuses` → `stats.validation_statuses_all` in provider stats. Old key reference in `test_get_provider_stats` and `test_provider_stats_includes_archived` updated in Task 3 Step 5.
