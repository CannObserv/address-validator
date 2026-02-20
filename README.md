# Address Validator

FastAPI service that parses and standardizes US physical addresses per
[USPS Publication 28](https://pe.usps.com/text/pub28/welcome.htm).

## Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET`  | `/`  | Web interface |
| `POST` | `/api/parse` | Parse raw address string into labelled components |
| `POST` | `/api/standardize` | Standardize address to USPS format |
| `GET`  | `/docs` | Interactive API docs (Swagger UI) |

### `POST /api/parse`

```json
{"address": "1600 Pennsylvania Avenue NW, Washington, DC 20500"}
```

Returns parsed components with their labels.

### `POST /api/standardize`

Accepts **either** a raw string or pre-parsed components:

```json
{"address": "350 Fifth Ave Suite 3300, New York, NY 10118"}
```

or:

```json
{"components": {"address_number": "350", "street_name": "Fifth", ...}}
```

Returns the standardized address with USPS abbreviations applied.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Project Structure

```
main.py                  # App entry point
routers/
  parse.py               # /api/parse endpoint
  standardize.py         # /api/standardize endpoint
  web.py                 # Web UI (GET /)
services/
  parser.py              # usaddress wrapper
  standardizer.py        # USPS Pub 28 standardization
usps_data/
  suffixes.py            # Street suffix abbreviations
  directionals.py        # Directional abbreviations
  states.py              # State name/abbreviation map
  units.py               # Secondary unit designators
static/index.html        # Web interface
```
