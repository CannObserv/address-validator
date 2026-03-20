# Admin Dashboard Design

**Date:** 2026-03-20

## Summary

Add an administrative dashboard to the address-validator service. Server-side rendered HTML (Jinja2 + HTMX + Tailwind CSS) mounted at `/admin/`, authenticated via exe.dev proxy headers. Includes audit logging middleware, historical backfill from journalctl, and views for stats, endpoints, and providers.

## Audit Log

### Table: `audit_log` (Alembic migration 004)

```
id                BIGSERIAL PRIMARY KEY
timestamp         TIMESTAMPTZ NOT NULL
request_id        TEXT NULL          -- ULID; null for backfilled rows
client_ip         TEXT NOT NULL
method            TEXT NOT NULL      -- GET, POST
endpoint          TEXT NOT NULL      -- /api/v1/validate, etc.
status_code       SMALLINT NOT NULL
latency_ms        INTEGER NULL       -- null for backfilled rows
provider          TEXT NULL          -- usps, google; null for non-validate
validation_status TEXT NULL          -- confirmed, not_confirmed, etc.
cache_hit         BOOLEAN NULL       -- null for non-validate or backfilled
error_detail      TEXT NULL          -- short message for 4xx/5xx
```

Indices:
- `idx_audit_ts` on `(timestamp DESC)`
- `idx_audit_ip` on `(client_ip, timestamp DESC)`
- `idx_audit_endpoint` on `(endpoint, timestamp DESC)`
- `idx_audit_provider` on `(provider, timestamp DESC) WHERE provider IS NOT NULL`

No `source` column — backfilled rows are self-evident from null `request_id`/`latency_ms`.

### Middleware: `middleware/audit.py`

- ASGI middleware, runs after `request_id` middleware
- Captures: start time, client IP (from `X-Forwarded-For`), method, path, status code, latency
- Validation-specific fields via `ContextVar` set in the validation service:
  - `_audit_provider`, `_audit_validation_status`, `_audit_cache_hit`
- After response: `asyncio.create_task(_write_audit_row(...))` — fire-and-forget async insert
- Skips non-API routes: `/`, `/docs`, `/redoc`, `/openapi.json`, `/admin/*`, `/static/*`
- Uses the same engine from `cache_db.get_engine()`

### Journal Backfill: `scripts/backfill_audit_log.py`

- Standalone script, run once: `uv run python scripts/backfill_audit_log.py`
- Reads `journalctl -u address-validator --output=json` via subprocess
- Regex extracts IP, method, path, status from `MESSAGE` field
- Timestamp from `__REALTIME_TIMESTAMP` (microseconds → datetime)
- Bulk insert with null `request_id`/`latency_ms`/`provider`/`validation_status`/`cache_hit`
- Idempotency: skip if any null-`request_id` rows exist in the journal's time range
- ~15 days of history available (since 2026-03-05)

## Dashboard Frontend

### Technology

- **Templating:** Jinja2, served by FastAPI
- **Interactivity:** HTMX 1.9 via CDN (`defer`)
- **CSS:** Tailwind CSS via standalone CLI binary (no Node.js)
- **Cache-busting:** git SHA injected as Jinja2 global `css_version`

### Authentication

- `get_admin_user(request)` reads `X-ExeDev-UserID` + `X-ExeDev-Email` proxy headers
- Missing headers → `RedirectResponse` to `/__exe.dev/login?redirect={current_path}`
- Returns `AdminUser(user_id=..., email=...)` dataclass
- Logout: POST form to `/__exe.dev/logout`
- No RBAC — any authenticated exe.dev user is admin

### Routing

```
src/address_validator/
  routers/admin/
    __init__.py
    router.py           -- top-level APIRouter(prefix="/admin"), mounts sub-routers
    deps.py             -- AdminUser dataclass, get_admin_user dependency
    dashboard.py        -- GET /admin/ — landing page with stat cards
    endpoints.py        -- GET /admin/endpoints/{name} — per-endpoint detail + filtered log
    providers.py        -- GET /admin/providers/{name} — per-provider detail + filtered log
    audit.py            -- GET /admin/audit/ — full audit log with filters
```

