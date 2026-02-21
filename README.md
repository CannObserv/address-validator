# Address Validator

FastAPI service that parses and standardizes US physical addresses per
[USPS Publication 28](https://pe.usps.com/text/pub28/welcome.htm).

## Features

- **Parse** a raw address string into labelled components using the
  [usaddress](https://github.com/datamade/usaddress) library.
- **Standardize** addresses to USPS format: all-caps, official suffix
  abbreviations (Avenue → AVE), directional abbreviations (South → S),
  state abbreviations (Illinois → IL), secondary unit designators
  (Suite → STE), and ZIP code normalization.
- **Intersection support** — `"Hollywood Blvd and Vine St"` →
  `"HOLLYWOOD BLVD & VINE ST"`.
- **API key authentication** — `/api/*` endpoints require an
  `X-API-Key` header; web UI and docs remain open.
- **CORS enabled** — cross-origin requests are allowed from any origin.
- **Web interface** for interactive use.

## Endpoints

| Method | Path               | Auth | Description                               |
|--------|--------------------|------|-------------------------------------------|
| `GET`  | `/`                |      | Web interface                             |
| `POST` | `/api/parse`       | 🔒   | Parse raw address string into components  |
| `POST` | `/api/standardize` | 🔒   | Standardize address to USPS Pub 28 format |
| `GET`  | `/docs`            |      | Interactive Swagger UI                    |
| `GET`  | `/redoc`           |      | ReDoc API documentation                   |

All `POST` endpoints accept and return `application/json`.  Address
inputs are limited to **1000 characters**.

### `POST /api/parse`

**Request:**

```json
{"address": "1600 Pennsylvania Avenue NW, Washington, DC 20500"}
```

**Response:**

```json
{
  "input": "1600 Pennsylvania Avenue NW, Washington, DC 20500",
  "components": {
    "address_number": "1600",
    "street_name": "Pennsylvania",
    "street_name_post_type": "Avenue",
    "street_name_post_directional": "NW",
    "city": "Washington",
    "state": "DC",
    "zip_code": "20500"
  },
  "type": "Street Address",
  "warning": null
}
```

The `type` field is one of `"Street Address"`, `"Intersection"`, or
`"Ambiguous"`.  When the parser encounters repeated labels, `type` is
`"Ambiguous"` and `warning` contains a human-readable message.

### `POST /api/standardize`

Accepts **either** a raw address string or pre-parsed components.  When
both are provided, `components` takes precedence and `address` is
ignored.

**Request (string):**

```json
{"address": "350 Fifth Ave Suite 3300, New York, NY 10118"}
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
  }
}
```

**Response:**

```json
{
  "address_line_1": "350 FIFTH AVE",
  "address_line_2": "STE 3300",
  "city": "NEW YORK",
  "state": "NY",
  "zip_code": "10118",
  "standardized": "350 FIFTH AVE  STE 3300  NEW YORK, NY 10118",
  "components": {
    "address_number": "350",
    "street_name": "FIFTH",
    "street_name_post_type": "AVE",
    "occupancy_type": "STE",
    "occupancy_identifier": "3300",
    "city": "NEW YORK",
    "state": "NY",
    "zip_code": "10118"
  }
}
```

The `standardized` field uses two-space separators between address
lines, matching the USPS single-line format convention.

## Authentication

All `/api/*` endpoints require an `X-API-Key` header.  Set the expected
key via the `API_KEY` environment variable:

```bash
export API_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
```

Requests without a valid key receive `401` or `403`.  The web UI (`/`),
Swagger (`/docs`), and ReDoc (`/redoc`) remain open.

```bash
curl -X POST http://localhost:8000/api/standardize \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: YOUR_KEY' \
  -d '{"address": "350 Fifth Ave, New York, NY 10118"}'
```

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
export API_KEY="your-secret-key"
uvicorn main:app --host 0.0.0.0 --port 8000
```

A systemd unit file (`address-validator.service`) is included for
persistent deployment.

## Project Structure

```
main.py                  # FastAPI app entry point, CORS config
auth.py                  # API key authentication dependency
models.py                # Shared Pydantic request/response models
routers/
  parse.py               # POST /api/parse
  standardize.py         # POST /api/standardize
  web.py                 # GET / (web UI)
services/
  parser.py              # usaddress wrapper, tag-name mapping
  standardizer.py        # USPS Pub 28 standardization logic
usps_data/
  suffixes.py            # Street suffix abbreviations
  directionals.py        # Directional abbreviations
  states.py              # State name → abbreviation map
  units.py               # Secondary unit designators
static/
  index.html             # Web interface
address-validator.service  # systemd unit file
```

## Standardization Rules Applied

- All output uppercased
- Street suffixes abbreviated (USPS Pub 28 Appendix C)
- Directionals abbreviated (N, S, E, W, NE, NW, SE, SW)
- State names converted to two-letter abbreviations
- Secondary unit designators abbreviated (Suite → STE, Apartment → APT, etc.)
- Unit identifiers without a designator default to `#`
- Periods removed from all components
- ZIP codes normalized to 5-digit or 5+4 format
- Intersections formatted as `STREET1 & STREET2`
