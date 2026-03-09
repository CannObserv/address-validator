# Google Maps Validation Provider Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a Google Maps Address Validation provider as a full drop-in alternative to the USPS provider, and normalize `ValidateResponseV1` to mirror `StandardizeResponseV1` structure (including lat/lng and warnings).

**Architecture:** Nine sequential tasks following strict TDD (red → green → commit). Models change first, then existing providers are updated to the new shape, then `GoogleClient`/`GoogleProvider` are added, then the factory gains the `google` branch. A shared `_helpers.py` module carries the `_build_validated_string` function used by both providers.

**Tech Stack:** FastAPI, Pydantic v2, httpx (async HTTP client), pytest + pytest-asyncio, ruff.

---

## Background / conventions to know

- Run tests: `uv run pytest --no-cov -x` (fast, stop on first failure)
- Run all tests with coverage: `uv run pytest`
- Lint: `uv run ruff check .` — must be clean before every commit
- Format: `uv run ruff format .`
- Commit style: `#<issue> [type]: <description>` if tied to an issue; else `[type]: <description>`
- The `validated` single-line string uses **two-space** separators between logical address lines — same convention as `standardized` in `StandardizeResponseV1`. Example: `"123 MAIN ST  APT 4  SPRINGFIELD, IL 62701-1234"`.
- `ComponentSet` wraps address components with spec metadata. Use `spec=USPS_PUB28_SPEC` and `spec_version=USPS_PUB28_SPEC_VERSION` from `usps_data/spec.py`.
- Tests for async code use `@pytest.mark.asyncio`.
- Provider tests use `AsyncMock` to replace the HTTP client without real network calls.
- The factory uses module-level singletons; tests reset them via a `reset_*_singleton` fixture (see `test_provider_factory.py` for the pattern).

---

## Task 1: Add `ValidationResult` model and rewrite `ValidateResponseV1` in `models.py`

**Files:**
- Modify: `models.py`
- Modify: `tests/unit/test_validate_router.py` (update field references to new shape)
- Modify: `tests/unit/validation/test_null_provider.py` (update field references)
- Modify: `tests/unit/validation/test_usps_provider.py` (update field references — will still fail until Task 4)

### Step 1: Write a failing test for `ValidationResult`

Add to the bottom of `tests/unit/test_validate_router.py` (or a new `tests/unit/test_models.py` if the router test is already large — check first):

```python
from models import ValidationResult, ValidateResponseV1

class TestValidationResult:
    def test_confirmed_status(self) -> None:
        r = ValidationResult(status="confirmed", dpv_match_code="Y", provider="usps")
        assert r.status == "confirmed"
        assert r.dpv_match_code == "Y"
        assert r.provider == "usps"

    def test_unavailable_has_no_dpv(self) -> None:
        r = ValidationResult(status="unavailable")
        assert r.dpv_match_code is None
        assert r.provider is None


class TestValidateResponseV1Shape:
    def test_address_fields_present(self) -> None:
        r = ValidateResponseV1(
            country="US",
            validation=ValidationResult(status="unavailable"),
        )
        assert r.address_line_1 is None
        assert r.address_line_2 is None
        assert r.city is None
        assert r.region is None
        assert r.postal_code is None
        assert r.validated is None
        assert r.components is None
        assert r.latitude is None
        assert r.longitude is None
        assert r.warnings == []
        assert r.api_version == "1"

    def test_full_confirmed_response(self) -> None:
        from models import ComponentSet
        r = ValidateResponseV1(
            address_line_1="123 MAIN ST",
            address_line_2="",
            city="SPRINGFIELD",
            region="IL",
            postal_code="62701-1234",
            country="US",
            validated="123 MAIN ST  SPRINGFIELD, IL 62701-1234",
            components=ComponentSet(
                spec="usps-pub28",
                spec_version="unknown",
                values={"address_line_1": "123 MAIN ST", "city": "SPRINGFIELD",
                        "region": "IL", "postal_code": "62701-1234"},
            ),
            validation=ValidationResult(status="confirmed", dpv_match_code="Y", provider="usps"),
            latitude=39.7817,
            longitude=-89.6501,
            warnings=[],
        )
        assert r.validation.status == "confirmed"
        assert r.validation.dpv_match_code == "Y"
        assert r.postal_code == "62701-1234"
        assert r.latitude == 39.7817
```

### Step 2: Run to verify it fails

```bash
uv run pytest --no-cov -x tests/unit/test_validate_router.py -v
```

Expected: `ImportError` or `ValidationError` — `ValidationResult` does not exist yet and `ValidateResponseV1` has the wrong shape.

### Step 3: Update `models.py`

Add `ValidationResult` above `ValidateResponseV1`. Rewrite `ValidateResponseV1`. The existing fields (`input_address`, `validation_status`, `dpv_match_code`, `zip_plus4`, `vacant`, `corrected_components`, `provider`) are **removed** and replaced.

```python
class ValidationResult(BaseModel):
    """Provider-returned validation outcome metadata.

    ``status`` is the primary machine-readable result:

    * ``confirmed``                   — DPV code Y: fully confirmed delivery point.
    * ``confirmed_missing_secondary`` — DPV code S: building confirmed, unit missing.
    * ``confirmed_bad_secondary``     — DPV code D: building confirmed, unit unrecognised.
    * ``not_confirmed``               — DPV code N: address not found in USPS database.
    * ``unavailable``                 — provider not configured or unreachable.
    """

    status: Literal[
        "confirmed",
        "confirmed_missing_secondary",
        "confirmed_bad_secondary",
        "not_confirmed",
        "unavailable",
    ]
    dpv_match_code: Literal["Y", "S", "D", "N"] | None = Field(
        default=None,
        description="USPS DPV match code. Y=confirmed, S=missing secondary, "
        "D=bad secondary, N=not found. None when unavailable.",
    )
    provider: str | None = Field(
        default=None,
        description="Provider that performed validation ('usps', 'google', etc.). "
        "None when unavailable.",
    )


class ValidateResponseV1(BaseModel):
    """Response body for POST /api/v1/validate.

    Mirrors the structure of ``StandardizeResponseV1``.  Address fields are
    ``str | None`` because corrected components are only present when the
    provider returns a confirmed or corrected address.

    ``postal_code`` is the jurisdiction-neutral postal identifier.  For US
    addresses it carries the full ZIP+4 (e.g. ``"62701-1234"``) when the
    provider returns it, or the 5-digit ZIP otherwise.

    ``vacant`` and other USPS-specific indicators appear in
    ``components.values`` when the provider returns them.
    """

    address_line_1: str | None = None
    address_line_2: str | None = None
    city: str | None = None
    region: str | None = None
    postal_code: str | None = None
    country: str
    validated: str | None = Field(
        default=None,
        description="Single-line canonical address using two-space separator convention.",
    )
    components: ComponentSet | None = None
    validation: ValidationResult
    latitude: float | None = None
    longitude: float | None = None
    warnings: list[str] = Field(default_factory=list)
    api_version: Literal["1"] = "1"
```

