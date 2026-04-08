# Canada Parity v2 — Plan 1: ISO Foundation + API v2 Surface

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename all internal component keys from USPS snake_case to ISO 19160-4 element names, wire v1 routers to translate back to USPS keys (invisible to v1 clients), and stand up the `/api/v2/` surface with ISO keys by default and a `component_profile` query parameter.

**Architecture:** Service layer adopts ISO 19160-4 as its internal vocabulary. v1 routers apply a `usps-pub28` translation at the response boundary so existing clients see no change. v2 routers expose ISO keys by default; `?component_profile=usps-pub28` restores old key names for migrating clients. No Canadian address support yet — that is Plan 2.

**Tech Stack:** Python 3.12+, FastAPI, usaddress, pydantic-settings. No new dependencies.

---

## File Map

**Create:**
- `src/address_validator/services/component_profiles.py` — profile mappings + translate function
- `src/address_validator/routers/v2/__init__.py`
- `src/address_validator/routers/v2/parse.py`
- `src/address_validator/routers/v2/standardize.py`
- `src/address_validator/routers/v2/validate.py`
- `src/address_validator/routers/v2/countries.py`
- `tests/unit/test_component_profiles.py`
- `tests/integration/test_v2_parse.py`
- `tests/integration/test_v2_standardize.py`
- `tests/integration/test_v2_validate.py`

**Modify:**
- `src/address_validator/models.py` — add `ParseResponseV2`, `StandardizeResponseV2`, `ValidateResponseV2`, `CountryFormatResponseV2`
- `src/address_validator/services/parser.py` — rename `TAG_NAMES` + all internal key refs
- `src/address_validator/services/standardizer.py` — rename all internal key refs
- `src/address_validator/routers/v1/parse.py` — apply `usps-pub28` translation
- `src/address_validator/routers/v1/standardize.py` — apply `usps-pub28` translation
- `src/address_validator/middleware/api_version.py` — add v2 header
- `src/address_validator/main.py` — register v2 routers
- `tests/unit/test_parser.py` — update dict fixtures to ISO keys
- `tests/unit/test_standardizer.py` — update dict fixtures to ISO keys

---

## Task 1: component_profiles.py

**Files:**
- Create: `src/address_validator/services/component_profiles.py`
- Test: `tests/unit/test_component_profiles.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_component_profiles.py
"""Tests for component_profiles translation layer."""
import pytest
from address_validator.services.component_profiles import (
    VALID_PROFILES,
    translate_components,
)


class TestTranslateComponents:
    def test_iso_profile_is_identity(self) -> None:
        values = {"thoroughfare_name": "MAIN", "administrative_area": "WA", "postcode": "98101"}
        assert translate_components(values, "iso-19160-4") == values

    def test_usps_pub28_renames_core_keys(self) -> None:
        values = {
            "premise_number": "123",
            "thoroughfare_name": "MAIN",
            "thoroughfare_trailing_type": "ST",
            "locality": "SEATTLE",
            "administrative_area": "WA",
            "postcode": "98101",
        }
        result = translate_components(values, "usps-pub28")
        assert result["address_number"] == "123"
        assert result["street_name"] == "MAIN"
        assert result["street_name_post_type"] == "ST"
        assert result["city"] == "SEATTLE"
        assert result["state"] == "WA"
        assert result["zip_code"] == "98101"
        assert "premise_number" not in result
        assert "thoroughfare_name" not in result

    def test_unknown_keys_pass_through_unchanged(self) -> None:
        values = {"premise_number": "1", "some_future_key": "X"}
        result = translate_components(values, "usps-pub28")
        assert result["address_number"] == "1"
        assert result["some_future_key"] == "X"

    def test_unknown_profile_is_identity(self) -> None:
        values = {"thoroughfare_name": "OAK"}
        assert translate_components(values, "unknown-profile") == values

    def test_canada_post_profile_is_identity(self) -> None:
        # canada-post is reserved; currently identical to iso-19160-4
        values = {"thoroughfare_name": "MAIN", "postcode": "V5K 0A1"}
        assert translate_components(values, "canada-post") == values

    def test_valid_profiles_contains_expected_values(self) -> None:
        assert "iso-19160-4" in VALID_PROFILES
        assert "usps-pub28" in VALID_PROFILES
        assert "canada-post" in VALID_PROFILES
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/test_component_profiles.py -v
```

Expected: `ModuleNotFoundError` or `ImportError` — module does not exist yet.

- [ ] **Step 3: Create component_profiles.py**

