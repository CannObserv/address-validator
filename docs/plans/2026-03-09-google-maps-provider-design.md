# Design: Google Maps Validation Provider + ValidateResponseV1 Normalization

**Date:** 2026-03-09
**Status:** Approved

## Background

Issue #18 added the address validation endpoint (`POST /api/v1/validate`) with a
USPS v3 provider and a `NullProvider` fallback.  This design adds a Google Maps
Address Validation provider as an independent primary alternative to the USPS
provider, and normalizes `ValidateResponseV1` to mirror the structure of
`StandardizeResponseV1`.

## Goals

1. Add `GoogleProvider` — a full drop-in replacement for `USPSProvider` that
   additionally returns geocoordinates (lat/lng).
2. Normalize `ValidateResponseV1` to align structurally with `StandardizeResponseV1`.
3. Surface Google-specific verdict signals as `warnings` entries.

## Non-goals

- International address support (scope remains `SUPPORTED_COUNTRIES = {"US"}`).
- `placeId` exposure (deferred; not currently requested).
- Caching layer (deferred).

---

## 1. Model Changes (`models.py`)

### 1.1 New `ValidationResult` model

Groups provider-returned validation outcome metadata.  Replaces the flat
`validation_status`, `dpv_match_code`, and `provider` fields on
`ValidateResponseV1`.

```python
class ValidationResult(BaseModel):
    status: Literal[
        "confirmed",
        "confirmed_missing_secondary",
        "confirmed_bad_secondary",
        "not_confirmed",
        "unavailable",
    ]
    dpv_match_code: Literal["Y", "S", "D", "N"] | None = None
    provider: str | None = None
```

DPV semantics are unchanged:

| DPV code | `status` |
|---|---|
| `Y` | `confirmed` |
| `S` | `confirmed_missing_secondary` |
| `D` | `confirmed_bad_secondary` |
| `N` | `not_confirmed` |
| (none) | `unavailable` |

### 1.2 Rewritten `ValidateResponseV1`

Mirrors `StandardizeResponseV1` field layout.  All address fields are
`str | None` because corrected components are only present when the provider
returns them (i.e. not for `not_confirmed` or `unavailable`).

```python
class ValidateResponseV1(BaseModel):
    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    region: str | None = None
    postal_code: str | None = None   # full postal code; ZIP+4 (e.g. "90210-1234") when available
    country: str
    validated: str | None = None     # single-line canonical address, two-space separator convention
    components: ComponentSet | None = None
    validation: ValidationResult
    latitude: float | None = None
    longitude: float | None = None
    warnings: list[str] = Field(default_factory=list)
    api_version: Literal["1"] = "1"
```

**`postal_code` is the single canonical postal identifier.**  Providers
populate it at whatever precision they have: 5-digit ZIP, full ZIP+4, or
international formats.  The `max_length=20` constraint on the request model
already accommodates all formats; no response-side constraint is needed.  There
is no separate `zip_plus4` field.

**`vacant`** (USPS/CASS vacancy indicator) is surfaced in `components.values`
under the key `"vacant"` with value `"Y"` or `"N"` when the provider returns it.

**Field comparison with `StandardizeResponseV1`:**

| Field | `StandardizeResponseV1` | `ValidateResponseV1` |
|---|---|---|
| `address_line_1` | `str` | `str \| None` |
| `address_line_2` | `str` | `str \| None` |
| `city` | `str` | `str \| None` |
| `region` | `str` | `str \| None` |
| `postal_code` | `str` | `str \| None` |
| `country` | `str` | `str` |
| `standardized` / `validated` | `str` | `str \| None` |
| `components` | `ComponentSet` | `ComponentSet \| None` |
| `warnings` | `list[str]` | `list[str]` |
| `api_version` | `Literal["1"]` | `Literal["1"]` |
| `validation` | — | `ValidationResult` |
| `latitude` | — | `float \| None` |
| `longitude` | — | `float \| None` |

---

## 2. New Service Files

### 2.1 `services/validation/google_client.py`

Thin async HTTP client for the Google Address Validation API.

**Authentication:** API key passed as `?key=<GOOGLE_API_KEY>` query parameter.
No OAuth2, no token cache.

**Request:**
```json
{
  "address": { "addressLines": ["<address>", "<city>, <region> <postal_code>"] },
  "enableUspsCass": true
}
```
`enableUspsCass: true` is required to obtain USPS CASS-certified DPV codes.