Also remove the now-stale docstring from the old `ValidateResponseV1` that described `corrected_components`.

### Step 4: Run to verify model tests pass (other tests will still fail)

```bash
uv run pytest --no-cov -x tests/unit/test_validate_router.py::TestValidationResult tests/unit/test_validate_router.py::TestValidateResponseV1Shape -v
```

Expected: PASS for the new model tests. Other tests in the suite will fail — that is expected and will be fixed task-by-task.

### Step 5: Commit

```bash
git add models.py tests/unit/test_validate_router.py
git commit -m "feat: add ValidationResult model, rewrite ValidateResponseV1 to mirror StandardizeResponseV1"
```

---

## Task 2: Update `NullProvider` to new response shape

**Files:**
- Modify: `services/validation/null_provider.py`
- Modify: `tests/unit/validation/test_null_provider.py`

### Step 1: Rewrite the null provider tests

Replace the contents of `tests/unit/validation/test_null_provider.py`:

```python
"""Unit tests for the NullProvider (no-op validation backend)."""

import pytest

from models import ValidateRequestV1
from services.validation.null_provider import NullProvider


class TestNullProvider:
    @pytest.fixture()
    def provider(self) -> NullProvider:
        return NullProvider()

    @pytest.mark.asyncio
    async def test_returns_unavailable_status(self, provider: NullProvider) -> None:
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation.status == "unavailable"

    @pytest.mark.asyncio
    async def test_provider_name_is_none(self, provider: NullProvider) -> None:
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation.provider is None

    @pytest.mark.asyncio
    async def test_dpv_match_code_is_none(self, provider: NullProvider) -> None:
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation.dpv_match_code is None

    @pytest.mark.asyncio
    async def test_address_fields_are_none(self, provider: NullProvider) -> None:
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.address_line_1 is None
        assert result.components is None
        assert result.validated is None

    @pytest.mark.asyncio
    async def test_lat_lng_are_none(self, provider: NullProvider) -> None:
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.latitude is None
        assert result.longitude is None

    @pytest.mark.asyncio
    async def test_warnings_is_empty(self, provider: NullProvider) -> None:
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_api_version_is_1(self, provider: NullProvider) -> None:
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.api_version == "1"
```

### Step 2: Run to verify tests fail

```bash
uv run pytest --no-cov -x tests/unit/validation/test_null_provider.py -v
```

Expected: FAIL — `NullProvider.validate()` still returns old shape.

### Step 3: Update `null_provider.py`

```python
"""NullProvider — safe no-op backend used when no provider is configured."""

import logging

from models import ValidateRequestV1, ValidateResponseV1, ValidationResult

logger = logging.getLogger(__name__)


class NullProvider:
    """Returns ``validation.status='unavailable'`` for every request.

    Used as the default backend so the service starts cleanly without any
    external credentials.  Suitable for development and environments where
    validation is not yet required.
    """

    async def validate(self, request: ValidateRequestV1) -> ValidateResponseV1:
        logger.debug("NullProvider: returning unavailable for country=%s", request.country)
        return ValidateResponseV1(
            country=request.country,
            validation=ValidationResult(status="unavailable"),
        )
```

### Step 4: Run to verify tests pass

```bash
uv run pytest --no-cov -x tests/unit/validation/test_null_provider.py -v
```

Expected: All PASS.

### Step 5: Lint

```bash
uv run ruff check services/validation/null_provider.py tests/unit/validation/test_null_provider.py
```

### Step 6: Commit

```bash
git add services/validation/null_provider.py tests/unit/validation/test_null_provider.py
git commit -m "feat: update NullProvider to new ValidateResponseV1 shape"
```

---

## Task 3: Add shared `_build_validated_string` helper

**Files:**
- Create: `services/validation/_helpers.py`

This helper is used by both `USPSProvider` and `GoogleProvider` to build the two-space-separated single-line canonical address string — matching the convention used by `StandardizeResponseV1.standardized`.

No test file is needed for this module directly; it will be covered by the provider tests in Tasks 4 and 6. Write and commit it now so both providers can import it.

### Step 1: Create `services/validation/_helpers.py`

```python
"""Shared helpers for validation providers."""


def _build_validated_string(
    address_line_1: str | None,
    address_line_2: str | None,
    city: str | None,
    region: str | None,
    postal_code: str | None,
) -> str:
    """Build a single-line canonical address string.

    Uses two-space separators between logical address lines, matching
    the ``standardized`` field convention in ``StandardizeResponseV1``.

    Example output: ``"123 MAIN ST  APT 4  SPRINGFIELD, IL 62701-1234"``
    """
    if city and region:
        city_state = f"{city}, {region}"
    elif city:
        city_state = city
    elif region:
        city_state = region
    else:
        city_state = ""

    last_line = " ".join(p for p in (city_state, postal_code or "") if p)
    parts = [p for p in (address_line_1 or "", address_line_2 or "", last_line) if p]
    return "  ".join(parts)
```

### Step 2: Lint

```bash
uv run ruff check services/validation/_helpers.py
```

### Step 3: Commit

```bash
git add services/validation/_helpers.py
git commit -m "feat: add _build_validated_string helper for validation providers"
```

---

## Task 4: Update `USPSClient._map_response()` to flat output shape

**Files:**
- Modify: `services/validation/usps_client.py` (`_map_response` only)
- Modify: `tests/unit/validation/test_usps_client.py`

The current `_map_response` returns a dict with a nested `corrected_components` dict and a separate `zip_plus4` key. The new shape is flat, with `postal_code` carrying the full ZIP+4 and `vacant` as a top-level key. This is a pure data-mapping change — no change to auth, rate limiting, or HTTP logic.

### Step 1: Update `_map_response` test constants and assertions

In `tests/unit/validation/test_usps_client.py`, find `VALID_ADDRESS_RESPONSE` and the test for `_map_response`. Update the fixture to use a response with a `ZIPPlus4` extension, and update assertions to the new flat shape:

```python
# Update the existing map-response test (find TestUSPSClientMapResponse or similar class)
# Replace assertions like:
#   assert result["corrected_components"]["city"] == "SPRINGFIELD"
#   assert result["zip_plus4"] == "1234"
# With:
#   assert result["city"] == "SPRINGFIELD"
#   assert result["postal_code"] == "62701-1234"   # ZIP+4 merged
#   assert result["vacant"] == "N"
#   assert "corrected_components" not in result
#   assert "zip_plus4" not in result
```

Add a test for the case where `ZIPPlus4` is absent (5-digit only):

