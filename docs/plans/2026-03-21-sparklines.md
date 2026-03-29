# Sparkline Graphs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add inline SVG sparklines to all 5 admin dashboard stat boxes showing recent trends.

**Architecture:** New `_sparkline.py` module builds SVG strings from point arrays. New `get_sparkline_data()` query fetches time-bucketed counts from `audit_log`. Dashboard handler wires data → SVG builder → template context. Zero JS dependencies.

**Note:** The design spec defines `get_sparkline_data` returning `dict[str, list[dict]]` with `{"t": datetime, "v": float}` entries. This plan intentionally simplifies to `dict[str, list[float]]` — the SVG builder only needs values (x-axis is implicit from bucket order), so timestamps add no value. The design doc should be updated to match.

**Tech Stack:** Python, SQLAlchemy (raw SQL), inline SVG, Jinja2, pytest

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/address_validator/routers/admin/_sparkline.py` | SVG builder: points → inline `<svg>` string |
| Create | `tests/unit/test_sparkline.py` | Unit tests for SVG builder |
| Modify | `src/address_validator/routers/admin/queries.py` | Add `get_sparkline_data()` query function |
| Modify | `tests/unit/test_admin_queries.py` | Tests for `get_sparkline_data()` |
| Modify | `src/address_validator/routers/admin/dashboard.py` | Wire sparkline data into template context |
| Modify | `src/address_validator/templates/admin/dashboard.html` | Render sparkline SVGs in stat cards |
| Modify | `tests/unit/test_admin_views.py` | Verify sparkline SVGs appear in dashboard HTML |

---

### Task 1: SVG Builder — `_sparkline.py`

**Files:**
- Create: `src/address_validator/routers/admin/_sparkline.py`
- Create: `tests/unit/test_sparkline.py`

- [ ] **Step 1: Write failing tests for the SVG builder**

Create `tests/unit/test_sparkline.py`:

```python
"""Tests for sparkline SVG builder."""

from address_validator.routers.admin._sparkline import SPARKLINE_COLORS, build_sparkline_svg


def test_build_sparkline_normal_data() -> None:
    """Normal data produces an SVG with a polyline and trend label."""
    points = [3.0, 7.0, 2.0, 9.0, 5.0]
    svg = build_sparkline_svg(points, color="#6d4488", label="Test sparkline")
    assert "<svg" in svg
    assert 'role="img"' in svg
    assert "Test sparkline" in svg
    assert "<polyline" in svg
    assert "#6d4488" in svg
    # Trend descriptor appended to label.
    assert "trending" in svg or "stable" in svg


def test_build_sparkline_empty_data() -> None:
    """Empty data shows flat line and 'No data' text."""
    svg = build_sparkline_svg([], color="#6d4488", label="Empty")
    assert "<svg" in svg
    assert "No data" in svg
    assert "<line" in svg


def test_build_sparkline_all_zeros() -> None:
    """All-zero data shows flat line and 'No data' text."""
    svg = build_sparkline_svg([0, 0, 0, 0], color="#6d4488", label="Zeros")
    assert "No data" in svg
    assert "<line" in svg


def test_build_sparkline_single_point() -> None:
    """Single data point renders without error."""
    svg = build_sparkline_svg([5.0], color="#6d4488", label="Single")
    assert "<svg" in svg
    assert "#6d4488" in svg
    assert "steady" in svg  # constant (single point) → flat line with "steady"


def test_build_sparkline_constant_nonzero() -> None:
    """Constant non-zero data shows flat line with 'steady' (no 'No data')."""
    svg = build_sparkline_svg([5, 5, 5], color="#2d9f9f", label="Constant")
    assert "No data" not in svg
    assert "steady" in svg
    assert "<line" in svg


def test_build_sparkline_label_escaped() -> None:
    """Labels with special characters are HTML-escaped in aria-label."""
    svg = build_sparkline_svg([1, 2, 3], color="#6d4488", label='Rate "high" & rising')
    assert "Rate &quot;high&quot; &amp; rising" in svg


