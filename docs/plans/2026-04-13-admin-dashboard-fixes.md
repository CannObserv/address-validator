# Admin Dashboard Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix two admin-dashboard bugs from issue #101 — (1) reclassify 429 rate-limited responses out of the error bucket, (2) replace the provider "daily quota" tile (which shows token-bucket state) with an audit-log-derived "requests today" count.

**Architecture:** All changes are in the admin read-path — three SQLAlchemy query modules, two Jinja2 templates, two view handlers. No DB migration: `audit_daily_stats` already groups by `status_code`, so the rate-limited bucket can be derived from existing rows. New `get_provider_daily_usage` helper queries `audit_log` directly for same-day rows; fail-open on DB errors like the rest of the admin layer.

**Tech Stack:** FastAPI, SQLAlchemy Core (async), Jinja2, Postgres, pytest-asyncio.

---

## File Structure

**Modify:**
- `src/address_validator/db/tables.py` — add `RATE_LIMITED_STATUS = 429`.
- `src/address_validator/routers/admin/queries/_shared.py` — add `is_error_expr()` / `is_rate_limited_expr()` helpers for `audit_log`; `ARCHIVED_RATE_LIMITED_COUNT` expression for `audit_daily_stats`.
- `src/address_validator/routers/admin/queries/dashboard.py` — use helpers in `get_dashboard_stats` and `get_sparkline_data`; add `rate_limited_24h` aggregate.
- `src/address_validator/routers/admin/queries/endpoint.py` — use helpers in `get_endpoint_stats`; subtract archived 429s from archived error count; return `rate_limited` count.
- `src/address_validator/routers/admin/queries/provider.py` — add new `get_provider_daily_usage(engine) -> dict[str, int]`.
- `src/address_validator/routers/admin/queries/__init__.py` — export `get_provider_daily_usage`.
- `src/address_validator/routers/admin/dashboard.py` — call `get_provider_daily_usage`; pass per-provider today-count into `get_quota_info()` payload.
- `src/address_validator/routers/admin/providers.py` — same: pass today-count for the specific provider into the detail template.
- `src/address_validator/templates/admin/dashboard.html` — error-rate card shows amber "rate-limited: N" stat below the red error rate; quota tile replaced with "Requests Today: N / daily limit M".
- `src/address_validator/templates/admin/providers/detail.html` — same quota tile replacement; add rate-limited display if error rate is rendered.
- `tests/unit/test_admin_queries.py` — seed 429 rows, assert new buckets; seed provider+timestamp rows, assert `get_provider_daily_usage` counts.

**Not creating new files.** All work slots into the existing admin query module layout.

---

## Task 1: Add rate-limited constant and shared expressions

**Files:**
- Modify: `src/address_validator/db/tables.py:105`
- Modify: `src/address_validator/routers/admin/queries/_shared.py`
- Test: `tests/unit/test_admin_queries.py`

- [ ] **Step 1: Write the failing test**

