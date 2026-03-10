# Design: Standardize `/validate` Request Shape

**Date:** 2026-03-10
**Status:** Approved

## Context

Issue #21 standardized the `/validate` response shape to mirror `/standardize`.
This design completes the alignment by standardizing the *request* shape as well.

Currently `ValidateRequestV1` accepts individual named fields (`address` as the
street line only, plus optional `city`, `region`, `postal_code`). `StandardizeRequestV1`
accepts either a raw address string (`address`) or a pre-parsed components dict
(`components`). Callers cannot pipe `/standardize` output directly into `/validate`
without manual field mapping.

## Goal

`ValidateRequestV1` mirrors `StandardizeRequestV1`: accepts either a raw address
string or a pre-parsed components dict. Both input modes are run through the full
parse → standardize pipeline before the validation provider is called, so providers
always receive clean, USPS-formatted components.

Backward compatibility with the existing individual-field request shape is **not**
required — callers will be updated.

## Request model (`models.py`)

`ValidateRequestV1` drops `city`, `region`, `postal_code` and gains `components`:

```python
class ValidateRequestV1(CountryRequestMixin):
    """Request body for POST /api/v1/validate.

    Accepts either a raw address string or pre-parsed components — mirroring
    StandardizeRequestV1.  In both cases the input is run through the full
    parse → standardize pipeline before being sent to the validation provider,
    so providers always receive clean, USPS-formatted components.

    When both fields are supplied, ``components`` takes precedence.
    """
    address: str | None = Field(None, max_length=1000)
    components: dict[str, str] | None = None
```

## Router (`routers/v1/validate.py`)

The router gains the same normalization block as `/standardize`, then calls the
provider with the standardized result:

```
check_country
→ resolve input (components > address; error if neither)
→ parse (if raw string input)
→ standardize → StandardizeResponseV1
→ provider.validate(std)
→ merge std.warnings into response.warnings
→ return ValidateResponseV1
```

**Warning propagation:** warnings emitted by the parse and standardize steps are
merged into `ValidateResponseV1.warnings` by the router after the provider returns:

```python
result = await provider.validate(std)
if std.warnings:
    result = result.model_copy(update={"warnings": std.warnings + result.warnings})
return result
```

The router owns all normalization. Providers receive only clean, structured input.

The endpoint docstring describes the pipeline explicitly.

**Error handling** (identical to `/standardize`):
- Neither `address` nor `components` provided → `400 components_or_address_required`
- `address` present but blank → `400 address_required`
- Country check unchanged; parse/standardize failures propagate naturally

## Provider protocol (`services/validation/protocol.py`)

`ValidationProvider.validate` signature changes:

```python
async def validate(self, std: StandardizeResponseV1) -> ValidateResponseV1:
```

Providers no longer see raw user input — they receive a fully normalized address.

## Provider implementations

All three implementations are updated:

| Provider | Change |
|---|---|
| `NullProvider` | Reads `std.country` for the response |
| `USPSProvider` | Maps `std.address_line_1/city/region/postal_code` to USPS API fields |
| `GoogleProvider` | Same field remapping; `std.address_line_2` available but unused |

`address_line_2` is available from the standardizer output but not currently sent
to providers — consistent with existing behavior.

## Test strategy

- `tests/unit/validation/test_provider_*.py` — update mock input from `ValidateRequestV1`
  to `StandardizeResponseV1`
- `tests/unit/routers/test_validate*.py` — cover new request shapes and pipeline:
  - Raw string → parse+standardize → provider receives `StandardizeResponseV1`
  - Components dict → standardize → provider receives `StandardizeResponseV1`
  - `components` takes precedence when both supplied
  - Parse/standardize warnings propagate into response `warnings`
  - Missing-input error paths (`components_or_address_required`, `address_required`)

## Files touched

| File | Change |
|---|---|
| `models.py` | Replace `ValidateRequestV1` fields |
| `routers/v1/validate.py` | Add normalization block; call `provider.validate(std)` |
| `services/validation/protocol.py` | Update `validate()` signature |
| `services/validation/null_provider.py` | Update `validate()` to accept `StandardizeResponseV1` |
| `services/validation/usps_provider.py` | Remap fields from `StandardizeResponseV1` |
| `services/validation/google_provider.py` | Remap fields from `StandardizeResponseV1` |
| `tests/unit/validation/test_provider_*.py` | Update mock inputs |
| `tests/unit/routers/test_validate*.py` | New request shapes + pipeline assertions |
