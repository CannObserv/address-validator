# Agent Guidance — Address Validator

**Output style:** Terse. Bullets > prose. Sacrifice grammar while preserving clarity. No trailing summaries.

## What this project is

FastAPI service — parses and standardizes US (USPS Pub 28) and Canadian (libpostal sidecar) addresses. systemd+uvicorn on port 8000. libpostal sidecar on port 4400 (pelias/libpostal-service Docker, libpostal.service).

## Architecture

```
# All source modules live under src/address_validator/

HTTP request
 └─ middleware/api_version.py appends API-Version: 1 or 2 header on /api/v1/ and /api/v2/ responses
 └─ middleware/request_id.py  generates ULID, sets ContextVar, echoes X-Request-ID header
 └─ middleware/audit.py       records every API request to audit_log (fire-and-forget)
 └─ routers/v1/               thin handlers, validation, error handling; USPS Pub 28 key vocabulary
     ├─ parse            →   services/parser.py        usaddress wrapper + post-parse recovery
     ├─ standardize      →   services/standardizer.py  Pub 28 abbrev tables from usps_data/
     ├─ validate         →   parse → standardize → services/validation/
                                 config.py         pydantic-settings models (USPSConfig, GoogleConfig, ValidationConfig) + validate_config()
                                 registry.py       ProviderRegistry class — provider lifecycle, quota info, no globals
                                 null_provider.py  default no-op
                                 usps_provider.py  OAuth2 + quota guard; DPV → status
                                 google_provider.py  ADC; lat/lng; DPV → status; non-US via _map_response_international
                                 chain_provider.py   ordered fallback across providers
                                 _rate_limit.py      QuotaGuard, QuotaWindow + retry helpers
     └─ countries        →   services/country_format.py  i18naddress → CountryFormatResponse; label lookup tables
 └─ routers/v2/               ISO 19160-4 surface; component_profile query param (iso-19160-4 default, usps-pub28, canada-post)
     ├─ parse            →   US: usaddress pipeline; CA: libpostal sidecar via LibpostalClient; component_profile controls output key vocabulary
     ├─ standardize      →   US: ISO keys via USPS pipeline; CA: ISO keys via _standardize_ca() (canada-post spec); enabled via check_country_v2
     ├─ validate         →   US: same as v1; CA raw string: libpostal parse → _standardize_ca() → provider; other non-US: components-only; _v1_to_v2() drops lat/lng
     └─ countries        →   same service as v1 (CountryFormatResponseV2 adds api_version field)
 └─ routers/admin/            admin dashboard (Jinja2 + HTMX, exe.dev auth)
     ├─ router.py             top-level /admin router
     ├─ deps.py               AdminUser from exe.dev proxy headers
     ├─ _config.py            shared templates, CSS version, quota helpers
     ├─ _sparkline.py         inline SVG sparkline builder (colors, trend labels)
     ├─ dashboard.py          GET /admin/ — landing page
     ├─ audit_views.py        GET /admin/audit/ — audit log with filters
     ├─ endpoints.py          GET /admin/endpoints/{name}
     ├─ providers.py          GET /admin/providers/{name}
     └─ queries/              SQLAlchemy Core query helpers for dashboard views
         ├─ _shared.py        shared expressions, helpers, and time boundaries
         ├─ audit.py          get_audit_rows
         ├─ dashboard.py      get_dashboard_stats, get_sparkline_data
         ├─ endpoint.py       get_endpoint_stats
         └─ provider.py       get_provider_stats

db/tables.py        SQLAlchemy Core Table definitions (audit_log, audit_daily_stats, model_training_candidates)
db/engine.py        AsyncEngine singleton — init_engine(), get_engine(), close_engine(), Alembic migrations
models.py           API contract source of truth
core/address_format.py  build_validated_string — canonical single-line address string builder; shared across validation providers and the router layer
services/spec.py                 ISO 19160-4 spec identifiers (ISO_19160_4_SPEC, ISO_19160_4_SPEC_VERSION); used by v2 routers; USPS Pub 28 identifiers remain in usps_data/spec.py
services/component_profiles.py  ISO 19160-4 ↔ USPS Pub28 key translation; translate_components() / translate_components_to_iso(); VALID_PROFILES frozenset; identity pass-through for unknown profiles/keys
services/libpostal_client.py  async httpx client for pelias/libpostal-service (port 4400); maps libpostal tags → ISO 19160-4; LibpostalUnavailableError on failure; aclose() in lifespan
services/street_splitter.py  bilingual street component splitter; decomposes libpostal road token into thoroughfare ISO elements; English trailing-type + French leading-type + CA directionals
canada_post_data/directionals.py  bilingual EN/FR directional lookup (CA_DIRECTIONAL_MAP) for Canadian addresses; used by street_splitter
canada_post_data/provinces.py  Canada Post province/territory table (PROVINCE_MAP): full names + abbreviations → 2-letter abbreviation; used by _standardize_ca()
canada_post_data/suffixes.py   Canada Post street type table (CA_SUFFIX_MAP): bilingual EN/FR suffix → standard abbreviation; used by _standardize_ca()
canada_post_data/spec.py       CANADA_POST_SPEC / CANADA_POST_SPEC_VERSION — tags CA ComponentSet responses; spec="canada-post", spec_version="2025"
services/country_format.py  maps i18naddress ValidationRules → CountryFormatResponse; GET /api/v1/countries/{code}/format
services/audit.py   audit ContextVars + write_audit_row (fail-open DB insert)
services/training_candidates.py  training ContextVars + write_training_candidate (fail-open DB insert)
usps_data/          Pub 28 lookup tables (suffixes, directionals, states, units)
usps_data/spec.py   USPS_PUB28_SPEC* — tags every ComponentSet response
routers/v1/core.py  VALID_ISO2, SUPPORTED_COUNTRIES (US only), SUPPORTED_COUNTRIES_V2 (US+CA), APIError, check_country(), check_country_v2()
logging_filter.py   RequestIdFilter — injects request_id into every LogRecord via root logger
templates/admin/    Jinja2 templates (base, dashboard, audit, endpoints, providers); _thead.html + _rows.html shared partials
static/admin/css/   Tailwind CSS (input.css + built tailwind.css)
static/admin/js/    ES modules — theme.js (dark mode), nav.js (hamburger)
tests/js/           Vitest + jsdom tests for admin JS (npm test)
package.json        Node dev-only deps (vitest, jsdom); type: "module"
vitest.config.js    Vitest config — jsdom environment, tests/js/ scope
static/admin/images/ Cannabis Observer brand SVGs

scripts/backfill_pattern_key.py  One-time backfill: populate NULL pattern_key on audit_log validate rows
scripts/model/       Training pipeline scripts (identify, label, train, test_model, deploy, performance, contribute)
skills/train-model/  /train-model skill — interactive 7-step pipeline orchestration
training/sessions/   Per-session training artifacts (timestamped dirs)
training/upstream/   Upstream usaddress training data (labeled.xml)
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
- Key at `/etc/address-validator/.env` (mode 640); loaded via `EnvironmentFile=` in systemd unit
- Open routes: `GET /`, `/docs`, `/redoc`, `/openapi.json`, `GET /api/v1/health`, `GET /api/v2/health`
- `GET /api/v1/health` returns `{"status": "ok"|"degraded", "api_version": "1", "database": "ok"|"error"|"unconfigured"}`; HTTP 503 when `status == "degraded"` (DB unreachable); `database == "unconfigured"` when no DSN is configured
- `GET /api/v2/health` returns the same shape plus `"libpostal": "ok"|"unavailable"`; libpostal unavailability does NOT affect `status` or HTTP status code
- Tests: `conftest.py` sets `API_KEY` via `os.environ.setdefault` — value is read by the lifespan startup hook when `TestClient` first starts the app
- Google provider uses Application Default Credentials (ADC) — no API key. Required IAM roles: `roles/addressvalidation.user`, `roles/cloudquotas.viewer`, `roles/monitoring.viewer`
- Admin dashboard (`/admin/*`) requires exe.dev proxy auth (`X-ExeDev-UserID`, `X-ExeDev-Email`); any authenticated user is admin

## Logging

No PII at INFO+. Address content never in log messages at INFO or above. See `docs/LOGGING.md` for event/level table.

## Validation provider

Env vars in `/etc/address-validator/.env`:

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
| `AUDIT_RETENTION_DAYS` | non-negative int | `90` |
| `AUDIT_ARCHIVE_BUCKET` | GCS bucket name | — (required for archival) |
| `AUDIT_ARCHIVE_PREFIX` | string | `audit/` |
| `CUSTOM_MODEL_PATH` | absolute path to `.crfsuite` model file | — (unset = bundled usaddress model) |

See `docs/VALIDATION-PROVIDERS.md` for DPV code mapping and provider details.

## Deployment

- systemd unit: `/etc/systemd/system/address-validator.service` (repo copy is canonical)
- Env file: `/etc/address-validator/.env`
- Restart: `sudo systemctl restart address-validator`
- Logs: `journalctl -u address-validator -f`
- Re-install unit: `sudo cp address-validator.service /etc/systemd/system/ && sudo systemctl daemon-reload`
- Pre-commit: `uv run pre-commit install` (ruff + Tailwind CSS build)
- Backfill audit log: `source /etc/address-validator/.env && uv run python scripts/backfill_audit_log.py`
- Archive audit log: `source /etc/address-validator/.env && uv run python scripts/archive_audit.py`
- Backfill rollups: `source /etc/address-validator/.env && uv run python scripts/archive_audit.py --backfill`
- Backfill pattern_key: `source /etc/address-validator/.env && uv run python scripts/backfill_pattern_key.py` (dry-run; add `--apply`)
- Install timer: `sudo cp audit-archive.service audit-archive.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now audit-archive.timer`

## Infrastructure

Single-VM dev+prod model ([exe.dev](https://exe.dev)):
- Port 8000 = systemd production service (main worktree) — **never** start uvicorn manually on this port
- Port 8001 = dev server (active git worktree, `--reload`)
- exe.dev proxy: dev server accessible at `https://address-validator.exe.xyz:8001/`
- All development work happens on git worktrees — never modify the main worktree directly
- Standard workflow: `/brainstorming` → design doc → worktree → implement → PR → merge → clean up worktree

## Server lifecycle

| After… | Do this |
|---|---|
| Code change (no env/service) | `sudo systemctl restart address-validator` |
| Env var change | Edit `/etc/address-validator/.env`, then restart |
| Service unit change | `sudo cp address-validator.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart address-validator` |
| New worktree created | Kill any dev server on 8001 (`lsof -ti:8001 \| xargs kill 2>/dev/null`), then start from new worktree with `--reload` |
| Dev/test iteration | Dev server on 8001 with `--reload` auto-picks up changes |
| Worktree finished | Kill dev server on 8001, delete worktree |
| Stale process suspected | `ps aux \| grep uvicorn` — kill anything not PID from `systemctl show address-validator -p MainPID` |

## Environment

| File | Contents | Loaded by |
|---|---|---|
| `/etc/address-validator/.env` | Production secrets (`API_KEY`, DSN, provider creds, `CUSTOM_MODEL_PATH`) | systemd (required) |
| `/home/exedev/address-validator/.env` | Dev/agent secrets (`GH_TOKEN`) | systemd (optional with `-` prefix), manual `export` |

## Testing and linting

```
uv run pytest                   # all tests + coverage
uv run pytest --no-cov -x      # fast, stop on first failure
npm test                        # admin JS tests (vitest + jsdom)
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

PAT in `.env` (project root) as `GH_TOKEN`:

```bash
export GH_TOKEN=$(grep GH_TOKEN .env | cut -d= -f2)
```

## Sensitive areas

| File/Module | Risk |
|---|---|
| `src/address_validator/routers/v1/parse.py`, `standardize.py` | Route handlers MUST be `async def` — sync `def` routes run in a threadpool via `run_in_threadpool()`, which copies the contextvars context; ContextVar writes (e.g. `set_candidate_data`) inside the copy are invisible to the outer ASGI audit middleware. Changing these back to `def` silently breaks training candidate collection. |
| `src/address_validator/services/parser.py` | `parse_address()` is now `async` — dispatches to libpostal for CA, usaddress for US; `_parse()` remains sync; all callers must `await parse_address()` |
| `src/address_validator/services/parser.py` pre-processing | Regex strips parens before `usaddress` — changes affect US parsing |
| `src/address_validator/services/parser.py` post-parse recovery | `_recover_*` and vocabulary sets — affect US component assignment |
| `src/address_validator/usps_data/` tables | Verify against USPS Pub 28 before editing |
| `src/address_validator/services/standardizer.py` `_get()` | Every component value flows through this; changes cascade everywhere |
| `src/address_validator/models.py` | Breaking API change if field names/types change |
| `src/address_validator/models.py` `AddressInputMixin` | Single enforcement point for address/components input validation across all endpoints — removing or weakening the `model_validator` silently removes the 422 guard for both `/standardize` and `/validate` |
| `src/address_validator/usps_data/spec.py` | `USPS_PUB28_SPEC*` tags every response |
| `src/address_validator/auth.py` | API key read from `app.state.api_key` (set by lifespan); raises 503 when `API_KEY` unset — module is importable without the env var |
| `src/address_validator/services/validation/config.py` | `validate_config()` is called from the lifespan startup hook and raises `ValueError` on misconfiguration; pydantic-settings validators enforce business rules — changes affect all env-var parsing |
| `src/address_validator/services/validation/registry.py` | `ProviderRegistry` owns provider lifecycle — `_build_google_provider` mixes credential resolution, quota discovery, monitoring, and reconciliation wiring; `get_quota_info()` reads quota state via public `provider.client.quota_guard` API; instance stored on `app.state.registry` |
| `src/address_validator/db/engine.py` | `AsyncEngine` singleton; `init_engine()` (lifespan) creates engine + runs Alembic; `get_engine()` is sync, raises pre-init — schema changes go through `alembic/versions/` |
| `src/address_validator/services/validation/cache_provider.py` | Key hash changes (`_make_pattern_key`, `_make_canonical_key`) silently orphan all existing cache entries; `validated_at` is the TTL anchor — a schema or backfill change to this column silently breaks expiry for all rows; `except Exception` blocks in `validate()` are intentional fail-open behavior — do not narrow to a specific exception type; queries use Core Table expressions from `db/tables.py`; JSONB columns require `model_dump()`/`model_validate()` (not JSON string variants); on the cache-miss path `set_audit_context(pattern_key=...)` and `_register_query_pattern()` are called **before** `_inner.validate()` so that rate-limited (429) requests still produce a joinable `query_patterns` row with `raw_input` and carry `pattern_key` in the audit log; `query_patterns.canonical_key` is nullable — `_lookup` must guard for NULL before the orphan-delete path (NULL means partial registration, not an orphan); `_store()` ON CONFLICT back-fills both `canonical_key` (when NULL from eager registration) and `raw_input` (when NULL) via `coalesce`, never overwriting non-NULL values |
| `src/address_validator/services/validation/chain_provider.py` | Catches `ProviderRateLimitedError`, `ProviderAtCapacityError`, and `ProviderBadRequestError` — other exceptions propagate immediately without trying further providers; when all providers fail, transient errors (rate-limited/at-capacity) take precedence over bad-request so callers can retry; only raises `ProviderBadRequestError("all")` when *every* provider rejected the input |
| `src/address_validator/services/validation/google_provider.py` | Passes `country=std.country` to `client.validate_address()`; reads `status` from `raw["status"]` (pre-mapped by client — do not re-derive from DPV); US results use `USPS_PUB28_SPEC`/`USPS_PUB28_SPEC_VERSION`, non-US use `spec="raw"`/`spec_version="1"` — these branches must stay in sync with `google_client._map_response` vs `_map_response_international` |
| `src/address_validator/services/validation/_rate_limit.py` | `QuotaGuard` and `QuotaWindow` — `acquire()` holds the single lock across all windows; changes to the refill/consume logic affect every provider; `FixedResetQuotaWindow` is Google-specific — daily window resets at midnight PT (not rolling 86400 s); `adjust_tokens()` is called by quota reconciliation — only adjusts downward; `get_daily_quota_state()` assumes daily window is at index 1 (`_DAILY_WINDOW_INDEX`) — must stay in sync with window construction order in `registry._build_usps_provider` / `_build_google_provider` |
| `src/address_validator/services/validation/gcp_auth.py` | ADC credentials and project ID resolution; `get_credentials()` called at provider construction and during startup validation — credential errors surface as `ValueError` at boot |
| `src/address_validator/services/validation/gcp_quota_sync.py` | Quota discovery (Cloud Quotas API), usage monitoring (Cloud Monitoring API), and reconciliation loop; `reconcile_once()` only adjusts tokens downward — never grants above current window level; `run_reconciliation_loop()` runs as background asyncio task, cancelled on shutdown |
| `src/address_validator/middleware/request_id.py` | Pure ASGI middleware; runs on every request — `_request_id_var` ContextVar scoped per asyncio task; `reset(token)` in `finally` is load-bearing; must be outermost relative to audit so `get_request_id()` is set when the audit row is written |
| `src/address_validator/logging_filter.py` | Installed on root logger at import time in `main.py`; `addFilter` is idempotent only for the same instance — importing `main` twice would add a second filter |
| `src/address_validator/middleware/audit.py` | Pure ASGI middleware; runs on every API request; reads engine from `scope["app"].state.engine` (set during lifespan); `_background_tasks` set prevents GC of fire-and-forget writes; middleware ordering is load-bearing (must run inside request_id middleware); ContextVars set by the validation pipeline are read after `self.app()` returns — this only works because pure ASGI runs in one asyncio task (BaseHTTPMiddleware broke this); `_check_validate_invariants` overrides `error_detail` to `"audit_invariant_violated"` when `/api/v1/validate` or `/api/v2/validate` + 2xx has NULL audit fields — covers both versions via `_VALIDATE_ENDPOINTS` frozenset; changes to invariant logic silently affect audit row content |
| `src/address_validator/middleware/api_version.py` | Pure ASGI middleware; appends `API-Version: 1` header to `/api/v1/` responses; no state or ContextVars |
| `src/address_validator/db/tables.py` | SQLAlchemy Core Table definitions (audit + cache + training candidates) and shared constants — column changes here affect `services/audit.py`, `services/validation/cache_provider.py`, `routers/admin/queries/`, `scripts/archive_audit.py`, and `scripts/model/identify.py`; `validated_addresses` has CHECK constraint on `status` and JSONB columns (`components_json`, `warnings_json`); `model_training_candidates` has CHECK constraint on `status` and JSONB columns (`parsed_tokens`, `recovered_components`); must stay in sync with Alembic migrations |
| `src/address_validator/routers/admin/deps.py` | `AdminContext` composite DI — `get_admin_context` is the single entry point for all admin routes; `AdminAuthRequired` and `DatabaseUnavailable` exceptions are caught by app-level handlers in `main.py`; removing or weakening auth check here silently drops auth for all admin views |
| `src/address_validator/routers/admin/queries/` | SQLAlchemy Core query composition split into per-area modules; `_shared.py` provides `_ARCHIVED_DATE_GUARD`, `_from_archived`, and `_from_live` helpers — changes affect all archived-data queries; `_VS_CANONICAL_ORDER` defines the display order for validation statuses and must stay in sync with `VS_META` in `routers/admin/_config.py` — `VS_META` is the single source of truth for status labels/symbols/colors; templates derive `vs_order` from `vs_meta.keys()` |
| `src/address_validator/services/audit.py` | ContextVar reset in middleware is load-bearing; `except Exception` in `write_audit_row` is intentional fail-open; five ContextVars: `_audit_provider`, `_audit_validation_status`, `_audit_cache_hit`, `_audit_pattern_key`, `_audit_parse_type` — all reset together by `reset_audit_context()`; `set_audit_context(parse_type=...)` is set by the parser for every parse request; `set_audit_context(pattern_key=...)` uses an `if not None` guard (calling it with `None` is a no-op, not a clear) |
| `src/address_validator/services/training_candidates.py` | ContextVar reset in middleware is load-bearing (must be called at request start alongside `reset_audit_context()`); `except Exception` in `write_training_candidate` is intentional fail-open — do not narrow; `_candidate_data` ContextVar holds a dict or None; only the last `set_candidate_data()` call per request wins (post-parse overwrite is intentional) |
| `src/address_validator/services/libpostal_client.py` | `RuntimeError` is caught alongside httpx errors to handle closed-client state (e.g. during test teardown when multiple `TestClient` instances share `app.state`); `aclose()` must be called in lifespan shutdown; `health_check()` is non-fatal — returns False if sidecar is down; CA parse returns 503 when sidecar is unreachable |
| `src/address_validator/main.py` `_load_custom_model` | Swaps `usaddress.TAGGER` at boot if `CUSTOM_MODEL_PATH` env var is set; falls back silently to bundled model on missing path or failed load — changes to fallback behavior affect parse quality for all requests without surfacing a 5xx |
| `scripts/archive_audit.py` | Deletes audit_log rows after archival — verify GCS upload succeeded before deletion; `ON CONFLICT DO NOTHING` in aggregation is load-bearing for idempotency |

## Commit convention

With issue: `#<n> [type]: <description>`
Without: `[type]: <description>`
Multiple issues: `#12, #14 [type]: <description>`
Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`
