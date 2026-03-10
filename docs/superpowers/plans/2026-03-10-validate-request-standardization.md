# Validate Request Standardization Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ValidateRequestV1` mirror `StandardizeRequestV1` — accepting either a raw address string or a pre-parsed components dict — and run both through the full parse → standardize pipeline before calling the validation provider.

**Architecture:** The router gains the same normalization block as `/standardize` (parse if raw string, then standardize), then passes the resulting `StandardizeResponseV1` to the provider. All provider implementations update their `validate()` signature to accept `StandardizeResponseV1` instead of `ValidateRequestV1`. The router merges parse/standardize warnings into the provider response.

**Tech Stack:** FastAPI, Pydantic v2, pytest-asyncio, httpx. Run tests with `uv run pytest --no-cov`, lint with `uv run ruff check .`.

**Design doc:** `docs/plans/2026-03-10-validate-request-standardization-design.md`

---

## Chunk 1: Model + Protocol

### Task 1: Update `ValidateRequestV1` in `models.py`

**Files:**
- Modify: `models.py`

**Context:** `ValidateRequestV1` currently has `address: str` (required, street line only), `city: str | None`, `region: str | None`, `postal_code: str | None`. We replace all of that with `address: str | None` and `components: dict[str, str] | None`, mirroring `StandardizeRequestV1`. This is a breaking change — callers (tests included) will be updated in subsequent tasks.

- [ ] **Step 1: Write failing tests for new model shape**

Add to the bottom of `tests/unit/test_validate_router.py` (in a new `TestValidateRequestV1Model` class):

```python
class TestValidateRequestV1Model:
    def test_accepts_raw_address_string(self) -> None:
        req = ValidateRequestV1(address="123 Main St, Springfield, IL 62701")
        assert req.address == "123 Main St, Springfield, IL 62701"
        assert req.components is None

    def test_accepts_components_dict(self) -> None:
        req = ValidateRequestV1(
            components={"address_number": "123", "street_name": "MAIN"}
        )
        assert req.components == {"address_number": "123", "street_name": "MAIN"}
        assert req.address is None

    def test_both_fields_none_is_valid_at_model_level(self) -> None:
        # Validation happens in the router, not the model
        req = ValidateRequestV1()
        assert req.address is None
        assert req.components is None

    def test_country_defaults_to_us(self) -> None:
        req = ValidateRequestV1(address="123 Main St")
        assert req.country == "US"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/test_validate_router.py::TestValidateRequestV1Model --no-cov -v
```

Expected: FAIL — `ValidateRequestV1` does not yet have a `components` field, and `address` is required.

- [ ] **Step 3: Update `ValidateRequestV1` in `models.py`**

Replace the current `ValidateRequestV1` class (lines 136–149) with:

```python
class ValidateRequestV1(CountryRequestMixin):
    """Request body for POST /api/v1/validate.

    Accepts either a raw address string *or* pre-parsed components — mirroring
    :class:`StandardizeRequestV1`.  In both cases the input is run through the
    full parse → standardize pipeline before the validation provider is called,
    so providers always receive clean, USPS-formatted components.

    ``address`` is the full raw address string (not just the street line).
    When both fields are supplied, ``components`` takes precedence and
    ``address`` is ignored.  Validation of which fields are present happens
    in the router, not here.
    """

    address: str | None = Field(None, max_length=1000)
    components: dict[str, str] | None = None
```

- [ ] **Step 4: Run model tests to confirm they pass**

```bash
uv run pytest tests/unit/test_validate_router.py::TestValidateRequestV1Model --no-cov -v
```

Expected: PASS (4 tests).

- [ ] **Step 5: Lint**

```bash
uv run ruff check .
```

Expected: no errors. Fix any before continuing.

- [ ] **Step 6: Commit the model change (red commit — other tests will break)**

```bash
git add models.py tests/unit/test_validate_router.py
git commit -m "test: add ValidateRequestV1 model shape tests (red — providers/router tests break)"
```

---

### Task 2: Update `ValidationProvider` protocol signature

**Files:**
- Modify: `services/validation/protocol.py`

**Context:** The protocol currently declares `validate(self, request: ValidateRequestV1)`. It needs to change to accept `StandardizeResponseV1`. This is the interface contract all providers implement.

- [ ] **Step 1: Update the import and signature in `protocol.py`**

Replace the file content:

```python
"""ValidationProvider protocol — the interface every backend must satisfy."""

from typing import Protocol, runtime_checkable

from models import StandardizeResponseV1, ValidateResponseV1


@runtime_checkable
class ValidationProvider(Protocol):
    """Async interface for address-validation backends.

    All providers receive a fully normalised :class:`~models.StandardizeResponseV1`
    (the result of the parse → standardize pipeline) rather than raw user input.
    The router owns normalisation; providers own validation only.

    Concrete implementations: :class:`~services.validation.null_provider.NullProvider`,
    :class:`~services.validation.usps_provider.USPSProvider`,
    :class:`~services.validation.google_provider.GoogleProvider`.
    """

    async def validate(self, std: StandardizeResponseV1) -> ValidateResponseV1:
        """Validate the standardised address *std* and return an authoritative response."""
        ...
```