```python
def test_map_response_without_zip_plus4(self) -> None:
    raw = {
        "address": {
            "streetAddress": "123 MAIN ST",
            "city": "SPRINGFIELD",
            "state": "IL",
            "ZIPCode": "62701",
        },
        "addressAdditionalInfo": {
            "DPVConfirmation": "Y",
            "vacant": "N",
        },
    }
    result = USPSClient._map_response(raw)
    assert result["postal_code"] == "62701"

def test_map_response_merges_zip_plus4(self) -> None:
    raw = {
        "address": {
            "streetAddress": "123 MAIN ST",
            "city": "SPRINGFIELD",
            "state": "IL",
            "ZIPCode": "62701",
            "ZIPPlus4": "1234",
        },
        "addressAdditionalInfo": {"DPVConfirmation": "Y", "vacant": "N"},
    }
    result = USPSClient._map_response(raw)
    assert result["postal_code"] == "62701-1234"

def test_map_response_secondary_address(self) -> None:
    raw = {
        "address": {
            "streetAddress": "123 MAIN ST",
            "secondaryAddress": "APT 4",
            "city": "SPRINGFIELD",
            "state": "IL",
            "ZIPCode": "62701",
        },
        "addressAdditionalInfo": {"DPVConfirmation": "S"},
    }
    result = USPSClient._map_response(raw)
    assert result["address_line_2"] == "APT 4"

def test_map_response_vacant_surfaced(self) -> None:
    raw = {
        "address": {"streetAddress": "123 MAIN ST", "city": "X", "state": "IL", "ZIPCode": "62701"},
        "addressAdditionalInfo": {"DPVConfirmation": "Y", "vacant": "Y"},
    }
    result = USPSClient._map_response(raw)
    assert result["vacant"] == "Y"

def test_map_response_no_street_returns_no_address_fields(self) -> None:
    raw = {"address": {}, "addressAdditionalInfo": {"DPVConfirmation": "N"}}
    result = USPSClient._map_response(raw)
    assert result["address_line_1"] == ""
```

### Step 2: Run to verify tests fail

```bash
uv run pytest --no-cov -x tests/unit/validation/test_usps_client.py -v
```

Expected: FAIL on the map-response assertions.

### Step 3: Rewrite `_map_response` in `usps_client.py`

Replace the existing `_map_response` static method:

```python
@staticmethod
def _map_response(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalise the USPS v3 JSON response to a provider-neutral dict.

    Returns a flat dict with keys:
    ``dpv_match_code``, ``address_line_1``, ``address_line_2``,
    ``city``, ``region``, ``postal_code``, ``vacant``.
    """
    addr = raw.get("address", {})
    extra = raw.get("addressAdditionalInfo", {})

    zip_code = addr.get("ZIPCode", "")
    zip_ext = addr.get("ZIPPlus4", "") or ""
    postal_code = f"{zip_code}-{zip_ext}" if zip_ext else zip_code

    return {
        "dpv_match_code": extra.get("DPVConfirmation") or None,
        "address_line_1": addr.get("streetAddress", ""),
        "address_line_2": addr.get("secondaryAddress", ""),
        "city": addr.get("city", ""),
        "region": addr.get("state", ""),
        "postal_code": postal_code,
        "vacant": extra.get("vacant") or None,
    }
```

Also update the docstring on `validate_address` (returns line starting with "Returns a normalised dict with keys:") to list the new keys.

### Step 4: Run to verify tests pass

```bash
uv run pytest --no-cov -x tests/unit/validation/test_usps_client.py -v
```

Expected: All PASS.

### Step 5: Lint

```bash
uv run ruff check services/validation/usps_client.py tests/unit/validation/test_usps_client.py
```

### Step 6: Commit

```bash
git add services/validation/usps_client.py tests/unit/validation/test_usps_client.py
git commit -m "feat: update USPSClient._map_response to flat provider-neutral dict"
```

---

## Task 5: Update `USPSProvider` to new `ValidateResponseV1` shape

**Files:**
- Modify: `services/validation/usps_provider.py`
- Modify: `tests/unit/validation/test_usps_provider.py`

### Step 1: Rewrite `test_usps_provider.py`

The old test fixtures use the old `corrected_components`/`zip_plus4` dict keys. Replace with the new flat shape from Task 4.

```python
"""Unit tests for USPSProvider — validates request→response mapping."""

from unittest.mock import AsyncMock

import httpx
import pytest

from models import ValidateRequestV1
from services.validation.usps_provider import USPSProvider

# Flat dict returned by the updated USPSClient._map_response
CLIENT_RESULT_Y = {
    "dpv_match_code": "Y",
    "address_line_1": "123 MAIN ST",
    "address_line_2": "",
    "city": "SPRINGFIELD",
    "region": "IL",
    "postal_code": "62701-1234",
    "vacant": "N",
}

CLIENT_RESULT_N = {
    "dpv_match_code": "N",
    "address_line_1": "",
    "address_line_2": "",
    "city": "",
    "region": "",
    "postal_code": "",
    "vacant": None,
}


class TestUSPSProvider:
    @pytest.fixture()
    def mock_client(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture()
    def provider(self, mock_client: AsyncMock) -> USPSProvider:
        p = USPSProvider.__new__(USPSProvider)
        p._client = mock_client
        return p

    @pytest.mark.asyncio
    async def test_dpv_y_sets_confirmed_status(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation.status == "confirmed"
        assert result.validation.dpv_match_code == "Y"

    @pytest.mark.asyncio
    async def test_dpv_s_sets_confirmed_missing_secondary(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = {**CLIENT_RESULT_Y, "dpv_match_code": "S"}
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation.status == "confirmed_missing_secondary"

    @pytest.mark.asyncio
    async def test_dpv_d_sets_confirmed_bad_secondary(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = {**CLIENT_RESULT_Y, "dpv_match_code": "D"}
        req = ValidateRequestV1(address="123 Main St Apt 999", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation.status == "confirmed_bad_secondary"

    @pytest.mark.asyncio
    async def test_dpv_n_sets_not_confirmed(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_N
        req = ValidateRequestV1(address="999 Fake St", city="Nowhere", region="IL")
        result = await provider.validate(req)
        assert result.validation.status == "not_confirmed"

    @pytest.mark.asyncio
    async def test_provider_field_is_usps(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation.provider == "usps"

    @pytest.mark.asyncio
    async def test_postal_code_with_zip_plus4(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.postal_code == "62701-1234"

    @pytest.mark.asyncio
    async def test_address_lines_populated(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.address_line_1 == "123 MAIN ST"
        assert result.city == "SPRINGFIELD"
        assert result.region == "IL"

    @pytest.mark.asyncio
    async def test_components_contains_vacant(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.components is not None
        assert result.components.values.get("vacant") == "N"

    @pytest.mark.asyncio
    async def test_components_spec_is_usps_pub28(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.components is not None
        assert result.components.spec == "usps-pub28"

    @pytest.mark.asyncio
    async def test_validated_string_built(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validated == "123 MAIN ST  SPRINGFIELD, IL 62701-1234"

    @pytest.mark.asyncio
    async def test_lat_lng_are_none(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.latitude is None
        assert result.longitude is None

    @pytest.mark.asyncio
    async def test_warnings_is_empty(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_not_confirmed_has_no_components(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_N
        req = ValidateRequestV1(address="999 Fake St", city="Nowhere", region="IL")
        result = await provider.validate(req)
        assert result.components is None
        assert result.validated is None

    @pytest.mark.asyncio
    async def test_http_error_raises(self, provider: USPSProvider, mock_client: AsyncMock) -> None:
        mock_client.validate_address.side_effect = httpx.TimeoutException("timeout")
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        with pytest.raises(httpx.TimeoutException):
            await provider.validate(req)
```