Add this test at the bottom of `tests/unit/test_admin_queries.py` (it will fail because the helpers don't exist yet):

```python
def test_shared_is_error_expr_excludes_429() -> None:
    """is_error_expr should treat >=400 but not 429 as errors."""
    from address_validator.db.tables import audit_log
    from address_validator.routers.admin.queries._shared import (
        is_error_expr,
        is_rate_limited_expr,
    )

    expr_err = is_error_expr(audit_log.c.status_code)
    expr_rl = is_rate_limited_expr(audit_log.c.status_code)
    sql_err = str(expr_err.compile(compile_kwargs={"literal_binds": True}))
    sql_rl = str(expr_rl.compile(compile_kwargs={"literal_binds": True}))

    assert "429" in sql_err  # the NOT 429 clause must reference 429
    assert ">= 400" in sql_err or ">=400" in sql_err
    assert "= 429" in sql_rl
```

- [ ] **Step 2: Run the failing test**

Run: `uv run pytest tests/unit/test_admin_queries.py::test_shared_is_error_expr_excludes_429 -v --no-cov`
Expected: FAIL with `ImportError: cannot import name 'is_error_expr'`.

- [ ] **Step 3: Add constant to `db/tables.py`**

Change line 105 area:

```python
# Shared query constants
ERROR_STATUS_MIN = 400
RATE_LIMITED_STATUS = 429
```

- [ ] **Step 4: Add helpers to `_shared.py`**

Append to `src/address_validator/routers/admin/queries/_shared.py`:

```python
from address_validator.db.tables import (
    ERROR_STATUS_MIN,
    RATE_LIMITED_STATUS,
)


def is_error_expr(status_code_col: ColumnElement) -> ColumnElement:
    """True for response status codes counted as errors.

    Rate-limited (429) responses are excluded — rate limiting is traffic
    control, not failure. Callers should surface 429 separately.
    """
    return sa.and_(
        status_code_col >= ERROR_STATUS_MIN,
        status_code_col != RATE_LIMITED_STATUS,
    )


def is_rate_limited_expr(status_code_col: ColumnElement) -> ColumnElement:
    """True for 429 Too Many Requests responses."""
    return status_code_col == RATE_LIMITED_STATUS
```

Also update the existing import block at the top of the file to add `ERROR_STATUS_MIN` and `RATE_LIMITED_STATUS` from `address_validator.db.tables`. The `sa.and_` call requires `sqlalchemy as sa` which is already imported.

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/unit/test_admin_queries.py::test_shared_is_error_expr_excludes_429 -v --no-cov`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/db/tables.py src/address_validator/routers/admin/queries/_shared.py tests/unit/test_admin_queries.py
git commit -m "#101 refactor: add is_error_expr / is_rate_limited_expr helpers"
```

---

## Task 2: Exclude 429 from dashboard error aggregates and add rate-limited bucket

**Files:**
- Modify: `src/address_validator/routers/admin/queries/dashboard.py:40-59` (aggregate) and `:205-221` (sparkline)
- Test: `tests/unit/test_admin_queries.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_admin_queries.py`:

```python
async def test_dashboard_stats_429_not_counted_as_error(db: AsyncEngine) -> None:
    """429 responses must not inflate the 24h error rate."""
    now = datetime.now(UTC)
    async with db.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO audit_log "
                "(timestamp, client_ip, method, endpoint, status_code, provider) "
                "VALUES "
                "(:ts,'1.1.1.1','POST','/api/v1/validate',429,'usps'),"
                "(:ts,'1.1.1.1','POST','/api/v1/validate',429,'usps'),"
                "(:ts,'1.1.1.1','POST','/api/v1/validate',200,'usps'),"
                "(:ts,'1.1.1.1','POST','/api/v1/validate',500,'usps')"
            ),
            {"ts": now},
        )

    stats = await get_dashboard_stats(db)
    # 4 API requests, 1 true error (500), 2 rate-limited (429).
    assert stats["error_rate"] == pytest.approx(25.0)
    assert stats["rate_limited_24h"] == 2


async def test_dashboard_stats_rate_limited_zero_when_no_429(db: AsyncEngine) -> None:
    """rate_limited_24h should default to 0, not None."""
    stats = await get_dashboard_stats(db)
    assert stats["rate_limited_24h"] == 0
```

- [ ] **Step 2: Run the failing tests**

Run: `uv run pytest tests/unit/test_admin_queries.py::test_dashboard_stats_429_not_counted_as_error tests/unit/test_admin_queries.py::test_dashboard_stats_rate_limited_zero_when_no_429 -v --no-cov`
Expected: FAIL — error_rate = 75% (counting 429s as errors), and `rate_limited_24h` key is absent.

- [ ] **Step 3: Update `get_dashboard_stats`**

In `src/address_validator/routers/admin/queries/dashboard.py`:

Update imports:

```python
from address_validator.db.tables import (
    audit_daily_stats,
    audit_log,
)

from ._shared import (
    _API_ENDPOINT_FILTER,
    _from_archived,
    _from_live,
    _time_boundaries,
    is_error_expr,
    is_rate_limited_expr,
)
```

(Remove `ERROR_STATUS_MIN` from the `db.tables` import — it is now used only via the helpers.)

Replace the `errors_24h` column (lines 43-49) and add a new `rate_limited_24h` column:

```python
func.count()
.filter(
    is_error_expr(audit_log.c.status_code),
    audit_log.c.timestamp >= last_24h,
    _API_ENDPOINT_FILTER,
)
.label("errors_24h"),
func.count()
.filter(
    is_rate_limited_expr(audit_log.c.status_code),
    audit_log.c.timestamp >= last_24h,
    _API_ENDPOINT_FILTER,
)
.label("rate_limited_24h"),
```

Add `"rate_limited_24h": row.rate_limited_24h` to the returned dict at line ~136.

- [ ] **Step 4: Update `get_sparkline_data` error series**

Replace the `error_rows` block (lines 205-221) in the same file:

```python
error_rows = (
    await conn.execute(
        _from_live(
            [
                day_bucket,
                func.count()
                .filter(is_error_expr(audit_log.c.status_code))
                .label("errors"),
                func.count().label("total"),
            ],
            _API_ENDPOINT_FILTER,
            audit_log.c.timestamp >= start_7d,
        )
        .group_by(sa.literal_column("bucket"))
        .order_by(sa.literal_column("bucket"))
    )
).fetchall()
```

- [ ] **Step 5: Run the new tests**

Run: `uv run pytest tests/unit/test_admin_queries.py::test_dashboard_stats_429_not_counted_as_error tests/unit/test_admin_queries.py::test_dashboard_stats_rate_limited_zero_when_no_429 -v --no-cov`
Expected: PASS.

- [ ] **Step 6: Run full admin-queries suite for regressions**

Run: `uv run pytest tests/unit/test_admin_queries.py -v --no-cov`
Expected: all tests PASS.

- [ ] **Step 7: Commit**

```bash
git add src/address_validator/routers/admin/queries/dashboard.py tests/unit/test_admin_queries.py
git commit -m "#101 fix: exclude 429 from dashboard error rate, surface rate-limited count"
```

---

## Task 3: Same treatment for endpoint stats

**Files:**
- Modify: `src/address_validator/routers/admin/queries/endpoint.py`
- Test: `tests/unit/test_admin_queries.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_admin_queries.py`:

```python
async def test_endpoint_stats_429_not_counted_as_error(db: AsyncEngine) -> None:
    """429s on /api/v1/validate must not inflate endpoint error rate."""
    now = datetime.now(UTC)
    async with db.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO audit_log "
                "(timestamp, client_ip, method, endpoint, status_code) "
                "VALUES "
                "(:ts,'1.1.1.1','POST','/api/v1/validate',429),"
                "(:ts,'1.1.1.1','POST','/api/v1/validate',429),"
                "(:ts,'1.1.1.1','POST','/api/v1/validate',200),"
                "(:ts,'1.1.1.1','POST','/api/v1/validate',500)"
            ),
            {"ts": now},
        )

    stats = await get_endpoint_stats(db, "validate")
    assert stats["error_rate"] == pytest.approx(25.0)
    assert stats["rate_limited"] == 2
```

- [ ] **Step 2: Run the failing test**

Run: `uv run pytest tests/unit/test_admin_queries.py::test_endpoint_stats_429_not_counted_as_error -v --no-cov`
Expected: FAIL.

- [ ] **Step 3: Update `get_endpoint_stats`**

In `src/address_validator/routers/admin/queries/endpoint.py`:

Update imports:

```python
from address_validator.db.tables import (
    audit_daily_stats,
    audit_log,
)

from ._shared import (
    _ARCHIVED_DATE_GUARD,
    _from_archived,
    _from_live,
    _time_boundaries,
    is_error_expr,
    is_rate_limited_expr,
)
```

(Remove `ERROR_STATUS_MIN`.)

Replace the `errors` filter in the live-stats SELECT (lines 40-42) and add a rate-limited filter:

```python
func.count()
.filter(is_error_expr(audit_log.c.status_code))
.label("errors"),
func.count()
.filter(is_rate_limited_expr(audit_log.c.status_code))
.label("rate_limited"),
```

In the archived SELECT (lines 53-65), split the archived `errors` sum so 429 rows are excluded from the error bucket and counted separately:

```python
archived = (
    await conn.execute(
        _from_archived(
            [
                func.coalesce(func.sum(audit_daily_stats.c.request_count), 0).label(
                    "total"
                ),
                func.coalesce(
                    func.sum(audit_daily_stats.c.error_count).filter(
                        audit_daily_stats.c.status_code
                        != sa.literal(429),
                    ),
                    0,
                ).label("errors"),
                func.coalesce(
                    func.sum(audit_daily_stats.c.request_count).filter(
                        audit_daily_stats.c.status_code
                        == sa.literal(429),
                    ),
                    0,
                ).label("rate_limited"),
            ],
            audit_daily_stats.c.endpoint == endpoint_path,
        )
    )
).one()
```

Update the return dict (line ~128-140):

```python
total = row.total + archived.total
errors = row.errors + archived.errors
rate_limited = row.rate_limited + archived.rate_limited
error_rate = (errors / total * 100) if total > 0 else None
return {
    "total": total,
    "last_24h": row.last_24h,
    "last_7d": row.last_7d,
    "error_rate": error_rate,
    "rate_limited": rate_limited,
    "avg_latency_ms": round(row.avg_latency) if row.avg_latency else None,
    "status_codes_all": {r.status_code: r.count for r in status_rows},
    "status_codes_24h": {r.status_code: r.cnt for r in live_status_24h_rows},
    "status_codes_7d": {r.status_code: r.cnt for r in live_status_7d_rows},
}
```

- [ ] **Step 4: Run new test**

Run: `uv run pytest tests/unit/test_admin_queries.py::test_endpoint_stats_429_not_counted_as_error -v --no-cov`
Expected: PASS.

- [ ] **Step 5: Run existing endpoint-stats tests for regressions**

Run: `uv run pytest tests/unit/test_admin_queries.py -k endpoint -v --no-cov`
Expected: all PASS (including pre-existing `test_endpoint_stats_includes_archived`, `test_get_endpoint_stats`, etc.).

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/routers/admin/queries/endpoint.py tests/unit/test_admin_queries.py
git commit -m "#101 fix: exclude 429 from endpoint error rate"
```

---

## Task 4: Template updates for rate-limited bucket

**Files:**
- Modify: `src/address_validator/templates/admin/dashboard.html:41-49`
- Modify: `src/address_validator/templates/admin/endpoints/` — check for error-rate usage and mirror

- [ ] **Step 1: Inspect endpoint template**

Run: `grep -rn "error_rate\|rate_limited" src/address_validator/templates/admin/`
Expected output: shows every template line touching error stats — note any per-endpoint partials to update.

- [ ] **Step 2: Update dashboard.html error-rate card**

Replace the error-rate card (lines 41-49 of `src/address_validator/templates/admin/dashboard.html`):

```html
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Error Rate (Last 24 Hours)</p>
        <p class="text-2xl font-bold {% if stats.get("error_rate") and stats.error_rate > 5 %}text-red-600 dark:text-red-400{% else %}text-gray-900 dark:text-gray-100{% endif %}">
            {% if stats.get("error_rate") is not none %}{{ "%.1f" | format(stats.error_rate) }}%{% else %}N/A{% endif %}
        </p>
        {% if stats.get("rate_limited_24h", 0) > 0 %}
        <p class="text-xs text-amber-600 dark:text-amber-400 mt-1">
            {{ stats.rate_limited_24h }} rate-limited (not counted as errors)
        </p>
        {% endif %}
        {% if sparkline_svgs and "error_rate" in sparkline_svgs %}
        <div class="mt-2">{{ sparkline_svgs.error_rate | safe }}</div>
        {% endif %}
    </div>
```

- [ ] **Step 3: Update endpoint detail template if it renders error_rate**

If the grep in Step 1 surfaced an endpoint detail template with an error-rate block, mirror the same amber "rate-limited: N" line using `stats.rate_limited` (new key added in Task 3). Keep the styling consistent with the dashboard change. If no such block exists, skip this step.

- [ ] **Step 4: Smoke-test the admin page**

Start the dev server on port 8001:

```bash
lsof -ti:8001 | xargs kill 2>/dev/null; uv run uvicorn address_validator.main:app --host 0.0.0.0 --port 8001 --reload &
```

Hit `https://address-validator.exe.xyz:8001/admin/` in a browser (or `curl` with the exe.dev proxy headers). Confirm the error-rate card renders with no template errors; if there's seeded 429 data, the amber line should appear. Then stop the dev server:

```bash
lsof -ti:8001 | xargs kill 2>/dev/null
```

If you cannot test the UI directly, state so explicitly in the PR description instead of claiming success.

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/templates/admin/
git commit -m "#101 fix: surface rate-limited bucket in admin templates"
```

---

## Task 5: Add `get_provider_daily_usage` query

**Files:**
- Modify: `src/address_validator/routers/admin/queries/provider.py`
- Modify: `src/address_validator/routers/admin/queries/__init__.py`
- Test: `tests/unit/test_admin_queries.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_admin_queries.py`:

```python
async def test_get_provider_daily_usage_counts_today_only(db: AsyncEngine) -> None:
    """Counts current-UTC-day rows per provider; older rows are ignored."""
    from address_validator.routers.admin.queries import get_provider_daily_usage

    today = datetime.now(UTC).replace(hour=12, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)
    async with db.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO audit_log "
                "(timestamp, client_ip, method, endpoint, status_code, provider) "
                "VALUES "
                "(:today,'1.1.1.1','POST','/api/v1/validate',200,'usps'),"
                "(:today,'1.1.1.1','POST','/api/v1/validate',200,'usps'),"
                "(:today,'1.1.1.1','POST','/api/v1/validate',200,'google'),"
                "(:yesterday,'1.1.1.1','POST','/api/v1/validate',200,'usps')"
            ),
            {"today": today, "yesterday": yesterday},
        )

    usage = await get_provider_daily_usage(db)
    assert usage == {"usps": 2, "google": 1}


