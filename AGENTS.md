# Agent Guidance — Address Validator

## What this project is

A FastAPI service that parses and standardizes US physical addresses
according to USPS Publication 28.  It is deployed as a systemd service
running uvicorn on port 8000.

## Architecture

- **`main.py`** — FastAPI app, CORS middleware, router registration.
- **`models.py`** — Shared Pydantic request/response models used by
  both routers and services.  This is the single source of truth for
  API contracts.
- **`routers/`** — Thin HTTP handlers.  Validation and error handling
  live here; business logic is delegated to services.  All active
  routes live under `routers/v1/`; the flat `routers/*.py` shim layer
  was removed in v2.0.0 (issue #12).
- **`services/`** — Core logic.
  - `parser.py` wraps the `usaddress` library and maps its tag names
    to snake_case keys.  Post-parse recovery steps fix common
    `usaddress` mis-tagging: unit designators absorbed into city,
    stray identifier fragments, and single-word wayfinding text.
  - `standardizer.py` applies USPS Pub 28 abbreviation rules using
    lookup tables from `usps_data/`.
- **`usps_data/`** — Pure-data modules exporting `dict[str, str]` maps
  for suffixes, directionals, states, and unit designators.  Sourced
  from USPS Pub 28 appendices.
- **`routers/v1/core.py`** — Shared v1 utilities: country validation sets
  (`VALID_ISO2` built from `pycountry`, `SUPPORTED_COUNTRIES`), `APIError`
  exception, `api_error_response` helper (uses `ErrorResponse` directly),
  and `check_country()`.
- **`usps_data/spec.py`** — USPS Publication 28 spec identifier constants
  (`USPS_PUB28_SPEC`, `USPS_PUB28_SPEC_VERSION`).  Imported by services
  to tag `ComponentSet` instances; also re-exported by `routers/v1/core.py`.
- **`docs/usps-pub28.md`** — Research notes on the Pub 28 edition used,
  verification procedure for pinning `USPS_PUB28_SPEC_VERSION`, and
  notes on the USPS Addresses API v3 models.
- **`docs/usps-addresses-v3r2_3.yaml`** — Archived USPS Addresses API v3.2.2
  OpenAPI spec (retrieved 2026-03-03).

## Logging

All service and auth modules use Python's standard `logging` module via a
module-level logger:

```python
import logging
logger = logging.getLogger(__name__)
```

This gives loggers the names `services.parser`, `services.standardizer`, and
`auth`, making them filterable via the standard Python logging hierarchy.

| Event | Level | Module | Notes |
|---|---|---|---|
| Successful parse | `DEBUG` | `services.parser` | `type=` and `country=` |
| Ambiguous parse (RepeatedLabelError) | `WARNING` + `DEBUG` | `services.parser` | WARNING first, then DEBUG with `type=Ambiguous` |
| Standardize call | `DEBUG` | `services.standardizer` | `count=` and `country=` |
| Auth rejection — missing key (401) | `INFO` | `auth` | includes `path=` |
| Auth rejection — invalid key (403) | `INFO` | `auth` | includes `path=` |

**No PII rule:** address content must never appear in log messages at `INFO`
or above. Component counts and type labels are safe; raw address strings are not.

Log level is controlled at runtime by uvicorn's `--log-level` flag (set in the
systemd unit). `DEBUG` is off by default in production. JSON structured logging
can be added later via `python-json-logger` if log aggregation is introduced.

New routers and services should follow the same pattern: one `getLogger(__name__)`
per module, and `caplog` assertions in the corresponding unit tests.

## Key conventions

- Routers return Pydantic response models.  Services also return these
  models (not raw dicts).
- The `_get()` helper in `standardizer.py` normalizes component values
  (strip whitespace → uppercase → remove periods → remove parentheses
  → strip trailing commas/semicolons) before any further processing.
  Parenthesis stripping is redundant for parser output (which strips
  parenthesized text pre-parse) but is retained for direct component
  input via `/api/standardize`.  The `_lookup()` function does its
  own defensive normalization so it is safe to call with raw or
  pre-cleaned input.
