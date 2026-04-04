# Provider & Endpoint Dashboard Polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Polish the provider and endpoint admin detail pages for consistency with the main dashboard — card layout, card ordering, a new 7-day card on providers, accessible validation status column in provider audit table, color fix for `confirmed_missing_secondary`, canonical filter pill ordering, and table heading rename.

**Architecture:** Four independent change layers: (1) query extension, (2) shared audit partial + router wiring, (3) provider template overhaul, (4) endpoint template reorder. Each produces a working, tested commit before the next begins.

**Tech Stack:** FastAPI, SQLAlchemy Core async, Jinja2, Tailwind CSS, pytest-asyncio

---

## File map

| File | Change |
|---|---|
| `src/address_validator/routers/admin/queries.py` | Add `last_7d`, `status_codes_7d`, `validation_statuses_7d` to `get_provider_stats()`; add `_VS_CANONICAL_ORDER` + `_sort_validation_statuses()` helper |
| `src/address_validator/routers/admin/providers.py` | Pass `show_result=True` to both full and HTMX partial responses |
| `src/address_validator/routers/admin/endpoints.py` | Pass `show_result=False` to both full and HTMX partial responses |
| `src/address_validator/templates/admin/audit/_thead.html` | Conditional `<th>Result</th>` gated on `show_result` |
| `src/address_validator/templates/admin/audit/_rows.html` | Conditional Result `<td>`; dynamic `colspan` in empty-state row |
| `src/address_validator/templates/admin/providers/detail.html` | Two-row card layout (All Time · 7d · 24h then Cache/Quota); new 7d card; color fix; canonical pill order; rename heading |
| `src/address_validator/templates/admin/endpoints/detail.html` | Two-row card layout (All Time · 7d · 24h then Latency/Error); reorder cards; rename heading |
| `tests/unit/test_admin_queries.py` | Tests for new provider stats keys and canonical VS ordering |
| `tests/unit/test_admin_views.py` | Tests for Result column, card order, color fix, pill order, heading rename |

---

## Task 1: Extend `get_provider_stats()` with 7-day data and canonical VS ordering

**Files:**
- Modify: `src/address_validator/routers/admin/queries.py`
- Test: `tests/unit/test_admin_queries.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_admin_queries.py`:

```python
@pytest.mark.asyncio
async def test_get_provider_stats_has_last_7d(db: AsyncEngine) -> None:
    """get_provider_stats returns a last_7d request count."""
    await _seed_rows(db)
    stats = await get_provider_stats(db, "usps")
    assert "last_7d" in stats
    assert stats["last_7d"] == 2  # 2 usps rows seeded


@pytest.mark.asyncio
async def test_get_provider_stats_has_status_codes_7d(db: AsyncEngine) -> None:
    """get_provider_stats returns status_codes_7d (live only, 7-day window)."""
    await _seed_rows(db)
    stats = await get_provider_stats(db, "usps")
    assert "status_codes_7d" in stats
    assert stats["status_codes_7d"][200] == 2


@pytest.mark.asyncio
async def test_get_provider_stats_has_validation_statuses_7d(db: AsyncEngine) -> None:
    """get_provider_stats returns validation_statuses_7d (live only, 7-day window)."""
    await _seed_rows(db)
    stats = await get_provider_stats(db, "usps")
    assert "validation_statuses_7d" in stats
    assert stats["validation_statuses_7d"]["confirmed"] == 2


@pytest.mark.asyncio
async def test_get_provider_stats_validation_statuses_canonical_order(
    db: AsyncEngine,
) -> None:
    """validation_statuses_all keys appear in canonical order regardless of DB order."""
    now = datetime.now(UTC)
    async with db.begin() as conn:
        for vs in ("not_confirmed", "confirmed_missing_secondary", "confirmed"):
            await conn.execute(
                text("""
                    INSERT INTO audit_log (timestamp, client_ip, method, endpoint,
                        status_code, provider, validation_status, cache_hit)
                    VALUES (:ts, '1.2.3.4', 'POST', '/api/v1/validate',
                        200, 'usps', :vs, false)
                """),
                {"ts": now, "vs": vs},
            )
    stats = await get_provider_stats(db, "usps")
    keys = list(stats["validation_statuses_all"].keys())
    assert keys.index("confirmed") < keys.index("confirmed_missing_secondary")
    assert keys.index("confirmed_missing_secondary") < keys.index("not_confirmed")
```

- [ ] **Step 2: Run to verify they fail**

```
uv run pytest tests/unit/test_admin_queries.py \
  -k "last_7d or status_codes_7d or validation_statuses_7d or canonical_order" \
  --no-cov -v
```