async def test_get_provider_daily_usage_empty(db: AsyncEngine) -> None:
    from address_validator.routers.admin.queries import get_provider_daily_usage

    assert await get_provider_daily_usage(db) == {}
```

`timedelta` is already imported at the top of the test file — if not, add `from datetime import timedelta` to the existing datetime import line.

- [ ] **Step 2: Run the failing tests**

Run: `uv run pytest tests/unit/test_admin_queries.py::test_get_provider_daily_usage_counts_today_only tests/unit/test_admin_queries.py::test_get_provider_daily_usage_empty -v --no-cov`
Expected: FAIL — `get_provider_daily_usage` does not exist.

- [ ] **Step 3: Implement the query**

Append to `src/address_validator/routers/admin/queries/provider.py`:

```python
async def get_provider_daily_usage(engine: AsyncEngine) -> dict[str, int]:
    """Count audit_log rows for the current UTC day, grouped by provider.

    Returns a {provider_name: count} mapping. Providers with zero requests
    today are omitted. Rows with NULL provider are excluded.
    Fails open: returns {} on any exception.
    """
    tb = _time_boundaries()
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    _from_live(
                        [
                            audit_log.c.provider,
                            func.count().label("cnt"),
                        ],
                        audit_log.c.provider.isnot(None),
                        audit_log.c.timestamp >= tb["today"],
                    ).group_by(audit_log.c.provider)
                )
            ).fetchall()
    except Exception:
        return {}
    return {r.provider: r.cnt for r in rows}