```python
# src/address_validator/services/component_profiles.py
"""ISO 19160-4 component key translation profiles.

The service layer uses strict ISO 19160-4 element names throughout.
This module translates those keys into alternative vocabularies at the
response boundary — e.g. the ``usps-pub28`` profile restores the
snake_case USPS key names used by v1 clients.

``translate_components`` is a pure function: it does not modify the
input dict and unknown keys always pass through unchanged.
"""

# Keys in this mapping are ISO 19160-4 element names.
# Values are the target vocabulary keys for that profile.
_USPS_PUB28: dict[str, str] = {
    "premise_number":                  "address_number",
    "premise_number_prefix":           "address_number_prefix",
    "premise_number_suffix":           "address_number_suffix",
    "premise_name":                    "building_name",
    "thoroughfare_pre_direction":      "street_name_pre_directional",
    "thoroughfare_pre_modifier":       "street_name_pre_modifier",
    "thoroughfare_leading_type":       "street_name_pre_type",
    "thoroughfare_name":               "street_name",
    "thoroughfare_trailing_type":      "street_name_post_type",
    "thoroughfare_post_direction":     "street_name_post_directional",
    "thoroughfare_post_modifier":      "street_name_post_modifier",
    "sub_premise_type":                "occupancy_type",
    "sub_premise_number":              "occupancy_identifier",
    "dependent_sub_premise_type":      "subaddress_type",
    "dependent_sub_premise_number":    "subaddress_identifier",
    "locality":                        "city",
    "administrative_area":             "state",
    "postcode":                        "zip_code",
    "general_delivery_type":           "usps_box_type",
    "general_delivery":                "usps_box_id",
    "general_delivery_group_type":     "usps_box_group_type",
    "general_delivery_group":          "usps_box_group_id",
    "addressee":                       "recipient",
    "landmark":                        "landmark_name",
    "second_thoroughfare_name":        "second_street_name",
    "second_thoroughfare_pre_direction": "second_street_name_pre_directional",
    "second_thoroughfare_pre_modifier":  "second_street_name_pre_modifier",
    "second_thoroughfare_leading_type":  "second_street_name_pre_type",
    "second_thoroughfare_post_direction": "second_street_name_post_directional",
    "second_thoroughfare_post_modifier":  "second_street_name_post_modifier",
    "second_thoroughfare_trailing_type":  "second_street_name_post_type",
}

# Profile registry.  ``iso-19160-4`` and ``canada-post`` use an empty
# mapping (identity transform).  Add entries here as new profiles are needed.
_PROFILES: dict[str, dict[str, str]] = {
    "iso-19160-4": {},
    "usps-pub28":  _USPS_PUB28,
    "canada-post": {},  # reserved; diverges from ISO as Canada Post spec requires
}

#: Set of valid profile identifiers accepted by the API.
VALID_PROFILES: frozenset[str] = frozenset(_PROFILES)


def translate_components(values: dict[str, str], profile: str) -> dict[str, str]:
    """Return *values* with keys renamed per *profile*.

    Unknown keys pass through unchanged.  Unknown *profile* strings are
    treated as the identity transform (ISO 19160-4).
    """
    mapping = _PROFILES.get(profile, {})
    if not mapping:
        return values
    return {mapping.get(k, k): v for k, v in values.items()}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/unit/test_component_profiles.py -v
```

Expected: all 6 tests pass.

- [ ] **Step 5: Lint**

```bash
uv run ruff check src/address_validator/services/component_profiles.py tests/unit/test_component_profiles.py --fix
uv run ruff format src/address_validator/services/component_profiles.py tests/unit/test_component_profiles.py
```

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/services/component_profiles.py tests/unit/test_component_profiles.py
git commit -m "#90 feat: add component_profiles ISO 19160-4 translation layer"
```

---

## Task 2: models.py — v2 response models

**Files:**
- Modify: `src/address_validator/models.py`

- [ ] **Step 1: Add v2 response models**

Append the following to `src/address_validator/models.py` after the existing v1 models:

```python
# ---------------------------------------------------------------------------
# Response models — v2
# ---------------------------------------------------------------------------


class ParseResponseV2(BaseModel):
    """Response body for POST /api/v2/parse."""

    input: str
    country: str
    components: ComponentSet
    type: str
    warnings: list[str] = Field(default_factory=list)
    api_version: Literal["2"] = "2"


class StandardizeResponseV2(BaseModel):
    """Response body for POST /api/v2/standardize."""

    address_line_1: str
    address_line_2: str
    city: str
    region: str
    postal_code: str
    country: str
    standardized: str
    components: ComponentSet
    warnings: list[str] = Field(default_factory=list)
    api_version: Literal["2"] = "2"


class ValidateResponseV2(BaseModel):
    """Response body for POST /api/v2/validate."""

    address_line_1: str = ""
    address_line_2: str = ""
    city: str = ""
    region: str = ""
    postal_code: str = ""
    country: str
    validated: str | None = None
    validation: ValidationResult
    components: ComponentSet | None = None
    warnings: list[str] = Field(default_factory=list)
    api_version: Literal["2"] = "2"


class CountryFormatResponseV2(BaseModel):
    """Response body for GET /api/v2/countries/{code}/format."""

    country: str = Field(..., description="ISO 3166-1 alpha-2 country code (uppercased).")
    fields: list[CountryFieldDefinition] = Field(
        ...,
        description=(
            "Address fields for this country, in form display order. "
            "Fields absent from this list should be hidden in the UI."
        ),
    )
    api_version: Literal["2"] = "2"