- [ ] **Step 2: Lint**

```bash
uv run ruff check .
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add services/validation/protocol.py
git commit -m "refactor: update ValidationProvider protocol to accept StandardizeResponseV1"
```

---

## Chunk 2: Provider Implementations

### Task 3: Update `NullProvider`

**Files:**
- Modify: `services/validation/null_provider.py`
- Modify: `tests/unit/validation/test_null_provider.py`

**Context:** `NullProvider.validate` currently accepts `ValidateRequestV1` and reads `request.country`. After the change it accepts `StandardizeResponseV1` and reads `std.country`. All test fixtures must be replaced.

- [ ] **Step 1: Rewrite provider tests to use `StandardizeResponseV1` input**

Replace `tests/unit/validation/test_null_provider.py` entirely:

```python
"""Unit tests for the NullProvider (no-op validation backend)."""

import pytest

from models import ComponentSet, StandardizeResponseV1
from services.validation.null_provider import NullProvider
from usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION


def _make_std(country: str = "US") -> StandardizeResponseV1:
    return StandardizeResponseV1(
        address_line_1="123 MAIN ST",
        address_line_2="",
        city="SPRINGFIELD",
        region="IL",
        postal_code="62701",
        country=country,
        standardized="123 MAIN ST  SPRINGFIELD, IL 62701",
        components=ComponentSet(
            spec=USPS_PUB28_SPEC,
            spec_version=USPS_PUB28_SPEC_VERSION,
            values={"address_number": "123", "street_name": "MAIN"},
        ),
        warnings=[],
    )


class TestNullProvider:
    @pytest.fixture()
    def provider(self) -> NullProvider:
        return NullProvider()

    @pytest.mark.asyncio
    async def test_returns_unavailable_status(self, provider: NullProvider) -> None:
        result = await provider.validate(_make_std())
        assert result.validation.status == "unavailable"

    @pytest.mark.asyncio
    async def test_provider_name_is_none(self, provider: NullProvider) -> None:
        result = await provider.validate(_make_std())
        assert result.validation.provider is None

    @pytest.mark.asyncio
    async def test_dpv_match_code_is_none(self, provider: NullProvider) -> None:
        result = await provider.validate(_make_std())
        assert result.validation.dpv_match_code is None

    @pytest.mark.asyncio
    async def test_address_fields_are_none(self, provider: NullProvider) -> None:
        result = await provider.validate(_make_std())
        assert result.address_line_1 is None
        assert result.components is None
        assert result.validated is None

    @pytest.mark.asyncio
    async def test_lat_lng_are_none(self, provider: NullProvider) -> None:
        result = await provider.validate(_make_std())
        assert result.latitude is None
        assert result.longitude is None

    @pytest.mark.asyncio
    async def test_warnings_is_empty(self, provider: NullProvider) -> None:
        result = await provider.validate(_make_std())
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_api_version_is_1(self, provider: NullProvider) -> None:
        result = await provider.validate(_make_std())
        assert result.api_version == "1"

    @pytest.mark.asyncio
    async def test_country_passed_through(self, provider: NullProvider) -> None:
        result = await provider.validate(_make_std(country="US"))
        assert result.country == "US"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/validation/test_null_provider.py --no-cov -v
```

Expected: FAIL — `NullProvider.validate` still accepts `ValidateRequestV1`, not `StandardizeResponseV1`.

- [ ] **Step 3: Update `null_provider.py`**

Replace the file:

```python
"""NullProvider — safe no-op backend used when no provider is configured."""

import logging

from models import StandardizeResponseV1, ValidateResponseV1, ValidationResult

logger = logging.getLogger(__name__)


class NullProvider:
    """Returns ``validation.status='unavailable'`` for every request.

    Used as the default backend so the service starts cleanly without any
    external credentials.  Suitable for development and environments where
    validation is not yet required.
    """

    async def validate(self, std: StandardizeResponseV1) -> ValidateResponseV1:
        logger.debug("NullProvider: returning unavailable for country=%s", std.country)
        return ValidateResponseV1(
            country=std.country,
            validation=ValidationResult(status="unavailable"),
        )
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/unit/validation/test_null_provider.py --no-cov -v
```

Expected: PASS (8 tests).

- [ ] **Step 5: Lint**

```bash
uv run ruff check .
```

- [ ] **Step 6: Commit**

```bash
git add services/validation/null_provider.py tests/unit/validation/test_null_provider.py
git commit -m "refactor: update NullProvider to accept StandardizeResponseV1"
```

---

### Task 4: Update `USPSProvider`

**Files:**
- Modify: `services/validation/usps_provider.py`
- Modify: `tests/unit/validation/test_usps_provider.py`