### Step 2: Run to verify tests fail

```bash
uv run pytest --no-cov -x tests/unit/validation/test_usps_provider.py -v
```

Expected: FAIL — provider still returns old shape.

### Step 3: Rewrite `usps_provider.py`

```python
"""USPSProvider — validation backend backed by USPS Addresses API v3."""

import logging
from typing import Literal

from models import ComponentSet, ValidateRequestV1, ValidateResponseV1, ValidationResult
from services.validation._helpers import _build_validated_string
from services.validation.usps_client import USPSClient
from usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION

logger = logging.getLogger(__name__)

_DPV_TO_STATUS: dict[
    str,
    Literal[
        "confirmed",
        "confirmed_missing_secondary",
        "confirmed_bad_secondary",
        "not_confirmed",
    ],
] = {
    "Y": "confirmed",
    "S": "confirmed_missing_secondary",
    "D": "confirmed_bad_secondary",
    "N": "not_confirmed",
}


class USPSProvider:
    """Validates US addresses against the USPS Addresses API v3.

    Constructed by :func:`~services.validation.factory.get_provider`; do not
    instantiate directly in application code.
    """

    def __init__(self, client: USPSClient) -> None:
        self._client = client

    async def validate(self, request: ValidateRequestV1) -> ValidateResponseV1:
        logger.debug("USPSProvider.validate: calling USPS API, country=%s", request.country)
        raw = await self._client.validate_address(
            street_address=request.address,
            city=request.city,
            state=request.region,
            zip_code=request.postal_code,
        )

        dpv = raw.get("dpv_match_code")
        status = _DPV_TO_STATUS.get(dpv or "", "not_confirmed")

        address_line_1 = raw.get("address_line_1") or None
        address_line_2 = raw.get("address_line_2") or None
        city = raw.get("city") or None
        region = raw.get("region") or None
        postal_code = raw.get("postal_code") or None
        vacant = raw.get("vacant")

        # Only build components and validated string when we have a street.
        components: ComponentSet | None = None
        validated: str | None = None
        if address_line_1:
            comp_values: dict[str, str] = {
                k: v
                for k, v in {
                    "address_line_1": address_line_1,
                    "address_line_2": address_line_2 or "",
                    "city": city or "",
                    "region": region or "",
                    "postal_code": postal_code or "",
                    "vacant": vacant or "",
                }.items()
                if v
            }
            components = ComponentSet(
                spec=USPS_PUB28_SPEC,
                spec_version=USPS_PUB28_SPEC_VERSION,
                values=comp_values,
            )
            validated = _build_validated_string(
                address_line_1, address_line_2, city, region, postal_code
            )

        return ValidateResponseV1(
            address_line_1=address_line_1,
            address_line_2=address_line_2,
            city=city,
            region=region,
            postal_code=postal_code,
            country=request.country,
            validated=validated,
            components=components,
            validation=ValidationResult(
                status=status,
                dpv_match_code=dpv,  # type: ignore[arg-type]
                provider="usps",
            ),
        )
```

### Step 4: Run to verify tests pass

```bash
uv run pytest --no-cov -x tests/unit/validation/test_usps_provider.py -v
```

Expected: All PASS.

### Step 5: Lint

```bash
uv run ruff check services/validation/usps_provider.py tests/unit/validation/test_usps_provider.py
```

### Step 6: Commit

```bash
git add services/validation/usps_provider.py tests/unit/validation/test_usps_provider.py
git commit -m "feat: update USPSProvider to new ValidateResponseV1 shape"
```

---

## Task 6: Update validate router tests and verify full suite passes

**Files:**
- Modify: `tests/unit/test_validate_router.py`

The router test uses the TestClient (sync) and checks response JSON fields. All field references to the old shape (`validation_status`, `dpv_match_code` at top-level, `corrected_components`, etc.) need updating to the new nested shape.

### Step 1: Read `tests/unit/test_validate_router.py` in full before editing

(Do not guess at its structure — read it first.)

### Step 2: Update all response field assertions

Old top-level → new nested:

| Old | New |
|---|---|
| `resp["validation_status"]` | `resp["validation"]["status"]` |
| `resp["dpv_match_code"]` | `resp["validation"]["dpv_match_code"]` |
| `resp["provider"]` | `resp["validation"]["provider"]` |
| `resp["corrected_components"]` | `resp["components"]` |
| `resp["zip_plus4"]` | `resp["postal_code"]` (ZIP+4 is now in postal_code) |
| `resp["input_address"]` | *(removed — drop the assertion)* |

Add assertions for any new fields the tests should verify (e.g. `resp["warnings"] == []`).

### Step 3: Run full test suite

```bash
uv run pytest --no-cov -x -v
```

Expected: All PASS. If any test still references old fields, fix them now.

### Step 4: Lint

```bash
uv run ruff check .
```

### Step 5: Commit

```bash
git add tests/unit/test_validate_router.py
git commit -m "test: update validate router tests to new ValidateResponseV1 shape"
```

---

## Task 7: Implement `GoogleClient`

**Files:**
- Create: `services/validation/google_client.py`
- Create: `tests/unit/validation/test_google_client.py`

### Step 1: Write `test_google_client.py` (failing)