```

- [ ] **Step 2: Run existing tests to confirm no breakage**

```bash
uv run pytest tests/unit/test_models.py -v --no-cov
```

Expected: all pass.

- [ ] **Step 3: Lint and commit**

```bash
uv run ruff check src/address_validator/models.py --fix && uv run ruff format src/address_validator/models.py
git add src/address_validator/models.py
git commit -m "#90 feat: add v2 response models to models.py"
```

---

## Task 3: parser.py — rename TAG_NAMES to ISO 19160-4

**Files:**
- Modify: `src/address_validator/services/parser.py`
- Modify: `tests/unit/test_parser.py`

This task renames all internal component key strings in `parser.py` from USPS snake_case to ISO 19160-4 element names. The public interface (`parse_address()`) signature does not change, but `ComponentSet.values` in the returned response will now contain ISO keys.

- [ ] **Step 1: Update TAG_NAMES dict (parser.py lines 51–87)**

Replace the entire `TAG_NAMES` block with:

```python
TAG_NAMES: dict[str, str] = {
    "AddressNumber":                    "premise_number",
    "AddressNumberPrefix":              "premise_number_prefix",
    "AddressNumberSuffix":              "premise_number_suffix",
    "StreetNamePreDirectional":         "thoroughfare_pre_direction",
    "StreetNamePreModifier":            "thoroughfare_pre_modifier",
    "StreetNamePreType":                "thoroughfare_leading_type",
    "StreetName":                       "thoroughfare_name",
    "StreetNamePostDirectional":        "thoroughfare_post_direction",
    "StreetNamePostModifier":           "thoroughfare_post_modifier",
    "StreetNamePostType":               "thoroughfare_trailing_type",
    "SubaddressType":                   "dependent_sub_premise_type",
    "SubaddressIdentifier":             "dependent_sub_premise_number",
    "OccupancyType":                    "sub_premise_type",
    "OccupancyIdentifier":              "sub_premise_number",
    "PlaceName":                        "locality",
    "StateName":                        "administrative_area",
    "ZipCode":                          "postcode",
    "USPSBoxType":                      "general_delivery_type",
    "USPSBoxID":                        "general_delivery",
    "USPSBoxGroupType":                 "general_delivery_group_type",
    "USPSBoxGroupID":                   "general_delivery_group",
    "BuildingName":                     "premise_name",
    "Recipient":                        "addressee",
    "NotAddress":                       "not_address",
    "IntersectionSeparator":            "intersection_separator",
    "LandmarkName":                     "landmark",
    "CornerOf":                         "corner_of",
    # Second street (intersections)
    "SecondStreetName":                 "second_thoroughfare_name",
    "SecondStreetNamePreDirectional":   "second_thoroughfare_pre_direction",
    "SecondStreetNamePreModifier":      "second_thoroughfare_pre_modifier",
    "SecondStreetNamePreType":          "second_thoroughfare_leading_type",
    "SecondStreetNamePostDirectional":  "second_thoroughfare_post_direction",
    "SecondStreetNamePostModifier":     "second_thoroughfare_post_modifier",
    "SecondStreetNamePostType":         "second_thoroughfare_trailing_type",
}
```

- [ ] **Step 2: Update constants that reference internal key names**

Replace the three constants immediately after `TAG_NAMES`:

```python
# Designator slots in priority order: primary unit first, then sub-unit.
_UNIT_SLOT_PAIRS = (
    ("sub_premise_type", "sub_premise_number"),
    ("dependent_sub_premise_type", "dependent_sub_premise_number"),
)

# Keys that represent unit-type fields (primary or sub-unit type).
_UNIT_TYPE_KEYS: frozenset[str] = frozenset(
    {"sub_premise_type", "dependent_sub_premise_type"}
)

# Keys that signal the end of the street portion of an address.
_POST_STREET_KEYS: frozenset[str] = frozenset(
    {"locality", "administrative_area", "postcode"}
)
```

- [ ] **Step 3: Update internal key references in recovery functions**

Apply these replacements throughout `parser.py` (below the constants block):

| Old string | New string |
|---|---|
| `"address_number"` | `"premise_number"` |
| `"city"` | `"locality"` |
| `"state"` | `"administrative_area"` |
| `"zip_code"` | `"postcode"` |
| `"occupancy_type"` | `"sub_premise_type"` |
| `"occupancy_identifier"` | `"sub_premise_number"` |
| `"subaddress_type"` | `"dependent_sub_premise_type"` |
| `"subaddress_identifier"` | `"dependent_sub_premise_number"` |
| `"building_name"` | `"premise_name"` |
| `"landmark_name"` | `"landmark"` |

Use careful search-and-replace; the strings appear as dict keys, `.get()` arguments, and direct assignments. Do not rename Python variable names (e.g. `city = components.get("locality", "")` — the variable `city` stays `city`; only the string key changes).

- [ ] **Step 4: Update unit tests to use ISO keys**

In `tests/unit/test_parser.py`, replace all dict fixture key strings using the same table above. Examples:

```python
# Before:
c: dict[str, str] = {"city": "BASEMENT, FREELAND"}
assert c["occupancy_type"] == "BASEMENT"
assert c["city"] == "FREELAND"

# After:
c: dict[str, str] = {"locality": "BASEMENT, FREELAND"}
assert c["sub_premise_type"] == "BASEMENT"
assert c["locality"] == "FREELAND"
```

Apply to every test fixture dict and every assertion on component keys throughout the file.

- [ ] **Step 5: Run parser unit tests**

```bash
uv run pytest tests/unit/test_parser.py -v --no-cov
```

Expected: all pass.

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest --no-cov -x
```

