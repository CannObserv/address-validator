"""Integration tests for POST /api/v2/validate."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from address_validator.main import app
from address_validator.models import ValidateResponseV1, ValidationResult
from address_validator.services.validation.errors import ProviderBadRequestError

pytestmark = pytest.mark.integration


def _mock_registry_with(provider):
    mock_reg = MagicMock()
    mock_reg.get_provider.return_value = provider
    return patch.object(app.state, "registry", mock_reg)


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


class TestV2ValidateUnparseableInput:
    """GH-114: inputs without a USPS-parseable street must not return 500.

    Before the fix:
      - Standardize produced empty address_line_1
      - USPS API returned 400 → ProviderBadRequestError → ChainProvider fallthrough
      - Google API returned 400 for empty address → raw httpx.HTTPStatusError → 500
      - text/plain "Internal Server Error" body; no audit row written

    After the fix:
      - Empty address_line_1 → pipeline passes raw input through (B2)
      - Google returns rich postalAddress data, mapper reads it (B1)
      - Google 400 (if it still rejects) → ProviderBadRequestError → 200 status=error (Fix 1)
    """

    @pytest.mark.parametrize(
        "addr",
        [
            "Lynnwood City Hall, 44th Avenue West, Lynnwood, WA, USA",
            "Lynnwood City Hall, 44th Avenue West",
            "Lynnwood City Hall, Lynnwood, WA, USA",
            "Lynnwood City Hall",
            "44th Avenue West, Lynnwood, WA, USA",
        ],
    )
    def test_unparseable_input_returns_200_with_geocoded_response(self, client, addr) -> None:
        """When the provider returns a geocoded address, the route returns 200
        with a populated, structured response — not 500 text/plain."""
        google_response = ValidateResponseV1(
            address_line_1="Lynnwood City Hall",
            address_line_2="44th Ave W",
            city="Lynnwood",
            region="WA",
            postal_code="98036-5635",
            country="US",
            latitude=47.8253139,
            longitude=-122.2936207,
            validation=ValidationResult(status="invalid", provider="google"),
            warnings=["Provider inferred one or more address components not present in input"],
        )
        provider = AsyncMock()
        provider.validate = AsyncMock(return_value=google_response)
        provider.supports_non_us = True
        with _mock_registry_with(provider):
            response = client.post(
                "/api/v2/validate",
                json={"address": addr, "country": "US"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["validation"]["status"] == "invalid"
        assert body["city"] == "Lynnwood"
        assert body["region"] == "WA"
        assert body["postal_code"] == "98036-5635"
        # Provider must have been called (i.e. pipeline didn't short-circuit on empty street).
        assert provider.validate.await_count == 1
        call_std = provider.validate.await_args.args[0]
        # Pipeline must have promoted the raw input into address_line_1.
        assert call_std.address_line_1.lower().startswith(addr.split(",")[0].lower())

    def test_unparseable_input_provider_bad_request_returns_200_status_error(self, client) -> None:
        """If the provider rejects the address as malformed (e.g. Google 400 →
        ProviderBadRequestError), the route returns 200 with status='error' —
        not 500 text/plain."""
        provider = AsyncMock()
        provider.validate = AsyncMock(
            side_effect=ProviderBadRequestError("google", detail="HTTP 400")
        )
        provider.supports_non_us = True
        with _mock_registry_with(provider):
            response = client.post(
                "/api/v2/validate",
                json={"address": "Lynnwood City Hall", "country": "US"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["validation"]["status"] == "error"
        assert body["validation"]["provider"] == "google"
