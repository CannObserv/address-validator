# Non-US Validate via Google Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow `POST /api/v1/validate` to accept non-US addresses when `components` are supplied, routing them directly to the Google provider while bypassing the US-specific parse → standardize pipeline. Non-US raw address strings are rejected with 422.

**Architecture:** Three-layer change: (1) `validate.py` adds a country guard and a passthrough `StandardizeResponseV1` builder for non-US + components; (2) `google_client.py` adds a `country` parameter, a non-US request path (`regionCode`, no `enableUspsCass`), and a new `_map_response_international()` mapper; (3) `google_provider.py` passes `std.country` to the client and reads the pre-mapped `"status"` key from the response dict (eliminating DPV-specific status logic from the provider). The `_map_response()` US path also gains a `"status"` key so both paths share the same provider contract.

**Tech Stack:** FastAPI, Pydantic, `httpx`, Google Address Validation API (`result.address.postalAddress` + `result.verdict` for non-US), `pytest`, `unittest.mock`

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `src/address_validator/routers/v1/validate.py` | Country guard + non-US passthrough path |
| Modify | `src/address_validator/services/validation/google_client.py` | Non-US API request + `_map_response_international` |
| Modify | `src/address_validator/services/validation/google_provider.py` | Pass country; read `raw["status"]`; non-US component spec |
| Modify | `tests/unit/test_validate_router.py` | Non-US acceptance + rejection tests |
| Modify | `tests/unit/validation/test_google_client.py` | `_map_response_international` tests; US `_map_response` gains `"status"` |
| Modify | `tests/unit/validation/test_google_provider.py` | Non-US provider tests; update fixtures with `"status"` key |

---

### Task 1: Update `validate.py` — country guard and passthrough builder

**Files:**
- Modify: `src/address_validator/routers/v1/validate.py`
- Modify: `tests/unit/test_validate_router.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_validate_router.py`:

```python
# -- Non-US tests ----------------------------------------------------------

class TestValidateNonUS:
    def test_non_us_raw_string_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={"address": "10 Downing St, London SW1A 2AA", "country": "GB"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"] == "country_not_supported"

    def test_non_us_raw_string_error_message_mentions_components(
        self, client: TestClient
    ) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={"address": "10 Downing St, London SW1A 2AA", "country": "GB"},
        )
        assert "components" in resp.json()["message"].lower()

    def test_non_us_components_calls_provider(self, client: TestClient) -> None:
        provider = _make_null_provider(
            ValidateResponseV1(
                country="GB",
                validation=ValidationResult(status="unavailable"),
            )
        )
        with _mock_registry_with(provider):
            resp = client.post(
                "/api/v1/validate",
                json={
                    "components": {
                        "address_line_1": "10 Downing St",
                        "city": "London",
                        "postal_code": "SW1A 2AA",
                    },
                    "country": "GB",
                },
            )
        assert resp.status_code == 200
        provider.validate.assert_awaited_once()

    def test_non_us_components_provider_receives_correct_country(
        self, client: TestClient
    ) -> None:
        provider = _make_null_provider(
            ValidateResponseV1(
                country="GB",
                validation=ValidationResult(status="unavailable"),
            )
        )
        with _mock_registry_with(provider):
            client.post(
                "/api/v1/validate",
                json={
                    "components": {
                        "address_line_1": "10 Downing St",
                        "city": "London",
                        "postal_code": "SW1A 2AA",
                    },
                    "country": "GB",
                },
            )
        std_arg = provider.validate.call_args[0][0]
        assert std_arg.country == "GB"

    def test_non_us_components_skips_usps_standardize(
        self, client: TestClient
    ) -> None:
        # The provider should receive the raw component values, not USPS-munged ones
        provider = _make_null_provider(
            ValidateResponseV1(
                country="GB",
                validation=ValidationResult(status="unavailable"),
            )
        )
        with _mock_registry_with(provider):
            client.post(
                "/api/v1/validate",
                json={
                    "components": {
                        "address_line_1": "10 Downing St",
                        "city": "London",
                        "postal_code": "SW1A 2AA",
                    },
                    "country": "GB",
                },
            )
        std_arg = provider.validate.call_args[0][0]
        # city should be exactly as supplied — not USPS-uppercased/truncated
        assert std_arg.city == "London"
        assert std_arg.address_line_1 == "10 Downing St"

    def test_us_requests_still_work(self, client: TestClient) -> None:
        with _mock_registry_with(_make_null_provider(NULL_RESPONSE)):
            resp = client.post(
                "/api/v1/validate",
                json={"address": "123 Main St, Springfield, IL 62701"},
            )
        assert resp.status_code == 200
```