```

Export it from `src/address_validator/routers/admin/queries/__init__.py` alongside the other helpers.

- [ ] **Step 4: Run new tests**

Run: `uv run pytest tests/unit/test_admin_queries.py::test_get_provider_daily_usage_counts_today_only tests/unit/test_admin_queries.py::test_get_provider_daily_usage_empty -v --no-cov`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/routers/admin/queries/ tests/unit/test_admin_queries.py
git commit -m "#101 feat: add get_provider_daily_usage admin query"
```

---

## Task 6: Wire daily usage into admin views and templates

**Files:**
- Modify: `src/address_validator/routers/admin/dashboard.py`
- Modify: `src/address_validator/routers/admin/providers.py`
- Modify: `src/address_validator/templates/admin/dashboard.html:51-70`
- Modify: `src/address_validator/templates/admin/providers/detail.html:73-92`

- [ ] **Step 1: Update dashboard view**

Replace `src/address_validator/routers/admin/dashboard.py` with:

```python
"""Admin dashboard landing page."""

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from address_validator.routers.admin._config import get_css_version, get_quota_info, templates
from address_validator.routers.admin._sparkline import SPARKLINE_CONFIG, build_sparkline_svg
from address_validator.routers.admin.deps import AdminContext, get_admin_context
from address_validator.routers.admin.queries import (
    get_dashboard_stats,
    get_provider_daily_usage,
    get_sparkline_data,
)

router = APIRouter()


@router.get("/", response_class=HTMLResponse, response_model=None)
async def admin_dashboard(ctx: AdminContext = Depends(get_admin_context)) -> Response:
    stats = await get_dashboard_stats(ctx.engine)
    sparkline_points = await get_sparkline_data(ctx.engine)
    sparkline_svgs = {
        key: build_sparkline_svg(
            sparkline_points.get(key, []),
            color=color,
            label=label,
        )
        for key, (color, label) in SPARKLINE_CONFIG.items()
    }
    daily_usage = await get_provider_daily_usage(ctx.engine)
    quota = [
        {**q, "requests_today": daily_usage.get(q["provider"], 0)}
        for q in get_quota_info(ctx.request)
    ]
    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": ctx.request,
            "user": ctx.user,
            "active_nav": "dashboard",
            "css_version": get_css_version(),
            "stats": stats,
            "quota": quota,
            "sparkline_svgs": sparkline_svgs,
        },
    )
```