Expected: 4 FAILs — `last_7d`, `status_codes_7d`, `validation_statuses_7d` not in stats; order not enforced.

- [ ] **Step 3: Add `_VS_CANONICAL_ORDER` and `_sort_validation_statuses` near the top of `queries.py` (after the imports block, before the module-level constants)**

```python
# ---------------------------------------------------------------------------
# Validation status helpers
# ---------------------------------------------------------------------------

_VS_CANONICAL_ORDER = (
    "confirmed",
    "confirmed_missing_secondary",
    "confirmed_bad_secondary",
    "not_confirmed",
)


def _sort_validation_statuses(vs_dict: dict) -> dict:
    """Return vs_dict with keys in canonical display order.

    Unknown statuses sort after the known four, alphabetically among themselves.
    """
    priority = {vs: i for i, vs in enumerate(_VS_CANONICAL_ORDER)}
    return dict(
        sorted(
            vs_dict.items(),
            key=lambda kv: (priority.get(kv[0], len(_VS_CANONICAL_ORDER)), kv[0]),
        )
    )
```

- [ ] **Step 4: Extend `get_provider_stats()` — add `last_7d` to the main aggregate row**

Locate the main single-row aggregate inside `get_provider_stats()` (currently fetches `total`, `last_24h`, `cache_hits`, `cache_total`). Add a `last_7d` label:

```python
        row = (
            await conn.execute(
                _from_live(
                    [
                        func.count().label("total"),
                        func.count()
                        .filter(audit_log.c.timestamp >= tb["last_24h"])
                        .label("last_24h"),
                        func.count()
                        .filter(audit_log.c.timestamp >= tb["last_7d"])
                        .label("last_7d"),
                        func.count()
                        .filter(
                            audit_log.c.cache_hit.is_(True),
                            audit_log.c.timestamp >= tb["last_7d"],
                        )
                        .label("cache_hits"),
                        func.count()
                        .filter(
                            audit_log.c.cache_hit.isnot(None),
                            audit_log.c.timestamp >= tb["last_7d"],
                        )
                        .label("cache_total"),
                    ],
                    audit_log.c.provider == provider_name,
                )
            )
        ).one()
```

- [ ] **Step 5: Add `status_codes_7d` and `validation_statuses_7d` queries inside `get_provider_stats()`, after the existing `live_status_24h_rows` query**

```python
        live_status_7d_rows = (
            await conn.execute(
                select(
                    audit_log.c.status_code,
                    sa.cast(func.count(), sa.Integer).label("cnt"),
                )
                .where(
                    audit_log.c.provider == provider_name,
                    audit_log.c.timestamp >= tb["last_7d"],
                )
                .group_by(audit_log.c.status_code)
            )
        ).fetchall()

        vs_7d_rows = (
            await conn.execute(
                _from_live(
                    [
                        audit_log.c.validation_status,
                        func.count().label("count"),
                    ],
                    audit_log.c.provider == provider_name,
                    audit_log.c.validation_status.isnot(None),
                    audit_log.c.timestamp >= tb["last_7d"],
                )
                .group_by(audit_log.c.validation_status)
                .order_by(func.count().desc())
            )
        ).fetchall()
```

- [ ] **Step 6: Update the `return` dict at the bottom of `get_provider_stats()` to include the new keys and wrap all `validation_statuses_*` with `_sort_validation_statuses()`**

```python
    cache_hit_rate = (row.cache_hits / row.cache_total * 100) if row.cache_total > 0 else None
    return {
        "total": row.total + archived_total,
        "last_24h": row.last_24h,
        "last_7d": row.last_7d,
        "cache_hit_rate": cache_hit_rate,
        "status_codes_24h": {r.status_code: r.cnt for r in live_status_24h_rows},
        "status_codes_7d": {r.status_code: r.cnt for r in live_status_7d_rows},
        "status_codes_all": {r.status_code: r.count for r in status_all_rows},
        "validation_statuses_all": _sort_validation_statuses(
            {r.validation_status: r.count for r in status_rows}
        ),
        "validation_statuses_24h": _sort_validation_statuses(
            {r.validation_status: r.count for r in vs_24h_rows}
        ),
        "validation_statuses_7d": _sort_validation_statuses(
            {r.validation_status: r.count for r in vs_7d_rows}
        ),
    }
```

- [ ] **Step 7: Run the new tests and full query test suite**

```
uv run pytest tests/unit/test_admin_queries.py --no-cov -v
```

Expected: all pass. If `test_get_provider_stats_has_per_window_breakdowns` fails because it doesn't expect `last_7d` — check it: it only asserts specific keys exist and their values, no negative assertions on unknown keys, so it should pass without modification.

