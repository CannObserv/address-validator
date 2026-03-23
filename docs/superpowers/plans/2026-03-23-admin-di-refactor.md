# Admin Route DI Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace direct `get_engine()` imports and manual `get_admin_user()` calls in admin route handlers with a single `Depends(get_admin_context)` that provides typed `AdminContext`.

**Architecture:** Custom exceptions (`AdminAuthRequired`, `DatabaseUnavailable`) let dependencies abort requests with proper HTML responses (redirect / 503 page). `AdminContext` dataclass bundles `user`, `engine`, `request`. Exception handlers registered on the app return the appropriate response type.

**Tech Stack:** FastAPI `Depends()`, Starlette exception handlers, Jinja2 templates

**Behavior change note:** The current handlers use try/except around `get_engine()` and query calls, silently returning empty data when the DB is unavailable. After this refactor:
- **Engine not configured** (`app.state.engine is None`): returns a 503 HTML error page via `DatabaseUnavailable` exception — intentional upgrade from silent degradation.
- **Transient query failures** (engine exists but query throws): propagate as 500 errors. This is also intentional — transient DB errors should surface, not be silently swallowed. The old try/except masked real problems.

---

### Task 1: Custom exceptions and dependency functions

**Files:**
- Modify: `src/address_validator/routers/admin/deps.py`
- Test: `tests/unit/test_admin_deps.py` (create)

- [ ] **Step 1: Write tests for `get_admin_user`, `get_admin_context`, and exceptions**

Create `tests/unit/test_admin_deps.py`:

```python
"""Unit tests for admin dependency injection."""

import pytest
from fastapi import FastAPI, Request

from address_validator.routers.admin.deps import (
    AdminAuthRequired,
    AdminContext,
    AdminUser,
    DatabaseUnavailable,
    get_admin_context,
    get_admin_user,
)


def _make_request(app: FastAPI, headers: dict | None = None) -> Request:
    """Build a fake Request with the given headers."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/admin/",
        "query_string": b"",
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
        ],
        "app": app,
    }
    return Request(scope)


class TestGetAdminUser:
    def test_returns_admin_user_when_headers_present(self) -> None:
        app = FastAPI()
        req = _make_request(app, {
            "x-exedev-userid": "u1",
            "x-exedev-email": "a@b.com",
        })
        user = get_admin_user(req)
        assert isinstance(user, AdminUser)
        assert user.user_id == "u1"
        assert user.email == "a@b.com"

    def test_raises_auth_required_when_no_headers(self) -> None:
        app = FastAPI()
        req = _make_request(app, {})
        with pytest.raises(AdminAuthRequired) as exc_info:
            get_admin_user(req)
        assert "/__exe.dev/login" in exc_info.value.redirect_url

    def test_redirect_url_includes_current_path(self) -> None:
        app = FastAPI()
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/admin/audit/",
            "query_string": b"page=2",
            "headers": [],
            "app": app,
        }
        req = Request(scope)
        with pytest.raises(AdminAuthRequired) as exc_info:
            get_admin_user(req)
        assert "/admin/audit/" in exc_info.value.redirect_url
        assert "page=2" in exc_info.value.redirect_url


class TestGetAdminContext:
    def test_returns_context_with_user_and_engine(self) -> None:
        app = FastAPI()
        app.state.engine = "fake-engine"
        req = _make_request(app, {
            "x-exedev-userid": "u1",
            "x-exedev-email": "a@b.com",
        })
        ctx = get_admin_context(req)
        assert isinstance(ctx, AdminContext)
        assert ctx.user.user_id == "u1"
        assert ctx.engine == "fake-engine"
        assert ctx.request is req

    def test_auth_checked_before_engine(self) -> None:
        """Unauthenticated request raises AdminAuthRequired, not DatabaseUnavailable."""
        app = FastAPI()
        app.state.engine = None
        req = _make_request(app, {})
        with pytest.raises(AdminAuthRequired):
            get_admin_context(req)

    def test_raises_database_unavailable_when_no_engine(self) -> None:
        app = FastAPI()
        app.state.engine = None
        req = _make_request(app, {
            "x-exedev-userid": "u1",
            "x-exedev-email": "a@b.com",
        })
        with pytest.raises(DatabaseUnavailable) as exc_info:
            get_admin_context(req)
        assert exc_info.value.user.user_id == "u1"

    def test_raises_database_unavailable_when_no_state_attr(self) -> None:
        app = FastAPI()
        req = _make_request(app, {
            "x-exedev-userid": "u1",
            "x-exedev-email": "a@b.com",
        })
        with pytest.raises(DatabaseUnavailable):
            get_admin_context(req)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_admin_deps.py -v --no-cov -x`