- Address input is limited to 1000 characters (enforced by Pydantic
  `Field(max_length=1000)` on both request models).
- The `standardized` field uses two-space separators between logical
  address lines (USPS single-line convention).
- Response models (`ParseResponseV1`, `StandardizeResponseV1`) use
  geography-neutral field names: `region` and `postal_code`.

## Authentication

- API endpoints (`/api/*`) require an `X-API-Key` header.
- The expected key is read from the `API_KEY` environment variable.
- The key is stored in `/etc/address-validator/env` (mode 600) and
  loaded via `EnvironmentFile=` in the systemd unit.
- `auth.py` provides the `require_api_key` FastAPI dependency.
- `GET /`, `/docs`, `/redoc`, and `/openapi.json` remain open.
- The web UI persists the entered key in `localStorage`.

## GitHub CLI / PAT

A GitHub Personal Access Token is stored in `env` (project root) as
`GITHUB_TOKEN`.  This file is **not** committed (gitignored via the
system `.gitignore`; add it if needed).  Load it for `gh` commands:

```bash
export GH_TOKEN=$(grep GITHUB_TOKEN env | cut -d= -f2)
gh issue list          # example
```

Or prefix individual commands:

```bash
GH_TOKEN=$(grep GITHUB_TOKEN env | cut -d= -f2) gh issue create ...
```

Do **not** pass the token via `--auth-token`; use `GH_TOKEN` env var
(the `gh` CLI reads it automatically).

## Deployment

- Python venv at `./.venv/` (managed by `uv`).
- systemd unit: `/etc/systemd/system/address-validator.service`.
- Environment file: `/etc/address-validator/env` (contains `API_KEY=...`).
- Restart after changes: `sudo systemctl restart address-validator`.
- Logs: `journalctl -u address-validator -f`.
- Re-install unit after editing: `sudo cp address-validator.service /etc/systemd/system/ && sudo systemctl daemon-reload`. The repo copy is canonical.

## Testing and linting

- **Run tests:** `uv run pytest`
- **Run tests (no coverage, faster):** `uv run pytest --no-cov`
- **Run a single file:** `uv run pytest tests/unit/test_parser.py`
- **Lint:** `uv run ruff check .`
- **Lint + autofix:** `uv run ruff check . --fix`
- **Format:** `uv run ruff format .`

### TDD workflow (red → green)
1. Write a failing test and confirm it fails: `uv run pytest --no-cov -x`
2. Commit with `test: <description>` (the red commit).
3. Write minimal production code to make it pass.
4. Commit with `feat:` / `fix:` / `refactor:` as appropriate.
5. Ruff must be clean before any commit.

### Coverage
- Floor: **80% line + branch** (enforced by `--cov-fail-under=80`).
- Current baseline: ~93%.

### Auth in tests
`auth.py` reads `API_KEY` at import time.  `tests/conftest.py` sets
`os.environ["API_KEY"]` before importing the app, so tests can run
without the systemd environment file.  The deferred `from main import app`
import in `conftest.py` is intentional — do not move it above the
`os.environ.setdefault` call.

## Common tasks

- **Install / refresh dependencies:** `uv sync`
- **Add a dependency:** `uv add <package>` (updates `pyproject.toml` and `uv.lock`)
- **Upgrade all deps to latest allowed:** `uv lock --upgrade && uv sync` (then update lower bounds — see Dependency version pinning below)
- **Run a command in the venv:** `uv run <command>` (e.g. `uv run uvicorn main:app ...`)
- **Commit lockfile after any dep change:** always commit `uv.lock` alongside `pyproject.toml`
- **Dependency version pinning:** pin every dependency within a major version boundary
  (`>=X.Y,<X+1`).  This applies to all new libraries added to the project — no
  unbounded upper pins.  After each intentional upgrade cycle, update the lower bound
  to the newly installed version.  Example: after upgrading FastAPI to 0.130.x,
  update `pyproject.toml` to `fastapi>=0.130,<1`.  This prevents silent breakage
  from future minor releases while still allowing patch-level updates.

