# Canada Full Parity + API v2 Design

**Date:** 2026-04-08

## Overview

Full parsing, standardization, and validation parity for Canadian addresses, delivered
alongside a new `/api/v2/` surface. v2 adopts ISO 19160-4 as the internal and default
external component vocabulary, replacing the USPS-flavored tag names used in v1.

This builds on the non-US validation groundwork from #86 (components-only, Google
provider, no parsing/standardization). The goal here is raw-string parity: a Canadian
address string can be submitted to `/api/v2/parse`, `/api/v2/standardize`, and
`/api/v2/validate` exactly as a US address string is today.

---

## Standards Alignment

### ISO 19160-4

ISO 19160-4 (_International postal address components and template language_, 2023
revision) incorporates and updates UPU S42. It defines a hierarchical vocabulary —
Segments → Constructs → Elements → Sub-elements — and the Postal Address Template
Definition Language (PATDL) for machine-readable per-country rendering templates.

This standard provides the internal element vocabulary used throughout v2. National
postal standards (USPS Pub 28, Canada Post Addressing Guidelines) become **national
profiles** rendered from the ISO 19160-4 internal model.

The `google-i18n-address` library already used in `country_format.py` is an open
implementation of equivalent per-country format data; it serves as the source for
`standardized` single-line output templates via `core/address_format.py`.

### Canada Post Addressing Guidelines

Canada Post's addressing specification is less prescriptive than USPS Pub 28 — no
equivalent of the full suffix table exists — but defines:

- 13 province/territory abbreviations (AB, BC, MB, NB, NL, NS, NT, NU, ON, PE, QC,
  SK, YT)
- Postal code format: `A1A 1A1` (FSA + space + LDU; always uppercase)
- Street type conventions (shorter list than USPS; bilingual)

---

## Internal Component Model

All internal processing uses strict ISO 19160-4 element names. No translation occurs
within the service boundary. Translation to other vocabularies happens only at the
v2 response boundary via `component_profile`.

### Canonical elements

| ISO 19160-4 element          | Concept                        | Replaces (v1 usaddress name)       |
|------------------------------|--------------------------------|------------------------------------|
| `premise_number`             | House / building number        | `AddressNumber`                    |
| `premise_name`               | Building name                  | `BuildingName`                     |
| `thoroughfare_pre_direction` | Directional before street name | `StreetNamePreDirectional`         |
| `thoroughfare_leading_type`  | Street type before name (FR)   | `StreetNamePreType`                |
| `thoroughfare_name`          | Street name                    | `StreetName`                       |
| `thoroughfare_trailing_type` | Street type after name (EN)    | `StreetNamePostType`               |
| `thoroughfare_post_direction`| Directional after street type  | `StreetNamePostDirectional`        |
| `sub_premise_type`           | Unit type (Suite, Apt, Unit)   | `OccupancyType`                    |
| `sub_premise_number`         | Unit number                    | `OccupancyIdentifier`              |
| `locality`                   | City / municipality            | `PlaceName`                        |
| `administrative_area`        | State / province / territory   | `StateName`                        |
| `postcode`                   | ZIP code / postal code         | `ZipCode`                          |
| `dependent_locality`         | Neighbourhood / district       | _(new)_                            |
| `general_delivery`           | PO Box, RR, etc.               | `USPSBoxType` + related            |

`ComponentSet.spec` records what national standard was applied during processing
(`"usps-pub28"`, `"canada-post"`, `"raw"`). This is independent of `component_profile`,
which controls response key vocabulary only.

---

## API Versioning

### `/api/v2/` — new surface

Introduced with this work. Differences from v1:

- ISO 19160-4 component keys by default
- Canadian address support (parse, standardize, validate)
- `component_profile` query parameter on parse, standardize, validate
- `API-Version: 2` response header on all `/api/v2/` responses

### `/api/v1/` — frozen

Maintained through a short transition window. No new features, no new countries.
Existing behavior and USPS-flavored component keys preserved unchanged. Clients should
migrate to v2 during the transition window.

### Router structure

```
routers/
  v1/          # frozen; no changes
  v2/
    parse.py
    standardize.py
    validate.py
    countries.py
```