Expected: all pass. (v1 integration tests will fail if `parse.py` router has not yet been updated — that is Task 5. If failures appear only in `test_v1_parse.py`, that is expected and will be fixed in Task 5.)

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check src/address_validator/services/parser.py tests/unit/test_parser.py --fix
uv run ruff format src/address_validator/services/parser.py tests/unit/test_parser.py
git add src/address_validator/services/parser.py tests/unit/test_parser.py
git commit -m "#90 refactor: rename parser internal keys to ISO 19160-4 element names"
```

---

## Task 4: standardizer.py — rename internal keys to ISO 19160-4

**Files:**
- Modify: `src/address_validator/services/standardizer.py`
- Modify: `tests/unit/test_standardizer.py`

- [ ] **Step 1: Rename all internal key strings in standardizer.py**

Apply these replacements throughout `standardizer.py`:

| Old string | New string |
|---|---|
| `"address_number"` | `"premise_number"` |
| `"address_number_prefix"` | `"premise_number_prefix"` |
| `"address_number_suffix"` | `"premise_number_suffix"` |
| `"street_name_pre_directional"` | `"thoroughfare_pre_direction"` |
| `"street_name_pre_modifier"` | `"thoroughfare_pre_modifier"` |
| `"street_name_pre_type"` | `"thoroughfare_leading_type"` |
| `"street_name"` | `"thoroughfare_name"` |
| `"street_name_post_type"` | `"thoroughfare_trailing_type"` |
| `"street_name_post_directional"` | `"thoroughfare_post_direction"` |
| `"street_name_post_modifier"` | `"thoroughfare_post_modifier"` |
| `"occupancy_type"` | `"sub_premise_type"` |
| `"occupancy_identifier"` | `"sub_premise_number"` |
| `"subaddress_type"` | `"dependent_sub_premise_type"` |
| `"subaddress_identifier"` | `"dependent_sub_premise_number"` |
| `"building_name"` | `"premise_name"` |
| `"landmark_name"` | `"landmark"` |
| `"city"` (dict key only) | `"locality"` |
| `"state"` (dict key only) | `"administrative_area"` |
| `"zip_code"` | `"postcode"` |

**Important:** The top-level response fields `city`, `region`, `postal_code` on `StandardizeResponseV1` come from:
```python
# Before rename (in standardize()):
city = std.get("city", "")
state = std.get("state", "")
zip_code = std.get("zip_code", "")
```
After rename these become:
```python
city = std.get("locality", "")
state = std.get("administrative_area", "")
zip_code = std.get("postcode", "")
```
The response fields themselves (`city=city`, `region=state`, `postal_code=zip_code`) do not change — only the dict key strings change.

Also update the `_standardize_street_fields()` function's field list and all `_get()` calls within it to use new key names.

- [ ] **Step 2: Update standardizer unit tests**

In `tests/unit/test_standardizer.py`, update all input dict fixtures and output assertions:

```python
# Before:
comps = {
    "address_number": "123",
    "street_name": "MAIN",
    "street_name_post_type": "STREET",
    "city": "SPRINGFIELD",
    "state": "IL",
    "zip_code": "62701",
}

# After:
comps = {
    "premise_number": "123",
    "thoroughfare_name": "MAIN",
    "thoroughfare_trailing_type": "STREET",
    "locality": "SPRINGFIELD",
    "administrative_area": "IL",
    "postcode": "62701",
}
```

Update every fixture dict and every assertion on `ComponentSet.values` keys throughout the file. Top-level response field assertions (e.g. `result.city`, `result.region`) do not change.

- [ ] **Step 3: Run standardizer unit tests**

```bash
uv run pytest tests/unit/test_standardizer.py -v --no-cov
```

Expected: all pass.

- [ ] **Step 4: Lint and commit**

```bash
uv run ruff check src/address_validator/services/standardizer.py tests/unit/test_standardizer.py --fix
uv run ruff format src/address_validator/services/standardizer.py tests/unit/test_standardizer.py
git add src/address_validator/services/standardizer.py tests/unit/test_standardizer.py
git commit -m "#90 refactor: rename standardizer internal keys to ISO 19160-4 element names"
```

---

## Task 5: v1 routers — apply usps-pub28 translation

**Files:**
- Modify: `src/address_validator/routers/v1/parse.py`
- Modify: `src/address_validator/routers/v1/standardize.py`

After Tasks 3 and 4, the service layer returns ISO 19160-4 keys in `ComponentSet.values`. v1 clients expect the old USPS snake_case keys. This task wires the translation into v1 routers. v1 validate.py does not need changes (its response components come from providers, which build their own vocabularies independently).

- [ ] **Step 1: Update v1 parse.py**

Add import and apply translation:

```python
# At the top of src/address_validator/routers/v1/parse.py, add:
from address_validator.services.component_profiles import translate_components

# In the route handler, replace the direct return with:
async def parse(req: ParseRequestV1, request: Request) -> ParseResponseV1:
    check_country(req.country)
    result = parse_address(req.address.strip(), country=req.country)
    translated = translate_components(result.components.values, "usps-pub28")
    return ParseResponseV1(
        input=result.input,
        country=result.country,
        components=ComponentSet(
            spec=result.components.spec,
            spec_version=result.components.spec_version,
            values=translated,
        ),
        type=result.type,
        warnings=result.warnings,
    )
```

Read the current parse route handler to find the exact function name and check if it is `async def` or `def`. It must remain `async def` (see AGENTS.md sensitive areas — `parse.py` must be `async def`).

- [ ] **Step 2: Update v1 standardize.py**

Apply the same pattern:

```python
# Add import:
from address_validator.services.component_profiles import translate_components