Expected: ImportError / FAIL (symbols don't exist yet)

- [ ] **Step 3: Implement deps.py**

Replace `src/address_validator/routers/admin/deps.py` with:

```python
"""Admin dashboard dependency injection.

Provides ``AdminContext`` via FastAPI ``Depends()`` — bundles authenticated
user, database engine, and request into a single typed dependency.

Custom exceptions (``AdminAuthRequired``, ``DatabaseUnavailable``) let
dependencies abort requests; exception handlers in ``main.py`` convert them
to the appropriate HTML responses (302 redirect / 503 error page).
"""

from dataclasses import dataclass
from urllib.parse import quote

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncEngine


# ---------------------------------------------------------------------------
# Custom exceptions — caught by app-level exception handlers in main.py
# ---------------------------------------------------------------------------


class AdminAuthRequired(Exception):
    """User is not authenticated via exe.dev proxy headers."""

    def __init__(self, redirect_url: str) -> None:
        self.redirect_url = redirect_url


class DatabaseUnavailable(Exception):
    """Database engine is not configured or not initialised."""

    def __init__(self, user: "AdminUser") -> None:
        self.user = user


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AdminUser:
    """Authenticated admin user from exe.dev proxy headers."""

    user_id: str
    email: str


@dataclass(frozen=True)
class AdminContext:
    """Composite dependency injected into every admin route handler."""

    user: AdminUser
    engine: AsyncEngine
    request: Request


# ---------------------------------------------------------------------------
# Dependency functions (used with FastAPI Depends())
# ---------------------------------------------------------------------------


def get_admin_user(request: Request) -> AdminUser:
    """Read exe.dev proxy headers; raise ``AdminAuthRequired`` if absent."""
    user_id = request.headers.get("X-ExeDev-UserID")
    email = request.headers.get("X-ExeDev-Email")

    if not user_id or not email:
        next_url = str(request.url.path)
        if request.url.query:
            next_url = f"{next_url}?{request.url.query}"
        raise AdminAuthRequired(
            redirect_url=f"/__exe.dev/login?redirect={quote(next_url)}",
        )

    return AdminUser(user_id=user_id, email=email)


def get_admin_context(request: Request) -> AdminContext:
    """Composite dependency — auth first, then engine.

    Raises ``AdminAuthRequired`` if unauthenticated.
    Raises ``DatabaseUnavailable`` if engine is None (carries the user for
    the 503 template).
    """
    user = get_admin_user(request)
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        raise DatabaseUnavailable(user=user)
    return AdminContext(user=user, engine=engine, request=request)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_admin_deps.py -v --no-cov -x`
Expected: All PASS

- [ ] **Step 5: Run linter**

Run: `uv run ruff check src/address_validator/routers/admin/deps.py tests/unit/test_admin_deps.py`
Expected: Clean

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/routers/admin/deps.py tests/unit/test_admin_deps.py
git commit -m "#63 feat: add AdminContext DI with custom exceptions"
```

---

### Task 2: 503 error template and exception handlers

**Files:**
- Create: `src/address_validator/templates/admin/error_503.html`
- Modify: `src/address_validator/main.py`
- Modify: `tests/unit/test_admin_views.py` (add 503 integration tests)

- [ ] **Step 1: Write integration tests for 503 behavior**

Add to `tests/unit/test_admin_views.py`:

```python
def test_admin_dashboard_503_when_no_engine(client: TestClient, admin_headers: dict) -> None:
    """Authenticated request returns 503 when database engine is None."""
    original = getattr(client.app.state, "engine", None)  # type: ignore[union-attr]
    try:
        client.app.state.engine = None  # type: ignore[union-attr]
        response = client.get("/admin/", headers=admin_headers)
        assert response.status_code == 503
        assert "Database Not Available" in response.text
    finally:
        client.app.state.engine = original  # type: ignore[union-attr]


def test_admin_audit_503_when_no_engine(client: TestClient, admin_headers: dict) -> None:
    """Audit view returns 503 when database engine is None."""
    original = getattr(client.app.state, "engine", None)  # type: ignore[union-attr]
    try:
        client.app.state.engine = None  # type: ignore[union-attr]
        response = client.get("/admin/audit/", headers=admin_headers)
        assert response.status_code == 503
    finally:
        client.app.state.engine = original  # type: ignore[union-attr]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_admin_views.py::test_admin_dashboard_503_when_no_engine tests/unit/test_admin_views.py::test_admin_audit_503_when_no_engine -v --no-cov -x`
Expected: FAIL (handlers still use try/except internally and return 200 with empty data)

- [ ] **Step 3: Create the 503 template**

Create `src/address_validator/templates/admin/error_503.html`:

```html
{% extends "admin/base.html" %}
{% block title %}Service Unavailable{% endblock %}
{% block content %}
<div class="max-w-lg mx-auto mt-16 text-center">
    <h1 class="text-2xl font-bold text-gray-800 dark:text-gray-100 mb-4">Database Not Available</h1>
    <p class="text-gray-600 dark:text-gray-400 mb-6">
        The database is not configured or is currently unreachable.
        Admin features that require data are unavailable.
    </p>
    <a href="/admin/"
       class="inline-block px-4 py-2 bg-co-purple text-white rounded hover:bg-co-purple-700 focus:outline-none focus:ring-2 focus:ring-co-purple-700 focus:ring-offset-1 dark:ring-offset-gray-900 min-h-[44px]">
        Back to Dashboard
    </a>
</div>
{% endblock %}
```

- [ ] **Step 4: Add exception handlers to main.py**

Add imports near the top of `main.py` (with existing imports):

```python
from address_validator.routers.admin._config import get_css_version, templates as admin_templates
from address_validator.routers.admin.deps import AdminAuthRequired, DatabaseUnavailable
```

Add after the `app = FastAPI(...)` block but before middleware registration:

```python
@app.exception_handler(AdminAuthRequired)
async def _admin_auth_redirect(request: Request, exc: AdminAuthRequired) -> Response:
    return RedirectResponse(url=exc.redirect_url, status_code=302)


@app.exception_handler(DatabaseUnavailable)
async def _admin_db_unavailable(request: Request, exc: DatabaseUnavailable) -> Response:
    return admin_templates.TemplateResponse(
        "admin/error_503.html",
        {
            "request": request,
            "user": exc.user,
            "active_nav": "",
            "css_version": get_css_version(),
        },
        status_code=503,
    )
```

Note: import as `admin_templates` to avoid shadowing if there's ever a name conflict. `DatabaseUnavailable` carries `exc.user` because auth runs before the engine check in `get_admin_context`.

- [ ] **Step 5: Run 503 tests — still expect failure**

Run: `uv run pytest tests/unit/test_admin_views.py::test_admin_dashboard_503_when_no_engine -v --no-cov -x`
Expected: FAIL — route handlers still call `get_engine()` directly, so `DatabaseUnavailable` is never raised. The exception handlers are registered but won't fire until Task 3 migrates the handlers.

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/templates/admin/error_503.html src/address_validator/main.py tests/unit/test_admin_views.py
git commit -m "#63 feat: add 503 template and exception handlers"
```

---

### Task 3: Migrate route handlers

**Files:**
- Modify: `src/address_validator/routers/admin/dashboard.py`
- Modify: `src/address_validator/routers/admin/audit_views.py`
- Modify: `src/address_validator/routers/admin/endpoints.py`
- Modify: `src/address_validator/routers/admin/providers.py`

- [ ] **Step 1: Migrate dashboard.py**

Replace the full file contents:

```python
"""Admin dashboard landing page."""

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from address_validator.routers.admin._config import get_css_version, get_quota_info, templates
from address_validator.routers.admin._sparkline import SPARKLINE_CONFIG, build_sparkline_svg
from address_validator.routers.admin.deps import AdminContext, get_admin_context
from address_validator.routers.admin.queries import get_dashboard_stats, get_sparkline_data

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
    return templates.TemplateResponse(
        "admin/dashboard.html",
        {
            "request": ctx.request,
            "user": ctx.user,
            "active_nav": "dashboard",
            "css_version": get_css_version(),
            "stats": stats,
            "quota": get_quota_info(ctx.request),
            "sparkline_svgs": sparkline_svgs,
        },
    )
```

Key changes:
- Removed `from address_validator.db import engine as db_engine`
- Removed manual `get_admin_user()` call + `isinstance` check
- Removed try/except blocks around `get_engine()` and query calls
- Added `ctx: AdminContext = Depends(get_admin_context)`
- No `Request` parameter — uses `ctx.request` (no HTMX logic in this handler)

- [ ] **Step 2: Migrate audit_views.py**

Replace the full file contents:

```python
"""Audit log view — paginated, filterable audit trail."""

import math

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from address_validator.routers.admin._config import get_css_version, templates
from address_validator.routers.admin.deps import AdminContext, get_admin_context
from address_validator.routers.admin.queries import get_audit_rows

router = APIRouter(prefix="/audit")

_PER_PAGE = 50


@router.get("/", response_class=HTMLResponse, response_model=None)
async def audit_list(
    request: Request,
    page: int = Query(1, ge=1),
    client_ip: str | None = Query(None),
    endpoint: str | None = Query(None),
    status_min: int | None = Query(None, ge=100, le=599),
    ctx: AdminContext = Depends(get_admin_context),
) -> Response:
    rows, total = await get_audit_rows(
        ctx.engine,
        page=page,
        per_page=_PER_PAGE,
        endpoint=endpoint,
        client_ip=client_ip,
        status_min=status_min,
    )

    total_pages = max(1, math.ceil(total / _PER_PAGE))
    filters = {"client_ip": client_ip, "endpoint": endpoint, "status_min": status_min}

    # HTMX partial — return just the rows (skip for boosted nav)
    if request.headers.get("HX-Request") and not request.headers.get("HX-Boosted"):
        return templates.TemplateResponse(
            "admin/audit/_rows.html",
            {"request": request, "rows": rows},
        )

    return templates.TemplateResponse(
        "admin/audit/list.html",
        {
            "request": request,
            "user": ctx.user,
            "active_nav": "audit",
            "css_version": get_css_version(),
            "rows": rows,
            "page": page,
            "total_pages": total_pages,
            "filters": filters,
        },
    )
```

Note: `audit_views.py`, `endpoints.py`, and `providers.py` keep `request: Request` as a parameter because they check HTMX headers directly on `request`. `dashboard.py` uses `ctx.request` instead since it has no HTMX logic.

- [ ] **Step 3: Migrate endpoints.py**

Replace the full file contents:

```python
"""Per-endpoint detail view."""

import math

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
    )

    total_pages = max(1, math.ceil(total / _PER_PAGE))
    filters = {"client_ip": client_ip}

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