- [ ] **Step 8: Lint**

```
uv run ruff check src/address_validator/routers/admin/queries.py --fix
uv run ruff format src/address_validator/routers/admin/queries.py
```

- [ ] **Step 9: Commit**

```bash
git add src/address_validator/routers/admin/queries.py \
        tests/unit/test_admin_queries.py
git commit -m "#85 feat: extend get_provider_stats with last_7d, status_codes_7d, validation_statuses_7d, canonical VS order"
```

---

## Task 2: Conditional Result column in shared audit partials + router wiring

**Files:**
- Modify: `src/address_validator/templates/admin/audit/_thead.html`
- Modify: `src/address_validator/templates/admin/audit/_rows.html`
- Modify: `src/address_validator/routers/admin/providers.py`
- Modify: `src/address_validator/routers/admin/endpoints.py`
- Test: `tests/unit/test_admin_views.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_admin_views.py`:

```python
def test_provider_table_has_result_column_header(client: TestClient, admin_headers: dict) -> None:
    """Provider detail page has a Result column in the audit table header."""
    response = client.get("/admin/providers/usps", headers=admin_headers)
    assert response.status_code == 200
    assert "<th" in response.text
    assert "Result" in response.text


def test_endpoint_table_has_no_result_column_header(client: TestClient, admin_headers: dict) -> None:
    """Endpoint detail page does NOT have a Result column in the audit table header."""
    response = client.get("/admin/endpoints/parse", headers=admin_headers)
    assert response.status_code == 200
    assert "Result" not in response.text


def test_provider_result_column_shows_symbol_and_sronly_text(
    client: TestClient, admin_headers: dict
) -> None:
    """Provider audit rows show a shape symbol and sr-only text for validation_status."""
    from unittest.mock import AsyncMock, patch

    mock_rows = AsyncMock(
        return_value=(
            [
                {
                    "timestamp": None,
                    "client_ip": "1.2.3.4",
                    "method": "POST",
                    "endpoint": "/api/v1/validate",
                    "status_code": 200,
                    "latency_ms": 50,
                    "provider": "usps",
                    "validation_status": "confirmed",
                    "cache_hit": True,
                    "error_detail": None,
                    "raw_input": None,
                }
            ],
            1,
        )
    )
    with patch("address_validator.routers.admin.providers.get_audit_rows", mock_rows):
        response = client.get("/admin/providers/usps", headers=admin_headers)
    assert response.status_code == 200
    html = response.text
    # sr-only text present
    assert 'class="sr-only"' in html
    assert "confirmed" in html
    # Green checkmark symbol for "confirmed"
    assert "&#10003;" in html


def test_provider_result_column_colspan_ten_on_empty(
    client: TestClient, admin_headers: dict
) -> None:
    """Empty-state row in provider table uses colspan=10 (includes Result column)."""
    response = client.get("/admin/providers/usps", headers=admin_headers)
    assert response.status_code == 200
    assert 'colspan="10"' in response.text


def test_endpoint_result_column_colspan_nine_on_empty(
    client: TestClient, admin_headers: dict
) -> None:
    """Empty-state row in endpoint table uses colspan=9 (no Result column)."""
    response = client.get("/admin/endpoints/parse", headers=admin_headers)
    assert response.status_code == 200
    assert 'colspan="9"' in response.text
    assert 'colspan="10"' not in response.text
```

- [ ] **Step 2: Run to verify they fail**

```
uv run pytest tests/unit/test_admin_views.py \
  -k "result_column or colspan" \
  --no-cov -v
```

Expected: all 5 FAILs.

- [ ] **Step 3: Update `_thead.html` — add conditional Result `<th>` before Raw Input**

Full replacement of `src/address_validator/templates/admin/audit/_thead.html`:

```html
<tr>
    <th class="px-3 py-2">Time</th>
    <th class="px-3 py-2">IP</th>
    <th class="px-3 py-2">Method</th>
    <th class="px-3 py-2">Endpoint</th>
    <th class="px-3 py-2">Status</th>
    <th class="px-3 py-2 text-right">Latency</th>
    <th class="px-3 py-2">Provider</th>
    <th class="px-3 py-2">Cache</th>
    {% if show_result %}<th class="px-3 py-2">Result</th>{% endif %}
    <th class="px-3 py-2">Raw Input</th>
</tr>
```

- [ ] **Step 4: Update `_rows.html` — add conditional Result `<td>` and dynamic colspan**

Full replacement of `src/address_validator/templates/admin/audit/_rows.html`:

