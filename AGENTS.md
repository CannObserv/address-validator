# Agent Guidance — Address Validator

**Output style:** Terse. Bullets > prose. Sacrifice grammar while preserving clarity. No trailing summaries.

## What this project is

FastAPI service — parses and standardizes US physical addresses per USPS Publication 28. systemd+uvicorn on port 8000.

## Architecture

```
HTTP request
 └─ routers/v1/              thin handlers, validation, error handling
     ├─ parse            →   services/parser.py        usaddress wrapper + post-parse recovery
     ├─ standardize      →   services/standardizer.py  Pub 28 abbrev tables from usps_data/
     └─ validate         →   parse → standardize → services/validation/
                                 factory.py        get_provider() + validate_config() read VALIDATION_PROVIDER env
                                 null_provider.py  default no-op
                                 usps_provider.py  OAuth2 + token bucket; DPV → status
                                 google_provider.py  API key; lat/lng; DPV → status
                                 chain_provider.py   ordered fallback across providers
                                 _rate_limit.py      shared TokenBucket + retry helpers

models.py           API contract source of truth
usps_data/          Pub 28 lookup tables (suffixes, directionals, states, units)
usps_data/spec.py   USPS_PUB28_SPEC* — tags every ComponentSet response
routers/v1/core.py  VALID_ISO2, SUPPORTED_COUNTRIES, APIError, check_country()
```

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
- Tests: `conftest.py` sets `API_KEY` before importing app — don't move the `from main import app` above the `setdefault` call

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
| `USPS_DAILY_LIMIT` | non-negative int | `10000` |
| `GOOGLE_API_KEY` | string | — |
| `GOOGLE_RATE_LIMIT_RPM` | float | `5` |
| `GOOGLE_DAILY_LIMIT` | positive int | `160` |
| `VALIDATION_LATENCY_BUDGET_S` | positive float | `1.0` |
| `VALIDATION_CACHE_DB` | path | `/var/lib/address-validator/validation_cache.db` |
| `VALIDATION_CACHE_TTL_DAYS` | non-negative int | `30` |

See `docs/VALIDATION-PROVIDERS.md` for DPV code mapping and provider details.

## Deployment

- systemd unit: `/etc/systemd/system/address-validator.service` (repo copy is canonical)
- Env file: `/etc/address-validator/env`
- Restart: `sudo systemctl restart address-validator`
- Logs: `journalctl -u address-validator -f`
- Re-install unit: `sudo cp address-validator.service /etc/systemd/system/ && sudo systemctl daemon-reload`

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
| `services/parser.py` pre-processing | Regex strips parens before `usaddress` — changes affect all parsing |
| `services/parser.py` post-parse recovery | `_recover_*` and vocabulary sets — affect component assignment |
| `usps_data/` tables | Verify against USPS Pub 28 before editing |
| `standardizer.py` `_get()` | Every component value flows through this; changes cascade everywhere |
| `models.py` | Breaking API change if field names/types change |
| `usps_data/spec.py` | `USPS_PUB28_SPEC*` tags every response |
| `auth.py` | API key read once at import time; raises 503 on first request if `API_KEY` unset — module is importable without the env var |
| `services/validation/factory.py` | Module-level singletons (`_usps_provider`, `_google_provider`, `_http_client`, `_caching_provider`) — reset to `None` in test fixtures; `validate_config()` is called from the lifespan startup hook and raises `ValueError` on misconfiguration; `_parse_latency_budget()`, `_parse_usps_config()`, `_parse_google_config()` — adding a new `QuotaWindow` or changing enforcement mode requires updating factory construction and `validate_config()` in sync |
| `services/validation/cache_db.py` | Schema changes require DB recreation — `IF NOT EXISTS` silently skips migrations |
| `services/validation/cache_provider.py` | Key hash changes (`_make_pattern_key`, `_make_canonical_key`) silently orphan all existing cache entries; `validated_at` is the TTL anchor — a schema or backfill change to this column silently breaks expiry for all rows; `except Exception` blocks in `validate()` are intentional fail-open behavior — do not narrow to a specific exception type |
| `services/validation/chain_provider.py` | Catches both `ProviderRateLimitedError` and `ProviderAtCapacityError` — other exceptions propagate immediately without trying further providers |
| `services/validation/_rate_limit.py` | `QuotaGuard` and `QuotaWindow` — `acquire()` holds the single lock across all windows; changes to the refill/consume logic affect every provider |

## Commit convention

With issue: `#<n> [type]: <description>`
Without: `[type]: <description>`
Multiple issues: `#12, #14 [type]: <description>`
Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`
