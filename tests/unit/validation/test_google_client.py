"""Unit tests for GoogleClient — response mapping and request construction."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from address_validator.services.validation._rate_limit import _RETRY_MAX, QuotaGuard, QuotaWindow
from address_validator.services.validation.errors import (
    ProviderAtCapacityError,
    ProviderRateLimitedError,
)
from address_validator.services.validation.google_client import GoogleClient

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
        "verdict": {
            "validationGranularity": "SUB_PREMISE",
            "addressComplete": True,
            "hasInferredComponents": False,
            "hasReplacedComponents": True,
            "hasUnconfirmedComponents": False,
        },
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
        "verdict": {
            "validationGranularity": "PREMISE",
            "addressComplete": True,
            "hasInferredComponents": True,
            "hasReplacedComponents": False,
            "hasUnconfirmedComponents": False,
        },
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
    def _default_guard(self) -> QuotaGuard:
        return QuotaGuard(
            windows=[
                QuotaWindow(limit=5, duration_s=60.0, mode="soft"),
                QuotaWindow(limit=160, duration_s=86_400.0, mode="hard"),
            ],
            latency_budget_s=1.0,
            provider_name="google",
        )

    @pytest.fixture()
    def mock_credentials(self):
        creds = MagicMock()
        creds.token = "test-bearer-token"
        creds.valid = True
        return creds

    @pytest.fixture()
    def client(
        self, mock_http: AsyncMock, _default_guard: QuotaGuard, mock_credentials
    ) -> GoogleClient:
        return GoogleClient(
            credentials=mock_credentials,
            http_client=mock_http,
            quota_guard=_default_guard,
        )

    def _make_response(self, json_data: dict, status_code: int = 200) -> MagicMock:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        resp.json.return_value = json_data
        resp.raise_for_status = MagicMock()
        return resp

    @pytest.mark.asyncio
    async def test_posts_to_correct_url(self, client: GoogleClient, mock_http: AsyncMock) -> None:
        mock_http.post.return_value = self._make_response(GOOGLE_RESPONSE_Y)
        await client.validate_address(street_address="123 Main St", city="Springfield", state="IL")
        call_args = mock_http.post.call_args
        assert "addressvalidation.googleapis.com" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_sends_bearer_token(self, client: GoogleClient, mock_http: AsyncMock) -> None:
        mock_http.post.return_value = self._make_response(GOOGLE_RESPONSE_Y)
        await client.validate_address("123 Main St")
        call_kwargs = mock_http.post.call_args
        headers = call_kwargs.kwargs.get("headers") or call_kwargs[1].get("headers", {})
        assert headers.get("Authorization") == "Bearer test-bearer-token"

    @pytest.mark.asyncio
    async def test_refreshes_expired_credentials_via_thread(
        self, mock_http: AsyncMock, _default_guard: QuotaGuard
    ) -> None:
        expired_creds = MagicMock()
        expired_creds.valid = False
        expired_creds.token = "refreshed-token"
        client = GoogleClient(
            credentials=expired_creds, http_client=mock_http, quota_guard=_default_guard
        )
        mock_http.post.return_value = self._make_response(GOOGLE_RESPONSE_Y)
        await client.validate_address("123 Main St")
        expired_creds.refresh.assert_called_once()

    @pytest.mark.asyncio
    async def test_enables_usps_cass(self, client: GoogleClient, mock_http: AsyncMock) -> None:
        mock_http.post.return_value = self._make_response(GOOGLE_RESPONSE_Y)
        await client.validate_address("123 Main St")
        call_args = mock_http.post.call_args
        body = call_args[1]["json"]
        assert body.get("enableUspsCass") is True

    @pytest.mark.asyncio
    async def test_http_error_raises(self, client: GoogleClient, mock_http: AsyncMock) -> None:
        mock_http.post.side_effect = httpx.TimeoutException("timeout")
        with pytest.raises(httpx.TimeoutException):
            await client.validate_address("123 Main St")

    @pytest.mark.asyncio
    async def test_non_429_status_error_propagates(
        self, client: GoogleClient, mock_http: AsyncMock
    ) -> None:
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.status_code = 403
        bad_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "403", request=MagicMock(), response=bad_resp
        )
        mock_http.post.return_value = bad_resp
        with pytest.raises(httpx.HTTPStatusError):
            await client.validate_address("123 Main St")

    @pytest.mark.asyncio
    async def test_429_raises_provider_rate_limited_error_after_retries(
        self, client: GoogleClient, mock_http: AsyncMock
    ) -> None:
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.status_code = 429
        bad_resp.headers = {}
        bad_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "429", request=MagicMock(), response=bad_resp
        )
        mock_http.post.return_value = bad_resp

        with (
            patch("address_validator.services.validation.google_client.asyncio.sleep"),
            pytest.raises(ProviderRateLimitedError) as exc_info,
        ):
            await client.validate_address("123 Main St")
        assert exc_info.value.provider == "google"
        assert exc_info.value.retry_after_seconds > 0

    @pytest.mark.asyncio
    async def test_429_retries_before_giving_up(
        self, client: GoogleClient, mock_http: AsyncMock
    ) -> None:
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.status_code = 429
        bad_resp.headers = {}
        bad_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "429", request=MagicMock(), response=bad_resp
        )
        mock_http.post.return_value = bad_resp

        with (
            patch("address_validator.services.validation.google_client.asyncio.sleep"),
            pytest.raises(ProviderRateLimitedError),
        ):
            await client.validate_address("123 Main St")
        assert mock_http.post.call_count == _RETRY_MAX + 1

    @pytest.mark.asyncio
    async def test_429_then_success_returns_result(
        self, client: GoogleClient, mock_http: AsyncMock
    ) -> None:
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.status_code = 429
        bad_resp.headers = {}
        bad_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "429", request=MagicMock(), response=bad_resp
        )
        good_resp = self._make_response(GOOGLE_RESPONSE_Y)
        mock_http.post.side_effect = [bad_resp, good_resp]

        with patch("address_validator.services.validation.google_client.asyncio.sleep"):
            result = await client.validate_address("123 Main St")
        assert result["dpv_match_code"] == "Y"

    def test_accepts_quota_guard(self, mock_http: AsyncMock) -> None:
        guard = QuotaGuard(
            windows=[QuotaWindow(limit=5, duration_s=60.0, mode="soft")],
            provider_name="google",
        )
        mock_creds = MagicMock()
        mock_creds.token = "tok"
        mock_creds.valid = True
        client = GoogleClient(credentials=mock_creds, http_client=mock_http, quota_guard=guard)
        assert client._rate_limiter is guard

    @pytest.mark.asyncio
    async def test_at_capacity_raises_before_http_call(
        self, client: GoogleClient, mock_http: AsyncMock
    ) -> None:
        """QuotaGuard raising ProviderAtCapacityError must prevent any HTTP call."""
        with (
            patch.object(
                client._rate_limiter,
                "acquire",
                side_effect=ProviderAtCapacityError("google"),
            ),
            pytest.raises(ProviderAtCapacityError),
        ):
            await client.validate_address("123 Main St")

        mock_http.post.assert_not_called()