```html
{% for row in rows %}
<tr class="border-b border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-700 text-sm">
    <td class="px-3 py-2 whitespace-nowrap text-gray-500 dark:text-gray-400">{{ row["timestamp"].strftime('%Y-%m-%d %H:%M:%S') if row["timestamp"] else '' }}</td>
    <td class="px-3 py-2 whitespace-nowrap text-gray-700 dark:text-gray-300">{{ row["client_ip"] or '' }}</td>
    <td class="px-3 py-2 whitespace-nowrap text-gray-700 dark:text-gray-300">{{ row["method"] or '' }}</td>
    <td class="px-3 py-2 whitespace-nowrap text-gray-700 dark:text-gray-300">{{ row["endpoint"] or '' }}</td>
    <td class="px-3 py-2 whitespace-nowrap">
        {% if row["status_code"] and row["status_code"] < 400 %}
            <span class="inline-flex items-center gap-1 text-green-700 dark:text-green-400">&#10003; {{ row["status_code"] }}</span>
        {% elif row["status_code"] and row["status_code"] < 500 %}
            <span class="inline-flex items-center gap-1 text-yellow-600 dark:text-yellow-400">&#9650; {{ row["status_code"] }}</span>
        {% elif row["status_code"] %}
            <span class="inline-flex items-center gap-1 text-red-600 dark:text-red-400">&#10005; {{ row["status_code"] }}</span>
        {% endif %}
    </td>
    <td class="px-3 py-2 whitespace-nowrap text-gray-700 dark:text-gray-300 text-right">{% if row["latency_ms"] is not none %}{{ row["latency_ms"] }}ms{% endif %}</td>
    <td class="px-3 py-2 whitespace-nowrap text-gray-700 dark:text-gray-300">{{ row["provider"] or '' }}</td>
    <td class="px-3 py-2 whitespace-nowrap">
        {% if row["cache_hit"] is true %}
            <span class="text-green-600 dark:text-green-400 font-medium">HIT</span>
        {% elif row["cache_hit"] is false %}
            <span class="text-gray-400 dark:text-gray-500">MISS</span>
        {% endif %}
    </td>
    {% if show_result %}
    <td class="px-3 py-2 whitespace-nowrap">
        {% set vs = row.get("validation_status") %}
        {% if vs == "confirmed" %}
            <span class="inline-flex items-center gap-1 text-green-700 dark:text-green-400">&#10003;<span class="sr-only">{{ vs }}</span></span>
        {% elif vs in ("confirmed_missing_secondary", "confirmed_bad_secondary") %}
            <span class="inline-flex items-center gap-1 text-yellow-600 dark:text-yellow-400">&#9650;<span class="sr-only">{{ vs }}</span></span>
        {% elif vs == "not_confirmed" %}
            <span class="inline-flex items-center gap-1 text-red-600 dark:text-red-400">&#10005;<span class="sr-only">{{ vs }}</span></span>
        {% else %}
            <span class="text-gray-400 dark:text-gray-600">—</span>
        {% endif %}
    </td>
    {% endif %}
    <td class="px-3 py-2 text-gray-700 dark:text-gray-300 max-w-xs">
        {% if row["raw_input"] %}
            <span title="{{ row['raw_input'] }}" class="block truncate text-xs font-mono">{{ row["raw_input"] }}</span>
        {% else %}
            <span class="text-gray-400 dark:text-gray-600">—</span>
        {% endif %}
    </td>
</tr>
{% else %}
<tr><td colspan="{{ 10 if show_result else 9 }}" class="px-3 py-8 text-center text-gray-400 dark:text-gray-500">No audit log entries found.</td></tr>
{% endfor %}
```

- [ ] **Step 5: Update `providers.py` — pass `show_result=True` to both response paths**

In the HTMX partial branch:
```python
    if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"):
        return templates.TemplateResponse(
            "admin/audit/_rows.html",
            {"request": request, "rows": rows, "show_result": True},
        )
```

In the full-page response, add `"show_result": True` to the context dict:
```python
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
            "show_result": True,
        },
    )
```

- [ ] **Step 6: Update `endpoints.py` — pass `show_result=False` to both response paths**

In the HTMX partial branch:
```python
    if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"):
        return templates.TemplateResponse(
            "admin/audit/_rows.html",
            {"request": request, "rows": rows, "show_result": False},
        )
```

In the full-page response, add `"show_result": False` to the context dict:
```python
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
            "show_result": False,
        },
    )
```

- [ ] **Step 7: Run the new tests plus full admin view suite**

```
uv run pytest tests/unit/test_admin_views.py --no-cov -v
```

Expected: all pass.