**HTTP client config:** 15s timeout, 5s connect, 20 max connections, 10
keepalive — same as the USPS client.

**No rate limiter.** Google's Address Validation API does not publish a
per-second rate limit.  A token-bucket limiter can be added if throttling
errors are observed in production.

**`_map_response(raw: dict) -> dict` output keys:**

| Key | Source |
|---|---|
| `dpv_match_code` | `uspsData.dpvConfirmation` |
| `address_line_1` | `uspsData.standardizedAddress.firstAddressLine` |
| `address_line_2` | `uspsData.standardizedAddress.secondAddressLine` (if present) |
| `city` | `uspsData.standardizedAddress.city` |
| `region` | `uspsData.standardizedAddress.state` |
| `postal_code` | `uspsData.standardizedAddress.zipCode` + `-` + `uspsData.standardizedAddress.zipCodeExtension` (when extension present) |
| `vacant` | `uspsData.dpvVacant` |
| `latitude` | `geocode.location.latitude` |
| `longitude` | `geocode.location.longitude` |
| `has_inferred_components` | `verdict.hasInferredComponents` |
| `has_replaced_components` | `verdict.hasReplacedComponents` |
| `has_unconfirmed_components` | `verdict.hasUnconfirmedComponents` |

### 2.2 `services/validation/google_provider.py`

Maps `GoogleClient` output to `ValidateResponseV1`.

**DPV mapping:** identical 1:1 mapping as `USPSProvider` (Y/S/D/N → status).

**`components` construction:** `ComponentSet(spec="usps-pub28",
spec_version=USPS_PUB28_SPEC_VERSION, values={...})` — same spec tag as the
standardizer, since CASS-corrected components conform to Pub 28.  The `values`
dict includes `address_line_1`, `city`, `region`, `postal_code`, and `vacant`
(when present).

**`validated` string:** built with two-space separator convention matching
`StandardizeResponseV1.standardized`.

**Warning strings (emitted when truthy):**

| Flag | Warning string |
|---|---|
| `has_inferred_components` | `"Provider inferred one or more address components not present in input"` |
| `has_replaced_components` | `"Provider replaced one or more address components"` |
| `has_unconfirmed_components` | `"One or more address components could not be confirmed"` |

**Module-level singleton** — no token state, but keeps `httpx.AsyncClient`
alive across requests.

---

## 3. Factory Changes (`services/validation/factory.py`)

### New environment variable

| Variable | Values | Default | Notes |
|---|---|---|---|
| `VALIDATION_PROVIDER` | `none`, `usps`, `google` | `none` | Gains `google` option |
| `GOOGLE_API_KEY` | string | — | Required when `VALIDATION_PROVIDER=google` |

### Singleton

Add `_google_provider: GoogleProvider | None = None` alongside
`_usps_provider`.  `get_provider()` gains a `google` branch that constructs
`GoogleProvider(GoogleClient(api_key=GOOGLE_API_KEY))` on first call and
caches it.

---

## 4. Updates to Existing Providers

### `null_provider.py`

Return `ValidationResult(status="unavailable")`.  All address fields `None`,
`warnings=[]`.

### `usps_provider.py`

Populate new shape: `address_line_1/2`, `city`, `region`, `postal_code`
(ZIP+4 when available), `validated`, `components` (`ComponentSet`).
`latitude`/`longitude` remain `None`.  `warnings` remains `[]`.

---

## 5. AGENTS.md Update

Add `GOOGLE_API_KEY` row to the validation provider env var table.

---

## 6. Test Strategy

| File | Coverage |
|---|---|
| `tests/unit/validation/test_google_client.py` | `_map_response()` for all DPV codes; verdict flag extraction; missing/partial USPS data; HTTP error handling |
| `tests/unit/validation/test_google_provider.py` | All four DPV → status mappings; all three warning strings; lat/lng population; `ComponentSet` spec tag; `vacant` in `components.values`; `postal_code` ZIP+4 formatting |
| `tests/unit/validation/test_provider_factory.py` | `google` branch construction; `_google_provider` singleton reset fixture (same pattern as USPS) |
| `tests/unit/test_models.py` | `ValidationResult` shape; new `ValidateResponseV1` field layout |
| Existing validate router + USPS tests | Update assertions to new response shape |