# In the route handler, translate components before returning:
result = standardize(comps, country=req.country, upstream_warnings=upstream_warnings)
translated = translate_components(result.components.values, "usps-pub28")
return StandardizeResponseV1(
    address_line_1=result.address_line_1,
    address_line_2=result.address_line_2,
    city=result.city,
    region=result.region,
    postal_code=result.postal_code,
    country=result.country,
    standardized=result.standardized,
    components=ComponentSet(
        spec=result.components.spec,
        spec_version=result.components.spec_version,
        values=translated,
    ),
    warnings=result.warnings,
)
```

Read the current standardize route handler to find the exact handler shape before editing.

- [ ] **Step 3: Run v1 integration tests**

```bash
uv run pytest tests/integration/test_v1_parse.py tests/integration/test_v1_standardize.py tests/integration/test_v1_validate.py -v --no-cov
```

Expected: all pass. The v1 API contract is byte-for-byte identical to pre-Plan-1 behaviour.

- [ ] **Step 4: Run full test suite**

```bash
uv run pytest --no-cov -x
```

Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/address_validator/routers/v1/parse.py src/address_validator/routers/v1/standardize.py --fix
uv run ruff format src/address_validator/routers/v1/parse.py src/address_validator/routers/v1/standardize.py
git add src/address_validator/routers/v1/parse.py src/address_validator/routers/v1/standardize.py
git commit -m "#90 feat: apply usps-pub28 translation in v1 parse and standardize routers"
```

---

## Task 6: api_version.py — v2 header

**Files:**
- Modify: `src/address_validator/middleware/api_version.py`

- [ ] **Step 1: Read the current middleware**

Read `src/address_validator/middleware/api_version.py` to understand how the v1 path check works before editing.

- [ ] **Step 2: Add v2 header support**

The middleware currently appends `API-Version: 1` when the path starts with `/api/v1/`. Extend it to also append `API-Version: 2` for `/api/v2/`:

```python
# The exact edit depends on the current implementation.
# Pattern: add an elif branch (or a dict lookup) so:
#   /api/v1/... → API-Version: 1
#   /api/v2/... → API-Version: 2
```

After reading the file, make the minimal change. The middleware must remain pure ASGI (no BaseHTTPMiddleware). See AGENTS.md sensitive areas for middleware ordering constraints.

- [ ] **Step 3: Write a unit test**

In `tests/unit/test_request_id.py` (or a new `tests/unit/test_api_version.py` if the existing file does not cover this middleware), add:

```python
def test_api_version_header_v2(client) -> None:
    """v2 endpoints return API-Version: 2 header."""
    # This test requires the v2 router to be registered (Task 8).
    # Add it now but it will pass only after Task 8 is complete.
    response = client.post(
        "/api/v2/parse",
        json={"address": "123 Main St, Seattle, WA 98101"},
        headers={"X-API-Key": "test-api-key-for-pytest"},
    )
    assert response.headers.get("API-Version") == "2"
```

- [ ] **Step 4: Lint and commit**

```bash
uv run ruff check src/address_validator/middleware/api_version.py --fix
uv run ruff format src/address_validator/middleware/api_version.py
git add src/address_validator/middleware/api_version.py
git commit -m "#90 feat: add API-Version: 2 header for /api/v2/ responses"
```

---

## Task 7: routers/v2/parse.py

**Files:**
- Create: `src/address_validator/routers/v2/__init__.py`
- Create: `src/address_validator/routers/v2/parse.py`
- Create: `tests/integration/test_v2_parse.py`

- [ ] **Step 1: Write failing integration tests**

```python
# tests/integration/test_v2_parse.py
"""Integration tests for POST /api/v2/parse."""
import pytest


class TestV2ParseISO:
    def test_returns_iso_keys_by_default(self, client) -> None:
        response = client.post(
            "/api/v2/parse",
            json={"address": "123 Main St, Seattle, WA 98101"},
        )
        assert response.status_code == 200
        values = response.json()["components"]["values"]
        assert values["premise_number"] == "123"
        assert values["thoroughfare_name"] == "MAIN"
        assert values["thoroughfare_trailing_type"] == "ST"
        assert values["locality"] == "SEATTLE"
        assert values["administrative_area"] == "WA"
        assert values["postcode"] == "98101"
        assert "address_number" not in values
        assert "street_name" not in values

    def test_api_version_in_body(self, client) -> None:
        response = client.post(
            "/api/v2/parse",
            json={"address": "123 Main St"},
        )
        assert response.json()["api_version"] == "2"

    def test_component_profile_usps_pub28_restores_v1_keys(self, client) -> None:
        response = client.post(
            "/api/v2/parse?component_profile=usps-pub28",
            json={"address": "123 Main St, Seattle, WA 98101"},
        )
        assert response.status_code == 200
        values = response.json()["components"]["values"]
        assert values["address_number"] == "123"
        assert values["street_name"] == "MAIN"
        assert values["city"] == "SEATTLE"
        assert values["state"] == "WA"
        assert values["zip_code"] == "98101"

    def test_invalid_component_profile_returns_422(self, client) -> None:
        response = client.post(
            "/api/v2/parse?component_profile=bad-profile",
            json={"address": "123 Main St"},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "invalid_component_profile"

    def test_canada_not_yet_supported_via_parse(self, client) -> None:
        # CA is not in SUPPORTED_COUNTRIES for v2 parse until Plan 2.
        # Adjust or remove this test in Plan 2 when CA is enabled.
        response = client.post(
            "/api/v2/parse",
            json={"address": "350 rue des Lilas, Quebec QC G1L 1B6", "country": "CA"},
        )
        assert response.status_code == 422
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/integration/test_v2_parse.py -v --no-cov
```