- [ ] **Step 4: Migrate providers.py**

Replace the full file contents:

```python
"""Per-provider detail view."""

import math

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
    )

    total_pages = max(1, math.ceil(total / _PER_PAGE))
    filters = {"client_ip": client_ip}

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

- [ ] **Step 5: Run all admin tests**

Run: `uv run pytest tests/unit/test_admin_views.py tests/unit/test_admin_deps.py -v --no-cov -x`
Expected: All PASS — including the 503 tests from Task 2 (which can now fire because handlers use `Depends(get_admin_context)`)

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest --no-cov -x`
Expected: All PASS

- [ ] **Step 7: Lint**

Run: `uv run ruff check src/address_validator/routers/admin/ tests/unit/test_admin_deps.py tests/unit/test_admin_views.py`
Expected: Clean

- [ ] **Step 8: Commit**

```bash
git add src/address_validator/routers/admin/dashboard.py src/address_validator/routers/admin/audit_views.py src/address_validator/routers/admin/endpoints.py src/address_validator/routers/admin/providers.py
git commit -m "#63 refactor: migrate admin handlers to Depends(get_admin_context)"
```

---

### Task 4: Update AGENTS.md sensitive-areas

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Update the sensitive-areas table**

Add a new row for `deps.py`:

```markdown
| `src/address_validator/routers/admin/deps.py` | `AdminContext` composite DI — `get_admin_context` is the single entry point for all admin routes; `AdminAuthRequired` and `DatabaseUnavailable` exceptions are caught by app-level handlers in `main.py`; removing or weakening auth check here silently drops auth for all admin views |
```

- [ ] **Step 2: Search for and remove stale references**

Search AGENTS.md for any references to admin routes importing `cache_db` or `get_engine()` directly. Update the Architecture section if it mentions admin route DB access patterns.

- [ ] **Step 3: Lint and commit**

```bash
git add AGENTS.md
git commit -m "#63 docs: update sensitive-areas for admin DI refactor"
```

---

### Task 5: Final verification

- [ ] **Step 1: Full test suite with coverage**

Run: `uv run pytest`
Expected: All PASS, coverage >= 80%

- [ ] **Step 2: Lint entire project**

Run: `uv run ruff check .`
Expected: Clean

- [ ] **Step 3: Manual smoke test**

Run: `curl -s -o /dev/null -w '%{http_code}' -H 'X-ExeDev-UserID: test' -H 'X-ExeDev-Email: test@test.com' http://localhost:8000/admin/`
Expected: 200 (if DB configured) or 503 (if not)

Run: `curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/admin/`
Expected: 302 (redirect to login)