def test_sparkline_colors_has_all_keys() -> None:
    """SPARKLINE_COLORS has entries for all 5 dashboard cards."""
    expected = {"requests_all", "requests_week", "requests_today", "cache_hit_rate", "error_rate"}
    assert set(SPARKLINE_COLORS.keys()) == expected
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_sparkline.py -v --no-cov`
Expected: ImportError — module does not exist yet.

- [ ] **Step 3: Implement the SVG builder**

Create `src/address_validator/routers/admin/_sparkline.py`:

```python
"""Inline SVG sparkline builder for admin dashboard stat cards."""

from __future__ import annotations

from html import escape

SPARKLINE_COLORS: dict[str, str] = {
    "requests_all": "#6d4488",      # co-purple
    "requests_week": "#2d9f9f",     # teal
    "requests_today": "#4a7fbf",    # blue
    "cache_hit_rate": "#d4882a",    # orange
    "error_rate": "#c44e8a",        # magenta
}

# SVG dimensions (viewBox units — scales responsively).
_WIDTH = 120
_HEIGHT = 32
_PAD = 2  # vertical padding so strokes aren't clipped


def build_sparkline_svg(
    points: list[float],
    *,
    color: str,
    label: str = "",
    width: int = _WIDTH,
    height: int = _HEIGHT,
) -> str:
    """Build an inline SVG sparkline from a list of values.

    Returns an ``<svg>`` element string with ``role="img"`` and ``aria-label``.
    Empty or all-zero data renders a flat midpoint line with a "No data" label.
    """
    usable_h = height - 2 * _PAD
    mid_y = _PAD + usable_h / 2

    # Empty or all-zero → "No data" flat line.
    if not points or all(v == 0 for v in points):
        return _no_data_svg(color=color, label=label, width=width, height=height, mid_y=mid_y)

    # Constant non-zero → flat line at midpoint (no "No data" label).
    mn, mx = min(points), max(points)
    if mn == mx:
        full_label = f"{label}, steady" if label else "steady"
        return _flat_line_svg(color=color, label=full_label, width=width, height=height, mid_y=mid_y)

    # Normal case — build polyline.
    trend = _describe_trend(points)
    full_label = f"{label}, {trend}" if label else trend

    n = len(points)
    x_step = width / max(n - 1, 1)
    coords: list[str] = []
    for i, v in enumerate(points):
        x = round(i * x_step, 1)
        # Invert y: higher values → lower y coordinate.
        y = round(_PAD + usable_h - (v - mn) / (mx - mn) * usable_h, 1)
        coords.append(f"{x},{y}")

    polyline = (
        f'<polyline points="{" ".join(coords)}" '
        f'fill="none" stroke="{color}" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
    )
    return _wrap_svg(polyline, label=full_label, width=width, height=height)


def _describe_trend(points: list[float]) -> str:
    """Return a short trend descriptor: 'trending up', 'trending down', or 'stable'."""
    mid = len(points) // 2
    first_half = sum(points[:mid]) / max(mid, 1)
    second_half = sum(points[mid:]) / max(len(points) - mid, 1)
    if second_half > first_half * 1.1:
        return "trending up"
    if second_half < first_half * 0.9:
        return "trending down"
    return "stable"


def _no_data_svg(*, color: str, label: str, width: int, height: int, mid_y: float) -> str:
    line = (
        f'<line x1="0" y1="{mid_y}" x2="{width}" y2="{mid_y}" '
        f'stroke="{color}" stroke-width="1.5" stroke-opacity="0.3" stroke-dasharray="4 3"/>'
    )
    text = (
        f'<text x="{width / 2}" y="{mid_y + 4}" '
        f'text-anchor="middle" font-size="9" fill="gray" opacity="0.6">No data</text>'
    )
    return _wrap_svg(line + text, label=label or "No data", width=width, height=height)


