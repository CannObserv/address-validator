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
  live here; business logic is delegated to services.
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
  (`VALID_ISO2`, `SUPPORTED_COUNTRIES`), `APIError` exception,
  `api_error_response` helper, and `check_country()`.  Previously
  re-exported `USPS_PUB28_SPEC` / `USPS_PUB28_SPEC_VERSION` but those
  imports were removed when no router used them; restore if needed.
- **`usps_data/spec.py`** — USPS Publication 28 spec identifier constants
  (`USPS_PUB28_SPEC`, `USPS_PUB28_SPEC_VERSION`).  Imported by services
  to tag `ComponentSet` instances; also re-exported by `routers/v1/core.py`.

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
  address lines (USPS single-line convention).  This applies to both
  v1 (`StandardizeResponseV1`) and legacy (`StandardizeResponse`) routes.
- v1 response models (`ParseResponseV1`, `StandardizeResponseV1`) use
  geography-neutral field names: `region` (was `state`) and `postal_code`
  (was `zip_code`).  Legacy models retain the original names for
  backward compatibility during the deprecation window.

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
- Current baseline: ~88%.
- `routers/standardize.py` (deprecated route) is the main gap — backfill
  integration tests when the deprecation window closes.

### Auth in tests
`auth.py` reads `API_KEY` at import time.  `tests/conftest.py` sets
`os.environ["API_KEY"]` before importing the app, so tests can run
without the systemd environment file.  The deferred `from main import app`
import in `conftest.py` is intentional — do not move it above the
`os.environ.setdefault` call.

## Common tasks

- **Install / refresh dependencies:** `uv sync`
- **Add a dependency:** `uv add <package>` (updates `pyproject.toml` and `uv.lock`)
- **Upgrade all deps to latest allowed:** `uv lock --upgrade && uv sync`
- **Run a command in the venv:** `uv run <command>` (e.g. `uv run uvicorn main:app ...`)
- **Commit lockfile after any dep change:** always commit `uv.lock` alongside `pyproject.toml`

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
  v1 models use `region` / `postal_code`; legacy models retain `state` /
  `zip_code`.  Changing field names or types in v1 models is a breaking
  API change.  Changing legacy models risks breaking existing callers
  during the deprecation window.  The `_country_field()` factory and
  `_normalise_country()` helper govern how country codes are accepted
  and normalised on all v1 requests.
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

Commits use **Conventional Commits** style for standalone work:
```
<type>: <description>
```
Common types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`.

When a commit closes or advances a GitHub issue, use the issue number
as the sole prefix — drop the type:
```
#<number>: <description>
```
For multiple issues: `#12, #14: <description>`

The `ship` playbook in `PLAYBOOKS.md` follows this convention when
auto-committing uncommitted work.

## Playbooks

When the user references a playbook by name or trigger phrase (e.g., `CR`, `ship it`), read `PLAYBOOKS.md` and execute the matching procedure. Playbooks define the expected steps, output format, and interaction protocol.

**Resolution order** (most specific wins):
1. **Project-level** — `PLAYBOOKS.md` in the project root
2. **Global** — `~/.config/shelley/PLAYBOOKS.md` (cross-project defaults)

If a playbook name exists in both files, the project-level definition takes precedence. If a playbook exists only in the global file, use it.