Also add the import at the top of the test file (already imported in existing file, but verify `ValidationResult` is imported):
```python
# Verify these are already imported at the top:
# from address_validator.models import ValidateResponseV1, ValidationResult
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_validate_router.py::TestValidateNonUS -v
```
Expected: `test_non_us_raw_string_returns_422` → FAIL (currently gets `country_not_supported` from `check_country` but only for valid ISO2 codes already in SUPPORTED_COUNTRIES). Actually the current behavior already returns 422 for all non-US countries via `check_country`. Verify the test fails correctly:
- `test_non_us_raw_string_returns_422` will PASS accidentally (current `check_country` blocks all non-US)
- `test_non_us_components_calls_provider` will FAIL (422 instead of 200 — current code blocks non-US components too)

Continue — the implementation will make the full suite pass.

- [ ] **Step 3: Update `validate.py` — add passthrough path for non-US + components**

Replace the current `check_country(req.country)` call and the existing pipeline block in `validate_address_v1`. The function currently reads:

```python
async def validate_address_v1(req: ValidateRequestV1, request: Request) -> ValidateResponseV1:
    check_country(req.country)

    upstream_warnings: list[str] = []

    if req.components:
        comps = req.components
        raw_input: str | None = json.dumps(...)
    else:
        parse_result = parse_address(...)
        comps = parse_result.components.values
        upstream_warnings = parse_result.warnings
        raw_input = req.address

    std = standardize(comps, country=req.country, upstream_warnings=upstream_warnings)
    ...
```

Replace the top of the function body with:

```python
async def validate_address_v1(req: ValidateRequestV1, request: Request) -> ValidateResponseV1:
    if req.country != "US":
        if not req.components:
            raise APIError(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                error="country_not_supported",
                message=(
                    f"Raw address strings are only supported for US. "
                    f"Supply pre-parsed 'components' for non-US addresses."
                ),
            )
        std = _build_non_us_std(req.components, req.country)
        raw_input: str | None = json.dumps(req.components, separators=(",", ":"), ensure_ascii=True)
    else:
        check_country(req.country)

        upstream_warnings: list[str] = []

        if req.components:
            comps = req.components
            raw_input = json.dumps(req.components, separators=(",", ":"), ensure_ascii=True)
        else:
            parse_result = parse_address(req.address.strip(), country=req.country)  # type: ignore[union-attr]
            comps = parse_result.components.values
            upstream_warnings = parse_result.warnings
            raw_input = req.address

        std = standardize(comps, country=req.country, upstream_warnings=upstream_warnings)

    provider = request.app.state.registry.get_provider()
    ...  # rest of function unchanged
```

Also add the `_build_non_us_std` helper function and its import. Add to the imports at the top of `validate.py`:

```python
from fastapi import status
from address_validator.models import ComponentSet, StandardizeResponseV1
from address_validator.services.validation._helpers import _build_validated_string
```

Add the helper function before the router definition:

```python
def _build_non_us_std(
    components: dict[str, str], country: str
) -> StandardizeResponseV1:
    """Build a passthrough StandardizeResponseV1 from raw components for non-US addresses.

    Skips the USPS Pub 28 pipeline entirely.  Components are used verbatim.
    The ``components.spec`` is ``"raw"`` to indicate no standardization was applied.
    """
    address_line_1 = components.get("address_line_1", "")
    address_line_2 = components.get("address_line_2", "")
    city = components.get("city", "")
    region = components.get("region", "")
    postal_code = components.get("postal_code", "")
    standardized = _build_validated_string(address_line_1, address_line_2, city, region, postal_code)
    return StandardizeResponseV1(
        address_line_1=address_line_1,
        address_line_2=address_line_2,
        city=city,
        region=region,
        postal_code=postal_code,
        country=country,
        standardized=standardized,
        components=ComponentSet(spec="raw", spec_version="1", values=components),
    )
```