Expected: `404 Not Found` for `/api/v2/parse` — router not registered yet.

- [ ] **Step 3: Create routers/v2/__init__.py**

```python
# src/address_validator/routers/v2/__init__.py
```

(Empty file.)

- [ ] **Step 4: Create routers/v2/parse.py**

```python
# src/address_validator/routers/v2/parse.py
"""v2 parse endpoint — ISO 19160-4 component keys by default."""

from fastapi import APIRouter, Depends, Query, Request

from address_validator.auth import require_api_key
from address_validator.models import ComponentSet, ErrorResponse, ParseRequestV1, ParseResponseV2
from address_validator.routers.v1.core import APIError, check_country
from address_validator.services.component_profiles import VALID_PROFILES, translate_components
from address_validator.services.parser import parse_address

router = APIRouter(
    prefix="/api/v2",
    tags=["v2"],
    dependencies=[Depends(require_api_key)],
)

_COMPONENT_PROFILE_DESCRIPTION = (
    "Component key vocabulary. "
    "`iso-19160-4` (default): ISO 19160-4 element names. "
    "`usps-pub28`: USPS Publication 28 snake_case names (v1 backward compat). "
    "`canada-post`: reserved; currently identical to `iso-19160-4`."
)


@router.post(
    "/parse",
    response_model=ParseResponseV2,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
    summary="Parse address into ISO 19160-4 components",
)
async def parse(
    req: ParseRequestV1,
    request: Request,
    component_profile: str = Query(
        default="iso-19160-4",
        description=_COMPONENT_PROFILE_DESCRIPTION,
    ),
) -> ParseResponseV2:
    if component_profile not in VALID_PROFILES:
        raise APIError(
            status_code=422,
            error="invalid_component_profile",
            message=(
                f"Unknown component_profile '{component_profile}'. "
                f"Valid values: {sorted(VALID_PROFILES)}."
            ),
        )
    check_country(req.country)
    result = parse_address(req.address.strip(), country=req.country)
    translated = translate_components(result.components.values, component_profile)
    return ParseResponseV2(
        input=result.input,
        country=result.country,
        components=ComponentSet(
            spec=result.components.spec,
            spec_version=result.components.spec_version,
            values=translated,
        ),
        type=result.type,
        warnings=result.warnings,
    )
```

Note: `ParseRequestV1` is reused for the v2 request body — the request shape is identical. `check_country` uses `SUPPORTED_COUNTRIES` which at this point is `{"US"}`. CA will be added in Plan 2.

- [ ] **Step 5: Register router in main.py (temporary)**

To enable tests, temporarily add the v2 parse router to `main.py`. Task 11 will do a clean final registration of all v2 routers together. For now, add just this one:

Read `src/address_validator/main.py`, find where v1 routers are imported and included, and add:

```python
from address_validator.routers.v2 import parse as v2_parse
# ...
app.include_router(v2_parse.router)
```

- [ ] **Step 6: Run v2 parse tests**

```bash
uv run pytest tests/integration/test_v2_parse.py -v --no-cov
```

Expected: all pass except the last test (`test_canada_not_yet_supported_via_parse`) which may return 422 with a different message — adjust the assertion to match the actual error returned.

- [ ] **Step 7: Run full suite to check no regressions**

```bash
uv run pytest --no-cov -x
```

- [ ] **Step 8: Lint and commit**

```bash
uv run ruff check src/address_validator/routers/v2/ tests/integration/test_v2_parse.py --fix
uv run ruff format src/address_validator/routers/v2/ tests/integration/test_v2_parse.py
git add src/address_validator/routers/v2/ tests/integration/test_v2_parse.py src/address_validator/main.py
git commit -m "#90 feat: add /api/v2/parse endpoint with ISO 19160-4 keys and component_profile param"
```

---

## Task 8: routers/v2/standardize.py

**Files:**
- Create: `src/address_validator/routers/v2/standardize.py`
- Create: `tests/integration/test_v2_standardize.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/integration/test_v2_standardize.py
"""Integration tests for POST /api/v2/standardize."""


class TestV2StandardizeISO:
    def test_returns_iso_keys_by_default(self, client) -> None:
        response = client.post(
            "/api/v2/standardize",
            json={"address": "123 n main st ste 4, seattle wa 98101"},
        )
        assert response.status_code == 200
        values = response.json()["components"]["values"]
        assert values["premise_number"] == "123"
        assert values["thoroughfare_pre_direction"] == "N"
        assert values["thoroughfare_name"] == "MAIN"
        assert values["thoroughfare_trailing_type"] == "ST"
        assert values["sub_premise_type"] == "STE"
        assert values["sub_premise_number"] == "4"
        assert values["locality"] == "SEATTLE"
        assert values["administrative_area"] == "WA"
        assert values["postcode"] == "98101"

    def test_api_version_is_2(self, client) -> None:
        response = client.post(
            "/api/v2/standardize",
            json={"address": "123 Main St, Seattle, WA 98101"},
        )
        assert response.json()["api_version"] == "2"

    def test_top_level_fields_unchanged(self, client) -> None:
        response = client.post(
            "/api/v2/standardize",
            json={"address": "123 Main St, Seattle, WA 98101"},
        )
        body = response.json()
        assert body["city"] == "SEATTLE"
        assert body["region"] == "WA"
        assert body["postal_code"] == "98101"

    def test_component_profile_usps_pub28(self, client) -> None:
        response = client.post(
            "/api/v2/standardize?component_profile=usps-pub28",
            json={"address": "123 Main St, Seattle, WA 98101"},
        )
        values = response.json()["components"]["values"]
        assert values["address_number"] == "123"
        assert values["street_name"] == "MAIN"
        assert values["city"] == "SEATTLE"

    def test_invalid_profile_returns_422(self, client) -> None:
        response = client.post(
            "/api/v2/standardize?component_profile=not-a-profile",
            json={"address": "123 Main St"},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "invalid_component_profile"
```