v2 routers are thin: they validate requests, call the same service layer as v1, and
apply `component_profile` translation before returning. The service layer
(`services/parser.py`, `services/standardizer.py`, `services/validation/`) is shared.

v1 routers translate service output (ISO 19160-4 keys) back to USPS-flavored keys
using the `usps-pub28` profile mapping — this is the only change to v1 internals, and
it is invisible to v1 clients.

### `API-Version` header

`middleware/api_version.py` updated to append `API-Version: 2` on `/api/v2/` responses
(in addition to existing `API-Version: 1` on `/api/v1/` responses).

---

## `component_profile` Query Parameter

Controls the key vocabulary used in `ComponentSet.values` in the response. Does not
affect any other response field.

**Parameter:** `component_profile: str = "iso-19160-4"`

**Applies to:** `GET/POST /api/v2/parse`, `/api/v2/standardize`, `/api/v2/validate`

### Supported values

| Value          | Keys returned                                        | Use case                               |
|----------------|------------------------------------------------------|----------------------------------------|
| `iso-19160-4`  | `thoroughfare_name`, `administrative_area`, `postcode`, … | Default; all new clients          |
| `usps-pub28`   | `StreetName`, `StateName`, `ZipCode`, …              | Backward compat for v1 US clients      |
| `canada-post`  | Same as `iso-19160-4` for now; reserved              | Future CA-specific conventions         |

Invalid values → 422 `invalid_component_profile`.

### Translation layer

New module: `src/address_validator/services/component_profiles.py`

```python
PROFILES: dict[str, dict[str, str]] = {
    "usps-pub28": {
        "premise_number":              "AddressNumber",
        "premise_name":                "BuildingName",
        "thoroughfare_pre_direction":  "StreetNamePreDirectional",
        "thoroughfare_leading_type":   "StreetNamePreType",
        "thoroughfare_name":           "StreetName",
        "thoroughfare_trailing_type":  "StreetNamePostType",
        "thoroughfare_post_direction": "StreetNamePostDirectional",
        "sub_premise_type":            "OccupancyType",
        "sub_premise_number":          "OccupancyIdentifier",
        "locality":                    "PlaceName",
        "administrative_area":         "StateName",
        "postcode":                    "ZipCode",
        "general_delivery":            "USPSBoxType",
    },
    "canada-post": {},  # identity map; reserved for future divergence
}

def translate_components(values: dict[str, str], profile: str) -> dict[str, str]:
    mapping = PROFILES.get(profile, {})
    return {mapping.get(k, k): v for k, v in values.items()}
```

Called at the v2 router layer after service layer returns. v1 routers call
`translate_components(values, "usps-pub28")` unconditionally (no query param).

---

## Infrastructure: libpostal Sidecar

### Why libpostal

`usaddress` is a CRF model trained on US address data. It cannot parse Canadian
addresses: province codes are misclassified, postal codes (`A1A 1A1`) are not
recognised, and French street constructions are not handled. libpostal, trained on
OpenStreetMap and OpenAddresses data globally, handles Canadian addresses (including
Quebec French) with high accuracy.

### Deployment

The `pelias/libpostal-service` Docker image wraps libpostal in a Go HTTP server
exposing `GET /parse?address=...`. Managed via a dedicated systemd service unit.

**`libpostal.service`:**

```ini
[Unit]
Description=libpostal address parsing service
After=docker.service
Requires=docker.service

[Service]
Restart=always
ExecStartPre=-/usr/bin/docker stop libpostal
ExecStartPre=-/usr/bin/docker rm libpostal
ExecStart=/usr/bin/docker run --rm --name libpostal \
  -p 127.0.0.1:4400:4400 \
  --memory=2.5g \
  pelias/libpostal-service
ExecStop=/usr/bin/docker stop libpostal

[Install]
WantedBy=multi-user.target
```

- Port bound to `127.0.0.1` — not externally reachable
- `--memory=2.5g` bounds OOM exposure on a no-swap VM (7.2 GB total RAM; ~660 MB
  baseline; libpostal ~2 GB; leaves ~4.5 GB headroom at steady state)
- Disk: Docker image + model data ~2.5 GB against 9.7 GB available

**address-validator.service addition:**