**Context:** `USPSProvider.validate` reads `request.address/city/region/postal_code/country` and passes them to the USPS client. After the change it reads the equivalent fields from `StandardizeResponseV1`: `std.address_line_1` (standardized street line), `std.city`, `std.region`, `std.postal_code`, `std.country`. The USPS client call and all response-mapping logic are unchanged.

- [ ] **Step 1: Update provider tests to use `StandardizeResponseV1` input**

Replace the import and all `req = ValidateRequestV1(...)` fixtures in `tests/unit/validation/test_usps_provider.py`. Replace `from models import ValidateRequestV1` with `from models import ComponentSet, StandardizeResponseV1` and add the import for spec constants. Add a `_make_std()` helper and update every test method:

```python
"""Unit tests for USPSProvider — validates request→response mapping."""

from unittest.mock import AsyncMock

import httpx
import pytest

from models import ComponentSet, StandardizeResponseV1
from services.validation.usps_provider import USPSProvider
from usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION

# Flat dicts matching the updated USPSClient._map_response output
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


def _make_std(
    address_line_1: str = "123 MAIN ST",
    city: str = "SPRINGFIELD",
    region: str = "IL",
    postal_code: str = "62701",
    country: str = "US",
) -> StandardizeResponseV1:
    return StandardizeResponseV1(
        address_line_1=address_line_1,
        address_line_2="",
        city=city,
        region=region,
        postal_code=postal_code,
        country=country,
        standardized=f"{address_line_1}  {city}, {region} {postal_code}",
        components=ComponentSet(
            spec=USPS_PUB28_SPEC,
            spec_version=USPS_PUB28_SPEC_VERSION,
            values={"address_number": "123", "street_name": "MAIN"},
        ),
        warnings=[],
    )


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
        result = await provider.validate(_make_std())
        assert result.validation.status == "confirmed"
        assert result.validation.dpv_match_code == "Y"

    @pytest.mark.asyncio
    async def test_dpv_s_sets_confirmed_missing_secondary(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = {**CLIENT_RESULT_Y, "dpv_match_code": "S"}
        result = await provider.validate(_make_std())
        assert result.validation.status == "confirmed_missing_secondary"

    @pytest.mark.asyncio
    async def test_dpv_d_sets_confirmed_bad_secondary(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = {**CLIENT_RESULT_Y, "dpv_match_code": "D"}
        result = await provider.validate(_make_std(address_line_1="123 MAIN ST APT 999"))
        assert result.validation.status == "confirmed_bad_secondary"

    @pytest.mark.asyncio
    async def test_dpv_n_sets_not_confirmed(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_N
        result = await provider.validate(_make_std(address_line_1="999 FAKE ST", city="NOWHERE"))
        assert result.validation.status == "not_confirmed"

    @pytest.mark.asyncio
    async def test_provider_field_is_usps(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.validation.provider == "usps"

    @pytest.mark.asyncio
    async def test_postal_code_with_zip_plus4(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.postal_code == "62701-1234"

    @pytest.mark.asyncio
    async def test_address_lines_populated(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.address_line_1 == "123 MAIN ST"
        assert result.city == "SPRINGFIELD"
        assert result.region == "IL"

    @pytest.mark.asyncio
    async def test_components_contains_vacant(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.components is not None
        assert result.components.values.get("vacant") == "N"

    @pytest.mark.asyncio
    async def test_components_spec_is_usps_pub28(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.components is not None
        assert result.components.spec == "usps-pub28"

    @pytest.mark.asyncio
    async def test_validated_string_built(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.validated == "123 MAIN ST  SPRINGFIELD, IL 62701-1234"

    @pytest.mark.asyncio
    async def test_lat_lng_are_none(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.latitude is None
        assert result.longitude is None

    @pytest.mark.asyncio
    async def test_warnings_is_empty(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_not_confirmed_has_no_components(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_N
        result = await provider.validate(_make_std(address_line_1="999 FAKE ST"))
        assert result.components is None
        assert result.validated is None

    @pytest.mark.asyncio
    async def test_client_called_with_standardized_fields(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        """Provider must forward std fields (not raw user input) to the USPS client."""
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        std = _make_std(
            address_line_1="123 MAIN ST", city="SPRINGFIELD", region="IL", postal_code="62701"
        )
        await provider.validate(std)
        mock_client.validate_address.assert_called_once_with(
            street_address="123 MAIN ST",
            city="SPRINGFIELD",
            state="IL",
            zip_code="62701",
        )

    @pytest.mark.asyncio
    async def test_http_error_raises(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.side_effect = httpx.TimeoutException("timeout")
        with pytest.raises(httpx.TimeoutException):
            await provider.validate(_make_std())
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/validation/test_usps_provider.py --no-cov -v
```

Expected: FAIL — `USPSProvider.validate` still accepts `ValidateRequestV1`.

- [ ] **Step 3: Update `usps_provider.py`**