- [ ] **Step 2: Run to confirm 404**

```bash
uv run pytest tests/integration/test_v2_standardize.py -v --no-cov
```

- [ ] **Step 3: Create routers/v2/standardize.py**

```python
# src/address_validator/routers/v2/standardize.py
"""v2 standardize endpoint — ISO 19160-4 component keys by default."""

from fastapi import APIRouter, Depends, Query, Request

from address_validator.auth import require_api_key
from address_validator.models import (
    ComponentSet,
    ErrorResponse,
    StandardizeRequestV1,
    StandardizeResponseV2,
)
from address_validator.routers.v1.core import APIError, check_country
from address_validator.services.component_profiles import VALID_PROFILES, translate_components
from address_validator.services.standardizer import standardize

router = APIRouter(
    prefix="/api/v2",
    tags=["v2"],
    dependencies=[Depends(require_api_key)],
)

_COMPONENT_PROFILE_DESCRIPTION = (
    "Component key vocabulary. `iso-19160-4` (default) or `usps-pub28` for v1 compat."
)


@router.post(
    "/standardize",
    response_model=StandardizeResponseV2,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
    summary="Standardize address per national postal profile",
)
async def standardize_address(
    req: StandardizeRequestV1,
    request: Request,
    component_profile: str = Query(
        default="iso-19160-4",
        description=_COMPONENT_PROFILE_DESCRIPTION,
    ),
) -> StandardizeResponseV2:
    if component_profile not in VALID_PROFILES:
        raise APIError(
            status_code=422,
            error="invalid_component_profile",
            message=(
                f"Unknown component_profile '{component_profile}'. "
                f"Valid values: {sorted(VALID_PROFILES)}."
            ),
        )
    check_country(req.country)
    comps = req.components or {}
    if req.address and not comps:
        from address_validator.services.parser import parse_address
        parse_result = parse_address(req.address.strip(), country=req.country)
        comps = parse_result.components.values

    result = standardize(comps, country=req.country)
    translated = translate_components(result.components.values, component_profile)
    return StandardizeResponseV2(
        address_line_1=result.address_line_1,
        address_line_2=result.address_line_2,
        city=result.city,
        region=result.region,
        postal_code=result.postal_code,
        country=result.country,
        standardized=result.standardized,
        components=ComponentSet(
            spec=result.components.spec,
            spec_version=result.components.spec_version,
            values=translated,
        ),
        warnings=result.warnings,
    )
```

Read `src/address_validator/routers/v1/standardize.py` before writing to ensure the components-vs-address precedence logic matches v1 exactly.

- [ ] **Step 4: Register in main.py and run tests**

Add to `main.py`:
```python
from address_validator.routers.v2 import standardize as v2_standardize
app.include_router(v2_standardize.router)
```

```bash
uv run pytest tests/integration/test_v2_standardize.py -v --no-cov
```

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/address_validator/routers/v2/standardize.py tests/integration/test_v2_standardize.py --fix
uv run ruff format src/address_validator/routers/v2/standardize.py tests/integration/test_v2_standardize.py
git add src/address_validator/routers/v2/standardize.py tests/integration/test_v2_standardize.py src/address_validator/main.py
git commit -m "#90 feat: add /api/v2/standardize endpoint"
```

---

## Task 9: routers/v2/validate.py (US only)

**Files:**
- Create: `src/address_validator/routers/v2/validate.py`
- Create: `tests/integration/test_v2_validate.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/integration/test_v2_validate.py
"""Integration tests for POST /api/v2/validate."""