```ini
After=libpostal.service postgresql.service
Wants=libpostal.service
```

`Wants=` (not `Requires=`): address-validator starts regardless. US requests are
unaffected if libpostal is down. CA parse/standardize return 503 until libpostal
recovers.

### Configuration

New env var: `LIBPOSTAL_URL` (default `http://localhost:4400`). Added to a new
`ParserConfig` pydantic-settings model in `services/validation/config.py` (or a
parallel `services/parser_config.py`).

### Lifespan

`LibpostalClient` initialised at startup with a health-check parse. Stored on
`app.state.libpostal_client`. Failure is logged as a warning but does not prevent
startup or affect US requests.

---

## Parsing

### LibpostalClient

New module: `src/address_validator/services/libpostal_client.py`

- Async `httpx.AsyncClient` (persistent connection)
- `async def parse(address: str) -> dict[str, str]` — calls pelias service, applies
  libpostal → ISO 19160-4 tag mapping, then passes result through the street splitter
- Raises `LibpostalUnavailableError` (→ 503) on connection failure

**libpostal → ISO 19160-4 tag mapping:**

| libpostal label              | ISO 19160-4 element          | Notes                                      |
|------------------------------|------------------------------|--------------------------------------------|
| `house_number`               | `premise_number`             |                                            |
| `house`                      | `premise_name`               | Building name                              |
| `road`                       | → street splitter            | Composite; decomposed by splitter          |
| `unit`                       | `sub_premise_number`         |                                            |
| `city`                       | `locality`                   |                                            |
| `state`                      | `administrative_area`        | Province for CA                            |
| `postcode`                   | `postcode`                   | Preserved as-is (`A1A 1A1`)               |
| `suburb` / `city_district`   | `dependent_locality`         |                                            |
| `po_box`                     | `general_delivery`           |                                            |
| `country`                    | _(dropped)_                  | Already known from request                 |

### Bilingual Street Component Splitter

New module: `src/address_validator/services/street_splitter.py`

libpostal returns the entire street as a single `road` token. The splitter
decomposes it into ISO 19160-4 thoroughfare elements. Required for correct
representation of both English-style (`Main St`) and French-style (`rue des Lilas`)
Canadian addresses, as well as Quebec directionals (`Ouest`, `Nord-Est`).

**Algorithm (left-to-right, position-aware):**

1. Normalise: strip extra whitespace; preserve case for name; lowercase for lookup
2. **Leading type check:** if first token matches the leading/either type table →
   extract as `thoroughfare_leading_type`; remainder is candidate string
3. **Trailing directional check:** if last token(s) of candidate match the bilingual
   directional table → extract as `thoroughfare_post_direction`
4. **Trailing type check:** if last remaining token matches the trailing/either type
   table → extract as `thoroughfare_trailing_type`
5. Remainder → `thoroughfare_name`
6. **Fallback:** on any ambiguous or unrecognised construction, store the full `road`
   value in `thoroughfare_name` without splitting

**Type table structure:**

```python
# Each entry: normalised_form → position ("leading", "trailing", "either")
STREET_TYPES: dict[str, str] = {
    # French — leading position
    "rue": "leading", "boulevard": "either", "avenue": "either",
    "chemin": "leading", "côte": "leading", "montée": "leading",
    "rang": "leading", "route": "leading", "voie": "leading",
    "place": "either", "allée": "leading", "impasse": "leading",
    "promenade": "either", "quai": "leading", "ruelle": "leading",
    "sentier": "leading", "traverse": "leading", "croissant": "either",
    # English — trailing position
    "street": "trailing", "st": "trailing",
    "avenue": "either",   "ave": "either",
    "boulevard": "either","blvd": "either",
    "drive": "trailing",  "dr": "trailing",
    "road": "trailing",   "rd": "trailing",
    "lane": "trailing",   "ln": "trailing",
    "court": "trailing",  "crt": "trailing",
    "crescent": "trailing","cres": "trailing",
    "place": "either",    "pl": "either",
    "way": "trailing",
    # … full tables in canada_post_data/suffixes.py and usps_data/suffixes.py
}
```