## Sensitive areas

- **`services/parser.py` pre-processing** — parenthesized text and
  bare parentheses are stripped from every raw address before
  `usaddress` sees it.  Changes to this regex affect all parsing.
- **`services/parser.py` post-parse recovery** —
  `_recover_unit_from_city()`, `_recover_identifier_fragment_from_city()`,
  and the `_ADDRESS_VOCABULARY` / `_NO_ID_DESIGNATORS` sets run on
  every parsed address.  Changes here affect component assignment
  for any address where `usaddress` mis-tags secondary designators
  or wayfinding text as city data.
- **`usps_data/` tables** — changes here affect all standardization
  output.  Verify against USPS Pub 28 before editing.
- **`_get()` normalization** — every component value flows through
  this; changes cascade everywhere.
- **`models.py`** — the single source of truth for API contracts.
  Models use geography-neutral field names (`region`, `postal_code`).
  Changing field names or types is a breaking API change.
  `CountryRequestMixin` provides the `country` field and normalisation
  validator; all v1 request models that accept a country code must
  inherit from it.  The `_country_field()` factory returns a fresh
  `FieldInfo` per model.
- **`usps_data/spec.py`** — changing `USPS_PUB28_SPEC` or
  `USPS_PUB28_SPEC_VERSION` affects the `ComponentSet.spec` /
  `spec_version` fields on every parse and standardize response.
- **`auth.py`** — the authentication gate for all `/api/*` endpoints.
  The API key is read once at import time; changes to the loading
  logic affect service startup.
- **`/etc/address-validator/env`** — contains the `API_KEY` secret.
  Owned by `root:exedev`, mode 640.  Editing requires root; the
  service must be restarted to pick up a new key.

## Commit message convention

When **not** associated with a GitHub issue:
```
[type]: <description>
```

When associated with one or more GitHub issues:
```
#<number> [type]: <description>
```
Multiple issues: `#12, #14 [type]: <description>`

Common types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`.

The `shipping-work-claude` skill follows this convention when auto-committing uncommitted work.

## Agent Skills

This project follows the [agentskills.io](https://agentskills.io) spec.
Skills live in the `skills/` directory and are auto-discovered by the agent
framework.  A skill is either a **local override** (committed directory) or a
**symlink** to an external skills repo vendored as a git submodule.

### External skill repos (git submodules)

| Repo | Submodule path |
|---|---|
| [`gregoryfoster/skills`](https://github.com/gregoryfoster/skills) | `vendor/gregoryfoster-skills/` |

After cloning this project, initialize submodules:
```bash
git submodule update --init --recursive
```

**At the start of every conversation**, pull the latest upstream skills:
```bash
git submodule update --remote --merge vendor/gregoryfoster-skills
```
If the submodule ref changed, commit it:
```bash
git add vendor/gregoryfoster-skills
git commit -m "chore: update gregoryfoster-skills submodule"
```

To add a new external skill repo, follow the `managing-skills-claude` skill
(available in `vendor/gregoryfoster-skills/skills/managing-skills-claude/`).

### Available skills

| Skill | Source | Triggers |
|---|---|---|
| `reviewing-code-claude` | Local override | CR, code review, perform a review |
| `reviewing-architecture-claude` | Symlink → `vendor/gregoryfoster-skills/` | AR, architecture review, architectural review |
| `shipping-work-claude` | Local override | ship it, push GH, close GH, wrap up |

### Local overrides

A committed directory in `skills/` with the same name as a symlinked global
skill **completely supersedes** the global version (no inheritance).  The local
version must be fully self-contained.

| Skill | Override reason |
|---|---|
| `reviewing-code-claude` | Adds `ruff` to gather-context; FastAPI/Pydantic-specific review dimensions; auth blast-radius flag |
| `shipping-work-claude` | Concrete `uv run pytest --no-cov` + `uv run ruff check` in `pre-ship.sh`; encodes `#<n> [type]: <desc>` commit convention |