```python
"""Unit tests for GoogleClient — response mapping and request construction."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from services.validation.google_client import GoogleClient

API_KEY = "test-api-key"

# Minimal realistic Google Address Validation API response for a confirmed address.
GOOGLE_RESPONSE_Y = {
    "result": {
        "verdict": {
            "inputGranularity": "PREMISE",
            "validationGranularity": "PREMISE",
            "geocodeGranularity": "PREMISE",
            "addressComplete": True,
            "hasUnconfirmedComponents": False,
            "hasInferredComponents": False,
            "hasReplacedComponents": False,
        },
        "geocode": {
            "location": {"latitude": 39.7817, "longitude": -89.6501},
        },
        "uspsData": {
            "standardizedAddress": {
                "firstAddressLine": "123 MAIN ST",
                "city": "SPRINGFIELD",
                "state": "IL",
                "zipCode": "62701",
                "zipCodeExtension": "1234",
            },
            "dpvConfirmation": "Y",
            "dpvVacant": "N",
        },
    }
}

GOOGLE_RESPONSE_N = {
    "result": {
        "verdict": {
            "validationGranularity": "OTHER",
            "addressComplete": False,
        },
        "geocode": {},
        "uspsData": {
            "standardizedAddress": {},
            "dpvConfirmation": "N",
        },
    }
}

GOOGLE_RESPONSE_WITH_SECONDARY = {
    "result": {
        "verdict": {"validationGranularity": "SUB_PREMISE", "addressComplete": True,
                    "hasInferredComponents": False, "hasReplacedComponents": True,
                    "hasUnconfirmedComponents": False},
        "geocode": {"location": {"latitude": 40.0, "longitude": -88.0}},
        "uspsData": {
            "standardizedAddress": {
                "firstAddressLine": "123 MAIN ST",
                "secondAddressLine": "APT 4",
                "city": "SPRINGFIELD",
                "state": "IL",
                "zipCode": "62701",
                "zipCodeExtension": "5678",
            },
            "dpvConfirmation": "S",
            "dpvVacant": "N",
        },
    }
}

GOOGLE_RESPONSE_INFERRED = {
    "result": {
        "verdict": {"validationGranularity": "PREMISE", "addressComplete": True,
                    "hasInferredComponents": True, "hasReplacedComponents": False,
                    "hasUnconfirmedComponents": False},
        "geocode": {"location": {"latitude": 39.7, "longitude": -89.6}},
        "uspsData": {
            "standardizedAddress": {
                "firstAddressLine": "123 MAIN ST",
                "city": "SPRINGFIELD",
                "state": "IL",
                "zipCode": "62701",
            },
            "dpvConfirmation": "Y",
            "dpvVacant": "N",
        },
    }
}


class TestGoogleClientMapResponse:
    """Tests for the static _map_response method — no HTTP calls."""

    def test_dpv_y_extracted(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_Y)
        assert result["dpv_match_code"] == "Y"

    def test_dpv_n_extracted(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_N)
        assert result["dpv_match_code"] == "N"

    def test_address_line_1_extracted(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_Y)
        assert result["address_line_1"] == "123 MAIN ST"

    def test_address_line_2_extracted(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_WITH_SECONDARY)
        assert result["address_line_2"] == "APT 4"

    def test_address_line_2_empty_when_absent(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_Y)
        assert result["address_line_2"] == ""

    def test_city_extracted(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_Y)
        assert result["city"] == "SPRINGFIELD"

    def test_region_extracted(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_Y)
        assert result["region"] == "IL"

    def test_postal_code_merges_zip_plus4(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_Y)
        assert result["postal_code"] == "62701-1234"

    def test_postal_code_without_extension(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_N)
        # No zipCode in standardizedAddress for N result
        assert result["postal_code"] == ""

    def test_vacant_extracted(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_Y)
        assert result["vacant"] == "N"

    def test_latitude_extracted(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_Y)
        assert result["latitude"] == pytest.approx(39.7817)

    def test_longitude_extracted(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_Y)
        assert result["longitude"] == pytest.approx(-89.6501)

    def test_lat_lng_none_when_no_geocode(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_N)
        assert result["latitude"] is None
        assert result["longitude"] is None

    def test_has_inferred_components_false(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_Y)
        assert result["has_inferred_components"] is False

    def test_has_inferred_components_true(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_INFERRED)
        assert result["has_inferred_components"] is True

    def test_has_replaced_components_true(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_WITH_SECONDARY)
        assert result["has_replaced_components"] is True

    def test_has_unconfirmed_components_false(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_Y)
        assert result["has_unconfirmed_components"] is False


class TestGoogleClientValidateAddress:
    """Tests for the validate_address method — uses mocked HTTP."""

    @pytest.fixture()
    def mock_http(self) -> AsyncMock:
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture()
    def client(self, mock_http: AsyncMock) -> GoogleClient:
        return GoogleClient(api_key=API_KEY, http_client=mock_http)

    def _make_response(self, json_data: dict, status_code: int = 200) -> MagicMock:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        resp.json.return_value = json_data
        resp.raise_for_status = MagicMock()
        return resp

    @pytest.mark.asyncio
    async def test_posts_to_correct_url(
        self, client: GoogleClient, mock_http: AsyncMock
    ) -> None:
        mock_http.post.return_value = self._make_response(GOOGLE_RESPONSE_Y)
        await client.validate_address(street_address="123 Main St", city="Springfield", state="IL")
        call_kwargs = mock_http.post.call_args
        assert "addressvalidation.googleapis.com" in call_kwargs[0][0]

    @pytest.mark.asyncio
    async def test_sends_api_key(self, client: GoogleClient, mock_http: AsyncMock) -> None:
        mock_http.post.return_value = self._make_response(GOOGLE_RESPONSE_Y)
        await client.validate_address("123 Main St")
        call_kwargs = mock_http.post.call_args
        assert call_kwargs[1]["params"]["key"] == API_KEY

    @pytest.mark.asyncio
    async def test_enables_usps_cass(self, client: GoogleClient, mock_http: AsyncMock) -> None:
        mock_http.post.return_value = self._make_response(GOOGLE_RESPONSE_Y)
        await client.validate_address("123 Main St")
        call_kwargs = mock_http.post.call_args
        body = call_kwargs[1]["json"]
        assert body.get("enableUspsCass") is True

    @pytest.mark.asyncio
    async def test_http_error_raises(self, client: GoogleClient, mock_http: AsyncMock) -> None:
        mock_http.post.side_effect = httpx.TimeoutException("timeout")
        with pytest.raises(httpx.TimeoutException):
            await client.validate_address("123 Main St")
```

### Step 2: Run to verify tests fail

```bash
uv run pytest --no-cov -x tests/unit/validation/test_google_client.py -v
```

Expected: `ModuleNotFoundError` — `google_client.py` does not exist yet.

### Step 3: Create `services/validation/google_client.py`

