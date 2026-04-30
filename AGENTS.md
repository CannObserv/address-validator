# Agent Guidance — Address Validator

**Output style:** Terse. Bullets > prose. Sacrifice grammar while preserving clarity. No trailing summaries.

## What this project is

FastAPI service — parses and standardizes US (USPS Pub 28) and Canadian (libpostal sidecar) addresses. systemd+uvicorn on port 8000. libpostal sidecar on port 4400 (pelias/libpostal-service Docker, `infra/libpostal.service`).

## Architecture

See `docs/ARCHITECTURE.md` for the full module map.

```
HTTP request
 └─ middleware: api_version → request_id → audit
 └─ routers/v1/   USPS Pub 28 vocabulary — parse, standardize, validate, countries
 └─ routers/v2/   ISO 19160-4 surface; component_profile param; CA via libpostal
 └─ routers/admin/  Jinja2 + HTMX dashboard (exe.dev auth)
 └─ services/validation/  provider pipeline (null/usps/google/chain + cache)
```

Key files: `models.py` (API contract) · `db/tables.py` (schema) · `core/countries.py` · `core/errors.py` · `services/validation/pipeline.py` (parse→std→provider)

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
- `/api/v1/health` → `{"status": "ok"|"degraded", "api_version": "1", "database": "ok"|"error"|"unconfigured"}`; HTTP 503 when degraded
- `/api/v2/health` → same shape plus `"libpostal": "ok"|"unavailable"`; libpostal state does NOT affect HTTP status
- Google provider uses ADC — no API key. IAM: `roles/addressvalidation.user`, `roles/cloudquotas.viewer`, `roles/monitoring.viewer`
- Admin (`/admin/*`) requires exe.dev proxy auth (`X-ExeDev-UserID`, `X-ExeDev-Email`)

## Logging

No PII at INFO+. Address content never in log messages at INFO or above. See `docs/LOGGING.md` for event/level table.

## Validation provider

Core env vars (see `docs/VALIDATION-PROVIDERS.md` for full reference):

| Variable | Values | Default |
|---|---|---|
| `VALIDATION_PROVIDER` | `none`, `usps`, `google`, comma-sep list | `none` |
| `VALIDATION_CACHE_DSN` | PostgreSQL DSN | — (required when non-null) |
| `VALIDATION_CACHE_TTL_DAYS` | non-negative int | `30` |
| `CUSTOM_MODEL_PATH` | path to `.crfsuite` file | — (bundled usaddress model) |

## Deployment

Quick ops (see `docs/DEPLOYMENT.md` for full reference):

- Restart: `sudo systemctl restart address-validator`
- Logs: `journalctl -u address-validator -f`
- Re-install unit: `sudo cp infra/address-validator.service /etc/systemd/system/ && sudo systemctl daemon-reload`
- Pre-commit hooks: `uv run pre-commit install`

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
| Service unit change | `sudo cp infra/address-validator.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart address-validator` |
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
uv run pytest --no-cov -m integration    # integration tests only
uv run pytest --no-cov -m "not integration"  # unit tests only (faster; coverage fails below 80% on partial runs)
npm test                        # admin JS tests (vitest + jsdom)
uv run ruff check .             # lint
uv run ruff check . --fix       # lint + autofix
uv run ruff format .            # format
```

Coverage floor: **80%** line + branch. Baseline ~93% — don't regress. Ruff must be clean before any commit.

**NEVER** source `/etc/address-validator/.env` before running tests. That file sets `VALIDATION_CACHE_DSN` to the production database; the audit middleware writes real rows on every `TestClient` request. `tests/conftest.py` sets `VALIDATION_CACHE_DSN` via `os.environ.setdefault` so no shell prep is needed for `uv run pytest`. See `.env.test` for standalone-script use.

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

Critical gotchas (see `docs/SENSITIVE-AREAS.md` for full per-module risk table):

- **Route handlers must be `async def`** — sync `def` runs in threadpool; ContextVar writes are invisible to the audit middleware, silently breaking training candidate collection
- **`AddressInputMixin.model_validator`** — sole 422 guard for address/components input across all endpoints; do not weaken
- **Middleware order is load-bearing** — `request_id` must wrap `audit`; `reset_audit_context()` + `reset_candidate_data()` must fire at request start
- **Cache key changes** (`_make_pattern_key`, `_make_canonical_key`) silently orphan all existing cache entries
- **`except Exception` in fail-open writes** — intentional in `write_audit_row`, `write_training_candidate`, `cache_provider.validate()`; do not narrow
- **`ALLOWED_TRANSITIONS`** in `training_batches.py` — single source of truth for batch status; all transitions go through it

## Skills

See `docs/SKILLS.md` for full descriptions. Key skills for development:

| Skill | When to use |
|---|---|
| `/brainstorming` | Before any new feature — design before code |
| `/writing-plans` | After brainstorming; before multi-step implementation |
| `/using-git-worktrees` | Every feature branch — isolated worktree on port 8001 |
| `/test-driven-development` | Before writing implementation code |
| `/systematic-debugging` | Any bug or unexpected test failure |
| `/verification-before-completion` | Before claiming done or opening a PR |
| `/reviewing-code-claude` | Code review — tiered findings, implements approved fixes |
| `/reviewing-architecture-claude` | Architecture review |
| `/shipping-work-claude` | Finalize — commit, push, close issues |
| `/train-model` | CRF model retraining pipeline |
| `/schedule` | Recurring or one-time background agents |
| `socraticode:codebase-exploration` | Semantic search, dependency graphs, architecture understanding |
| `socraticode:codebase-management` | Index management, health checks, file watching |

### SocratiCode: when to use each tool

**Principle: search before reading** — leverage the semantic index rather than consuming raw file content.

| Objective | Tool |
|---|---|
| Understand codebase purpose or feature location | `codebase_search` with broad queries |
| Locate specific functions, constants, or types | `codebase_search` with exact names |
| Find error messages, logs, or regex patterns | grep / ripgrep |
| View file imports and dependents | `codebase_graph_query` |
| Assess impact before code modifications | `codebase_graph_query` |
| Identify breaking changes from a modification | `codebase_impact target=X` |
| Trace execution from entry points | `codebase_flow entrypoint=X` |
| Discover project entry points | `codebase_flow` (no arguments) |
| Analyze callers and callees for a function | `codebase_symbol name=X` |
| List symbols within files | `codebase_symbols file=path` |
| Search symbols project-wide | `codebase_symbols query=X` |
| Detect architectural issues | `codebase_graph_circular`, `codebase_graph_stats` |
| Visualize module structure | `codebase_graph_visualize` |
| Verify index currency | `codebase_status` |
| Explore available project knowledge | `codebase_context` |
| Locate schemas, endpoints, or configs | `codebase_context_search` |

## Commit convention

With issue: `#<n> [type]: <description>`
Without: `[type]: <description>`
Multiple issues: `#12, #14 [type]: <description>`
Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`
