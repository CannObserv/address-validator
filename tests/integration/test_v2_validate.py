"""Integration tests for POST /api/v2/validate."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from address_validator.core import warnings as warning_catalogue
from address_validator.main import app
from address_validator.models import ValidateResponseV2, ValidationResult
from address_validator.services.validation._rate_limit import (
    FixedResetQuotaWindow,
    QuotaGuard,
    QuotaWindow,
)
from address_validator.services.validation.chain_provider import ChainProvider
from address_validator.services.validation.errors import (
    ProviderBadRequestError,
    ProviderRateLimitedError,
)
from address_validator.services.validation.google_client import GoogleClient
from address_validator.services.validation.google_provider import GoogleProvider
from address_validator.services.validation.usps_client import USPSClient
from address_validator.services.validation.usps_provider import USPSProvider
from tests.conftest import unreachable_google, unreachable_usps

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


class TestV2ValidateProviderUnreachable:
    """GH #257: a provider network failure (connect error, timeout) falls back to
    the next provider, and ends as 429 + Retry-After when none answers —
    never as a 500."""

    _GOOGLE_CONFIRMED = ValidateResponseV2(
        address_line_1="123 MAIN ST",
        city="SEATTLE",
        region="WA",
        postal_code="98101-1234",
        country="US",
        validation=ValidationResult(status="confirmed", dpv_match_code="Y", provider="google"),
    )

    def test_unreachable_primary_falls_back(self, client) -> None:
        google = AsyncMock()
        google.validate = AsyncMock(return_value=self._GOOGLE_CONFIRMED)
        google.supports_non_us = True
        chain = ChainProvider(providers=[unreachable_usps(), google])
        with _mock_registry_with(chain):
            response = client.post(
                "/api/v2/validate",
                json={"address": "123 Main St, Seattle, WA 98101"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["validation"]["status"] == "confirmed"
        assert body["validation"]["provider"] == "google"

    def test_all_unreachable_returns_429_with_retry_after(self, client) -> None:
        chain = ChainProvider(providers=[unreachable_usps(), unreachable_google()])
        with _mock_registry_with(chain):
            response = client.post(
                "/api/v2/validate",
                json={"address": "123 Main St, Seattle, WA 98101"},
            )
        assert response.status_code == 429, response.text
        assert response.json()["error"] == "provider_rate_limited"
        assert int(response.headers["Retry-After"]) >= 1


class TestV2ValidateQuotaExhausted:
    """GH #270: when every provider's local QuotaGuard refuses, the 429 carries the
    wait the guards computed, not Retry-After: 0."""

    @staticmethod
    def _drained_usps() -> USPSProvider:
        """USPS-shaped guard: soft daily window drained, one token every 10 s."""
        guard = QuotaGuard(
            windows=[
                QuotaWindow(limit=5, duration_s=1.0, mode="soft"),
                QuotaWindow(limit=8640, duration_s=86_400.0, mode="soft"),
            ],
            latency_budget_s=1.0,
            provider_name="usps",
        )
        guard._tokens[1] = 0.0
        return USPSProvider(
            USPSClient(
                consumer_key="key",
                consumer_secret="secret",
                http_client=AsyncMock(spec=httpx.AsyncClient),
                quota_guard=guard,
            )
        )

    @staticmethod
    def _drained_google() -> GoogleProvider:
        """Google-shaped guard: hard daily window drained until midnight PT."""
        guard = QuotaGuard(
            windows=[
                QuotaWindow(limit=60, duration_s=60.0, mode="soft"),
                FixedResetQuotaWindow(limit=160, mode="hard"),
            ],
            provider_name="google",
        )
        guard._tokens[1] = 0.0
        return GoogleProvider(
            GoogleClient(
                credentials=MagicMock(valid=True, token="tok"),
                http_client=AsyncMock(spec=httpx.AsyncClient),
                quota_guard=guard,
            )
        )

    def _post(self, client, chain: ChainProvider):
        with _mock_registry_with(chain):
            return client.post(
                "/api/v2/validate",
                json={"address": "123 Main St, Seattle, WA 98101"},
            )

    def test_google_daily_quota_retry_after_is_time_until_reset(self, client) -> None:
        chain = ChainProvider(providers=[self._drained_google()])
        response = self._post(client, chain)
        assert response.status_code == 429, response.text
        assert int(response.headers["Retry-After"]) > 10

    def test_both_drained_retry_after_is_soonest(self, client) -> None:
        chain = ChainProvider(providers=[self._drained_usps(), self._drained_google()])
        response = self._post(client, chain)
        assert response.status_code == 429, response.text
        assert response.headers["Retry-After"] == "10"


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
                unreachable_google(),
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

    def test_fallback_verdict_without_dpv_keeps_usps_answer(self, client) -> None:
        """GH #258: the 2026-09-30 production probe. Google's CASS returned no DPV
        code, so the non-CASS path mapped ``addressComplete`` to ``confirmed`` for a
        different street (AVE→St). The held USPS answer must win."""
        probe = {
            "result": {
                "verdict": {"addressComplete": True, "hasUnconfirmedComponents": True},
                "address": {
                    "postalAddress": {
                        "addressLines": ["301 E Hbr St"],
                        "locality": "Westport",
                        "administrativeArea": "WA",
                        "postalCode": "98595",
                    }
                },
                "uspsData": {"dpvConfirmation": ""},
            }
        }
        mapped = GoogleClient._map_response(probe)
        # Precondition (CR 2): the real mapping still yields a DPV-less `confirmed`.
        # If it stops doing so this test no longer exercises the #258 rule — update
        # the probe or the docstring rather than let it pass via all-undetermined.
        assert (mapped["status"], mapped["dpv_match_code"]) == ("confirmed", None)
        google_client = MagicMock()
        google_client.validate_address = AsyncMock(return_value=mapped)
        chain = ChainProvider(
            providers=[
                self._stub(return_value=self._USPS_UNDETERMINED),
                GoogleProvider(google_client),
            ]
        )
        with _mock_registry_with(chain):
            response = client.post(
                "/api/v2/validate",
                json={"address": "301 E Harbor Ave, Westport, WA 98595"},
            )
        assert response.status_code == 200, response.text
        body = response.json()
        google_client.validate_address.assert_awaited_once()
        assert body["validation"] == {
            "status": "undetermined",
            "dpv_match_code": None,
            "provider": "usps",
        }
        assert body["address_line_1"] == "301 E HARBOR AVE"
        assert body["warnings"] == []