```python
"""Low-level Google Address Validation API HTTP client.

Handles request construction (with ``enableUspsCass: true``), API key
authentication, and normalisation of the raw JSON response to a
provider-neutral dict consumed by
:class:`~services.validation.google_provider.GoogleProvider`.

Callers should not instantiate this class directly; use
:func:`~services.validation.factory.get_provider` instead.
"""

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_VALIDATE_URL = "https://addressvalidation.googleapis.com/v1:validateAddress"


class GoogleClient:
    """Async Google Address Validation API client.

    Parameters
    ----------
    api_key:
        Google Cloud API key restricted to the Address Validation API.
    http_client:
        Shared :class:`httpx.AsyncClient` instance (caller owns lifecycle).
    """

    def __init__(self, api_key: str, http_client: httpx.AsyncClient) -> None:
        self._api_key = api_key
        self._http = http_client

    async def validate_address(
        self,
        street_address: str,
        city: str | None = None,
        state: str | None = None,
        zip_code: str | None = None,
    ) -> dict[str, Any]:
        """Validate a single US address via the Google Address Validation API.

        Returns a normalised dict with keys:
        ``dpv_match_code``, ``address_line_1``, ``address_line_2``,
        ``city``, ``region``, ``postal_code``, ``vacant``,
        ``latitude``, ``longitude``,
        ``has_inferred_components``, ``has_replaced_components``,
        ``has_unconfirmed_components``.

        Raises :class:`httpx.HTTPStatusError` on non-2xx responses.
        """
        address_lines = [street_address]
        city_state_zip = " ".join(p for p in (city, state, zip_code) if p)
        if city_state_zip:
            address_lines.append(city_state_zip)

        logger.debug("GoogleClient: validating address, %d lines", len(address_lines))
        resp = await self._http.post(
            _VALIDATE_URL,
            params={"key": self._api_key},
            json={
                "address": {"addressLines": address_lines},
                "enableUspsCass": True,
            },
        )
        resp.raise_for_status()
        raw: dict[str, Any] = resp.json()
        return self._map_response(raw)

    @staticmethod
    def _map_response(raw: dict[str, Any]) -> dict[str, Any]:
        """Normalise the Google Address Validation API JSON response."""
        result = raw.get("result", {})
        verdict = result.get("verdict", {})
        usps = result.get("uspsData", {})
        std_addr = usps.get("standardizedAddress", {})
        geocode = result.get("geocode", {})
        location = geocode.get("location", {})

        zip_code = std_addr.get("zipCode", "")
        zip_ext = std_addr.get("zipCodeExtension", "") or ""
        postal_code = f"{zip_code}-{zip_ext}" if zip_ext else zip_code

        lat = location.get("latitude")
        lng = location.get("longitude")

        return {
            "dpv_match_code": usps.get("dpvConfirmation") or None,
            "address_line_1": std_addr.get("firstAddressLine", ""),
            "address_line_2": std_addr.get("secondAddressLine", ""),
            "city": std_addr.get("city", ""),
            "region": std_addr.get("state", ""),
            "postal_code": postal_code,
            "vacant": usps.get("dpvVacant") or None,
            "latitude": lat if lat is not None else None,
            "longitude": lng if lng is not None else None,
            "has_inferred_components": verdict.get("hasInferredComponents", False),
            "has_replaced_components": verdict.get("hasReplacedComponents", False),
            "has_unconfirmed_components": verdict.get("hasUnconfirmedComponents", False),
        }
```

### Step 4: Run to verify tests pass

```bash
uv run pytest --no-cov -x tests/unit/validation/test_google_client.py -v
```

Expected: All PASS.

### Step 5: Lint

```bash
uv run ruff check services/validation/google_client.py tests/unit/validation/test_google_client.py
```

### Step 6: Commit

```bash
git add services/validation/google_client.py tests/unit/validation/test_google_client.py
git commit -m "feat: add GoogleClient for Google Address Validation API"
```

---

## Task 8: Implement `GoogleProvider`

**Files:**
- Create: `services/validation/google_provider.py`
- Create: `tests/unit/validation/test_google_provider.py`

### Step 1: Write `test_google_provider.py` (failing)

```python
"""Unit tests for GoogleProvider — validates request→response mapping."""

from unittest.mock import AsyncMock

import httpx
import pytest

from models import ValidateRequestV1
from services.validation.google_provider import GoogleProvider

# Flat dicts matching GoogleClient._map_response output
CLIENT_RESULT_Y = {
    "dpv_match_code": "Y",
    "address_line_1": "123 MAIN ST",
    "address_line_2": "",
    "city": "SPRINGFIELD",
    "region": "IL",
    "postal_code": "62701-1234",
    "vacant": "N",
    "latitude": 39.7817,
    "longitude": -89.6501,
    "has_inferred_components": False,
    "has_replaced_components": False,
    "has_unconfirmed_components": False,
}

CLIENT_RESULT_N = {
    "dpv_match_code": "N",
    "address_line_1": "",
    "address_line_2": "",
    "city": "",
    "region": "",
    "postal_code": "",
    "vacant": None,
    "latitude": None,
    "longitude": None,
    "has_inferred_components": False,
    "has_replaced_components": False,
    "has_unconfirmed_components": False,
}

CLIENT_RESULT_WITH_WARNINGS = {
    **CLIENT_RESULT_Y,
    "has_inferred_components": True,
    "has_replaced_components": True,
    "has_unconfirmed_components": True,
}


class TestGoogleProvider:
    @pytest.fixture()
    def mock_client(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture()
    def provider(self, mock_client: AsyncMock) -> GoogleProvider:
        p = GoogleProvider.__new__(GoogleProvider)
        p._client = mock_client
        return p

    @pytest.mark.asyncio
    async def test_dpv_y_sets_confirmed(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation.status == "confirmed"
        assert result.validation.dpv_match_code == "Y"

    @pytest.mark.asyncio
    async def test_dpv_s_sets_confirmed_missing_secondary(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = {**CLIENT_RESULT_Y, "dpv_match_code": "S"}
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation.status == "confirmed_missing_secondary"

    @pytest.mark.asyncio
    async def test_dpv_d_sets_confirmed_bad_secondary(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = {**CLIENT_RESULT_Y, "dpv_match_code": "D"}
        req = ValidateRequestV1(address="123 Main St Apt 999", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation.status == "confirmed_bad_secondary"

    @pytest.mark.asyncio
    async def test_dpv_n_sets_not_confirmed(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_N
        req = ValidateRequestV1(address="999 Fake St", city="Nowhere", region="IL")
        result = await provider.validate(req)
        assert result.validation.status == "not_confirmed"

    @pytest.mark.asyncio
    async def test_provider_field_is_google(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation.provider == "google"

    @pytest.mark.asyncio
    async def test_lat_lng_populated(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.latitude == pytest.approx(39.7817)
        assert result.longitude == pytest.approx(-89.6501)

    @pytest.mark.asyncio
    async def test_lat_lng_none_when_not_confirmed(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_N
        req = ValidateRequestV1(address="999 Fake St", city="Nowhere", region="IL")
        result = await provider.validate(req)
        assert result.latitude is None
        assert result.longitude is None

    @pytest.mark.asyncio
    async def test_postal_code_with_zip_plus4(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.postal_code == "62701-1234"

    @pytest.mark.asyncio
    async def test_validated_string_built(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validated == "123 MAIN ST  SPRINGFIELD, IL 62701-1234"

    @pytest.mark.asyncio
    async def test_components_spec_is_usps_pub28(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.components is not None
        assert result.components.spec == "usps-pub28"

    @pytest.mark.asyncio
    async def test_components_contains_vacant(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.components is not None
        assert result.components.values.get("vacant") == "N"

    @pytest.mark.asyncio
    async def test_no_components_when_not_confirmed(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_N
        req = ValidateRequestV1(address="999 Fake St")
        result = await provider.validate(req)
        assert result.components is None
        assert result.validated is None

    @pytest.mark.asyncio
    async def test_inferred_components_warning(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_WITH_WARNINGS
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert any("inferred" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_replaced_components_warning(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_WITH_WARNINGS
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert any("replaced" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_unconfirmed_components_warning(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_WITH_WARNINGS
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert any("unconfirmed" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_no_warnings_when_all_false(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_http_error_raises(self, provider: GoogleProvider, mock_client: AsyncMock) -> None:
        mock_client.validate_address.side_effect = httpx.TimeoutException("timeout")
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        with pytest.raises(httpx.TimeoutException):
            await provider.validate(req)
```