**Directional table:** English (`North`, `South`, `East`, `West`, `NE`, `NW`, `SE`,
`SW` and abbreviations) + French (`Nord`, `Sud`, `Est`, `Ouest`, `Nord-Est`,
`Nord-Ouest`, `Sud-Est`, `Sud-Ouest`).

**Article passthrough:** Tokens `de`, `des`, `du`, `de la`, `de l'` immediately
following a leading type are treated as part of `thoroughfare_name`, not as type
tokens (`rue des Lilas` → `leading_type=rue`, `name=des Lilas`).

### Country routing in `parser.py`

```python
async def parse_address(
    address: str,
    country: str,
    libpostal_client: LibpostalClient | None = None,
) -> dict[str, str]:
    if country == "US":
        return _parse_us(address)      # usaddress; output remapped to ISO 19160-4 keys
    if country == "CA":
        if libpostal_client is None:
            raise LibpostalUnavailableError()
        return await libpostal_client.parse(address)
```

**US path remapping:** `_parse_us()` wraps the existing usaddress call and translates
output to ISO 19160-4 keys using the inverse of the `usps-pub28` profile mapping. The
usaddress parsing and post-parse recovery logic is unchanged.

**`parse_type` audit field:** Extended to `"usaddress"` (existing) and `"libpostal"`
(new).

---

## Standardization

### Canada Post national profile

New module: `src/address_validator/canada_post_data/`

```
canada_post_data/
  __init__.py
  provinces.py   # 13 entries: {"AB": "Alberta", "BC": "British Columbia", …}
  suffixes.py    # Canadian street type table (bilingual; ~60 entries)
  spec.py        # CANADA_POST_SPEC = "canada-post"; CANADA_POST_SPEC_VERSION = "2025"
```

### Standardization scope for `spec="canada-post"`

| Component              | Normalisation                                               |
|------------------------|-------------------------------------------------------------|
| `administrative_area`  | Expand to abbreviation; validate against province table     |
| `postcode`             | Uppercase; ensure FSA + single space + LDU (`a1a1a1` → `A1A 1A1`) |
| `thoroughfare_trailing_type` | Normalise against Canada Post suffix table           |
| `thoroughfare_leading_type`  | Normalise against Canada Post suffix table           |
| `thoroughfare_pre_direction` / `thoroughfare_post_direction` | Same directional table as US + French entries |

### Standardizer routing

`standardize()` dispatches on `country`. US path (`_standardize_us()`) unchanged.
CA path calls new `_standardize_ca()` using `canada_post_data/` tables.

### `standardized` single-line output

Generated from the i18naddress template for `CA` via the existing
`core/address_format.py` builder. This is the ISO 19160-4 PATDL-equivalent rendering;
the i18naddress library provides per-country format rules already used by
`country_format.py`.

---

## Validation

- `SUPPORTED_COUNTRIES` is v2-scoped: `frozenset({"US", "CA"})`. v1 retains
  `frozenset({"US"})`.
- Google provider: zero changes. Already sets `supports_non_us = True`, handles
  `regionCode="CA"`, maps response via `_map_response_international()`.
- `/api/v2/validate` with CA raw string: routes through full pipeline (parse →
  standardize → Google). No longer requires pre-parsed components for CA (that
  restriction applied only to the v1 non-US components-only pathway).
- `/api/v2/validate` with CA components: existing non-US pathway (unchanged).

---

## Country Format Endpoint

`/api/v2/countries/{code}/format` — identical behaviour to v1. No changes to
`services/country_format.py`. Already returns correct Canadian data (provinces,
postal code pattern, bilingual labels) via i18naddress.

---

## Data Flow Summary