- [ ] **Step 8: Lint**

```
uv run ruff check src/address_validator/routers/admin/ --fix
uv run ruff format src/address_validator/routers/admin/
```

- [ ] **Step 9: Commit**

```bash
git add src/address_validator/templates/admin/audit/_thead.html \
        src/address_validator/templates/admin/audit/_rows.html \
        src/address_validator/routers/admin/providers.py \
        src/address_validator/routers/admin/endpoints.py \
        tests/unit/test_admin_views.py
git commit -m "#85 feat: add conditional Result column to provider audit table (show_result)"
```

---

## Task 3: Provider detail page — two-row card layout, 7d card, color fixes, canonical pills, heading rename

**Files:**
- Modify: `src/address_validator/templates/admin/providers/detail.html`
- Test: `tests/unit/test_admin_views.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_admin_views.py`:

```python
def test_provider_detail_has_7d_requests_card(client: TestClient, admin_headers: dict) -> None:
    """Provider detail page has a Requests (Last 7 Days) card."""
    response = client.get("/admin/providers/usps", headers=admin_headers)
    assert response.status_code == 200
    assert "Requests (Last 7 Days)" in response.text


def test_provider_detail_card_order_all_time_before_7d_before_24h(
    client: TestClient, admin_headers: dict
) -> None:
    """Card order: All Time appears before 7 Days, which appears before 24 Hours."""
    response = client.get("/admin/providers/usps", headers=admin_headers)
    html = response.text
    all_time_pos = html.index("Requests (All Time)")
    seven_day_pos = html.index("Requests (Last 7 Days)")
    twenty_four_pos = html.index("Requests (Last 24 Hours)")
    assert all_time_pos < seven_day_pos < twenty_four_pos


def test_provider_confirmed_missing_secondary_is_yellow(
    client: TestClient, admin_headers: dict
) -> None:
    """confirmed_missing_secondary renders with yellow classes, not green, in cards and pills."""
    from unittest.mock import AsyncMock, patch

    with patch(
        "address_validator.routers.admin.providers.get_provider_stats",
        new_callable=AsyncMock,
        return_value={
            "total": 10,
            "last_24h": 5,
            "last_7d": 8,
            "cache_hit_rate": None,
            "status_codes_all": {200: 10},
            "status_codes_24h": {},
            "status_codes_7d": {},
            "validation_statuses_all": {"confirmed_missing_secondary": 3},
            "validation_statuses_24h": {},
            "validation_statuses_7d": {},
        },
    ):
        response = client.get("/admin/providers/usps", headers=admin_headers)
    html = response.text
    # Every occurrence of "confirmed_missing_secondary" text should be near yellow, not green.
    # Find the filter pill span for confirmed_missing_secondary.
    import re
    pill_match = re.search(
        r'value="confirmed_missing_secondary".*?<span class="([^"]*)"',
        html,
        re.DOTALL,
    )
    assert pill_match, "pill for confirmed_missing_secondary not found"
    pill_classes = pill_match.group(1)
    assert "yellow" in pill_classes, f"expected yellow in pill classes, got: {pill_classes}"
    assert "green" not in pill_classes, f"green should not appear in pill classes, got: {pill_classes}"


def test_provider_not_confirmed_pill_is_last(
    client: TestClient, admin_headers: dict
) -> None:
    """not_confirmed filter pill appears after confirmed_bad_secondary in DOM order."""
    from unittest.mock import AsyncMock, patch

    with patch(
        "address_validator.routers.admin.providers.get_provider_stats",
        new_callable=AsyncMock,
        return_value={
            "total": 10,
            "last_24h": 5,
            "last_7d": 8,
            "cache_hit_rate": None,
            "status_codes_all": {},
            "status_codes_24h": {},
            "status_codes_7d": {},
            "validation_statuses_all": {
                "not_confirmed": 1,
                "confirmed_bad_secondary": 2,
                "confirmed": 5,
            },
            "validation_statuses_24h": {},
            "validation_statuses_7d": {},
        },
    ):
        response = client.get("/admin/providers/usps", headers=admin_headers)
    html = response.text
    bad_secondary_pos = html.index('value="confirmed_bad_secondary"')
    not_confirmed_pos = html.index('value="not_confirmed"')
    assert bad_secondary_pos < not_confirmed_pos


def test_provider_table_heading_is_requests(client: TestClient, admin_headers: dict) -> None:
    """Provider detail table heading is 'Requests', not 'Recent Requests'."""
    response = client.get("/admin/providers/usps", headers=admin_headers)
    assert "Recent Requests" not in response.text
    assert "Requests" in response.text
```