Replace the `validate` method signature and the client call. Only lines 23–30 change (`request` → `std`, field mapping):

```python
"""USPSProvider — validation backend backed by USPS Addresses API v3."""

import logging

from models import ComponentSet, StandardizeResponseV1, ValidateResponseV1, ValidationResult
from services.validation._helpers import _DPV_TO_STATUS, _build_validated_string
from services.validation.usps_client import USPSClient
from usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION

logger = logging.getLogger(__name__)


class USPSProvider:
    """Validates US addresses against the USPS Addresses API v3.

    Receives a fully normalised :class:`~models.StandardizeResponseV1` from the
    router (the result of the parse → standardize pipeline).  The
    ``address_line_1`` field carries the standardized street line sent to the
    USPS API.

    Constructed by :func:`~services.validation.factory.get_provider`; do not
    instantiate directly in application code.
    """

    def __init__(self, client: USPSClient) -> None:
        self._client = client

    async def validate(self, std: StandardizeResponseV1) -> ValidateResponseV1:
        logger.debug("USPSProvider.validate: calling USPS API, country=%s", std.country)
        raw = await self._client.validate_address(
            street_address=std.address_line_1,
            city=std.city,
            state=std.region,
            zip_code=std.postal_code,
        )

        dpv = raw.get("dpv_match_code")
        status = _DPV_TO_STATUS[dpv] if dpv is not None else "unavailable"

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
            country=std.country,
            validated=validated,
            components=components,
            validation=ValidationResult(
                status=status,
                dpv_match_code=dpv,  # type: ignore[arg-type]
                provider="usps",
            ),
            warnings=[],
        )
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/unit/validation/test_usps_provider.py --no-cov -v
```

Expected: PASS (15 tests).

- [ ] **Step 5: Lint**

```bash
uv run ruff check .
```

- [ ] **Step 6: Commit**

```bash
git add services/validation/usps_provider.py tests/unit/validation/test_usps_provider.py
git commit -m "refactor: update USPSProvider to accept StandardizeResponseV1"
```

---

### Task 5: Update `GoogleProvider`

**Files:**
- Modify: `services/validation/google_provider.py`
- Modify: `tests/unit/validation/test_google_provider.py`

**Context:** Identical mapping change to Task 4. `request.address/city/region/postal_code/country` → `std.address_line_1/city/region/postal_code/country`. Warning logic and lat/lng handling are unchanged.

- [ ] **Step 1: Update provider tests to use `StandardizeResponseV1` input**

Replace `tests/unit/validation/test_google_provider.py` entirely:

```python
"""Unit tests for GoogleProvider — validates request→response mapping."""

from unittest.mock import AsyncMock

import httpx
import pytest

from models import ComponentSet, StandardizeResponseV1
from services.validation.google_provider import GoogleProvider
from usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION

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


def _make_std(
    address_line_1: str = "123 MAIN ST",
    city: str = "SPRINGFIELD",
    region: str = "IL",
    postal_code: str = "62701",
    country: str = "US",
) -> StandardizeResponseV1:
    return StandardizeResponseV1(
        address_line_1=address_line_1,
        address_line_2="",
        city=city,
        region=region,
        postal_code=postal_code,
        country=country,
        standardized=f"{address_line_1}  {city}, {region} {postal_code}",
        components=ComponentSet(
            spec=USPS_PUB28_SPEC,
            spec_version=USPS_PUB28_SPEC_VERSION,
            values={"address_number": "123", "street_name": "MAIN"},
        ),
        warnings=[],
    )


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
        result = await provider.validate(_make_std())
        assert result.validation.status == "confirmed"
        assert result.validation.dpv_match_code == "Y"

    @pytest.mark.asyncio
    async def test_dpv_s_sets_confirmed_missing_secondary(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = {**CLIENT_RESULT_Y, "dpv_match_code": "S"}
        result = await provider.validate(_make_std())
        assert result.validation.status == "confirmed_missing_secondary"

    @pytest.mark.asyncio
    async def test_dpv_d_sets_confirmed_bad_secondary(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = {**CLIENT_RESULT_Y, "dpv_match_code": "D"}
        result = await provider.validate(_make_std(address_line_1="123 MAIN ST APT 999"))
        assert result.validation.status == "confirmed_bad_secondary"

    @pytest.mark.asyncio
    async def test_dpv_n_sets_not_confirmed(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_N
        result = await provider.validate(_make_std(address_line_1="999 FAKE ST", city="NOWHERE"))
        assert result.validation.status == "not_confirmed"

    @pytest.mark.asyncio
    async def test_provider_field_is_google(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.validation.provider == "google"

    @pytest.mark.asyncio
    async def test_lat_lng_populated(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.latitude == pytest.approx(39.7817)
        assert result.longitude == pytest.approx(-89.6501)

    @pytest.mark.asyncio
    async def test_lat_lng_none_when_not_confirmed(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_N
        result = await provider.validate(_make_std(address_line_1="999 FAKE ST"))
        assert result.latitude is None
        assert result.longitude is None

    @pytest.mark.asyncio
    async def test_postal_code_with_zip_plus4(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.postal_code == "62701-1234"

    @pytest.mark.asyncio
    async def test_validated_string_built(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.validated == "123 MAIN ST  SPRINGFIELD, IL 62701-1234"

    @pytest.mark.asyncio
    async def test_components_spec_is_usps_pub28(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.components is not None
        assert result.components.spec == "usps-pub28"

    @pytest.mark.asyncio
    async def test_components_contains_vacant(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.components is not None
        assert result.components.values.get("vacant") == "N"

    @pytest.mark.asyncio
    async def test_no_components_when_not_confirmed(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_N
        result = await provider.validate(_make_std(address_line_1="999 FAKE ST"))
        assert result.components is None
        assert result.validated is None

    @pytest.mark.asyncio
    async def test_inferred_components_warning(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_WITH_WARNINGS
        result = await provider.validate(_make_std())
        assert any("inferred" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_replaced_components_warning(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_WITH_WARNINGS
        result = await provider.validate(_make_std())
        assert any("replaced" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_unconfirmed_components_warning(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_WITH_WARNINGS
        result = await provider.validate(_make_std())
        assert any("unconfirmed" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_no_warnings_when_all_false(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_client_called_with_standardized_fields(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        """Provider must forward std fields (not raw user input) to the Google client."""
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        std = _make_std(
            address_line_1="123 MAIN ST", city="SPRINGFIELD", region="IL", postal_code="62701"
        )
        await provider.validate(std)
        mock_client.validate_address.assert_called_once_with(
            street_address="123 MAIN ST",
            city="SPRINGFIELD",
            state="IL",
            zip_code="62701",
        )

    @pytest.mark.asyncio
    async def test_http_error_raises(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.side_effect = httpx.TimeoutException("timeout")
        with pytest.raises(httpx.TimeoutException):
            await provider.validate(_make_std())
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/validation/test_google_provider.py --no-cov -v
```

