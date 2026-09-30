"""Integration tests for POST /api/v2/validate."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from address_validator.core import warnings as warning_catalogue
from address_validator.main import app
from address_validator.models import ValidateResponseV2, ValidationResult
from address_validator.services.validation.chain_provider import ChainProvider
from address_validator.services.validation.errors import (
    ProviderBadRequestError,
    ProviderRateLimitedError,
)

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
    """GH-114 regression: unparseable-street inputs return 200 with a structured body, not 500."""

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
        """Geocoded provider response → 200 with structured body."""
        google_response = ValidateResponseV2(
            address_line_1="Lynnwood City Hall",
            address_line_2="44th Ave W",
            city="Lynnwood",
            region="WA",
            postal_code="98036-5635",
            country="US",
            latitude=47.8253139,
            longitude=-122.2936207,
            validation=ValidationResult(status="invalid", provider="google"),
            warnings=[warning_catalogue.PROVIDER_INFERRED],
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
        """Provider raises ProviderBadRequestError → 200 with status='error'."""
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
        assert warning_catalogue.PROVIDER_REJECTED_MALFORMED in body["warnings"]


class TestV2ValidateUndetermined:
    """GH #250: a USPS no-DPV answer reaches the client as 200 + `undetermined`,
    through a real ChainProvider, with the fallback-unreachable warning when the
    fallback failed transiently — and never as a 429."""

    _USPS_UNDETERMINED = ValidateResponseV2(
        address_line_1="301 E HARBOR AVE",
        city="WESTPORT",
        region="WA",
        postal_code="98595",
        country="US",
        validation=ValidationResult(status="undetermined", provider="usps"),
    )

    @staticmethod
    def _stub(**kwargs) -> AsyncMock:
        p = AsyncMock()
        p.validate = AsyncMock(**kwargs)
        p.supports_non_us = False
        return p

    def test_fallback_rate_limited_returns_200_undetermined_with_warning(self, client) -> None:
        chain = ChainProvider(
            providers=[
                self._stub(return_value=self._USPS_UNDETERMINED),
                self._stub(side_effect=ProviderRateLimitedError("google", retry_after_seconds=5)),
            ]
        )
        with _mock_registry_with(chain):
            response = client.post(
                "/api/v2/validate",
                json={"address": "301 E Harbor Ave, Westport, WA 98595"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["validation"] == {
            "status": "undetermined",
            "dpv_match_code": None,
            "provider": "usps",
        }
        assert warning_catalogue.PROVIDER_FALLBACK_UNREACHABLE in body["warnings"]

    def test_fallback_network_error_returns_200_not_500(self, client) -> None:
        """CR 9: a Google connect error after a USPS answer must not become a 500."""
        chain = ChainProvider(
            providers=[
                self._stub(return_value=self._USPS_UNDETERMINED),
                self._stub(side_effect=httpx.ConnectError("boom")),
            ]
        )
        with _mock_registry_with(chain):
            response = client.post(
                "/api/v2/validate",
                json={"address": "301 E Harbor Ave, Westport, WA 98595"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["validation"]["status"] == "undetermined"
        assert warning_catalogue.PROVIDER_FALLBACK_UNREACHABLE in body["warnings"]

    def test_fallback_determines_address(self, client) -> None:
        google = self._USPS_UNDETERMINED.model_copy(
            update={
                "postal_code": "98595-1234",
                "validation": ValidationResult(
                    status="confirmed", dpv_match_code="Y", provider="google"
                ),
            }
        )
        chain = ChainProvider(
            providers=[
                self._stub(return_value=self._USPS_UNDETERMINED),
                self._stub(return_value=google),
            ]
        )
        with _mock_registry_with(chain):
            response = client.post(
                "/api/v2/validate",
                json={"address": "301 E Harbor Ave, Westport, WA 98595"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["validation"]["status"] == "confirmed"
        assert body["validation"]["provider"] == "google"
        assert warning_catalogue.PROVIDER_FALLBACK_UNREACHABLE not in body["warnings"]
