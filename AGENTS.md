# Agent Guidance — Address Validator

**Output style:** Terse. Bullets > prose. Sacrifice grammar while preserving clarity. No trailing summaries.

## What this project is

FastAPI service — parses and standardizes US physical addresses per USPS Publication 28. systemd+uvicorn on port 8000.

## Architecture

```
# All source modules live under src/address_validator/

HTTP request
 └─ middleware/request_id.py  generates ULID, sets ContextVar, echoes X-Request-ID header
 └─ middleware/audit.py       records every API request to audit_log (fire-and-forget)
 └─ routers/v1/               thin handlers, validation, error handling
     ├─ parse            →   services/parser.py        usaddress wrapper + post-parse recovery
     ├─ standardize      →   services/standardizer.py  Pub 28 abbrev tables from usps_data/
     └─ validate         →   parse → standardize → services/validation/
                                 factory.py        get_provider() + validate_config() read VALIDATION_PROVIDER env
                                 null_provider.py  default no-op
                                 usps_provider.py  OAuth2 + quota guard; DPV → status
                                 google_provider.py  ADC; lat/lng; DPV → status
                                 chain_provider.py   ordered fallback across providers
                                 _rate_limit.py      QuotaGuard, QuotaWindow + retry helpers
 └─ routers/admin/            admin dashboard (Jinja2 + HTMX, exe.dev auth)
     ├─ router.py             top-level /admin router
     ├─ deps.py               AdminUser from exe.dev proxy headers
     ├─ _config.py            shared templates, CSS version, quota helpers
     ├─ _sparkline.py         inline SVG sparkline builder (colors, trend labels)
     ├─ dashboard.py          GET /admin/ — landing page
     ├─ audit_views.py        GET /admin/audit/ — audit log with filters
     ├─ endpoints.py          GET /admin/endpoints/{name}
     ├─ providers.py          GET /admin/providers/{name}
     └─ queries.py            SQL query helpers for dashboard views

models.py           API contract source of truth
services/audit.py   audit ContextVars + write_audit_row (fail-open DB insert)
usps_data/          Pub 28 lookup tables (suffixes, directionals, states, units)
usps_data/spec.py   USPS_PUB28_SPEC* — tags every ComponentSet response
routers/v1/core.py  VALID_ISO2, SUPPORTED_COUNTRIES, APIError, check_country()
logging_filter.py   RequestIdFilter — injects request_id into every LogRecord via root logger
templates/admin/    Jinja2 templates (base, dashboard, audit, endpoints, providers)
static/admin/css/   Tailwind CSS (input.css + built tailwind.css)
static/admin/js/    theme.js (dark mode), nav.js (hamburger)
static/admin/images/ Cannabis Observer brand SVGs
```

See also: `docs/STYLE.md` — visual design, a11y, responsive, and performance standards

## Key conventions

- `models.py` is the single source of truth for API contracts; field name/type changes are breaking
- Response models use geography-neutral names: `region`, `postal_code`
- `standardized` field: two-space separator between logical address lines (USPS single-line convention)
- Address input capped at 1000 chars (`Field(max_length=1000)`)
- `warnings: list[str]` on all response models; empty on clean input
- `components` takes precedence over `address` when both supplied
- All v1 request models accepting a country must inherit `CountryRequestMixin`

## Authentication

- All `/api/*` require `X-API-Key`; value from `API_KEY` env var
- Key at `/etc/address-validator/env` (mode 640); loaded via `EnvironmentFile=` in systemd unit
- Open routes: `GET /`, `/docs`, `/redoc`, `/openapi.json`
- Tests: `conftest.py` sets `API_KEY` before importing app — don't move the `from address_validator.main import app` above the `setdefault` call
- Google provider uses Application Default Credentials (ADC) — no API key. Required IAM roles: `roles/addressvalidation.user`, `roles/cloudquotas.viewer`, `roles/monitoring.viewer`
- Admin dashboard (`/admin/*`) requires exe.dev proxy auth (`X-ExeDev-UserID`, `X-ExeDev-Email`); any authenticated user is admin

## Logging

No PII at INFO+. Address content never in log messages at INFO or above. See `docs/LOGGING.md` for event/level table.

## Validation provider

Env vars in `/etc/address-validator/env`:

| Variable | Values | Default |
|---|---|---|
| `VALIDATION_PROVIDER` | `none`, `usps`, `google`, or comma-sep list e.g. `usps,google` | `none` |
| `USPS_CONSUMER_KEY` | string | — |
| `USPS_CONSUMER_SECRET` | string | — |
| `USPS_RATE_LIMIT_RPS` | float >= 1 | `5.0` |
| `USPS_DAILY_LIMIT` | positive int | `10000` |
| `GOOGLE_PROJECT_ID` | optional, auto-discovered from ADC | — |
| `GOOGLE_RATE_LIMIT_RPM` | positive int | `5` |
| `GOOGLE_DAILY_LIMIT` | positive int; optional override, auto-discovered from Cloud Quotas API | — |
| `GOOGLE_QUOTA_RECONCILE_INTERVAL_S` | positive float | `900` |
| `VALIDATION_LATENCY_BUDGET_S` | positive float | `1.0` |
| `VALIDATION_CACHE_DSN` | PostgreSQL DSN e.g. `postgresql+asyncpg://user:pass@localhost/address_validator` | — (required when provider is non-null) |
| `VALIDATION_CACHE_TTL_DAYS` | non-negative int | `30` |