- [ ] **Step 2: Run to verify they fail**

```
uv run pytest tests/unit/test_admin_views.py \
  -k "7d_requests_card or card_order_all_time or confirmed_missing or not_confirmed_pill or table_heading_is_requests" \
  --no-cov -v
```

Expected: 5 FAILs.

- [ ] **Step 3: Rewrite `providers/detail.html` stats section and filter pills**

Replace the entire file content of `src/address_validator/templates/admin/providers/detail.html`:

```html
{% extends "admin/base.html" %}
{% block title %}{{ provider_name | upper }} — Provider{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-6">{{ provider_name | upper }} Provider</h1>

{# ── Row 1: Request cards (All Time · 7 Days · 24 Hours) ─────────── #}
<div class="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Requests (All Time)</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ stats.total | default(0) }}</p>
        {% set non_200_all = stats.get("status_codes_all", {}) | dictsort | selectattr("0", "ne", 200) | list %}
        {% if non_200_all %}
        <p class="text-xs mt-1 text-gray-500 dark:text-gray-400">
            {% for code, cnt in non_200_all %}
            <span class="{% if code < 400 %}text-green-700 dark:text-green-400{% elif code < 500 %}text-yellow-600 dark:text-yellow-400{% else %}text-red-600 dark:text-red-400{% endif %}">{{ code }}: {{ cnt }}</span>{% if not loop.last %} · {% endif %}
            {% endfor %}
        </p>
        {% endif %}
        {% if stats.get("validation_statuses_all") %}
        <p class="text-xs mt-1 text-gray-500 dark:text-gray-400">
            {% for vs, cnt in stats.validation_statuses_all.items() %}
            <span class="{% if vs == 'confirmed' %}text-green-700 dark:text-green-400{% elif vs in ('confirmed_missing_secondary', 'confirmed_bad_secondary') %}text-yellow-600 dark:text-yellow-400{% elif vs == 'not_confirmed' %}text-red-600 dark:text-red-400{% else %}text-gray-500 dark:text-gray-400{% endif %}">{{ vs }}: {{ cnt }}</span>{% if not loop.last %} · {% endif %}
            {% endfor %}
        </p>
        {% endif %}
    </div>
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Requests (Last 7 Days)</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ stats.last_7d | default(0) }}</p>
        {% set non_200_7d = stats.get("status_codes_7d", {}) | dictsort | selectattr("0", "ne", 200) | list %}
        {% if non_200_7d %}
        <p class="text-xs mt-1 text-gray-500 dark:text-gray-400">
            {% for code, cnt in non_200_7d %}
            <span class="{% if code < 400 %}text-green-700 dark:text-green-400{% elif code < 500 %}text-yellow-600 dark:text-yellow-400{% else %}text-red-600 dark:text-red-400{% endif %}">{{ code }}: {{ cnt }}</span>{% if not loop.last %} · {% endif %}
            {% endfor %}
        </p>
        {% endif %}
        {% if stats.get("validation_statuses_7d") %}
        <p class="text-xs mt-1 text-gray-500 dark:text-gray-400">
            {% for vs, cnt in stats.validation_statuses_7d.items() %}
            <span class="{% if vs == 'confirmed' %}text-green-700 dark:text-green-400{% elif vs in ('confirmed_missing_secondary', 'confirmed_bad_secondary') %}text-yellow-600 dark:text-yellow-400{% elif vs == 'not_confirmed' %}text-red-600 dark:text-red-400{% else %}text-gray-500 dark:text-gray-400{% endif %}">{{ vs }}: {{ cnt }}</span>{% if not loop.last %} · {% endif %}
            {% endfor %}
        </p>
        {% endif %}
    </div>
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Requests (Last 24 Hours)</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ stats.last_24h | default(0) }}</p>
        {% set non_200_24h = stats.get("status_codes_24h", {}) | dictsort | selectattr("0", "ne", 200) | list %}
        {% if non_200_24h %}
        <p class="text-xs mt-1 text-gray-500 dark:text-gray-400">
            {% for code, cnt in non_200_24h %}
            <span class="{% if code < 400 %}text-green-700 dark:text-green-400{% elif code < 500 %}text-yellow-600 dark:text-yellow-400{% else %}text-red-600 dark:text-red-400{% endif %}">{{ code }}: {{ cnt }}</span>{% if not loop.last %} · {% endif %}
            {% endfor %}
        </p>
        {% endif %}
        {% if stats.get("validation_statuses_24h") %}
        <p class="text-xs mt-1 text-gray-500 dark:text-gray-400">
            {% for vs, cnt in stats.validation_statuses_24h.items() %}
            <span class="{% if vs == 'confirmed' %}text-green-700 dark:text-green-400{% elif vs in ('confirmed_missing_secondary', 'confirmed_bad_secondary') %}text-yellow-600 dark:text-yellow-400{% elif vs == 'not_confirmed' %}text-red-600 dark:text-red-400{% else %}text-gray-500 dark:text-gray-400{% endif %}">{{ vs }}: {{ cnt }}</span>{% if not loop.last %} · {% endif %}
            {% endfor %}
        </p>
        {% endif %}
    </div>
</div>

{# ── Row 2: Metric cards (Cache Hit Rate · Daily Quota) ───────────── #}
<div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-8">
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

{# ── Requests + filters ───────────────────────────────────────────── #}
<h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-3">Requests</h2>

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

    {# Validation status toggles — canonical order: confirmed → missing_secondary → bad_secondary → not_confirmed #}
    {% if stats.get("validation_statuses_all") %}
    {% set _vs_order = ['confirmed', 'confirmed_missing_secondary', 'confirmed_bad_secondary', 'not_confirmed'] %}
    <div class="flex flex-wrap items-center gap-2">
        <span class="text-xs text-gray-500 dark:text-gray-400 shrink-0">Result:</span>
        {% for vs in _vs_order %}
        {% if vs in stats.validation_statuses_all %}
        <label class="cursor-pointer">
            <input type="checkbox" name="validation_status" value="{{ vs }}"
                   {% if vs in filters.validation_statuses %}checked{% endif %}
                   class="sr-only peer">
            <span class="inline-flex items-center px-3 py-1 rounded-full text-sm font-medium
                peer-checked:ring-2 peer-checked:ring-offset-1 dark:peer-checked:ring-offset-gray-800
                {% if vs == 'confirmed' %}bg-green-100 text-green-800 dark:bg-green-900 dark:text-green-300 peer-checked:ring-green-600 dark:peer-checked:ring-green-400
                {% elif vs in ('confirmed_missing_secondary', 'confirmed_bad_secondary') %}bg-yellow-100 text-yellow-800 dark:bg-yellow-900 dark:text-yellow-300 peer-checked:ring-yellow-600 dark:peer-checked:ring-yellow-400
                {% elif vs == 'not_confirmed' %}bg-red-100 text-red-800 dark:bg-red-900 dark:text-red-300 peer-checked:ring-red-600 dark:peer-checked:ring-red-400
                {% else %}bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-300 peer-checked:ring-gray-500{% endif %}">
                {{ vs }}
            </span>
        </label>
        {% endif %}
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

- [ ] **Step 4: Run the new tests plus the full existing provider test suite**

```
uv run pytest tests/unit/test_admin_views.py --no-cov -v
```

Expected: all pass. Pay special attention to `test_provider_detail_filter_toggles_with_codes_and_statuses` — the `html.count("confirmed: 85") == 1` assertion still holds because the mock has no `validation_statuses_7d`.

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/templates/admin/providers/detail.html \
        tests/unit/test_admin_views.py
git commit -m "#85 feat: provider detail — two-row card layout, 7d card, color fix, canonical pill order, rename heading"
```