### Step 2: Run to verify tests fail

```bash
uv run pytest --no-cov -x tests/unit/validation/test_google_provider.py -v
```

Expected: `ModuleNotFoundError` — `google_provider.py` does not exist yet.

### Step 3: Create `services/validation/google_provider.py`

```python
"""GoogleProvider — validation backend backed by Google Address Validation API."""

import logging
from typing import Literal

from models import ComponentSet, ValidateRequestV1, ValidateResponseV1, ValidationResult
from services.validation._helpers import _build_validated_string
from services.validation.google_client import GoogleClient
from usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION

logger = logging.getLogger(__name__)

_DPV_TO_STATUS: dict[
    str,
    Literal[
        "confirmed",
        "confirmed_missing_secondary",
        "confirmed_bad_secondary",
        "not_confirmed",
    ],
] = {
    "Y": "confirmed",
    "S": "confirmed_missing_secondary",
    "D": "confirmed_bad_secondary",
    "N": "not_confirmed",
}

_WARNING_INFERRED = "Provider inferred one or more address components not present in input"
_WARNING_REPLACED = "Provider replaced one or more address components"
_WARNING_UNCONFIRMED = "One or more address components could not be confirmed"


class GoogleProvider:
    """Validates US addresses against the Google Address Validation API.

    Uses ``enableUspsCass: true`` to obtain USPS CASS-certified DPV codes,
    making this a full drop-in replacement for :class:`USPSProvider` that
    additionally returns geocoordinates.

    Constructed by :func:`~services.validation.factory.get_provider`; do not
    instantiate directly in application code.
    """

    def __init__(self, client: GoogleClient) -> None:
        self._client = client

    async def validate(self, request: ValidateRequestV1) -> ValidateResponseV1:
        logger.debug("GoogleProvider.validate: calling Google API, country=%s", request.country)
        raw = await self._client.validate_address(
            street_address=request.address,
            city=request.city,
            state=request.region,
            zip_code=request.postal_code,
        )

        dpv = raw.get("dpv_match_code")
        status = _DPV_TO_STATUS.get(dpv or "", "not_confirmed")

        address_line_1 = raw.get("address_line_1") or None
        address_line_2 = raw.get("address_line_2") or None
        city = raw.get("city") or None
        region = raw.get("region") or None
        postal_code = raw.get("postal_code") or None
        vacant = raw.get("vacant")
        latitude = raw.get("latitude")
        longitude = raw.get("longitude")

        # Only build components and validated string when we have a street.
        components: ComponentSet | None = None
        validated: str | None = None
        if address_line_1:
            comp_values: dict[str, str] = {
                k: v
                for k, v in {
                    "address_line_1": address_line_1,
                    "address_line_2": address_line_2 or "",
                    "city": city or "",
                    "region": region or "",
                    "postal_code": postal_code or "",
                    "vacant": vacant or "",
                }.items()
                if v
            }
            components = ComponentSet(
                spec=USPS_PUB28_SPEC,
                spec_version=USPS_PUB28_SPEC_VERSION,
                values=comp_values,
            )
            validated = _build_validated_string(
                address_line_1, address_line_2, city, region, postal_code
            )

        warnings: list[str] = []
        if raw.get("has_inferred_components"):
            warnings.append(_WARNING_INFERRED)
        if raw.get("has_replaced_components"):
            warnings.append(_WARNING_REPLACED)
        if raw.get("has_unconfirmed_components"):
            warnings.append(_WARNING_UNCONFIRMED)

        return ValidateResponseV1(
            address_line_1=address_line_1,
            address_line_2=address_line_2,
            city=city,
            region=region,
            postal_code=postal_code,
            country=request.country,
            validated=validated,
            components=components,
            validation=ValidationResult(
                status=status,
                dpv_match_code=dpv,  # type: ignore[arg-type]
                provider="google",
            ),
            latitude=latitude,
            longitude=longitude,
            warnings=warnings,
        )
```

### Step 4: Run to verify tests pass

```bash
uv run pytest --no-cov -x tests/unit/validation/test_google_provider.py -v
```

Expected: All PASS.

### Step 5: Lint

```bash
uv run ruff check services/validation/google_provider.py tests/unit/validation/test_google_provider.py
```

### Step 6: Commit

```bash
git add services/validation/google_provider.py tests/unit/validation/test_google_provider.py
git commit -m "feat: add GoogleProvider for Google Address Validation API"
```

---

## Task 9: Update factory to support `VALIDATION_PROVIDER=google`

**Files:**
- Modify: `services/validation/factory.py`
- Modify: `tests/unit/validation/test_provider_factory.py`

### Step 1: Add failing factory tests

Append to `TestGetProvider` in `test_provider_factory.py`:

```python
from services.validation.google_provider import GoogleProvider

# Also add to the reset fixture — reset _google_provider too:
@pytest.fixture(autouse=True)
def reset_singletons() -> None:
    factory_module._usps_provider = None
    factory_module._google_provider = None
    yield
    factory_module._usps_provider = None
    factory_module._google_provider = None

# New tests:
def test_google_keyword_gives_google_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VALIDATION_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_API_KEY", "my-key")
    assert isinstance(get_provider(), GoogleProvider)

def test_google_keyword_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VALIDATION_PROVIDER", "GOOGLE")
    monkeypatch.setenv("GOOGLE_API_KEY", "my-key")
    assert isinstance(get_provider(), GoogleProvider)

def test_google_provider_is_singleton(self, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VALIDATION_PROVIDER", "google")
    monkeypatch.setenv("GOOGLE_API_KEY", "my-key")
    assert get_provider() is get_provider()

def test_google_missing_api_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VALIDATION_PROVIDER", "google")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
        get_provider()

def test_unknown_provider_error_includes_google(self, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VALIDATION_PROVIDER", "smarty")
    with pytest.raises(ValueError, match="google"):
        get_provider()
```