- [ ] **Step 2: Update provider detail view**

In `src/address_validator/routers/admin/providers.py`, update the import:

```python
from address_validator.routers.admin.queries import (
    get_audit_rows,
    get_provider_daily_usage,
    get_provider_stats,
)
```

Replace the quota-lookup block (lines 52-56) with:

```python
    daily_usage = await get_provider_daily_usage(ctx.engine)
    quota = None
    for q in get_quota_info(ctx.request):
        if q["provider"] == name:
            quota = {**q, "requests_today": daily_usage.get(name, 0)}
            break
```

- [ ] **Step 3: Update dashboard.html quota tile**

Replace lines 51-70 of `src/address_validator/templates/admin/dashboard.html`:

```html
{% if quota %}
<h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-3">Validation Provider Usage Today</h2>
<div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-8">
    {% for q in quota %}
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">{{ q.provider | upper }} Requests Today</p>
        <div class="flex items-baseline gap-2">
            <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ q.requests_today }}</p>
            <p class="text-sm text-gray-500 dark:text-gray-400">/ {{ q.limit }} daily limit</p>
        </div>
        <div class="mt-2 w-full bg-gray-200 dark:bg-gray-600 rounded-full h-2" role="progressbar"
             aria-valuenow="{{ q.requests_today }}" aria-valuemin="0" aria-valuemax="{{ q.limit }}"
             aria-label="{{ q.provider }} daily usage">
            <div class="bg-co-purple h-2 rounded-full"
                 style="width: {{ ((q.requests_today / q.limit) * 100) | int if q.limit > 0 else 0 }}%"></div>
        </div>
    </div>
    {% endfor %}
</div>
{% endif %}
```