Expected: FAIL — `GoogleProvider.validate` still accepts `ValidateRequestV1`.

- [ ] **Step 3: Update `google_provider.py`**

Replace the file:

```python
"""GoogleProvider — validation backend backed by Google Address Validation API."""

import logging

from models import ComponentSet, StandardizeResponseV1, ValidateResponseV1, ValidationResult
from services.validation._helpers import _DPV_TO_STATUS, _build_validated_string
from services.validation.google_client import GoogleClient
from usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION

logger = logging.getLogger(__name__)

_WARNING_INFERRED = "Provider inferred one or more address components not present in input"
_WARNING_REPLACED = "Provider replaced one or more address components"
_WARNING_UNCONFIRMED = "One or more address components are unconfirmed"


class GoogleProvider:
    """Validates US addresses against the Google Address Validation API.

    Receives a fully normalised :class:`~models.StandardizeResponseV1` from the
    router (the result of the parse → standardize pipeline).  The
    ``address_line_1`` field carries the standardized street line sent to the
    Google API.

    Uses ``enableUspsCass: true`` to obtain USPS CASS-certified DPV codes,
    making this a full drop-in replacement for :class:`USPSProvider` that
    additionally returns geocoordinates.

    Constructed by :func:`~services.validation.factory.get_provider`; do not
    instantiate directly in application code.
    """

    def __init__(self, client: GoogleClient) -> None:
        self._client = client

    async def validate(self, std: StandardizeResponseV1) -> ValidateResponseV1:
        logger.debug("GoogleProvider.validate: calling Google API, country=%s", std.country)
        raw = await self._client.validate_address(
            street_address=std.address_line_1,
            city=std.city,
            state=std.region,
            zip_code=std.postal_code,
        )

        dpv = raw.get("dpv_match_code")
        status = _DPV_TO_STATUS[dpv] if dpv is not None else "unavailable"

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
            country=std.country,
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

- [ ] **Step 4: Run tests to confirm they pass**

```bash
uv run pytest tests/unit/validation/test_google_provider.py --no-cov -v
```

Expected: PASS (17 tests).

- [ ] **Step 5: Lint**

```bash
uv run ruff check .
```

- [ ] **Step 6: Commit**

```bash
git add services/validation/google_provider.py tests/unit/validation/test_google_provider.py
git commit -m "refactor: update GoogleProvider to accept StandardizeResponseV1"
```

---

## Chunk 3: Router + Router Tests

### Task 6: Update the validate router

**Files:**
- Modify: `routers/v1/validate.py`
- Modify: `tests/unit/test_validate_router.py`

**Context:** The router gains the same normalization block as `standardize.py`: resolve components (components > address), parse if raw string, standardize, then call `provider.validate(std)`. Parse/standardize warnings are merged into the provider response. The existing router tests (`TestValidateEndpoint`) need updating: old individual-field request shapes → new shapes; `test_missing_address_field_returns_422` becomes `test_missing_both_fields_returns_400`; new tests are added for raw-string and components-dict paths.

- [ ] **Step 1: Write failing router tests**

Replace the `TestValidateEndpoint` class in `tests/unit/test_validate_router.py` with the following (keep `TestValidationResult`, `TestValidateResponseV1Shape`, and `TestValidateRequestV1Model` intact):

```python
class TestValidateEndpoint:
    # --- raw address string path ---

    def test_raw_string_returns_200(self, client: TestClient) -> None:
        with patch(
            "routers.v1.validate.get_provider",
            return_value=_make_null_provider(NULL_RESPONSE),
        ):
            resp = client.post(
                "/api/v1/validate",
                json={"address": "123 Main St, Springfield, IL 62701"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["validation"]["status"] == "unavailable"
        assert body["api_version"] == "1"

    def test_raw_string_provider_receives_standardize_response(
        self, client: TestClient
    ) -> None:
        """Provider must receive a StandardizeResponseV1, not raw user input."""
        mock_provider = _make_null_provider(NULL_RESPONSE)
        with patch("routers.v1.validate.get_provider", return_value=mock_provider):
            client.post(
                "/api/v1/validate",
                json={"address": "123 Main St, Springfield, IL 62701"},
            )
        call_arg = mock_provider.validate.call_args[0][0]
        assert hasattr(call_arg, "address_line_1"), (
            "Provider should receive StandardizeResponseV1, not ValidateRequestV1"
        )

    # --- components dict path ---

    def test_components_dict_returns_200(self, client: TestClient) -> None:
        with patch(
            "routers.v1.validate.get_provider",
            return_value=_make_null_provider(NULL_RESPONSE),
        ):
            resp = client.post(
                "/api/v1/validate",
                json={
                    "components": {
                        "address_number": "123",
                        "street_name": "MAIN",
                        "street_name_post_type": "ST",
                        "place_name": "SPRINGFIELD",
                        "state_name": "IL",
                        "zip_code": "62701",
                    }
                },
            )
        assert resp.status_code == 200
        assert resp.json()["validation"]["status"] == "unavailable"

    def test_components_takes_precedence_over_address(self, client: TestClient) -> None:
        """When both fields are provided, components wins."""
        mock_provider = _make_null_provider(NULL_RESPONSE)
        with patch("routers.v1.validate.get_provider", return_value=mock_provider):
            client.post(
                "/api/v1/validate",
                json={
                    "address": "should be ignored",
                    "components": {
                        "address_number": "123",
                        "street_name": "MAIN",
                        "street_name_post_type": "ST",
                        "place_name": "SPRINGFIELD",
                        "state_name": "IL",
                        "zip_code": "62701",
                    },
                },
            )
        call_arg = mock_provider.validate.call_args[0][0]
        # Provider received a std with the components-derived content (not "should be ignored")
        assert "should" not in (call_arg.address_line_1 or "").lower()

    # --- confirmed response shape ---

    def test_confirmed_response_shape(self, client: TestClient) -> None:
        with patch(
            "routers.v1.validate.get_provider",
            return_value=_make_null_provider(CONFIRMED_RESPONSE),
        ):
            resp = client.post(
                "/api/v1/validate",
                json={"address": "123 Main St, Springfield, IL 62701"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["validation"]["dpv_match_code"] == "Y"
        assert body["validation"]["status"] == "confirmed"
        assert body["city"] == "SPRINGFIELD"

    # --- warning propagation ---

    def test_parse_warnings_merged_into_response(self, client: TestClient) -> None:
        """Warnings from the parse/standardize step appear in the response."""
        # An address with parenthesized text triggers a parse warning
        mock_provider = _make_null_provider(NULL_RESPONSE)
        with patch("routers.v1.validate.get_provider", return_value=mock_provider):
            resp = client.post(
                "/api/v1/validate",
                json={"address": "123 Main St (rear entrance), Springfield, IL 62701"},
            )
        assert resp.status_code == 200
        # Parenthesized text stripped warning should appear
        warnings = resp.json()["warnings"]
        assert any("parenthes" in w.lower() for w in warnings)

    def test_std_warnings_prepend_provider_warnings(self, client: TestClient) -> None:
        """std.warnings appear before provider warnings; neither is dropped."""
        provider_response = ValidateResponseV1(
            country="US",
            validation=ValidationResult(status="unavailable"),
            warnings=["provider warning"],
        )
        mock_provider = _make_null_provider(provider_response)
        with patch("routers.v1.validate.get_provider", return_value=mock_provider):
            resp = client.post(
                "/api/v1/validate",
                json={"address": "123 Main St (rear entrance), Springfield, IL 62701"},
            )
        warnings = resp.json()["warnings"]
        # Both warning sources must be present
        assert any("parenthes" in w.lower() for w in warnings), "std warning missing"
        assert "provider warning" in warnings, "provider warning dropped"
        # std warnings come first
        std_idx = next(i for i, w in enumerate(warnings) if "parenthes" in w.lower())
        provider_idx = warnings.index("provider warning")
        assert std_idx < provider_idx, "std warnings must precede provider warnings"

    # --- error paths ---

    def test_blank_address_returns_400(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={"address": "   "},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "address_required"

    def test_missing_both_fields_returns_400(self, client: TestClient) -> None:
        resp = client.post("/api/v1/validate", json={})
        assert resp.status_code == 400
        assert resp.json()["error"] == "components_or_address_required"

    def test_empty_components_dict_falls_through_to_address_error(
        self, client: TestClient
    ) -> None:
        """An empty components dict is treated as absent."""
        resp = client.post("/api/v1/validate", json={"components": {}})
        assert resp.status_code == 400
        assert resp.json()["error"] == "components_or_address_required"

    def test_unsupported_country_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={"address": "10 Downing St, London", "country": "GB"},
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "country_not_supported"

    def test_no_auth_returns_401(self, client_no_auth: TestClient) -> None:
        resp = client_no_auth.post(
            "/api/v1/validate",
            json={"address": "123 Main St, Springfield, IL 62701"},
        )
        assert resp.status_code == 401

    def test_bad_auth_returns_403(self, client_bad_auth: TestClient) -> None:
        resp = client_bad_auth.post(
            "/api/v1/validate",
            json={"address": "123 Main St, Springfield, IL 62701"},
        )
        assert resp.status_code == 403

    def test_address_too_long_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={"address": "A" * 1001},
        )
        assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/unit/test_validate_router.py::TestValidateEndpoint --no-cov -v
```

Expected: multiple FAILs — router still expects old request shape, no normalization pipeline.

- [ ] **Step 3: Update `routers/v1/validate.py`**

Replace the file:

```python
"""v1 validate endpoint.

POST /api/v1/validate — parses and standardizes the input address, then
confirms it represents a real deliverable location by delegating to the
configured :class:`~services.validation.protocol.ValidationProvider`.

Input pipeline
--------------
Both input modes run through the same parse → standardize pipeline before
the provider is called.  This guarantees that providers always receive
clean, USPS-formatted components regardless of how the caller supplied the
address.

* **Raw address string** (``address`` field): the string is parsed by
  :func:`~services.parser.parse_address` and then standardized by
  :func:`~services.standardizer.standardize`.
* **Pre-parsed components** (``components`` field): the dict is passed
  directly to :func:`~services.standardizer.standardize`, skipping the
  parse step.

When both fields are supplied, ``components`` takes precedence and
``address`` is ignored.

Warnings emitted by the parse or standardize step are merged into the
``warnings`` list of the final response alongside any warnings from the
provider itself.

The active provider is controlled by the ``VALIDATION_PROVIDER`` env var
(see :mod:`services.validation.factory`).  When no provider is configured
the endpoint still returns HTTP 200 with ``validation.status='unavailable'``
so upstream callers degrade gracefully.
"""

import logging

from fastapi import APIRouter, Depends

from auth import require_api_key
from models import ErrorResponse, ValidateRequestV1, ValidateResponseV1
from routers.v1.core import APIError, check_country
from services.parser import parse_address
from services.standardizer import standardize
from services.validation.factory import get_provider

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1",
    tags=["v1"],
    dependencies=[Depends(require_api_key)],
)


@router.post(
    "/validate",
    response_model=ValidateResponseV1,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
    summary="Validate an address against an authoritative source",
    description=(
        "Parses, standardizes, and then confirms that an address represents a "
        "real USPS deliverable location.\n\n"
        "**Input modes** (both run through parse → standardize before validation):\n"
        "- `address` — raw address string; parsed then standardized automatically.\n"
        "- `components` — pre-parsed component dict; standardized only (parse skipped).\n"
        "When both are supplied, `components` takes precedence.\n\n"
        "**DPV match codes**\n"
        "- `Y` — confirmed delivery point\n"
        "- `S` — building confirmed, secondary address (apt/unit) missing\n"
        "- `D` — building confirmed, secondary address not recognised\n"
        "- `N` — address not found\n\n"
        "When no validation provider is configured, `validation.status` is "
        "`unavailable` and all other result fields are `null`."
    ),
)
async def validate_address_v1(req: ValidateRequestV1) -> ValidateResponseV1:
    check_country(req.country)

    upstream_warnings: list[str] = []

    if req.components is not None and len(req.components) > 0:
        comps = req.components
    elif req.address is not None:
        raw = req.address.strip()
        if not raw:
            raise APIError(
                status_code=400,
                error="address_required",
                message="Provide 'address' (non-empty string) or 'components' (non-empty object).",
            )
        parse_result = parse_address(raw, country=req.country)
        comps = parse_result.components.values
        upstream_warnings = parse_result.warnings
    else:
        raise APIError(
            status_code=400,
            error="components_or_address_required",
            message="Provide 'address' (non-empty string) or 'components' (non-empty object).",
        )

    std = standardize(comps, country=req.country, upstream_warnings=upstream_warnings)

    provider = get_provider()
    logger.debug("validate_address_v1: provider=%s", type(provider).__name__)
    result = await provider.validate(std)

    if std.warnings:
        result = result.model_copy(update={"warnings": std.warnings + result.warnings})

    return result
```

- [ ] **Step 4: Run all router tests to confirm they pass**

```bash
uv run pytest tests/unit/test_validate_router.py --no-cov -v
```

Expected: PASS (all tests including new ones and retained model/response shape tests).

- [ ] **Step 5: Run the full test suite**

```bash
uv run pytest --no-cov -x
```

Expected: PASS. If any failures remain they will be in integration tests (updated in Task 7).

- [ ] **Step 6: Lint**

```bash
uv run ruff check .
```

- [ ] **Step 7: Commit**

```bash
git add routers/v1/validate.py tests/unit/test_validate_router.py
git commit -m "feat: update validate router — parse+standardize pipeline, new request shape"
```

---

### Task 7: Update integration tests

**Files:**
- Modify: `tests/integration/test_v1_validate.py`

**Context:** Integration tests post JSON directly to the HTTP client. The old shape `{"address": "123 Main St", "city": "Springfield", "region": "IL"}` must become `{"address": "123 Main St, Springfield, IL"}` (raw full string). The live USPS tests also update to full-string format.

- [ ] **Step 1: Update `tests/integration/test_v1_validate.py`**

Replace the file:

```python
"""Integration tests for POST /api/v1/validate.

The USPS live-API tests require real credentials and are skipped when
``USPS_CONSUMER_KEY`` / ``USPS_CONSUMER_SECRET`` are absent from the
environment.  They are never expected to run in CI without secrets.

The null-provider test always runs and exercises the full HTTP stack
against the running FastAPI app, including the parse → standardize pipeline.
"""

import os

import pytest
from fastapi.testclient import TestClient


class TestValidateNullProvider:
    """Always-run tests — NullProvider requires no external credentials."""

    def test_returns_unavailable_when_no_provider_configured(
        self,
        client: TestClient,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("VALIDATION_PROVIDER", raising=False)
        resp = client.post(
            "/api/v1/validate",
            json={"address": "123 Main St, Springfield, IL 62701"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["validation"]["status"] == "unavailable"
        assert body["validation"]["dpv_match_code"] is None
        assert body["validation"]["provider"] is None
        assert body["api_version"] == "1"

    def test_country_defaults_to_us(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VALIDATION_PROVIDER", raising=False)
        resp = client.post(
            "/api/v1/validate",
            json={"address": "123 Main St, Springfield, IL 62701"},
        )
        assert resp.json()["country"] == "US"

    def test_components_dict_input_accepted(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("VALIDATION_PROVIDER", raising=False)
        resp = client.post(
            "/api/v1/validate",
            json={
                "components": {
                    "address_number": "123",
                    "street_name": "MAIN",
                    "street_name_post_type": "ST",
                    "place_name": "SPRINGFIELD",
                    "state_name": "IL",
                    "zip_code": "62701",
                }
            },
        )
        assert resp.status_code == 200
        assert resp.json()["validation"]["status"] == "unavailable"


@pytest.mark.skipif(
    not os.environ.get("USPS_CONSUMER_KEY"),
    reason="USPS_CONSUMER_KEY not set — skipping live USPS API test",
)
class TestValidateUSPSLive:
    """Live USPS API tests — skipped unless credentials are present."""

    def test_known_good_address_confirmed(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        resp = client.post(
            "/api/v1/validate",
            json={"address": "1600 Pennsylvania Ave NW, Washington, DC 20500"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["validation"]["status"] in (
            "confirmed",
            "confirmed_missing_secondary",
            "confirmed_bad_secondary",
        )
        assert body["validation"]["provider"] == "usps"
        assert body["validation"]["dpv_match_code"] in ("Y", "S", "D")

    def test_fake_address_not_confirmed(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        resp = client.post(
            "/api/v1/validate",
            json={"address": "99999 Nonexistent Blvd, Nowhere, ZZ 00000"},
        )
        # May return 200 not_confirmed or a 4xx from USPS — both are acceptable.
        assert resp.status_code in (200, 400, 404, 422)
```

- [ ] **Step 2: Run integration tests**

```bash
uv run pytest tests/integration/test_v1_validate.py --no-cov -v
```

Expected: PASS (3 always-run tests; live USPS tests skipped unless credentials present).

- [ ] **Step 3: Run the full test suite with coverage**

```bash
uv run pytest
```

Expected: all tests pass, coverage ≥ 80%.

- [ ] **Step 4: Lint**

```bash
uv run ruff check .
```

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_v1_validate.py
git commit -m "test: update validate integration tests to new request shape"
```