---

## Task 4: Endpoint detail page — two-row card layout, reorder cards, rename heading

**Files:**
- Modify: `src/address_validator/templates/admin/endpoints/detail.html`
- Test: `tests/unit/test_admin_views.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_admin_views.py`:

```python
def test_endpoint_detail_card_order_all_time_before_7d_before_24h(
    client: TestClient, admin_headers: dict
) -> None:
    """Endpoint card order: All Time appears before 7 Days, which appears before 24 Hours."""
    response = client.get("/admin/endpoints/parse", headers=admin_headers)
    html = response.text
    all_time_pos = html.index("Requests (All Time)")
    seven_day_pos = html.index("Requests (Last 7 Days)")
    twenty_four_pos = html.index("Requests (Last 24 Hours)")
    assert all_time_pos < seven_day_pos < twenty_four_pos


def test_endpoint_table_heading_is_requests(client: TestClient, admin_headers: dict) -> None:
    """Endpoint detail table heading is 'Requests', not 'Recent Requests'."""
    response = client.get("/admin/endpoints/parse", headers=admin_headers)
    assert "Recent Requests" not in response.text
    assert "Requests" in response.text
```

- [ ] **Step 2: Run to verify they fail**

```
uv run pytest tests/unit/test_admin_views.py \
  -k "endpoint_detail_card_order or endpoint_table_heading" \
  --no-cov -v
```

Expected: 2 FAILs — card order is currently 24h · 7d · All Time; heading is "Recent Requests".

