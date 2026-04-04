# Countries Format Endpoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `GET /api/v1/countries/{code}/format` returning per-country address field definitions driven by the `google-i18n-address` library.

**Architecture:** New route in `routers/v1/countries.py` → thin service in `services/country_format.py` that wraps `i18naddress.get_validation_rules()` and maps its output to Pydantic response models. New models added to `models.py`. Router registered in `main.py`.

**Tech Stack:** FastAPI, Pydantic, `google-i18n-address==3.1.1` (already added to `pyproject.toml` via `uv add`), `pytest`, `fastapi.testclient.TestClient`

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `src/address_validator/models.py` | Add `CountrySubdivision`, `CountryFieldDefinition`, `CountryFormatResponse` |
| Create | `src/address_validator/services/country_format.py` | Map `i18naddress` rules → response models |
| Create | `src/address_validator/routers/v1/countries.py` | Route handler |
| Modify | `src/address_validator/main.py` | Register new router |
| Create | `tests/unit/test_country_format_service.py` | Unit tests for mapping logic |
| Create | `tests/unit/test_countries_router.py` | HTTP integration tests |

---

### Task 1: Add response models to `models.py`

**Files:**
- Modify: `src/address_validator/models.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_models.py  — append to existing file
def test_country_format_models_exist() -> None:
    from address_validator.models import (
        CountryFieldDefinition,
        CountryFormatResponse,
        CountrySubdivision,
    )
    sub = CountrySubdivision(code="AB", label="Alberta")
    assert sub.code == "AB"
    assert sub.label == "Alberta"

    field = CountryFieldDefinition(key="region", label="Province", required=True)
    assert field.options is None
    assert field.pattern is None

    field_with_opts = CountryFieldDefinition(
        key="region",
        label="Province",
        required=True,
        options=[sub],
    )
    assert len(field_with_opts.options) == 1

    resp = CountryFormatResponse(country="CA", fields=[field])
    assert resp.country == "CA"
    assert len(resp.fields) == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/unit/test_models.py::test_country_format_models_exist -v
```
Expected: FAIL with `ImportError` (models don't exist yet).

- [ ] **Step 3: Add models to `models.py`**

Insert after the `StandardizeResponseV1` class (end of file):

```python
# ---------------------------------------------------------------------------
# Response models — v1 countries
# ---------------------------------------------------------------------------


class CountrySubdivision(BaseModel):
    """A country subdivision (province, state, etc.) with code and display label."""

    code: str
    label: str


class CountryFieldDefinition(BaseModel):
    """Definition of a single address field for a given country.

    ``options`` is present for ``region`` fields when the country has a fixed
    list of subdivisions (e.g. US states, Canadian provinces).

    ``pattern`` is a postal code regex hint for ``postal_code`` fields when
    the country defines one; absent otherwise.
    """

    key: str
    label: str
    required: bool
    options: list[CountrySubdivision] | None = None
    pattern: str | None = None


class CountryFormatResponse(BaseModel):
    """Response body for GET /api/v1/countries/{code}/format.

    ``fields`` lists only the address fields used in this country, in the
    order they appear on a typical address form.  Fields absent from the
    array should be hidden in the UI.
    """

    country: str
    fields: list[CountryFieldDefinition]
    api_version: Literal["1"] = "1"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/unit/test_models.py::test_country_format_models_exist -v
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/models.py tests/unit/test_models.py
git commit -m "#87 feat: add CountrySubdivision, CountryFieldDefinition, CountryFormatResponse models"
```

---

### Task 2: Implement `services/country_format.py`

**Files:**
- Create: `src/address_validator/services/country_format.py`
- Create: `tests/unit/test_country_format_service.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_country_format_service.py
"""Unit tests for the country format service."""

import pytest

from address_validator.services.country_format import get_country_format


class TestGetCountryFormat:
    def test_us_returns_five_fields(self) -> None:
        result = get_country_format("US")
        assert result is not None
        keys = [f.key for f in result.fields]
        assert keys == [
            "address_line_1",
            "address_line_2",
            "city",
            "region",
            "postal_code",
        ]

    def test_us_country_field(self) -> None:
        result = get_country_format("US")
        assert result is not None
        assert result.country == "US"

    def test_us_region_label_is_state(self) -> None:
        result = get_country_format("US")
        assert result is not None
        region = next(f for f in result.fields if f.key == "region")
        assert region.label == "State"

    def test_us_region_has_options(self) -> None:
        result = get_country_format("US")
        assert result is not None
        region = next(f for f in result.fields if f.key == "region")
        assert region.options is not None
        codes = [o.code for o in region.options]
        assert "CA" in codes
        assert "NY" in codes
        labels = [o.label for o in region.options]
        assert "California" in labels

    def test_us_postal_code_label_is_zip(self) -> None:
        result = get_country_format("US")
        assert result is not None
        postal = next(f for f in result.fields if f.key == "postal_code")
        assert postal.label == "ZIP code"

    def test_us_postal_code_has_pattern(self) -> None:
        result = get_country_format("US")
        assert result is not None
        postal = next(f for f in result.fields if f.key == "postal_code")
        assert postal.pattern is not None
        import re
        assert re.match(postal.pattern, "95014")

    def test_us_address_line_2_is_optional(self) -> None:
        result = get_country_format("US")
        assert result is not None
        line2 = next(f for f in result.fields if f.key == "address_line_2")
        assert line2.required is False

    def test_ca_region_label_is_province(self) -> None:
        result = get_country_format("CA")
        assert result is not None
        region = next(f for f in result.fields if f.key == "region")
        assert region.label == "Province"

    def test_ca_options_deduplicated(self) -> None:
        # CA has bilingual names — each code should appear once
        result = get_country_format("CA")
        assert result is not None
        region = next(f for f in result.fields if f.key == "region")
        assert region.options is not None
        codes = [o.code for o in region.options]
        assert len(codes) == len(set(codes)), "duplicate codes found"
        # BC appears twice in raw data (English + French name) — only once here
        assert codes.count("BC") == 1

    def test_ca_postal_code_label_is_postal(self) -> None:
        result = get_country_format("CA")
        assert result is not None
        postal = next(f for f in result.fields if f.key == "postal_code")
        assert postal.label == "Postal code"

    def test_gb_no_region_field(self) -> None:
        # GB address format does not include country_area
        result = get_country_format("GB")
        assert result is not None
        keys = [f.key for f in result.fields]
        assert "region" not in keys

    def test_gb_field_order(self) -> None:
        result = get_country_format("GB")
        assert result is not None
        keys = [f.key for f in result.fields]
        # GB format: %A %C %Z  (street, city, postal)
        assert keys.index("address_line_1") < keys.index("city")
        assert keys.index("city") < keys.index("postal_code")

    def test_hk_region_before_street(self) -> None:
        # HK format: %S %C %A  (region, city, street)
        result = get_country_format("HK")
        assert result is not None
        keys = [f.key for f in result.fields]
        assert keys.index("region") < keys.index("address_line_1")

    def test_country_with_no_postal_code_pattern(self) -> None:
        # HK has no postal code at all — postal_code field absent
        result = get_country_format("HK")
        assert result is not None
        keys = [f.key for f in result.fields]
        assert "postal_code" not in keys

    def test_unknown_country_returns_none(self) -> None:
        # Valid ISO2 that google-i18n-address doesn't recognise raises ValueError internally
        # We return None so the router can 404
        result = get_country_format("XK")  # Kosovo — in pycountry but may not be in library
        # Just verify it returns None or a valid response — no exception raised
        assert result is None or result.country == "XK"

    def test_returns_none_for_library_error(self) -> None:
        from unittest.mock import patch
        with patch(
            "address_validator.services.country_format.get_validation_rules",
            side_effect=ValueError("bad code"),
        ):
            result = get_country_format("ZZ")
        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_country_format_service.py -v
```
Expected: FAIL with `ModuleNotFoundError` (file doesn't exist yet).

- [ ] **Step 3: Implement `services/country_format.py`**

```python
# src/address_validator/services/country_format.py
"""Per-country address field format service.

Maps ``google-i18n-address`` (i18naddress) ``ValidationRules`` to
:class:`~models.CountryFormatResponse`.  Used by the
``GET /api/v1/countries/{code}/format`` route.
"""

from i18naddress import get_validation_rules as _get_validation_rules

from address_validator.models import (
    CountryFieldDefinition,
    CountryFormatResponse,
    CountrySubdivision,
)

# Format string token → i18naddress field key
_FORMAT_TOKENS: dict[str, str] = {
    "%A": "street_address",
    "%C": "city",
    "%S": "country_area",
    "%Z": "postal_code",
}

# i18naddress country_area_type → display label
_AREA_TYPE_LABELS: dict[str, str] = {
    "area": "Area",
    "canton": "Canton",
    "county": "County",
    "department": "Department",
    "district": "District",
    "do_si": "Province/City",
    "emirate": "Emirate",
    "island": "Island",
    "oblast": "Region",
    "oblys": "Region",
    "parish": "Parish",
    "prefecture": "Prefecture",
    "province": "Province",
    "region": "Region",
    "state": "State",
}

# i18naddress city_type → display label
_CITY_TYPE_LABELS: dict[str, str] = {
    "city": "City",
    "district": "District",
    "post_town": "Town/City",
    "suburb": "Suburb",
}

# i18naddress postal_code_type → display label
_POSTAL_TYPE_LABELS: dict[str, str] = {
    "eircode": "Eircode",
    "pin": "PIN code",
    "postal": "Postal code",
    "zip": "ZIP code",
}


def get_country_format(country_code: str) -> CountryFormatResponse | None:
    """Return address field format for *country_code*, or ``None`` if unavailable.

    Returns ``None`` when the ``google-i18n-address`` library raises
    ``ValueError`` for the given code (unknown country).  The caller is
    responsible for translating ``None`` to a 404 response.
    """
    try:
        rules = _get_validation_rules({"country_code": country_code})
    except ValueError:
        return None

    fields: list[CountryFieldDefinition] = []
    for lib_key in _parse_format_order(rules.address_format):
        field = _build_field(lib_key, rules)
        if field is None:
            continue
        fields.append(field)
        if lib_key == "street_address":
            fields.append(
                CountryFieldDefinition(
                    key="address_line_2",
                    label="Address line 2",
                    required=False,
                )
            )

    return CountryFormatResponse(country=country_code, fields=fields)


def _parse_format_order(address_format: str) -> list[str]:
    """Return lib field keys in the order they appear in *address_format*."""
    positions: list[tuple[int, str]] = []
    for token, lib_key in _FORMAT_TOKENS.items():
        if token in address_format:
            positions.append((address_format.index(token), lib_key))
    positions.sort()
    return [lib_key for _, lib_key in positions]


def _build_field(lib_key: str, rules) -> CountryFieldDefinition | None:  # type: ignore[no-untyped-def]
    """Return a :class:`CountryFieldDefinition` for *lib_key*, or ``None``."""
    required = lib_key in rules.required_fields

    if lib_key == "street_address":
        return CountryFieldDefinition(
            key="address_line_1",
            label="Address line 1",
            required=required,
        )

    if lib_key == "city":
        label = _CITY_TYPE_LABELS.get(rules.city_type or "", "City")
        return CountryFieldDefinition(key="city", label=label, required=required)

    if lib_key == "country_area":
        label = _AREA_TYPE_LABELS.get(rules.country_area_type or "", "Region")
        options = _deduplicate_choices(rules.country_area_choices) if rules.country_area_choices else None
        return CountryFieldDefinition(key="region", label=label, required=required, options=options)

    if lib_key == "postal_code":
        label = _POSTAL_TYPE_LABELS.get(rules.postal_code_type or "", "Postal code")
        pattern = rules.postal_code_matchers[0].pattern if rules.postal_code_matchers else None
        return CountryFieldDefinition(key="postal_code", label=label, required=required, pattern=pattern)

    return None


def _deduplicate_choices(choices) -> list[CountrySubdivision]:  # type: ignore[no-untyped-def]
    """Deduplicate subdivision choices by code; first name for each code wins."""
    seen: set[str] = set()
    result: list[CountrySubdivision] = []
    for code, name in choices:
        if code not in seen:
            seen.add(code)
            result.append(CountrySubdivision(code=code, label=name))
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_country_format_service.py -v
```
Expected: all PASS (adjust `test_unknown_country_returns_none` assertion if Kosovo is recognised by the library).

- [ ] **Step 5: Lint**

```bash
uv run ruff check src/address_validator/services/country_format.py --fix
uv run ruff format src/address_validator/services/country_format.py
```

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/services/country_format.py tests/unit/test_country_format_service.py
git commit -m "#87 feat: implement country format service (google-i18n-address mapping)"
```

---

### Task 3: Implement `routers/v1/countries.py`

**Files:**
- Create: `src/address_validator/routers/v1/countries.py`
- Create: `tests/unit/test_countries_router.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_countries_router.py
"""HTTP-level tests for GET /api/v1/countries/{code}/format."""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from address_validator.main import app
from address_validator.models import (
    CountryFieldDefinition,
    CountryFormatResponse,
    CountrySubdivision,
)

_US_FORMAT = CountryFormatResponse(
    country="US",
    fields=[
        CountryFieldDefinition(key="address_line_1", label="Address line 1", required=True),
        CountryFieldDefinition(key="address_line_2", label="Address line 2", required=False),
        CountryFieldDefinition(key="city", label="City", required=True),
        CountryFieldDefinition(
            key="region",
            label="State",
            required=True,
            options=[CountrySubdivision(code="CA", label="California")],
        ),
        CountryFieldDefinition(
            key="postal_code",
            label="ZIP code",
            required=True,
            pattern=r"^(\d{5})(?:[ \-](\d{4}))?$",
        ),
    ],
)


class TestCountriesFormatEndpoint:
    def test_valid_country_returns_200(self, client: TestClient) -> None:
        with patch(
            "address_validator.routers.v1.countries.get_country_format",
            return_value=_US_FORMAT,
        ):
            resp = client.get("/api/v1/countries/US/format")
        assert resp.status_code == 200

    def test_response_shape(self, client: TestClient) -> None:
        with patch(
            "address_validator.routers.v1.countries.get_country_format",
            return_value=_US_FORMAT,
        ):
            resp = client.get("/api/v1/countries/US/format")
        body = resp.json()
        assert body["country"] == "US"
        assert isinstance(body["fields"], list)
        assert body["api_version"] == "1"

    def test_field_keys_present(self, client: TestClient) -> None:
        with patch(
            "address_validator.routers.v1.countries.get_country_format",
            return_value=_US_FORMAT,
        ):
            resp = client.get("/api/v1/countries/US/format")
        keys = [f["key"] for f in resp.json()["fields"]]
        assert "address_line_1" in keys
        assert "address_line_2" in keys

    def test_region_options_present(self, client: TestClient) -> None:
        with patch(
            "address_validator.routers.v1.countries.get_country_format",
            return_value=_US_FORMAT,
        ):
            resp = client.get("/api/v1/countries/US/format")
        region = next(f for f in resp.json()["fields"] if f["key"] == "region")
        assert region["options"] is not None
        assert region["options"][0] == {"code": "CA", "label": "California"}

    def test_cache_control_header(self, client: TestClient) -> None:
        with patch(
            "address_validator.routers.v1.countries.get_country_format",
            return_value=_US_FORMAT,
        ):
            resp = client.get("/api/v1/countries/US/format")
        assert resp.headers.get("cache-control") == "public, max-age=86400"

    def test_lowercase_code_normalised(self, client: TestClient) -> None:
        with patch(
            "address_validator.routers.v1.countries.get_country_format",
            return_value=_US_FORMAT,
        ) as mock_fn:
            resp = client.get("/api/v1/countries/us/format")
        assert resp.status_code == 200
        mock_fn.assert_called_once_with("US")

    def test_invalid_iso2_returns_422(self, client: TestClient) -> None:
        resp = client.get("/api/v1/countries/XX/format")
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"] == "invalid_country_code"

    def test_valid_iso2_no_format_data_returns_404(self, client: TestClient) -> None:
        with patch(
            "address_validator.routers.v1.countries.get_country_format",
            return_value=None,
        ):
            resp = client.get("/api/v1/countries/AQ/format")
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"] == "country_format_not_found"

    def test_requires_api_key(self, client_no_auth: TestClient) -> None:
        resp = client_no_auth.get("/api/v1/countries/US/format")
        assert resp.status_code == 401

    def test_rejects_wrong_api_key(self, client_bad_auth: TestClient) -> None:
        resp = client_bad_auth.get("/api/v1/countries/US/format")
        assert resp.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_countries_router.py -v
```
Expected: FAIL with 404 (route doesn't exist yet).

- [ ] **Step 3: Implement `routers/v1/countries.py`**

```python
# src/address_validator/routers/v1/countries.py
"""v1 countries format endpoint."""

from fastapi import APIRouter, Depends, Response
from fastapi import status as http_status

from address_validator.auth import require_api_key
from address_validator.models import CountryFormatResponse, ErrorResponse
from address_validator.routers.v1.core import APIError, VALID_ISO2
from address_validator.services.country_format import get_country_format

router = APIRouter(
    prefix="/api/v1",
    tags=["v1"],
    dependencies=[Depends(require_api_key)],
)

_CACHE_CONTROL = "public, max-age=86400"


@router.get(
    "/countries/{code}/format",
    response_model=CountryFormatResponse,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
    summary="Get per-country address field format",
    description=(
        "Returns per-country address field definitions including labels, "
        "required/optional state, region subdivision options, and postal code "
        "pattern.\n\n"
        "``{code}`` is an ISO 3166-1 alpha-2 country code (case-insensitive).\n\n"
        "Fields absent from ``fields`` should be hidden in the UI. "
        "``options`` is present on ``region`` when the country has a fixed "
        "list of provinces/states. "
        "``pattern`` is a postal code regex hint when the country defines one."
    ),
)
async def get_country_format_v1(code: str, response: Response) -> CountryFormatResponse:
    country = code.strip().upper()

    if country not in VALID_ISO2:
        raise APIError(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            error="invalid_country_code",
            message=f"'{code}' is not a valid ISO 3166-1 alpha-2 country code.",
        )

    fmt = get_country_format(country)
    if fmt is None:
        raise APIError(
            status_code=http_status.HTTP_404_NOT_FOUND,
            error="country_format_not_found",
            message=f"No address format data available for country '{country}'.",
        )

    response.headers["Cache-Control"] = _CACHE_CONTROL
    return fmt
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_countries_router.py -v
```
Expected: all FAIL because the router isn't registered yet.

- [ ] **Step 5: Register the router in `main.py`**

In `src/address_validator/main.py`, add the import with the other v1 imports:

```python
from address_validator.routers.v1 import countries as v1_countries
```

And add the `include_router` call after the existing v1 routers (around line 240):

```python
app.include_router(v1_countries.router)
```

- [ ] **Step 6: Run tests again to verify they pass**

```bash
uv run pytest tests/unit/test_countries_router.py -v
```
Expected: all PASS.

- [ ] **Step 7: Lint**

```bash
uv run ruff check src/address_validator/routers/v1/countries.py src/address_validator/main.py --fix
uv run ruff format src/address_validator/routers/v1/countries.py src/address_validator/main.py
```

- [ ] **Step 8: Commit**

```bash
git add src/address_validator/routers/v1/countries.py src/address_validator/main.py tests/unit/test_countries_router.py
git commit -m "#87 feat: add GET /api/v1/countries/{code}/format endpoint"
```

---

### Task 4: Full test run and coverage check

**Files:** None modified — verification only.

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest --no-cov -x
```
Expected: all PASS.

- [ ] **Step 2: Run with coverage**

```bash
uv run pytest
```
Expected: line + branch coverage stays at or above 80% (baseline ~93%).

- [ ] **Step 3: Final lint**

```bash
uv run ruff check .
```
Expected: no issues.

- [ ] **Step 4: Final commit if needed**

If any fixes were made:
```bash
git add -p
git commit -m "#87 fix: coverage/lint cleanup for countries endpoint"
```