- [ ] **Step 4: Update providers/detail.html quota tile**

Replace the block in `src/address_validator/templates/admin/providers/detail.html` (lines 73-92, the `{% if quota %}` section). Read the surrounding context first, then swap the `quota.remaining` usages for `quota.requests_today`:

```html
    {% if quota %}
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Requests Today</p>
        <div class="flex items-baseline gap-2">
            <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ quota.requests_today }}</p>
            <p class="text-sm text-gray-500 dark:text-gray-400">/ {{ quota.limit }} daily limit</p>
        </div>
        <div class="mt-2 w-full bg-gray-200 dark:bg-gray-600 rounded-full h-2" role="progressbar"
             aria-valuenow="{{ quota.requests_today }}" aria-valuemin="0" aria-valuemax="{{ quota.limit }}"
             aria-label="{{ provider_name }} daily usage">
            <div class="bg-co-purple h-2 rounded-full"
                 style="width: {{ ((quota.requests_today / quota.limit) * 100) | int if quota.limit > 0 else 0 }}%"></div>
        </div>
    </div>
    {% endif %}
```

- [ ] **Step 5: Admin-view test (optional regression guard)**

If `tests/unit/test_admin_views.py` currently asserts the dashboard view renders, extend one of those tests to seed two `audit_log` rows with `provider='usps'` + current timestamp and assert the rendered HTML contains `"USPS Requests Today"` and the count `"2"`. If no such coverage exists, skip — the query-layer tests already guard behavior.