**Note:** rename the existing `reset_usps_singleton` fixture to `reset_singletons` and add the `_google_provider` reset lines to it — the `autouse=True` will apply to all tests in the class.

### Step 2: Run to verify tests fail

```bash
uv run pytest --no-cov -x tests/unit/validation/test_provider_factory.py -v
```

Expected: FAIL — `google` branch doesn't exist; `_google_provider` attribute doesn't exist.

### Step 3: Update `factory.py`

Add `_google_provider` singleton and `google` branch. Also update the module docstring to document the new env vars. Full updated file:

```python
"""Provider factory -- reads env vars and returns the configured backend.

Environment variables
---------------------
VALIDATION_PROVIDER
    Which backend to use.  Accepted values (case-insensitive):

    ``none`` (default)
        :class:`~services.validation.null_provider.NullProvider` -- returns
        ``validation.status='unavailable'`` without any network calls.

    ``usps``
        :class:`~services.validation.usps_provider.USPSProvider` -- calls
        the USPS Addresses API v3.  Requires ``USPS_CONSUMER_KEY`` and
        ``USPS_CONSUMER_SECRET``.

    ``google``
        :class:`~services.validation.google_provider.GoogleProvider` -- calls
        the Google Address Validation API with USPS CASS data.
        Requires ``GOOGLE_API_KEY``.  Returns geocoordinates in addition to
        all data provided by the USPS provider.

USPS_CONSUMER_KEY
    OAuth2 client ID from the USPS Developer Portal.  Required when
    ``VALIDATION_PROVIDER=usps``.

USPS_CONSUMER_SECRET
    OAuth2 client secret.  Required when ``VALIDATION_PROVIDER=usps``.

GOOGLE_API_KEY
    Google Cloud API key restricted to the Address Validation API.  Required
    when ``VALIDATION_PROVIDER=google``.
"""

import logging
import os

import httpx

from services.validation.google_client import GoogleClient
from services.validation.google_provider import GoogleProvider
from services.validation.null_provider import NullProvider
from services.validation.protocol import ValidationProvider
from services.validation.usps_client import USPSClient
from services.validation.usps_provider import USPSProvider

logger = logging.getLogger(__name__)

# Module-level singletons -- created once, shared across all requests.
_http_client: httpx.AsyncClient | None = None
_usps_provider: USPSProvider | None = None
_google_provider: GoogleProvider | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client  # noqa: PLW0603
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _http_client


def _get_usps_provider(key: str, secret: str) -> USPSProvider:
    """Return the shared :class:`USPSProvider` singleton, creating it if needed."""
    global _usps_provider  # noqa: PLW0603
    if _usps_provider is None:
        _usps_provider = USPSProvider(
            client=USPSClient(
                consumer_key=key,
                consumer_secret=secret,
                http_client=_get_http_client(),
            )
        )
    return _usps_provider


def _get_google_provider(api_key: str) -> GoogleProvider:
    """Return the shared :class:`GoogleProvider` singleton, creating it if needed."""
    global _google_provider  # noqa: PLW0603
    if _google_provider is None:
        _google_provider = GoogleProvider(
            client=GoogleClient(
                api_key=api_key,
                http_client=_get_http_client(),
            )
        )
    return _google_provider


def get_provider() -> ValidationProvider:
    """Return the configured :class:`ValidationProvider`.

    Provider instances and the underlying HTTP client are module-level
    singletons so client state (token cache, connection pool) is shared
    across all requests.  NullProvider is stateless and is constructed
    cheaply on each call.
    """
    provider_name = os.environ.get("VALIDATION_PROVIDER", "none").strip().lower()

    if provider_name in ("none", ""):
        logger.debug("get_provider: using NullProvider")
        return NullProvider()

    if provider_name == "usps":
        key = os.environ.get("USPS_CONSUMER_KEY", "").strip()
        secret = os.environ.get("USPS_CONSUMER_SECRET", "").strip()
        if not key or not secret:
            raise ValueError(
                "USPS_CONSUMER_KEY and USPS_CONSUMER_SECRET must be set "
                "when VALIDATION_PROVIDER=usps"
            )
        logger.debug("get_provider: using USPSProvider")
        return _get_usps_provider(key, secret)

    if provider_name == "google":
        api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "GOOGLE_API_KEY must be set when VALIDATION_PROVIDER=google"
            )
        logger.debug("get_provider: using GoogleProvider")
        return _get_google_provider(api_key)

    raise ValueError(
        f"Unknown VALIDATION_PROVIDER value: '{provider_name}'. "
        "Supported values: 'none', 'usps', 'google'."
    )
```

### Step 4: Run to verify tests pass

```bash
uv run pytest --no-cov -x tests/unit/validation/test_provider_factory.py -v
```

Expected: All PASS.

### Step 5: Run full test suite

```bash
uv run pytest --no-cov -v
```

Expected: All PASS. If anything is still red, fix it before committing.

### Step 6: Lint

```bash
uv run ruff check .
```

### Step 7: Commit

```bash
git add services/validation/factory.py tests/unit/validation/test_provider_factory.py
git commit -m "feat: add google provider branch to validation factory"
```

---

## Task 10: Update AGENTS.md and run full coverage check

**Files:**
- Modify: `AGENTS.md`

### Step 1: Update the validation provider env var table in AGENTS.md

Find the table under `## Validation provider` and add the `GOOGLE_API_KEY` row:

```markdown
| `GOOGLE_API_KEY` | string | — | Required when `VALIDATION_PROVIDER=google` |
```

Also update the `VALIDATION_PROVIDER` row to list `none`, `usps`, `google`.

### Step 2: Run full coverage check

```bash
uv run pytest
```

Expected: All PASS, coverage ≥ 80%.

### Step 3: Lint

```bash
uv run ruff check .
```

### Step 4: Commit

```bash
git add AGENTS.md
git commit -m "docs: add GOOGLE_API_KEY to AGENTS.md validation provider table"
```

---

## Done

At this point:
- `ValidateResponseV1` mirrors `StandardizeResponseV1` structure
- `ValidationResult` groups outcome metadata cleanly
- `NullProvider` and `USPSProvider` emit the new shape
- `GoogleProvider` + `GoogleClient` are implemented and fully tested
- Factory supports `VALIDATION_PROVIDER=google` with `GOOGLE_API_KEY`
- All tests pass; coverage ≥ 80%; ruff clean
