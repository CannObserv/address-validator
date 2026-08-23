# Agent Guidance — Address Validator

**Output style:** Terse. Bullets > prose. Sacrifice grammar while preserving clarity. No trailing summaries.

## What this project is

FastAPI service — parses and standardizes US (USPS Pub 28) and Canadian (libpostal sidecar) addresses. systemd+uvicorn on port 8000. libpostal sidecar on port 4400 (pelias/libpostal-service Docker, `infra/libpostal.service`).

<!-- BEGIN socraticode-policy -->
## Code Exploration Policy

SocratiCode is the preferred semantic-search tool here once indexed (local Qdrant
store + on-disk graph; manifest `.socraticodecontextartifacts.json`). Its MCP
tools are **deferred** — schemas load only after the `ToolSearch` prefetch that
`.claude/hooks/socraticode-reminder.sh` prints each session; calling one before
that fails with `InputValidationError`.

**Negative rule.** Use SocratiCode MCP tools first for semantic questions ("where
is X", "how does Y work", "what depends on Z"). Reach for `grep`/`rg` only on
exact strings (error messages, log lines, known symbols). Reserve the Explore
subagent for path-pattern walks (`*.py` under `src/address_validator/routers/`),
not semantic search.

| Goal | Tool |
|---|---|
| Where is X defined / how does Y work / what touches Z | `codebase_search` |
| Exact string or regex (errors, log lines, known symbols) | `grep` / `rg` |
| Imports/dependents of a file · blast radius of a change | `codebase_graph_query` / `codebase_impact` |

Full tool table, prefetch query, per-tool guidance: [docs/SOCRATICODE.md](docs/SOCRATICODE.md).
<!-- END socraticode-policy -->

## Code Exploration Notes (repo-specific)

- Graph yield `ok` (376 edges / 238 files, 2026-08-22). The 71.4% `unresolvedPct`
  is a **call**-edge statistic, not imports — import edges probe exact, so an empty
  `codebase_graph_query`/`codebase_impact` answer means no dependents. Evidence and
  re-measurement recipe: [docs/SOCRATICODE.md](docs/SOCRATICODE.md) → Repo-specific notes.

## Architecture

See `docs/ARCHITECTURE.md` for the full module map.

```
HTTP request
 └─ middleware: api_version → request_id → audit
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
- `warnings: list[str]` on all response models; empty on clean input. Every warning string is defined in `core/warnings.py` (single source of truth) and catalogued in `docs/WARNINGS.md`; a drift test enforces sync — never inline a new warning literal
- `ValidationResult.status` vocabulary is defined in `core/validation_status.py` (single source of truth) and catalogued in `docs/VALIDATION-STATUS.md`; a drift test (`tests/unit/test_validation_status_catalogue.py`) enforces sync across the `Literal`, the `validated_addresses` `CheckConstraint`, the DPV→status map, and admin `VS_META` — never inline a new status literal. Adding a status also requires a new Alembic migration widening `ck_validated_addresses_status`
- Pipeline version (`core/pipeline_version.py`, single source of truth) stamps every cached `validated_addresses` row; code changes that alter parse/standardize output must bump `PIPELINE_CODE_VERSION` — drift test `tests/unit/test_pipeline_output_pin.py` enforces (re-pin hash + bump together). CRF model swaps via `CUSTOM_MODEL_PATH` invalidate automatically (fingerprint)
- `db/tables.py` mirrors the Alembic-head schema and is never used for DDL; drift test `tests/unit/db/test_schema_drift.py` compares it column-by-column (nullability, generated, identity, type) against a migrated DB — schema changes need a migration first, then the mirrored Table def
- `components` takes precedence over `address` when both supplied
- All request models accepting a country must inherit `CountryRequestMixin`

## Authentication

- All `/api/*` require `X-API-Key`; value from `API_KEY` env var
- Key at `/etc/address-validator/.env` (mode 640); loaded via `EnvironmentFile=` in systemd unit
- Open routes: `GET /`, `/docs`, `/redoc`, `/openapi.json`, `GET /api/v2/health`
- `/api/v2/health` → `{"status": "ok"|"degraded", "api_version": "2", "database": "ok"|"error"|"unconfigured", "libpostal": "ok"|"unavailable"}`; HTTP 503 when degraded (libpostal state does NOT affect HTTP status)
- Google provider uses ADC — no API key. IAM: `roles/addressvalidation.user`, `roles/cloudquotas.viewer`, `roles/monitoring.viewer`
- Admin (`/admin/*`) requires exe.dev proxy auth (`X-ExeDev-UserID`, `X-ExeDev-Email`)
- CORS: denied by default (GH #35). `ALLOWED_ORIGINS` env var grants browser origins — comma-separated list, or `*` for any; unset = no `Access-Control-Allow-Origin` ever emitted

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
- Disk hygiene: weekly timer (`infra/disk-hygiene.sh`, Sun 05:00 UTC) prunes VS Code server builds, npm/uv caches, orphaned worktrees; dry-run with `infra/disk-hygiene.sh --dry-run`

## Infrastructure

Single-VM dev+prod model ([exe.dev](https://exe.dev)):
- Port 8000 = systemd production service (main worktree) — **never** start uvicorn manually on this port
- Port 8001 = dev server (active git worktree, `--reload`)
- exe.dev proxy: dev server accessible at `https://address-validator.exe.xyz:8001/`
- All development work happens on git worktrees — never modify the main worktree directly
- Standard workflow: `/brainstorming` → design doc → worktree → implement → PR → merge → clean up worktree
- Worktrees: `.worktrees/<branch-slug>/` only, via the `using-git-worktrees` scripts — never `git worktree remove`
- New worktree: `uv sync` first — no `.venv` is linked in ([docs/DEPLOYMENT.md](docs/DEPLOYMENT.md))
- Dev server: from the worktree root, `PYTHONPATH=src` + `--log-config` both mandatory (boot fails without)
- A worktree that has run `doctor.sh` needs `worktree-destroy.sh <branch> --force`; full worktree + dev-server reference → [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)

## Environment

| File | Contents | Loaded by |
|---|---|---|
| `/etc/address-validator/.env` | Production secrets (`API_KEY`, DSN, provider creds, `CUSTOM_MODEL_PATH`) + `LOG_LEVEL` | systemd (required) |
| `/home/exedev/address-validator/.env` | Dev/agent secrets (`GH_TOKEN`, `GH_TOKEN_SKILLS`) | systemd (optional with `-` prefix), manual `export` |

`LOG_LEVEL` (default `INFO`) is the only knob for app-logger verbosity — uvicorn's `--log-level` reaches `uvicorn.error`/`uvicorn.access`/`uvicorn.asgi` and never root. See `docs/LOGGING.md`.

## Testing and linting

```
uv run pytest                   # all tests + coverage
uv run pytest --no-cov -x      # fast, stop on first failure
uv run pytest --no-cov -m integration    # integration tests only
uv run pytest --no-cov -m "not integration"  # unit tests only (faster; coverage fails below 80% on partial runs)
npm run test:js                 # admin JS tests (vitest + jsdom)
npm run lint:js                 # admin JS lint (ESLint flat config)
npm run format:js:check         # admin JS format check (Prettier)
npm run format:js               # admin JS format write
uv run ruff check .             # lint
uv run ruff check . --fix       # lint + autofix
uv run ruff format .            # format
bash tests/shell/disk-hygiene-test.sh    # infra shell-script sandbox tests
```

Coverage floor: **80%** line + branch. Baseline ~93% — don't regress. Pre-commit hooks must pass before any commit (ruff; ESLint + Prettier when admin JS is touched; shellcheck + sandbox rig when infra/test shell scripts are touched).

**NEVER** source `/etc/address-validator/.env` before running tests. That file sets `VALIDATION_CACHE_DSN` to the production database; the audit middleware writes real rows on every `TestClient` request. `tests/conftest.py` sets `VALIDATION_CACHE_DSN` via `os.environ.setdefault` so no shell prep is needed for `uv run pytest`. See `.env.test` for standalone-script use.

## Common tasks

```
uv sync                         # install/refresh deps
uv add <package>                # add dep; commit pyproject.toml + uv.lock together
uv lock --upgrade && uv sync    # upgrade all deps; then update lower bounds
```

Worktrees get **no** `.venv` (`.skills/worktree_venv=none`) — run `uv sync` in a new worktree before its first test run (~0.2 s, ~4 MiB). This is why: under the `link` default a worktree's `uv sync` *prunes* the venv the port-8000 service runs from. Rationale and measurements → [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

See `docs/DEPENDENCY-POLICY.md` for version pinning rules.

## GitHub CLI

Two PATs in `.env` — `GH_TOKEN` (this repo), `GH_TOKEN_SKILLS` (`gregoryfoster/skills`). Anchor the grep; unanchored matches both and `gh` rejects the result:

```bash
export GH_TOKEN=$(grep '^GH_TOKEN=' .env | cut -d= -f2)
```

## Sensitive areas

Critical gotchas (see `docs/SENSITIVE-AREAS.md` for full per-module risk table):

- **Route handlers must be `async def`** — sync `def` runs in threadpool; ContextVar writes are invisible to the audit middleware, silently breaking training candidate collection
- **`AddressInputMixin.model_validator`** — sole 422 guard for address/components input across all endpoints; do not weaken
- **Middleware order is load-bearing** — `request_id` must wrap `audit`; `reset_audit_context()` + `reset_candidate_data()` must fire at request start
- **Cache key changes** (`_make_pattern_key`, `_make_canonical_key`) silently orphan all existing cache entries
- **`PIPELINE_CODE_VERSION` bump discipline** — parse/standardize output changes without a bump silently serve stale cached results; `_store()`'s ON CONFLICT must keep refreshing `pipeline_version`
- **`except Exception` in fail-open writes** — intentional in `write_audit_row`, `write_training_candidate`, `cache_provider.validate()`; do not narrow
- **`ALLOWED_TRANSITIONS`** in `training_batches.py` — single source of truth for batch status; all transitions go through it

## Skills

See `docs/SKILLS.md` for full descriptions. Key skills for development:

| Skill | When to use |
|---|---|
| `/brainstorming` | Before any new feature — design before code |
| `/writing-plans` | After brainstorming; before multi-step implementation |
| `/using-git-worktrees` | Every feature branch — isolated worktree; ports above, lifecycle in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) |
| `/test-driven-development` | Before writing implementation code |
| `/systematic-debugging` | Any bug or unexpected test failure |
| `/verification-before-completion` | Before claiming done or opening a PR |
| `/reviewing-code-python-fastapi` | Code review — tiered findings, implements approved fixes |
| `/reviewing-architecture` | Architecture review |
| `/enforcing-architecture` | Turn an accepted AR finding into an executable fitness function — "add a fitness function", "enforce this contract", "lock this rule" |
| `/curating-context` | Trim AGENTS.md + docs to the 6,000-token budget; weekly maintenance |
| `/shipping-work-python-fastapi` | Finalize — commit, push, close issues |
| `/train-model` | CRF model retraining pipeline |
| `/schedule` | Recurring or one-time background agents |
| `socraticode:codebase-exploration` | Semantic search, dependency graphs — tool table in [docs/SOCRATICODE.md](docs/SOCRATICODE.md) |
| `socraticode:codebase-management` | Index management, health checks, file watching — see [docs/SOCRATICODE.md](docs/SOCRATICODE.md) |

## Commit convention

With issue: `#<n> [type]: <description>`
Without: `[type]: <description>`
Multiple issues: `#12, #14 [type]: <description>`
Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`

## Detail Docs

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — request flow, module ownership
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — units, timers, DB scripts, env, worktree + dev-server
- [docs/SENSITIVE-AREAS.md](docs/SENSITIVE-AREAS.md) — per-module risk table: what breaks silently
- [docs/VALIDATION-PROVIDERS.md](docs/VALIDATION-PROVIDERS.md) — provider env vars, DPV→status map, quotas
- [docs/VALIDATION-STATUS.md](docs/VALIDATION-STATUS.md) — `ValidationResult.status` vocabulary
- [docs/WARNINGS.md](docs/WARNINGS.md) — `warnings[]` catalogue
- [docs/LOGGING.md](docs/LOGGING.md) — event/level table, PII policy
- [docs/STYLE.md](docs/STYLE.md) — admin dashboard: brand, dark mode, WCAG 2.1 AA
- [docs/SKILLS.md](docs/SKILLS.md) — every vendored skill and its trigger
- [docs/SOCRATICODE.md](docs/SOCRATICODE.md) — `codebase_*` tool table, prefetch query, graph health
- [docs/DEPENDENCY-POLICY.md](docs/DEPENDENCY-POLICY.md) — version pinning rules
- [docs/usps-pub28.md](docs/usps-pub28.md) — Pub 28 edition behind `usps_data/`, API model notes
- Vendored USPS OpenAPI specs: [standard](docs/usps-addresses-v3r2_4.yaml), [Enhanced](docs/usps-enhanced-addresses-v3r2.yaml)

`docs/plans/`, `docs/research/` — dated snapshots, never current guidance.
