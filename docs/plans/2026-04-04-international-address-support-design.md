# International Address Support Design

**Date:** 2026-04-04
**Issue:** #86

## Overview

Two independent tracks, sequenced by complexity:

1. **`GET /api/v1/countries/{code}/format`** — new metadata endpoint; self-contained
2. **`/validate` non-US with pre-parsed components** — route to Google provider, bypassing the USPS pipeline

`/standardize` remains US-only. Non-US raw address strings on `/validate` are rejected (can't parse/standardize first).

---

## Track 1 — Countries format endpoint

### Route

```
GET /api/v1/countries/{code}/format
```

- Auth: `X-API-Key` (standard v1 dep)
- `{code}` uppercased before lookup
- `Cache-Control: public, max-age=86400` response header (matches Power Map's 24h in-process TTL)

### Error responses

| Condition | Status | `error` |
|---|---|---|
| Invalid ISO2 code | 422 | `invalid_country_code` |
| Valid ISO2, no format data | 404 | `country_format_not_found` |

### New models (`models.py`)

```python
class CountryFieldDefinition(BaseModel):
    key: str
    label: str
    required: bool
    options: list[str] | None = None   # region subdivisions when available
    pattern: str | None = None          # postal_code regex hint when defined

class CountryFormatResponse(BaseModel):
    country: str
    fields: list[CountryFieldDefinition]
```

Fields absent from `fields` should be hidden in the UI (per issue spec).

### Field keys

| Our key | Library field |
|---|---|
| `address_line_1` | `street_address` |
| `address_line_2` | `extended_address` |
| `city` | `city` |
| `region` | `country_area` |
| `postal_code` | `postal_code` |

### Data source

New dependency: `google-i18n-address` (wraps Google libaddressinput dataset — the reference cited in the issue).

- Labels: country-specific (e.g. "Province" for CA, "State" for US, "County" for GB)
- Required: from library's per-country required fields set
- Options: subdivision list for `region` when the country has enumerated provinces/states; omitted otherwise
- Pattern: postal code regex from library; omitted when not defined
- Field order: follows the library's format string

### New files

- `src/address_validator/routers/v1/countries.py` — route handler
- `src/address_validator/services/country_format.py` — `google-i18n-address` mapping logic

Register the new router in `main.py` alongside the other v1 routers.

### Tests

- Valid country with full data (e.g. `US`, `CA`, `GB`)
- Invalid ISO2 → 422
- Valid ISO2 with no library data → 404
- `Cache-Control` header present
- `options` present for region when subdivisions exist; absent for free-text region countries
- `pattern` present for postal_code when regex defined; absent otherwise

---

## Track 2 — `/validate` non-US with pre-parsed components

### Scope

- Non-US + `components` → bypass USPS pipeline, route to Google
- Non-US + raw `address` string → 422 `country_not_supported`
- US requests: no change

### Changes

#### `routers/v1/validate.py`

Replace the unconditional `check_country(req.country)` with conditional logic:

```python
if req.country != "US":
    if not req.components:
        raise APIError(
            status_code=422,
            error="country_not_supported",
            message=(
                f"Raw address strings are only supported for US addresses. "
                f"Supply pre-parsed components for non-US addresses."
            ),
        )
    # Non-US + components: skip USPS pipeline
    std = _build_passthrough_std(req.components, req.country)
    raw_input = json.dumps(req.components, separators=(",", ":"), ensure_ascii=True)
else:
    check_country(req.country)
    # ... existing US parse → standardize pipeline
```

`_build_passthrough_std()` constructs a `StandardizeResponseV1` directly from the raw components dict with `country` set and `spec=None` (no USPS Pub 28 spec applies). The existing `std.warnings` merge still applies.

**Edge case:** non-US + `components` but no Google provider configured. The current provider is the null provider or USPS — neither handles non-US. The router checks whether the active provider can handle non-US before calling it; if not, raises 422 `country_not_supported` with message indicating that a Google provider is required for non-US validation.

#### `services/validation/google_client.py`

`validate_address()` gains a `country: str = "US"` parameter:

- **US path** (unchanged): `enableUspsCass: True`, response parsed from `uspsData.standardizedAddress`
- **Non-US path**: `regionCode=country`, no `enableUspsCass`, response parsed from `result.address.postalAddress` + `result.verdict` via new `_map_response_international()`

Non-US status mapping (no DPV codes):

| Google `verdict` | Our `status` |
|---|---|
| `addressComplete: true`, no unconfirmed components | `confirmed` |
| `addressComplete: true`, has unconfirmed components | `confirmed` + warning |
| `addressComplete: false`, geocodable | `invalid` |
| Not geocodable | `not_found` |

#### `services/validation/google_provider.py`

Pass `std.country` to `client.validate_address()`. Update docstring to remove "US addresses" language.

### Tests

- Non-US + components + Google configured → 200 with mapped status
- Non-US + raw address string → 422 `country_not_supported`
- Non-US + components + no Google provider → 422 `country_not_supported`
- US requests unchanged (regression)
- `_map_response_international()` unit tests: each verdict → status mapping

---

## Dependency

```
uv add google-i18n-address
```

Track 1 only. Track 2 has no new dependencies.

## Sequencing

Track 1 and Track 2 are independent — can be implemented in parallel or sequentially.
Track 1 is lower risk (no pipeline changes) and delivers immediate value to Power Map.