def _flat_line_svg(*, color: str, label: str, width: int, height: int, mid_y: float) -> str:
    line = (
        f'<line x1="0" y1="{mid_y}" x2="{width}" y2="{mid_y}" '
        f'stroke="{color}" stroke-width="2" stroke-linecap="round"/>'
    )
    return _wrap_svg(line, label=label, width=width, height=height)


def _wrap_svg(inner: str, *, label: str, width: int, height: int) -> str:
    safe_label = escape(label, quote=True)
    return (
        f'<svg viewBox="0 0 {width} {height}" '
        f'class="w-full h-8" preserveAspectRatio="none" '
        f'role="img" aria-label="{safe_label}">'
        f"{inner}</svg>"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_sparkline.py -v --no-cov`
Expected: All 7 tests PASS.

- [ ] **Step 5: Run linter**

Run: `uv run ruff check src/address_validator/routers/admin/_sparkline.py tests/unit/test_sparkline.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/routers/admin/_sparkline.py tests/unit/test_sparkline.py
git commit -m "#47 feat: add sparkline SVG builder"
```

---

### Task 2: Sparkline Data Query — `queries.py`

**Files:**
- Modify: `src/address_validator/routers/admin/queries.py`
- Modify: `tests/unit/test_admin_queries.py`

- [ ] **Step 1: Write failing tests for `get_sparkline_data`**

In `tests/unit/test_admin_queries.py`, add `get_sparkline_data` to the existing import block at line 9:

```python
from address_validator.routers.admin.queries import (
    get_audit_rows,
    get_dashboard_stats,
    get_endpoint_stats,
    get_provider_stats,
    get_sparkline_data,
)
```

Then append these test functions at the end of the file:

```python
@pytest.mark.asyncio
async def test_get_sparkline_data_with_rows(db: AsyncEngine) -> None:
    """Sparkline data returns point lists keyed by card name."""
    await _seed_rows(db)
    data = await get_sparkline_data(db)
    assert set(data.keys()) == {
        "requests_all", "requests_week", "requests_today",
        "cache_hit_rate", "error_rate",
    }
    # Each value is a list of floats.
    for key in data:
        assert isinstance(data[key], list)
        assert all(isinstance(v, (int, float)) for v in data[key])
    # requests_today has hourly buckets — seed rows are all "now" so at least one non-zero.
    assert any(v > 0 for v in data["requests_today"])


@pytest.mark.asyncio
async def test_get_sparkline_data_empty_db(db: AsyncEngine) -> None:
    """Sparkline data returns empty lists on empty audit_log."""
    data = await get_sparkline_data(db)
    for key in data:
        assert all(v == 0 for v in data[key])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_admin_queries.py::test_get_sparkline_data_with_rows -v --no-cov`
Expected: ImportError — `get_sparkline_data` does not exist yet.

- [ ] **Step 3: Implement `get_sparkline_data` in `queries.py`**

Add at the end of `src/address_validator/routers/admin/queries.py`:

```python
async def get_sparkline_data(engine: AsyncEngine) -> dict[str, list[float]]:
    """Fetch time-bucketed values for dashboard sparklines.

    Returns a dict keyed by card name, each value a list of floats
    (zero-filled for missing buckets).
    """
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_30d = today_start - timedelta(days=29)
    start_7d = today_start - timedelta(days=6)
    start_24h = now - timedelta(hours=23)
    start_24h = start_24h.replace(minute=0, second=0, microsecond=0)

    async with engine.connect() as conn:
        # Daily request counts — last 30 days.
        daily_rows = (
            await conn.execute(
                text("""
                    SELECT date_trunc('day', timestamp) AS bucket, COUNT(*) AS cnt
                    FROM audit_log
                    WHERE timestamp >= :start
                    GROUP BY bucket ORDER BY bucket
                """),
                {"start": start_30d},
            )
        ).fetchall()

        # Hourly request counts — last 24 hours.
        hourly_rows = (
            await conn.execute(
                text("""
                    SELECT date_trunc('hour', timestamp) AS bucket, COUNT(*) AS cnt
                    FROM audit_log
                    WHERE timestamp >= :start
                    GROUP BY bucket ORDER BY bucket
                """),
                {"start": start_24h},
            )
        ).fetchall()

        # Daily cache hit rate — last 7 days (validate endpoint only).
        cache_rows = (
            await conn.execute(
                text("""
                    SELECT
                        date_trunc('day', timestamp) AS bucket,
                        COUNT(*) FILTER (WHERE cache_hit = true) AS hits,
                        COUNT(*) FILTER (WHERE cache_hit IS NOT NULL) AS total
                    FROM audit_log
                    WHERE endpoint = '/api/v1/validate' AND timestamp >= :start
                    GROUP BY bucket ORDER BY bucket
                """),
                {"start": start_7d},
            )
        ).fetchall()

        # Daily error rate — last 7 days (API endpoints only).
        error_rows = (
            await conn.execute(
                text("""
                    SELECT
                        date_trunc('day', timestamp) AS bucket,
                        COUNT(*) FILTER (WHERE status_code >= 400) AS errors,
                        COUNT(*) AS total
                    FROM audit_log
                    WHERE endpoint IN (
                        '/api/v1/parse', '/api/v1/standardize', '/api/v1/validate'
                    ) AND timestamp >= :start
                    GROUP BY bucket ORDER BY bucket
                """),
                {"start": start_7d},
            )
        ).fetchall()

    # --- Zero-fill helper ---
    def _fill_daily(rows: list, start: datetime, days: int) -> list[float]:
        by_day = {r.bucket.date(): float(r.cnt) for r in rows}
        return [by_day.get((start + timedelta(days=i)).date(), 0.0) for i in range(days)]

    def _fill_hourly(rows: list, start: datetime, hours: int) -> list[float]:
        by_hour = {r.bucket: float(r.cnt) for r in rows}
        return [by_hour.get(start + timedelta(hours=i), 0.0) for i in range(hours)]

    def _fill_rate_daily(rows: list, start: datetime, days: int, num_col: str, den_col: str) -> list[float]:
        by_day: dict = {}
        for r in rows:
            mapping = r._mapping  # noqa: SLF001
            den = mapping[den_col]
            by_day[r.bucket.date()] = (mapping[num_col] / den * 100) if den > 0 else 0.0
        return [by_day.get((start + timedelta(days=i)).date(), 0.0) for i in range(days)]

    return {
        "requests_all": _fill_daily(daily_rows, start_30d, 30),
        "requests_week": _fill_daily(daily_rows, start_7d, 7),
        "requests_today": _fill_hourly(hourly_rows, start_24h, 24),
        "cache_hit_rate": _fill_rate_daily(cache_rows, start_7d, 7, "hits", "total"),
        "error_rate": _fill_rate_daily(error_rows, start_7d, 7, "errors", "total"),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_admin_queries.py -v --no-cov`
Expected: All tests PASS (existing + new).

- [ ] **Step 5: Run linter**

Run: `uv run ruff check src/address_validator/routers/admin/queries.py tests/unit/test_admin_queries.py`

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/routers/admin/queries.py tests/unit/test_admin_queries.py
git commit -m "#47 feat: add sparkline time-series query"
```

---

### Task 3: Wire Dashboard Handler + Template

**Files:**
- Modify: `src/address_validator/routers/admin/dashboard.py`
- Modify: `src/address_validator/templates/admin/dashboard.html`
- Modify: `tests/unit/test_admin_views.py`

- [ ] **Step 1: Write failing test for sparkline SVGs in dashboard HTML**

Append to `tests/unit/test_admin_views.py`:

```python
def test_admin_dashboard_has_sparklines(client: TestClient, admin_headers: dict) -> None:
    """Dashboard HTML contains sparkline SVG elements."""
    response = client.get("/admin/", headers=admin_headers)
    html = response.text
    # All 5 sparklines should render (even if "No data").
    assert html.count('role="img"') >= 5
    # Spot-check one aria-label.
    assert "aria-label=" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_admin_views.py::test_admin_dashboard_has_sparklines -v --no-cov`
Expected: FAIL — no `role="img"` elements in current dashboard.

- [ ] **Step 3: Update `dashboard.py` to fetch sparkline data and build SVGs**

Replace the handler in `src/address_validator/routers/admin/dashboard.py`:

```python
"""Admin dashboard landing page."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.responses import Response

from address_validator.routers.admin._config import get_css_version, get_quota_info, templates
from address_validator.routers.admin._sparkline import SPARKLINE_COLORS, build_sparkline_svg
from address_validator.routers.admin.deps import get_admin_user
from address_validator.routers.admin.queries import get_dashboard_stats, get_sparkline_data
from address_validator.services.validation import cache_db

router = APIRouter()

_SPARKLINE_LABELS: dict[str, str] = {
    "requests_all": "All requests over 30 days",
    "requests_week": "Requests over 7 days",
    "requests_today": "Requests over 24 hours",
    "cache_hit_rate": "Cache hit rate over 7 days",
    "error_rate": "Error rate over 7 days",
}


@router.get("/", response_class=HTMLResponse, response_model=None)
async def admin_dashboard(request: Request) -> Response:
    user = get_admin_user(request)
    if isinstance(user, RedirectResponse):
        return user
    try:
        engine = await cache_db.get_engine()
        stats = await get_dashboard_stats(engine)
    except Exception:
        engine = None
        stats = {}
    try:
        if engine is None:
            engine = await cache_db.get_engine()
        sparkline_points = await get_sparkline_data(engine)
    except Exception:
        sparkline_points = {}
    sparkline_svgs = {
        key: build_sparkline_svg(
            sparkline_points.get(key, []),
            color=SPARKLINE_COLORS[key],
            label=_SPARKLINE_LABELS[key],
        )
        for key in SPARKLINE_COLORS
    }
    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": request,
            "user": user,
            "active_nav": "dashboard",
            "css_version": get_css_version(),
            "stats": stats,
            "quota": get_quota_info(),
            "sparkline_svgs": sparkline_svgs,
        },
    )
```

- [ ] **Step 4: Update `dashboard.html` to render sparklines in each card**

In the top 3 request-count cards, insert the sparkline SVG between the big number and the endpoint breakdown. In the cache hit rate and error rate cards, insert after the big number.

For the top 3 cards (inside the `{% for ... %}` loop), the sparkline key mapping:
- `"All Requests"` → `requests_all`
- `"Requests This Week"` → `requests_week`
- `"Requests Today"` → `requests_today`

Add a lookup dict at the top of the loop and insert `{{ sparkline_svgs[spark_key] | safe }}` with a `mt-2` wrapper div.

For the bottom 2 cards, insert `{{ sparkline_svgs.cache_hit_rate | safe }}` and `{{ sparkline_svgs.error_rate | safe }}` respectively.

The template changes (full replacement of content block):

```html
{% extends "admin/base.html" %}
{% block title %}Dashboard{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-6">Dashboard</h1>
{% set bd = stats.get("endpoint_breakdown", {}) %}
{% set ep_order = ["/parse", "/standardize", "/validate", "other"] %}
<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mb-4">
    {% for label, key, period in [("All Requests", "requests_all", "all"), ("Requests This Week", "requests_week", "week"), ("Requests Today", "requests_today", "today")] %}
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">{{ label }}</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ stats.get(key, 0) }}</p>
        {% if sparkline_svgs and key in sparkline_svgs %}
        <div class="mt-2">{{ sparkline_svgs[key] | safe }}</div>
        {% endif %}
        {% set period_bd = bd.get(period, {}) %}
        {% if period_bd %}
        <div class="mt-2 border-t border-gray-100 dark:border-gray-600 pt-2 space-y-0.5">
            {% for ep in ep_order %}
            {% if ep in period_bd %}
            <div class="flex justify-between text-xs">
                <span class="text-gray-400 dark:text-gray-500">{{ ep }}</span>
                <span class="text-gray-500 dark:text-gray-400 font-medium">{{ period_bd[ep] }}</span>
            </div>
            {% endif %}
            {% endfor %}
        </div>
        {% endif %}
    </div>
    {% endfor %}
</div>
<div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-8">
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Cache Hit Rate</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">
            {% if stats.get("cache_hit_rate") is not none %}{{ "%.1f" | format(stats.cache_hit_rate) }}%{% else %}N/A{% endif %}
        </p>
        {% if sparkline_svgs and "cache_hit_rate" in sparkline_svgs %}
        <div class="mt-2">{{ sparkline_svgs.cache_hit_rate | safe }}</div>
        {% endif %}
    </div>
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Error Rate (Today)</p>
        <p class="text-2xl font-bold {% if stats.get("error_rate") and stats.error_rate > 5 %}text-red-600 dark:text-red-400{% else %}text-gray-900 dark:text-gray-100{% endif %}">
            {% if stats.get("error_rate") is not none %}{{ "%.1f" | format(stats.error_rate) }}%{% else %}N/A{% endif %}
        </p>
        {% if sparkline_svgs and "error_rate" in sparkline_svgs %}
        <div class="mt-2">{{ sparkline_svgs.error_rate | safe }}</div>
        {% endif %}
    </div>
</div>
{% if quota %}
<h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-3">Validation Provider Quota</h2>
<div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-8">
    {% for q in quota %}
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">{{ q.provider | upper }} Daily Quota</p>
        <div class="flex items-baseline gap-2">
            <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ q.remaining }}</p>
            <p class="text-sm text-gray-500 dark:text-gray-400">/ {{ q.limit }}</p>
        </div>
        <div class="mt-2 w-full bg-gray-200 dark:bg-gray-600 rounded-full h-2" role="progressbar"
             aria-valuenow="{{ q.remaining }}" aria-valuemin="0" aria-valuemax="{{ q.limit }}"
             aria-label="{{ q.provider }} quota usage">
            <div class="bg-co-purple h-2 rounded-full"
                 style="width: {{ ((q.remaining / q.limit) * 100) | int if q.limit > 0 else 0 }}%"></div>
        </div>
    </div>
    {% endfor %}
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_admin_views.py::test_admin_dashboard_has_sparklines -v --no-cov`
Expected: PASS.

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest --no-cov -x`
Expected: All tests PASS (no regressions).

- [ ] **Step 7: Run linter**

Run: `uv run ruff check src/address_validator/routers/admin/dashboard.py`

- [ ] **Step 8: Commit**

```bash
git add src/address_validator/routers/admin/dashboard.py \
        src/address_validator/templates/admin/dashboard.html \
        tests/unit/test_admin_views.py
git commit -m "#47 feat: wire sparklines into dashboard handler and template"
```

---

### Task 4: Full Suite Green + Coverage Check

- [ ] **Step 1: Run full test suite with coverage**

Run: `uv run pytest`
Expected: All tests PASS, coverage ≥ 80%.

- [ ] **Step 2: Run linter on all changed files**

Run: `uv run ruff check .`
Expected: clean.

- [ ] **Step 3: Visual smoke test**

Run: `sudo systemctl restart address-validator` and visit `/admin/` in a browser.
Check:
- All 5 cards show sparklines (or "No data" flat line)
- Dark mode toggle works with sparklines visible in both modes
- Mobile responsive — sparklines scale within cards

- [ ] **Step 4: Final commit if any fixups needed**

```bash
git add -u
git commit -m "#47 fix: sparkline polish from smoke test"
```