See `docs/VALIDATION-PROVIDERS.md` for DPV code mapping and provider details.

## Deployment

- systemd unit: `/etc/systemd/system/address-validator.service` (repo copy is canonical)
- Env file: `/etc/address-validator/env`
- Restart: `sudo systemctl restart address-validator`
- Logs: `journalctl -u address-validator -f`
- Re-install unit: `sudo cp address-validator.service /etc/systemd/system/ && sudo systemctl daemon-reload`
- Pre-commit: `uv run pre-commit install` (ruff + Tailwind CSS build)
- Backfill audit log: `source /etc/address-validator/env && uv run python scripts/backfill_audit_log.py`

## Testing and linting

```
uv run pytest                   # all tests + coverage
uv run pytest --no-cov -x      # fast, stop on first failure
uv run ruff check .             # lint
uv run ruff check . --fix       # lint + autofix
uv run ruff format .            # format
```

Coverage floor: **80%** line + branch. Baseline ~93% — don't regress. Ruff must be clean before any commit.

## Common tasks

```
uv sync                         # install/refresh deps
uv add <package>                # add dep; commit pyproject.toml + uv.lock together
uv lock --upgrade && uv sync    # upgrade all deps; then update lower bounds
```

See `docs/DEPENDENCY-POLICY.md` for version pinning rules.

## GitHub CLI

PAT in `env` (project root) as `GITHUB_TOKEN`:

```bash
export GH_TOKEN=$(grep GITHUB_TOKEN env | cut -d= -f2)
```

## Sensitive areas

| File/Module | Risk |
|---|---|
| `src/address_validator/services/parser.py` pre-processing | Regex strips parens before `usaddress` — changes affect all parsing |
| `src/address_validator/services/parser.py` post-parse recovery | `_recover_*` and vocabulary sets — affect component assignment |
| `src/address_validator/usps_data/` tables | Verify against USPS Pub 28 before editing |
| `src/address_validator/services/standardizer.py` `_get()` | Every component value flows through this; changes cascade everywhere |
| `src/address_validator/models.py` | Breaking API change if field names/types change |
| `src/address_validator/usps_data/spec.py` | `USPS_PUB28_SPEC*` tags every response |
| `src/address_validator/auth.py` | API key read once at import time; raises 503 on first request if `API_KEY` unset — module is importable without the env var |
| `src/address_validator/services/validation/factory.py` | Module-level singletons (`_usps_provider`, `_google_provider`, `_http_client`, `_caching_provider`) — reset to `None` in test fixtures; `validate_config()` is called from the lifespan startup hook and raises `ValueError` on misconfiguration; `_parse_latency_budget()`, `_parse_usps_config()`, `_parse_google_config()` — adding a new `QuotaWindow` or changing enforcement mode requires updating factory construction and `validate_config()` in sync |
| `src/address_validator/services/validation/cache_db.py` | `AsyncEngine` singleton; `get_engine()` runs `alembic upgrade head` on first call — schema changes go through `alembic/versions/` migrations, not inline DDL |
| `src/address_validator/services/validation/cache_provider.py` | Key hash changes (`_make_pattern_key`, `_make_canonical_key`) silently orphan all existing cache entries; `validated_at` is the TTL anchor — a schema or backfill change to this column silently breaks expiry for all rows; `except Exception` blocks in `validate()` are intentional fail-open behavior — do not narrow to a specific exception type |
| `src/address_validator/services/validation/chain_provider.py` | Catches both `ProviderRateLimitedError` and `ProviderAtCapacityError` — other exceptions propagate immediately without trying further providers |
| `src/address_validator/services/validation/_rate_limit.py` | `QuotaGuard` and `QuotaWindow` — `acquire()` holds the single lock across all windows; changes to the refill/consume logic affect every provider; `FixedResetQuotaWindow` is Google-specific — daily window resets at midnight PT (not rolling 86400 s); `adjust_tokens()` is called by quota reconciliation — only adjusts downward |
| `src/address_validator/services/validation/gcp_auth.py` | ADC credentials and project ID resolution; `get_credentials()` called at provider construction and during startup validation — credential errors surface as `ValueError` at boot |
| `src/address_validator/services/validation/gcp_quota_sync.py` | Quota discovery (Cloud Quotas API), usage monitoring (Cloud Monitoring API), and reconciliation loop; `reconcile_once()` only adjusts tokens downward — never grants above current window level; `run_reconciliation_loop()` runs as background asyncio task, cancelled on shutdown |
| `src/address_validator/middleware/request_id.py` | Runs on every request — `_request_id_var` ContextVar scoped per asyncio task; `reset(token)` in `finally` is load-bearing; do not move the `set` call after `call_next` |
| `src/address_validator/logging_filter.py` | Installed on root logger at import time in `main.py`; `addFilter` is idempotent only for the same instance — importing `main` twice would add a second filter |
| `src/address_validator/middleware/audit.py` | Runs on every API request; `_background_tasks` set prevents GC of fire-and-forget writes; middleware ordering is load-bearing (must be innermost relative to request_id) |
| `src/address_validator/routers/admin/queries.py` | Raw SQL with f-string WHERE clause; conditions are hardcoded literals but pattern is fragile if extended carelessly |
| `src/address_validator/services/audit.py` | ContextVar reset in middleware is load-bearing; `except Exception` in `write_audit_row` is intentional fail-open |

## Commit convention

With issue: `#<n> [type]: <description>`
Without: `[type]: <description>`
Multiple issues: `#12, #14 [type]: <description>`
Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`
