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
    to snake_case keys.
  - `standardizer.py` applies USPS Pub 28 abbreviation rules using
    lookup tables from `usps_data/`.
- **`usps_data/`** — Pure-data modules exporting `dict[str, str]` maps
  for suffixes, directionals, states, and unit designators.  Sourced
  from USPS Pub 28 appendices.
- **`static/index.html`** — Self-contained single-page web UI.  Read
  once at import time by `routers/web.py`; changes require a restart.

## Key conventions

- Routers return Pydantic response models.  Services also return these
  models (not raw dicts).
- The `_get()` helper in `standardizer.py` normalizes component values
  (uppercase, strip whitespace, remove periods) before any further
  processing.  The `_lookup()` function does its own defensive
  normalization so it is safe to call with raw or pre-cleaned input.
- Address input is limited to 1000 characters (enforced by Pydantic
  `Field(max_length=1000)` on both request models).
- The `standardized` field uses two-space separators between logical
  address lines (USPS single-line convention).

## Authentication

- API endpoints (`/api/*`) require an `X-API-Key` header.
- The expected key is read from the `API_KEY` environment variable.
- The key is stored in `/etc/address-validator/env` (mode 600) and
  loaded via `EnvironmentFile=` in the systemd unit.
- `auth.py` provides the `require_api_key` FastAPI dependency.
- `GET /`, `/docs`, `/redoc`, and `/openapi.json` remain open.
- The web UI persists the entered key in `localStorage`.

## Deployment

- Python venv at `./venv/`.
- systemd unit: `/etc/systemd/system/address-validator.service`.
- Environment file: `/etc/address-validator/env` (contains `API_KEY=...`).
- Restart after changes: `sudo systemctl restart address-validator`.
- Logs: `journalctl -u address-validator -f`.

## Testing notes

There is no automated test suite yet.  When adding tests, cover at
minimum:

- Street suffix, directional, and state abbreviation lookup.
- Intersection parsing and assembly (`FIRST & SECOND`).
- Unit identifier without designator (should default to `#`).
- `RepeatedLabelError` fallback path in the parser.
- ZIP normalization edge cases (short, 5-digit, 9-digit, hyphenated).
- Input length rejection (>1000 chars).
- Whitespace-only and empty-body request rejection.

## Sensitive areas

- **`usps_data/` tables** — changes here affect all standardization
  output.  Verify against USPS Pub 28 before editing.
- **`_get()` normalization** — every component value flows through
  this; changes cascade everywhere.
- **`models.py`** — changing field names or types is a breaking API
  change.