- [ ] **Step 3: Rewrite `endpoints/detail.html` stats section and heading**

Replace the entire file content of `src/address_validator/templates/admin/endpoints/detail.html`:

```html
{% extends "admin/base.html" %}
{% block title %}/{{ endpoint_name }} — Endpoint{% endblock %}
{% block content %}
<h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-6">/api/v1/{{ endpoint_name }}</h1>

{# ── Row 1: Request cards (All Time · 7 Days · 24 Hours) ─────────── #}
<div class="grid grid-cols-1 sm:grid-cols-3 gap-4 mb-4">
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Requests (All Time)</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ stats.total | default(0) }}</p>
        {# dictsort → (code, cnt) tuples; selectattr("0",...) filters on the first element (code) #}
        {% set non_200_all = stats.get("status_codes_all", {}) | dictsort | selectattr("0", "ne", 200) | list %}
        {% if non_200_all %}
        <p class="text-xs mt-1 text-gray-500 dark:text-gray-400">
            {% for code, cnt in non_200_all %}
            <span class="{% if code < 400 %}text-green-700 dark:text-green-400{% elif code < 500 %}text-yellow-600 dark:text-yellow-400{% else %}text-red-600 dark:text-red-400{% endif %}">{{ code }}: {{ cnt }}</span>{% if not loop.last %} · {% endif %}
            {% endfor %}
        </p>
        {% endif %}
    </div>
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Requests (Last 7 Days)</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ stats.last_7d | default(0) }}</p>
        {% set non_200_7d = stats.get("status_codes_7d", {}) | dictsort | selectattr("0", "ne", 200) | list %}
        {% if non_200_7d %}
        <p class="text-xs mt-1 text-gray-500 dark:text-gray-400">
            {% for code, cnt in non_200_7d %}
            <span class="{% if code < 400 %}text-green-700 dark:text-green-400{% elif code < 500 %}text-yellow-600 dark:text-yellow-400{% else %}text-red-600 dark:text-red-400{% endif %}">{{ code }}: {{ cnt }}</span>{% if not loop.last %} · {% endif %}
            {% endfor %}
        </p>
        {% endif %}
    </div>
    <div class="bg-white dark:bg-gray-800 rounded-lg border border-gray-200 dark:border-gray-700 p-4">
        <p class="text-sm text-gray-500 dark:text-gray-400 mb-1">Requests (Last 24 Hours)</p>
        <p class="text-2xl font-bold text-gray-900 dark:text-gray-100">{{ stats.last_24h | default(0) }}</p>
        {% set non_200_24h = stats.get("status_codes_24h", {}) | dictsort | selectattr("0", "ne", 200) | list %}
        {% if non_200_24h %}
        <p class="text-xs mt-1 text-gray-500 dark:text-gray-400">
            {% for code, cnt in non_200_24h %}
            <span class="{% if code < 400 %}text-green-700 dark:text-green-400{% elif code < 500 %}text-yellow-600 dark:text-yellow-400{% else %}text-red-600 dark:text-red-400{% endif %}">{{ code }}: {{ cnt }}</span>{% if not loop.last %} · {% endif %}
            {% endfor %}
        </p>
        {% endif %}
    </div>
</div>

{# ── Row 2: Metric cards (Avg Latency · Error Rate) ───────────────── #}
<div class="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-8">
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

{# ── Requests + filters ───────────────────────────────────────────── #}
<h2 class="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-3">Requests</h2>

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

- [ ] **Step 4: Run the full test suite**

```
uv run pytest tests/unit/test_admin_views.py --no-cov -v
```

Expected: all pass, including the existing `test_endpoint_detail_has_all_time_card` (the card is present, just reordered).

- [ ] **Step 5: Run full suite with coverage**

```
uv run pytest --tb=short
```

Expected: all pass, coverage ≥ 80%.

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/templates/admin/endpoints/detail.html \
        tests/unit/test_admin_views.py
git commit -m "#85 feat: endpoint detail — two-row card layout, reorder All Time·7d·24h, rename heading"
```

---

## Verification checklist

After all 4 tasks are committed:

- [ ] `uv run pytest --tb=short` — all pass, coverage ≥ 80%
- [ ] `uv run ruff check .` — clean
- [ ] Check live dev server: provider detail shows All Time · 7 Days · 24 Hours cards in row 1, Cache/Quota in row 2
- [ ] Check live dev server: `confirmed_missing_secondary` renders yellow in cards and pills
- [ ] Check live dev server: Result column appears on provider table, absent on endpoint table
- [ ] Check `/parse` endpoint: pagination absent when ≤ 50 rows (expected, not a bug)