```
POST /api/v2/parse   { "address": "350 rue des Lilas Ouest, Quebec QC G1L 1B6",
                       "country": "CA" }
  ?component_profile=iso-19160-4  (default)

→ router/v2/parse.py
→ services/parser.py  parse_address("…", "CA", app.state.libpostal_client)
  → libpostal_client.parse("…")
    → GET http://localhost:4400/parse?address=…
    ← [{"label":"house_number","value":"350"},{"label":"road","value":"rue des Lilas Ouest"},
       {"label":"city","value":"Quebec"},{"label":"state","value":"QC"},
       {"label":"postcode","value":"G1L 1B6"}]
    → tag mapping → street_splitter.split("rue des Lilas Ouest")
      → thoroughfare_leading_type="rue", thoroughfare_name="des Lilas",
         thoroughfare_post_direction="Ouest"
    ← {"premise_number":"350","thoroughfare_leading_type":"rue",
       "thoroughfare_name":"des Lilas","thoroughfare_post_direction":"Ouest",
       "locality":"Quebec","administrative_area":"QC","postcode":"G1L 1B6"}
← ParseResponse
  components: ComponentSet(spec="raw", values={ISO keys})
  translated by component_profiles.translate_components(values, "iso-19160-4")  # no-op
← 200
```

---

## Migration Notes

### v1 internal change (transparent to clients)

The service layer now outputs ISO 19160-4 keys. v1 routers apply `translate_components(
values, "usps-pub28")` before returning. v1 API contract is byte-for-byte identical to
current behaviour.

### Client migration to v2

Clients must:
1. Update base URL from `/api/v1/` to `/api/v2/`
2. Update component key references from USPS names to ISO 19160-4 names

Or opt in to the legacy vocabulary during migration:

```
POST /api/v2/parse?component_profile=usps-pub28
```

---

## New Files

| Path | Purpose |
|---|---|
| `libpostal.service` | systemd unit for libpostal Docker sidecar |
| `src/address_validator/services/libpostal_client.py` | Async httpx client; tag mapping |
| `src/address_validator/services/street_splitter.py` | Bilingual street component splitter |
| `src/address_validator/services/component_profiles.py` | Translation layer; profile mappings |
| `src/address_validator/canada_post_data/__init__.py` | |
| `src/address_validator/canada_post_data/provinces.py` | 13 CA province abbreviations |
| `src/address_validator/canada_post_data/suffixes.py` | Bilingual street type table |
| `src/address_validator/canada_post_data/spec.py` | `CANADA_POST_SPEC*` constants |
| `src/address_validator/routers/v2/parse.py` | v2 parse handler |
| `src/address_validator/routers/v2/standardize.py` | v2 standardize handler |
| `src/address_validator/routers/v2/validate.py` | v2 validate handler |
| `src/address_validator/routers/v2/countries.py` | v2 countries handler |

---

## Modified Files (key)

| Path | Change |
|---|---|
| `services/parser.py` | Country routing; `_parse_us()` ISO remapping; `parse_type` extension |
| `services/standardizer.py` | `_standardize_ca()` dispatch; ISO key names throughout |
| `services/validation/config.py` | `ParserConfig` with `LIBPOSTAL_URL` |
| `middleware/api_version.py` | `/api/v2/` → `API-Version: 2` |
| `main.py` | Register v2 routers; `LibpostalClient` lifespan; `libpostal_client` on `app.state` |
| `routers/v1/parse.py` | Apply `translate_components(…, "usps-pub28")` on output |
| `routers/v1/standardize.py` | Same |
| `routers/v1/validate.py` | Same |

---

## Test Strategy

- **Translation layer:** Unit tests per profile; assert `usps-pub28` round-trip fidelity
  for all mapped keys; assert unknown keys pass through unchanged
- **Street splitter:** Unit tests covering English trailing type, French leading type,
  bilingual directionals, article passthrough, compound names, fallback path
- **libpostal client:** Mock HTTP; test tag mapping; test `LibpostalUnavailableError`
  on connection failure
- **CA parser:** Integration tests: English ON address, French QC address, postal code
  format variations, PO Box, building name
- **CA standardizer:** Unit tests for province normalisation, postal code formatting,
  suffix normalisation
- **v2 endpoints:** Integration tests via `TestClient`; assert ISO keys by default;
  assert USPS keys with `?component_profile=usps-pub28`; assert 422 on invalid profile
- **libpostal unavailable:** Assert US endpoints unaffected; CA parse/standardize → 503
- **v1 regression:** Existing v1 test suite passes unchanged; no modification to v1
  test files permitted
- **Coverage floor:** 80% line + branch maintained; CA paths must be covered

---

## Dependencies

```
uv add httpx          # if not already present (async libpostal client)
```

libpostal itself runs in Docker — no Python package required. No other new Python
dependencies.