- [ ] **Step 6: Smoke-test the admin pages**

Start dev server:

```bash
lsof -ti:8001 | xargs kill 2>/dev/null; uv run uvicorn address_validator.main:app --host 0.0.0.0 --port 8001 --reload &
```

Visit `https://address-validator.exe.xyz:8001/admin/` and `https://address-validator.exe.xyz:8001/admin/providers/usps` and confirm the tiles render "Requests Today: N / daily limit M" with no template errors. Stop the server:

```bash
lsof -ti:8001 | xargs kill 2>/dev/null
```

If browser access is unavailable, note that in the PR body instead of claiming UI success.

- [ ] **Step 7: Commit**

```bash
git add src/address_validator/routers/admin/dashboard.py src/address_validator/routers/admin/providers.py src/address_validator/templates/admin/ tests/unit/test_admin_views.py
git commit -m "#101 fix: show audit-derived requests-today on quota tile"
```

---

## Task 7: Full verification

- [ ] **Step 1: Lint**

Run: `uv run ruff check .`
Expected: clean.

- [ ] **Step 2: Format**

Run: `uv run ruff format --check .`
Expected: clean. If it reports changes, run `uv run ruff format .` and amend the last commit.

- [ ] **Step 3: Full test suite with coverage**

Run: `uv run pytest`
Expected: all pass; line + branch coverage stays above 80%.

- [ ] **Step 4: Push branch and open PR**

Reference issue #101 in the PR body.

```bash
git push -u origin HEAD
gh pr create --title "#101 fix: admin dashboard 429 classification and quota display" --body "$(cat <<'EOF'
## Summary
- 429 rate-limited responses no longer inflate admin error rate; dashboard and per-endpoint views surface a distinct rate-limited count
- Provider "Daily Quota" tile replaced with audit-log-derived "Requests Today" count — accurate across restarts and rolling-window quirks
- Closes #101

## Design doc
`docs/plans/2026-04-13-admin-dashboard-fixes-design.md`

## Test plan
- [ ] `uv run pytest` (full suite green, coverage ≥ 80%)
- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] Manual: admin dashboard renders rate-limited line when 429 rows are present
- [ ] Manual: provider tiles show "Requests Today" and increment on new `/api/v*/validate` traffic
EOF
)"
```

---

## Notes on historical rollups

`audit_daily_stats.error_count` was populated before this fix without excluding 429s. For any calendar day archived before the fix, the archived `error_count` may include a small number of 429s. Task 3 counteracts this by filtering archived status-code rows for the rate-limited bucket and excluding status 429 from the archived error sum — so future reads are consistent regardless of when the underlying day was rolled up. No backfill is required.