**Note on existing imports:** `validate.py` already imports `status` from `fastapi` (the HTTP status module) via `from fastapi import APIRouter, Depends, Request`. Add `status` to that import. Also `ComponentSet` and `StandardizeResponseV1` are not currently imported — add them to the `from address_validator.models import (...)` block.

- [ ] **Step 4: Run the new tests**

```bash
uv run pytest tests/unit/test_validate_router.py::TestValidateNonUS -v
```
Expected: all PASS.

- [ ] **Step 5: Run the full validate test suite to check for regressions**

```bash
uv run pytest tests/unit/test_validate_router.py -v
```
Expected: all PASS.

- [ ] **Step 6: Lint**

```bash
uv run ruff check src/address_validator/routers/v1/validate.py --fix
uv run ruff format src/address_validator/routers/v1/validate.py
```

- [ ] **Step 7: Commit**

```bash
git add src/address_validator/routers/v1/validate.py tests/unit/test_validate_router.py
git commit -m "#88 feat: validate non-US + components bypasses USPS pipeline, non-US + raw string returns 422"
```

---

### Task 2: Update `google_client.py` — non-US API path and response mapper

**Files:**
- Modify: `src/address_validator/services/validation/google_client.py`
- Modify: `tests/unit/validation/test_google_client.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/validation/test_google_client.py`:

```python
# -- Non-US response mapping -----------------------------------------------

GOOGLE_RESPONSE_INTERNATIONAL_CONFIRMED = {
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
        "address": {
            "postalAddress": {
                "regionCode": "GB",
                "postalCode": "SW1A 2AA",
                "administrativeArea": "",
                "locality": "London",
                "addressLines": ["10 Downing St"],
            }
        },
        "geocode": {
            "location": {"latitude": 51.5033, "longitude": -0.1276},
        },
    }
}

GOOGLE_RESPONSE_INTERNATIONAL_INCOMPLETE = {
    "result": {
        "verdict": {
            "validationGranularity": "ROUTE",
            "addressComplete": False,
            "hasUnconfirmedComponents": False,
        },
        "address": {
            "postalAddress": {
                "regionCode": "GB",
                "postalCode": "",
                "administrativeArea": "",
                "locality": "London",
                "addressLines": ["Downing St"],
            }
        },
        "geocode": {"location": {"latitude": 51.5, "longitude": -0.1}},
    }
}

GOOGLE_RESPONSE_INTERNATIONAL_NOT_FOUND = {
    "result": {
        "verdict": {
            "validationGranularity": "OTHER",
            "addressComplete": False,
        },
        "address": {"postalAddress": {}},
        "geocode": {},
    }
}

GOOGLE_RESPONSE_INTERNATIONAL_UNCONFIRMED = {
    "result": {
        "verdict": {
            "validationGranularity": "PREMISE",
            "addressComplete": True,
            "hasUnconfirmedComponents": True,
            "hasInferredComponents": False,
            "hasReplacedComponents": False,
        },
        "address": {
            "postalAddress": {
                "regionCode": "GB",
                "postalCode": "SW1A 2AA",
                "locality": "London",
                "addressLines": ["10 Downing St"],
            }
        },
        "geocode": {"location": {"latitude": 51.5033, "longitude": -0.1276}},
    }
}


class TestMapResponseInternational:
    def test_confirmed_address(self) -> None:
        result = GoogleClient._map_response_international(GOOGLE_RESPONSE_INTERNATIONAL_CONFIRMED)
        assert result["status"] == "confirmed"
        assert result["address_line_1"] == "10 Downing St"
        assert result["city"] == "London"
        assert result["postal_code"] == "SW1A 2AA"
        assert result["dpv_match_code"] is None
        assert result["latitude"] == pytest.approx(51.5033)
        assert result["longitude"] == pytest.approx(-0.1276)

    def test_incomplete_address_returns_invalid(self) -> None:
        result = GoogleClient._map_response_international(GOOGLE_RESPONSE_INTERNATIONAL_INCOMPLETE)
        assert result["status"] == "invalid"

    def test_not_found_returns_not_found(self) -> None:
        result = GoogleClient._map_response_international(GOOGLE_RESPONSE_INTERNATIONAL_NOT_FOUND)
        assert result["status"] == "not_found"

    def test_confirmed_with_unconfirmed_components(self) -> None:
        result = GoogleClient._map_response_international(GOOGLE_RESPONSE_INTERNATIONAL_UNCONFIRMED)
        assert result["status"] == "confirmed"
        assert result["has_unconfirmed_components"] is True

    def test_multiple_address_lines(self) -> None:
        raw = {
            "result": {
                "verdict": {"addressComplete": True, "validationGranularity": "PREMISE"},
                "address": {
                    "postalAddress": {
                        "addressLines": ["Flat 1", "10 Downing St"],
                        "locality": "London",
                        "postalCode": "SW1A 2AA",
                    }
                },
                "geocode": {},
            }
        }
        result = GoogleClient._map_response_international(raw)
        assert result["address_line_1"] == "Flat 1"
        assert result["address_line_2"] == "10 Downing St"

    def test_empty_address_lines(self) -> None:
        raw = {
            "result": {
                "verdict": {"addressComplete": False, "validationGranularity": "OTHER"},
                "address": {"postalAddress": {}},
                "geocode": {},
            }
        }
        result = GoogleClient._map_response_international(raw)
        assert result["address_line_1"] == ""
        assert result["address_line_2"] == ""


class TestMapResponseUsHasStatusKey:
    """_map_response (US path) must include 'status' so GoogleProvider can read it uniformly."""

    def test_confirmed_y_has_status(self) -> None:
        from tests.unit.validation.test_google_client import GOOGLE_RESPONSE_Y
        result = GoogleClient._map_response(GOOGLE_RESPONSE_Y)
        assert result["status"] == "confirmed"

    def test_not_confirmed_n_has_status(self) -> None:
        from tests.unit.validation.test_google_client import GOOGLE_RESPONSE_N
        result = GoogleClient._map_response(GOOGLE_RESPONSE_N)
        assert result["status"] == "not_confirmed"

    def test_no_dpv_has_unavailable_status(self) -> None:
        raw = {
            "result": {
                "verdict": {},
                "geocode": {},
                "uspsData": {"standardizedAddress": {}},
            }
        }
        result = GoogleClient._map_response(raw)
        assert result["status"] == "unavailable"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/validation/test_google_client.py::TestMapResponseInternational tests/unit/validation/test_google_client.py::TestMapResponseUsHasStatusKey -v
```
Expected: FAIL (`_map_response_international` doesn't exist, `_map_response` lacks `"status"` key).

- [ ] **Step 3: Update `google_client.py`**

Add import at the top of `google_client.py`:

```python
from address_validator.services.validation._helpers import _DPV_TO_STATUS
```

Add the `_NON_GRANULAR` constant and `_verdict_to_status` function before the class definition:

```python
# Verdict granularities that indicate the address was not geocodable at all.
_NON_GRANULAR: frozenset[str] = frozenset({"GRANULARITY_UNSPECIFIED", "OTHER", ""})


def _verdict_to_status(verdict: dict[str, Any]) -> str:
    """Derive a validation status from a non-US Google verdict dict."""
    if verdict.get("addressComplete"):
        return "confirmed"
    if verdict.get("validationGranularity", "") not in _NON_GRANULAR:
        return "invalid"
    return "not_found"
```

Update `_map_response` to add the `"status"` key (add one line to the return dict):

```python
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

        dpv = usps.get("dpvConfirmation") or None
        return {
            "dpv_match_code": dpv,
            "status": _DPV_TO_STATUS.get(dpv, "unavailable"),
            "address_line_1": std_addr.get("firstAddressLine", ""),
            "address_line_2": std_addr.get("secondAddressLine", ""),
            "city": std_addr.get("city", ""),
            "region": std_addr.get("state", ""),
            "postal_code": postal_code,
            "vacant": usps.get("dpvVacant") or None,
            "latitude": lat,
            "longitude": lng,
            "has_inferred_components": verdict.get("hasInferredComponents", False),
            "has_replaced_components": verdict.get("hasReplacedComponents", False),
            "has_unconfirmed_components": verdict.get("hasUnconfirmedComponents", False),
        }
```

Add the new `_map_response_international` static method after `_map_response`:

```python
    @staticmethod
    def _map_response_international(raw: dict[str, Any]) -> dict[str, Any]:
        """Normalise a non-US Google Address Validation API response.

        Reads from ``result.address.postalAddress`` and ``result.verdict``
        instead of ``result.uspsData``.  ``dpv_match_code`` is always ``None``
        (USPS-specific field, not present for non-US addresses).
        """
        result = raw.get("result", {})
        verdict = result.get("verdict", {})
        postal_addr = result.get("address", {}).get("postalAddress", {})
        geocode = result.get("geocode", {})
        location = geocode.get("location", {})

        address_lines = postal_addr.get("addressLines", [])
        address_line_1 = address_lines[0] if len(address_lines) > 0 else ""
        address_line_2 = address_lines[1] if len(address_lines) > 1 else ""

        lat = location.get("latitude")
        lng = location.get("longitude")

        return {
            "dpv_match_code": None,
            "status": _verdict_to_status(verdict),
            "address_line_1": address_line_1,
            "address_line_2": address_line_2,
            "city": postal_addr.get("locality", ""),
            "region": postal_addr.get("administrativeArea", ""),
            "postal_code": postal_addr.get("postalCode", ""),
            "vacant": None,
            "latitude": lat,
            "longitude": lng,
            "has_inferred_components": verdict.get("hasInferredComponents", False),
            "has_replaced_components": verdict.get("hasReplacedComponents", False),
            "has_unconfirmed_components": verdict.get("hasUnconfirmedComponents", False),
        }
```

Update `validate_address()` to accept a `country` parameter and route to the right mapper. Add `country: str = "US"` to the signature and update the request/response logic:

```python
    async def validate_address(
        self,
        street_address: str,
        city: str | None = None,
        state: str | None = None,
        zip_code: str | None = None,
        country: str = "US",
    ) -> dict[str, Any]:
        """Validate a single address via the Google Address Validation API.
        ...existing docstring...
        """
        address_lines = [street_address]
        city_state_zip = " ".join(p for p in (city, state, zip_code) if p)
        if city_state_zip:
            address_lines.append(city_state_zip)

        if country == "US":
            payload: dict[str, Any] = {
                "address": {"addressLines": address_lines},
                "enableUspsCass": True,
            }
        else:
            payload = {
                "address": {
                    "addressLines": address_lines,
                    "regionCode": country,
                },
            }

        for attempt in range(_RETRY_MAX + 1):
            await self._rate_limiter.acquire()
            logger.debug("GoogleClient: validating address, %d lines, country=%s", len(address_lines), country)
            resp = await self._http.post(
                _VALIDATE_URL,
                headers=await self._get_auth_headers(),
                json=payload,
            )
            try:
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == _HTTP_TOO_MANY_REQUESTS:
                    if attempt < _RETRY_MAX:
                        delay = _parse_retry_after(exc.response, attempt)
                        logger.warning(
                            "GoogleClient: 429 received, retrying in %.1fs (attempt %d/%d)",
                            delay,
                            attempt + 1,
                            _RETRY_MAX,
                        )
                        await asyncio.sleep(delay)
                        continue
                    delay = _parse_retry_after(exc.response, attempt)
                    raise ProviderRateLimitedError("google", retry_after_seconds=delay) from exc
                raise

            raw: dict[str, Any] = resp.json()
            if country == "US":
                return self._map_response(raw)
            return self._map_response_international(raw)

        raise ProviderRateLimitedError("google", retry_after_seconds=0.0)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/validation/test_google_client.py -v
```
Expected: all PASS. The existing tests should still pass since `_map_response` is unchanged except for the new `"status"` key.

- [ ] **Step 5: Lint**

```bash
uv run ruff check src/address_validator/services/validation/google_client.py --fix
uv run ruff format src/address_validator/services/validation/google_client.py
```

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/services/validation/google_client.py tests/unit/validation/test_google_client.py
git commit -m "#88 feat: google_client non-US path — regionCode request, _map_response_international, status key in both mappers"
```

---

### Task 3: Update `google_provider.py` — pass country, read `raw["status"]`

**Files:**
- Modify: `src/address_validator/services/validation/google_provider.py`
- Modify: `tests/unit/validation/test_google_provider.py`

- [ ] **Step 1: Update the test fixtures in `test_google_provider.py`**

The existing `CLIENT_RESULT_Y`, `CLIENT_RESULT_N`, and `CLIENT_RESULT_WITH_WARNINGS` dicts mock the client's return value. Now that `_map_response` includes `"status"`, add it to each fixture:

```python
CLIENT_RESULT_Y = {
    "dpv_match_code": "Y",
    "status": "confirmed",          # ADD THIS
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
    "status": "not_confirmed",      # ADD THIS
    "address_line_1": "",
    ...  # rest unchanged
}

CLIENT_RESULT_WITH_WARNINGS = {
    **CLIENT_RESULT_Y,
    "status": "confirmed",          # already inherited from CLIENT_RESULT_Y if using **
    "has_inferred_components": True,
    "has_replaced_components": True,
    "has_unconfirmed_components": True,
}
```

Also add fixtures for non-US results:

```python
CLIENT_RESULT_INTERNATIONAL_CONFIRMED = {
    "dpv_match_code": None,
    "status": "confirmed",
    "address_line_1": "10 Downing St",
    "address_line_2": "",
    "city": "London",
    "region": "",
    "postal_code": "SW1A 2AA",
    "vacant": None,
    "latitude": 51.5033,
    "longitude": -0.1276,
    "has_inferred_components": False,
    "has_replaced_components": False,
    "has_unconfirmed_components": False,
}

CLIENT_RESULT_INTERNATIONAL_NOT_FOUND = {
    "dpv_match_code": None,
    "status": "not_found",
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
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/unit/validation/test_google_provider.py`:

```python
def _make_gb_std(
    address_line_1: str = "10 Downing St",
    city: str = "London",
    postal_code: str = "SW1A 2AA",
    country: str = "GB",
) -> StandardizeResponseV1:
    return StandardizeResponseV1(
        address_line_1=address_line_1,
        address_line_2="",
        city=city,
        region="",
        postal_code=postal_code,
        country=country,
        standardized=f"{address_line_1}  {city} {postal_code}",
        components=ComponentSet(
            spec="raw",
            spec_version="1",
            values={"address_line_1": address_line_1, "city": city, "postal_code": postal_code},
        ),
    )


class TestGoogleProviderNonUS:
    @pytest.mark.asyncio
    async def test_non_us_confirmed(self) -> None:
        client = AsyncMock()
        client.validate_address = AsyncMock(return_value=CLIENT_RESULT_INTERNATIONAL_CONFIRMED)
        provider = GoogleProvider(client)
        result = await provider.validate(_make_gb_std())
        assert result.validation.status == "confirmed"
        assert result.country == "GB"

    @pytest.mark.asyncio
    async def test_non_us_not_found(self) -> None:
        client = AsyncMock()
        client.validate_address = AsyncMock(return_value=CLIENT_RESULT_INTERNATIONAL_NOT_FOUND)
        provider = GoogleProvider(client)
        result = await provider.validate(_make_gb_std())
        assert result.validation.status == "not_found"

    @pytest.mark.asyncio
    async def test_non_us_passes_country_to_client(self) -> None:
        client = AsyncMock()
        client.validate_address = AsyncMock(return_value=CLIENT_RESULT_INTERNATIONAL_CONFIRMED)
        provider = GoogleProvider(client)
        await provider.validate(_make_gb_std(country="GB"))
        client.validate_address.assert_awaited_once()
        call_kwargs = client.validate_address.call_args[1]
        assert call_kwargs["country"] == "GB"

    @pytest.mark.asyncio
    async def test_non_us_no_dpv_match_code_in_result(self) -> None:
        client = AsyncMock()
        client.validate_address = AsyncMock(return_value=CLIENT_RESULT_INTERNATIONAL_CONFIRMED)
        provider = GoogleProvider(client)
        result = await provider.validate(_make_gb_std())
        assert result.validation.dpv_match_code is None

    @pytest.mark.asyncio
    async def test_non_us_components_spec_is_raw(self) -> None:
        client = AsyncMock()
        client.validate_address = AsyncMock(return_value=CLIENT_RESULT_INTERNATIONAL_CONFIRMED)
        provider = GoogleProvider(client)
        result = await provider.validate(_make_gb_std())
        assert result.components is not None
        assert result.components.spec == "raw"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
uv run pytest tests/unit/validation/test_google_provider.py::TestGoogleProviderNonUS -v
```
Expected: FAIL (provider doesn't pass `country` to client yet; uses DPV logic for status).

Also run existing tests — they may also fail now because fixtures lack `"status"`:
```bash
uv run pytest tests/unit/validation/test_google_provider.py -v
```
Note which tests fail due to missing `"status"` key.

- [ ] **Step 4: Update `google_provider.py`**

Update `validate()` to:
1. Pass `country=std.country` to `client.validate_address()`
2. Read status from `raw["status"]` instead of `_DPV_TO_STATUS`
3. Use `"raw"` spec for non-US components

```python
    async def validate(
        self, std: StandardizeResponseV1, *, raw_input: str | None = None
    ) -> ValidateResponseV1:
        logger.debug("GoogleProvider.validate: calling Google API, country=%s", std.country)
        raw = await self._client.validate_address(
            street_address=std.address_line_1,
            city=std.city,
            state=std.region,
            zip_code=std.postal_code,
            country=std.country,
        )

        status = raw["status"]
        dpv = raw.get("dpv_match_code")

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
            # US results follow USPS Pub 28; non-US results are raw Google components.
            if std.country == "US":
                comp_spec = USPS_PUB28_SPEC
                comp_spec_version = USPS_PUB28_SPEC_VERSION
            else:
                comp_spec = "raw"
                comp_spec_version = "1"
            components = ComponentSet(
                spec=comp_spec,
                spec_version=comp_spec_version,
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

Also remove the now-unused import of `_DPV_TO_STATUS` from `google_provider.py`:

```python
# Remove this line:
from address_validator.services.validation._helpers import _DPV_TO_STATUS, _build_validated_string
# Replace with:
from address_validator.services.validation._helpers import _build_validated_string
```

- [ ] **Step 5: Run all Google provider tests**

```bash
uv run pytest tests/unit/validation/test_google_provider.py -v
```
Expected: all PASS.

- [ ] **Step 6: Lint**

```bash
uv run ruff check src/address_validator/services/validation/google_provider.py --fix
uv run ruff format src/address_validator/services/validation/google_provider.py
```

- [ ] **Step 7: Commit**

```bash
git add src/address_validator/services/validation/google_provider.py tests/unit/validation/test_google_provider.py
git commit -m "#88 feat: google_provider passes country to client, reads pre-mapped status, uses raw spec for non-US components"
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
git commit -m "#88 fix: coverage/lint cleanup for non-US validate"
```

---

## Notes for the implementer

- `validate.py` already imports `from fastapi import APIRouter, Depends, Request` — add `status` to that import.
- `validate.py` already imports `from address_validator.models import (ErrorResponse, ValidateRequestV1, ValidateResponseV1, ValidationResult)` — add `ComponentSet, StandardizeResponseV1` to that block.
- `google_provider.py` docstring says "Validates US addresses" — update to "Validates addresses" after the functional changes.
- The `test_google_provider.py` fixtures `CLIENT_RESULT_WITH_WARNINGS` uses `**CLIENT_RESULT_Y` spread — after adding `"status"` to `CLIENT_RESULT_Y`, it will inherit correctly; no separate fix needed for that fixture.