class TestV2ValidateBasic:
    def test_us_address_returns_200(self, client) -> None:
        response = client.post(
            "/api/v2/validate",
            json={"address": "123 Main St, Seattle, WA 98101"},
        )
        # Without a real provider configured, status will be "unavailable"
        assert response.status_code == 200

    def test_api_version_is_2(self, client) -> None:
        response = client.post(
            "/api/v2/validate",
            json={"address": "123 Main St, Seattle, WA 98101"},
        )
        assert response.json()["api_version"] == "2"

    def test_invalid_profile_returns_422(self, client) -> None:
        response = client.post(
            "/api/v2/validate?component_profile=bad",
            json={"address": "123 Main St"},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "invalid_component_profile"
```

- [ ] **Step 2: Create routers/v2/validate.py**

Read `src/address_validator/routers/v1/validate.py` in full before writing v2. The v2 validate handler mirrors v1 logic with two differences:
1. Returns `ValidateResponseV2` (api_version="2")
2. Accepts `component_profile` query parameter (validated; note: the validate response components come from providers and are not translated by `component_profile` — only parse/standardize components are affected)

Create `src/address_validator/routers/v2/validate.py` by copying v1 validate.py and applying:
- Change `prefix="/api/v1"` to `prefix="/api/v2"`
- Change tag to `"v2"`
- Import and return `ValidateResponseV2` instead of `ValidateResponseV1`
- Add `component_profile` query param with validation (same pattern as parse and standardize)
- Replace `_build_non_us_std()` return type annotation with `StandardizeResponseV2` if needed

- [ ] **Step 3: Register and test**

```python
# main.py addition:
from address_validator.routers.v2 import validate as v2_validate
app.include_router(v2_validate.router)
```

```bash
uv run pytest tests/integration/test_v2_validate.py -v --no-cov
```

- [ ] **Step 4: Lint and commit**

```bash
uv run ruff check src/address_validator/routers/v2/validate.py tests/integration/test_v2_validate.py --fix
uv run ruff format src/address_validator/routers/v2/validate.py tests/integration/test_v2_validate.py
git add src/address_validator/routers/v2/validate.py tests/integration/test_v2_validate.py src/address_validator/main.py
git commit -m "#90 feat: add /api/v2/validate endpoint (US only)"
```

---

## Task 10: routers/v2/countries.py

**Files:**
- Create: `src/address_validator/routers/v2/countries.py`

- [ ] **Step 1: Create routers/v2/countries.py**

The v2 countries endpoint is identical to v1 except it returns `CountryFormatResponseV2`. Read `src/address_validator/routers/v1/countries.py` and create the v2 version with:
- `prefix="/api/v2"`
- Tag `"v2"`
- Returns `CountryFormatResponseV2`

The service layer (`services/country_format.py`) is unchanged and shared.

- [ ] **Step 2: Register and run existing countries tests**

```python
# main.py:
from address_validator.routers.v2 import countries as v2_countries
app.include_router(v2_countries.router)
```

```bash
uv run pytest tests/integration/test_countries_router.py -v --no-cov
```

Add a minimal v2 countries test asserting `api_version == "2"` and that `GET /api/v2/countries/US/format` returns 200.

- [ ] **Step 3: Lint and commit**

```bash
uv run ruff check src/address_validator/routers/v2/countries.py --fix
uv run ruff format src/address_validator/routers/v2/countries.py
git add src/address_validator/routers/v2/countries.py src/address_validator/main.py
git commit -m "#90 feat: add /api/v2/countries endpoint"
```

---

## Task 11: main.py — clean v2 router registration

**Files:**
- Modify: `src/address_validator/main.py`

Tasks 7–10 each made incremental additions to `main.py`. This task consolidates them into a clean, grouped import block matching the v1 pattern.

- [ ] **Step 1: Read main.py**

Read `src/address_validator/main.py` to see the current v1 router registration pattern.

- [ ] **Step 2: Consolidate v2 router imports**

Replace the scattered individual v2 router additions from Tasks 7–10 with a single clean block:

```python
# v2 routers
from address_validator.routers.v2 import countries as v2_countries
from address_validator.routers.v2 import parse as v2_parse
from address_validator.routers.v2 import standardize as v2_standardize
from address_validator.routers.v2 import validate as v2_validate

# ...

app.include_router(v2_parse.router)
app.include_router(v2_standardize.router)
app.include_router(v2_validate.router)
app.include_router(v2_countries.router)
```

Place the v2 include_router calls immediately after the v1 include_router calls.

- [ ] **Step 3: Run full test suite and coverage check**

```bash
uv run pytest --no-cov -x
uv run pytest --cov --cov-report=term-missing
```

Expected: all tests pass; coverage ≥ 80%.

- [ ] **Step 4: Final lint pass**

```bash
uv run ruff check . --fix
uv run ruff format .
```

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/main.py
git commit -m "#90 chore: consolidate v2 router registration in main.py"
```

---

## Verification

After all tasks are complete, verify the following end-to-end:

```bash
# v1 parse — still returns USPS snake_case keys
curl -s -X POST http://localhost:8001/api/v1/parse \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"address": "123 Main St, Seattle, WA 98101"}' \
  | jq '.components.values | keys'
# Expected: ["address_number", "street_name", "street_name_post_type", ...]

# v2 parse — ISO keys by default
curl -s -X POST http://localhost:8001/api/v2/parse \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"address": "123 Main St, Seattle, WA 98101"}' \
  | jq '.components.values | keys'
# Expected: ["locality", "postcode", "premise_number", "thoroughfare_name", ...]

# v2 parse — USPS keys via profile param
curl -s -X POST "http://localhost:8001/api/v2/parse?component_profile=usps-pub28" \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"address": "123 Main St, Seattle, WA 98101"}' \
  | jq '.components.values | keys'
# Expected: same as v1

# API-Version header on v2
curl -si -X POST http://localhost:8001/api/v2/parse \
  -H "X-API-Key: $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"address": "123 Main St"}' \
  | grep API-Version
# Expected: API-Version: 2
```

---

**Plan 2 prerequisite:** This plan must be fully merged before Plan 2 (libpostal + CA parsing) begins. Plan 2 adds `LibpostalClient`, the bilingual street splitter, and CA country routing inside `parse_address()`.