### Templates

```
src/address_validator/
  templates/admin/
    base.html           -- layout: topbar (user email, logout) + sidebar nav + main
    dashboard.html      -- stat cards
    audit/
      list.html         -- full audit log table with IP/endpoint/status filters
      _rows.html        -- HTMX partial for table body
    endpoints/
      detail.html       -- per-endpoint stats + filtered audit log
      _rows.html
    providers/
      detail.html       -- per-provider stats + filtered audit log
      _rows.html
```

Convention: `_`-prefixed files are HTMX partials (no `base.html` extension).

### Frontend Patterns

- **Pagination:** server-side, 50 rows/page
- **Filtering:** `hx-trigger="change"` on dropdowns, `hx-trigger="input delay:300ms"` on search
- **URL state:** `hx-push-url="true"` for bookmarkable filtered views
- **Partials:** server checks `HX-Request` header — returns partial or full page
- **Accessibility:** focus rings, `aria-live="polite"` on swap targets, min 44px touch targets, shape+color status cues
- **Responsive:** mobile-first, sticky thead, `overflow-x-auto` tables, grid breakpoints

### Landing Page Cards

| Card | Query |
|---|---|
| Requests today / week / all time | `COUNT(*)` on `audit_log` with timestamp filters |
| Cache hit rate | `COUNT(*) WHERE cache_hit` / `COUNT(*) WHERE endpoint = '/api/v1/validate'` |
| Provider quota usage | Read `QuotaGuard` state from provider singletons in `factory.py` |
| Error rate (4xx/5xx) | `COUNT(*) WHERE status_code >= 400` / total, today |

### Per-Endpoint View (`/admin/endpoints/{name}`)

- Stats: request count (today/week), avg latency, error rate, status code breakdown
- Filtered audit log table (pre-filtered to that endpoint)
- Endpoints: `parse`, `standardize`, `validate`

### Per-Provider View (`/admin/providers/{name}`)

- Stats: requests routed to provider, validation status breakdown, cache hit rate, current quota (tokens remaining / limit)
- Filtered audit log table (pre-filtered to that provider)
- Providers: `usps`, `google`

## Tailwind Build

### Scripts

- `scripts/download-tailwind.sh` — downloads platform-specific standalone CLI binary to `scripts/bin/tailwindcss`
- `scripts/build-css.sh` — runs `tailwindcss -c tailwind.config.js -i src/address_validator/static/admin/css/input.css -o src/address_validator/static/admin/css/tailwind.css --minify`
- `scripts/pre-commit-tailwind.sh` — calls build, auto-stages `tailwind.css` if changed

### Gitignore

- `scripts/bin/` — the downloaded CLI binary
- `src/address_validator/static/admin/css/tailwind.css` — **committed** (no build step required to run)

### Pre-commit

Add `pre-commit` as dev dependency. Create `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.15.4
    hooks:
      - id: ruff
        args: [--fix]
      - id: ruff-format
  - repo: local
    hooks:
      - id: build-tailwind-css
        name: Build Tailwind CSS
        language: system
        entry: scripts/pre-commit-tailwind.sh
        files: '^(src/address_validator/templates/.*\.html|src/address_validator/static/admin/css/input\.css|tailwind\.config\.js)$'
        pass_filenames: false
```

## Static File Serving

```python
app.mount("/static/admin", StaticFiles(directory="src/address_validator/static/admin"), name="admin-static")
```

Cache-control middleware adds `Cache-Control: public, max-age=31536000` to `/static/` responses.

## Not in Scope

- RBAC / role-based access
- Real-time WebSocket updates
- Export/download of audit data
- Dark mode
- Alerting or notifications
