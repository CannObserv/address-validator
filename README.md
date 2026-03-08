# Address Validator

FastAPI service (v2.0.0) that parses and standardizes US physical
addresses per
[USPS Publication 28](https://pe.usps.com/text/pub28/welcome.htm).

All API routes live under `/api/v1/` and return an `api_version` field
in every response body plus an `API-Version: 1` response header.

## Features

- **Parse** a raw address string into labelled components using the
  [usaddress](https://github.com/datamade/usaddress) library.
- **Standardize** addresses to USPS format: all-caps, official suffix
  abbreviations (Avenue → AVE), directional abbreviations (South → S),
  state abbreviations (Illinois → IL), secondary unit designators
  (Suite → STE), and ZIP code normalization.
- **Intersection support** — `"Hollywood Blvd and Vine St"` →
  `"HOLLYWOOD BLVD & VINE ST"`.
- **Country validation** — requests accept an optional `country` field
  (ISO 3166-1 alpha-2, default `"US"`). Invalid codes are rejected
  using the `pycountry` library; only `US` is currently supported.
- **API key authentication** — `/api/v1/*` endpoints require an
  `X-API-Key` header; docs remain open.
- **CORS enabled** — cross-origin requests are allowed from any origin.
- **Health check** — `GET /api/v1/health` for liveness probes.

## Endpoints

| Method | Path                 | Auth | Description                               |
|--------|----------------------|------|-------------------------------------------|
| `POST` | `/api/v1/parse`      | 🔒   | Parse raw address string into components  |
| `POST` | `/api/v1/standardize`| 🔒   | Standardize address to USPS Pub 28 format |
| `GET`  | `/api/v1/health`     |      | Service health check                      |
| `GET`  | `/docs`              |      | Interactive Swagger UI                    |
| `GET`  | `/redoc`             |      | ReDoc API documentation                   |

All `POST` endpoints accept and return `application/json`.  Address
inputs are limited to **1000 characters**.

### `POST /api/v1/parse`

**Request:**

```json
{
  "address": "1600 Pennsylvania Avenue NW, Washington, DC 20500",
  "country": "US"
}
```

The `country` field is optional (defaults to `"US"`).

**Response:**

```json
{
  "input": "1600 Pennsylvania Avenue NW, Washington, DC 20500",
  "country": "US",
  "components": {
    "spec": "usps-pub28",
    "spec_version": "unknown",
    "values": {
      "address_number": "1600",
      "street_name": "Pennsylvania",
      "street_name_post_type": "Avenue",
      "street_name_post_directional": "NW",
      "city": "Washington",
      "state": "DC",
      "zip_code": "20500"
    }
  },
  "type": "Street Address",
  "warnings": [],
  "api_version": "1"
}
```

The `type` field is one of `"Street Address"`, `"Intersection"`, or
`"Ambiguous"`.  The `warnings` list is populated whenever input is
silently modified during parsing — for example when parenthesized text
is stripped, when repeated address numbers are joined into a range, or
when a unit designator is recovered from a mis-tagged field.  It is
empty on clean input.

The `components` field is a `ComponentSet` containing:

- **`spec`** — machine identifier for the component schema (e.g.
  `"usps-pub28"`).
- **`spec_version`** — edition of the spec the values conform to.
- **`values`** — the labelled address component key/value pairs.

### `POST /api/v1/standardize`

Accepts **either** a raw address string or pre-parsed components.  When
both are provided, `components` takes precedence and `address` is
ignored.

**Request (string):**

```json
{
  "address": "350 Fifth Ave Suite 3300, New York, NY 10118",
  "country": "US"
}
```

**Request (components):**

```json
{
  "components": {
    "address_number": "350",
    "street_name": "Fifth",
    "street_name_post_type": "Ave",
    "occupancy_type": "Suite",
    "occupancy_identifier": "3300",
    "city": "New York",
    "state": "NY",
    "zip_code": "10118"
  },
  "country": "US"
}
```

**Response:**

```json
{
  "address_line_1": "350 FIFTH AVE",
  "address_line_2": "STE 3300",
  "city": "NEW YORK",
  "region": "NY",
  "postal_code": "10118",
  "country": "US",
  "standardized": "350 FIFTH AVE  STE 3300  NEW YORK, NY 10118",
  "components": {
    "spec": "usps-pub28",
    "spec_version": "unknown",
    "values": {
      "address_number": "350",
      "street_name": "FIFTH",
      "street_name_post_type": "AVE",
      "occupancy_type": "STE",
      "occupancy_identifier": "3300",
      "city": "NEW YORK",
      "state": "NY",
      "zip_code": "10118"
    }
  },
  "warnings": [],
  "api_version": "1"
}
```

The response uses geography-neutral field names: `region` (not `state`)
and `postal_code` (not `zip_code`).  The `standardized` field uses
two-space separators between address lines, matching the USPS
single-line format convention.

### `GET /api/v1/health`

**Response:**

```json
{"status": "ok", "api_version": "1"}
```

No authentication required.

## Authentication

All `/api/v1/*` endpoints (except `/api/v1/health`) require an
`X-API-Key` header.  Set the expected key via the `API_KEY` environment
variable:

```bash
export API_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
```

Requests without a valid key receive `401` or `403`.  Swagger (`/docs`)
and ReDoc (`/redoc`) remain open.

```bash
curl -X POST http://localhost:8000/api/v1/standardize \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_KEY' \
  -d '{"address": "350 Fifth Ave, New York, NY 10118"}'
```

## Setup

Requires Python ≥ 3.12.  Dependencies are managed with
[uv](https://docs.astral.sh/uv/).

```bash
uv sync                  # install dependencies into .venv/
export API_KEY="your-secret-key"
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

A systemd unit file (`address-validator.service`) is included for
persistent deployment.  The API key is stored in
`/etc/address-validator/env` and loaded via `EnvironmentFile=`.

## Project Structure

```
main.py                        # FastAPI app entry point, CORS config
auth.py                        # API key authentication dependency
models.py                      # Shared Pydantic request/response models
routers/
  v1/
    core.py                    # Country validation, APIError, helpers
    health.py                  # GET /api/v1/health
    parse.py                   # POST /api/v1/parse
    standardize.py             # POST /api/v1/standardize
services/
  parser.py                    # usaddress wrapper, tag-name mapping
  standardizer.py              # USPS Pub 28 standardization logic
usps_data/
  spec.py                      # Pub 28 spec identifier constants
  suffixes.py                  # Street suffix abbreviations
  directionals.py              # Directional abbreviations
  states.py                    # State name → abbreviation map
  units.py                     # Secondary unit designators
docs/
  usps-pub28.md                # Pub 28 research notes
  usps-addresses-v3r2_3.yaml   # Archived USPS Addresses API v3 spec
tests/
  conftest.py                  # Shared fixtures, API_KEY bootstrap
  unit/                        # Unit tests (parser, standardizer, auth, data)
  integration/                 # Integration tests (HTTP endpoints)
address-validator.service      # systemd unit file
pyproject.toml                 # Project metadata, dependencies, tool config
```

## Standardization Rules Applied

- All output uppercased
- Parenthesized text removed (USPS Pub 28 §354 — not valid in
  standardized addresses; typically wayfinding notes)
- Trailing commas, semicolons, and stray punctuation stripped
- Street suffixes abbreviated (USPS Pub 28 Appendix C)
- Directionals abbreviated (N, S, E, W, NE, NW, SE, SW)
- State names converted to two-letter abbreviations
- Secondary unit designators abbreviated (Suite → STE, Apartment → APT,
  Building/Bldg/Bld → BLDG, etc.)
- Unit identifiers without a designator default to `#`
- Designator words folded into identifiers are extracted
  (e.g. `NO. 16` → `# 16`)
- Both occupancy and subaddress designators preserved when present
- Dual address numbers joined with hyphen (`1804 & 1810` → `1804-1810`)
- Periods removed from all components
- ZIP codes normalized to 5-digit or 5+4 format
- Unit designators mis-tagged as city by the parser are recovered
  (e.g. `BASEMENT, FREELAND` → line 2 `BSMT`, city `FREELAND`)
- Non-address wayfinding words (e.g. `YARD`) dropped from city
- Line 2 ordering: larger container (BLDG) before specific unit (STE)
- Intersections formatted as `STREET1 & STREET2`

## Testing

```bash
uv run pytest                          # full suite with coverage
uv run pytest --no-cov                 # fast, no coverage
uv run pytest tests/unit/test_parser.py # single file
uv run ruff check .                    # lint
uv run ruff format .                   # format
```

Coverage floor: 80% line + branch (enforced by `--cov-fail-under=80`).
