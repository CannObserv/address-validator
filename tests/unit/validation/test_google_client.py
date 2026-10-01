"""Unit tests for GoogleClient — response mapping and request construction."""

import copy
from unittest.mock import AsyncMock, MagicMock, patch

import google.auth.exceptions
import httpx
import pytest

from address_validator.services.validation._rate_limit import _RETRY_MAX, QuotaGuard, QuotaWindow
from address_validator.services.validation.errors import (
    ProviderAtCapacityError,
    ProviderBadRequestError,
    ProviderRateLimitedError,
    ProviderTransientError,
)
from address_validator.services.validation.google_client import GoogleClient

# An httpx error message can embed the request URL, and with it the address.
_ADDRESS_IN_MESSAGE = "GET /validate?streetAddress=123 Main St"

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

    @pytest.mark.parametrize(
        ("dpv", "status"),
        [
            # Google's uspsData.dpvConfirmation uses the USPS CASS codes: D =
            # secondary missing, S = secondary present but not confirmed (GH #253).
            ("D", "confirmed_missing_secondary"),
            ("S", "confirmed_bad_secondary"),
        ],
    )
    def test_secondary_dpv_maps_to_status(self, dpv: str, status: str) -> None:
        raw = copy.deepcopy(GOOGLE_RESPONSE_WITH_SECONDARY)
        raw["result"]["uspsData"]["dpvConfirmation"] = dpv
        result = GoogleClient._map_response(raw)
        assert result["dpv_match_code"] == dpv
        assert result["status"] == status

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
    async def test_secondary_address_folded_into_street_line(
        self, client: GoogleClient, mock_http: AsyncMock
    ) -> None:
        """GH #126: secondary_address must be folded into the street addressLine."""
        mock_http.post.return_value = self._make_response(GOOGLE_RESPONSE_Y)
        await client.validate_address(
            "9 BENNY DR", "OKANOGAN", "WA", zip_code="98840", secondary_address="LOT B"
        )
        body = mock_http.post.call_args[1]["json"]
        assert body["address"]["addressLines"][0] == "9 BENNY DR LOT B"

    @pytest.mark.asyncio
    async def test_street_line_unchanged_when_no_secondary(
        self, client: GoogleClient, mock_http: AsyncMock
    ) -> None:
        mock_http.post.return_value = self._make_response(GOOGLE_RESPONSE_Y)
        await client.validate_address("9 BENNY DR", "OKANOGAN", "WA", zip_code="98840")
        body = mock_http.post.call_args[1]["json"]
        assert body["address"]["addressLines"][0] == "9 BENNY DR"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [401, 403])
    async def test_auth_status_raises_bad_request(
        self, status: int, client: GoogleClient, mock_http: AsyncMock, caplog
    ) -> None:
        """GH-115: 401/403 from Google (expired creds, missing IAM) must map
        to ProviderBadRequestError so the chain falls through, while logging
        at ERROR so operators get paged."""
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.status_code = status
        bad_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            str(status), request=MagicMock(), response=bad_resp
        )
        mock_http.post.return_value = bad_resp

        with caplog.at_level("ERROR"), pytest.raises(ProviderBadRequestError) as exc_info:
            await client.validate_address("123 Main St")
        assert exc_info.value.provider == "google"
        assert str(status) in exc_info.value.detail
        assert any(
            "operator action required" in record.message.lower() and record.levelname == "ERROR"
            for record in caplog.records
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    async def test_5xx_raises_transient_error(
        self, status: int, client: GoogleClient, mock_http: AsyncMock
    ) -> None:
        """GH-115: Google 5xx must map to ProviderTransientError so the chain
        falls through (research doc: 'transient → fallback')."""
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.status_code = status
        bad_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            str(status), request=MagicMock(), response=bad_resp
        )
        mock_http.post.return_value = bad_resp

        with pytest.raises(ProviderTransientError) as exc_info:
            await client.validate_address("123 Main St")
        assert exc_info.value.provider == "google"
        assert exc_info.value.retry_after_seconds > 0

    @pytest.mark.asyncio
    async def test_unexpected_status_raises_transient_error(
        self, client: GoogleClient, mock_http: AsyncMock
    ) -> None:
        """GH-115: any non-2xx outside 400/401/403/429/5xx still maps cleanly
        — never let raw httpx.HTTPStatusError escape the client."""
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.status_code = 418  # I'm a teapot
        bad_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "418", request=MagicMock(), response=bad_resp
        )
        mock_http.post.return_value = bad_resp

        with pytest.raises(ProviderTransientError):
            await client.validate_address("123 Main St")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "exc",
        [
            httpx.ConnectError(_ADDRESS_IN_MESSAGE),
            httpx.ReadTimeout(_ADDRESS_IN_MESSAGE),
            httpx.DecodingError(_ADDRESS_IN_MESSAGE),
        ],
    )
    async def test_request_error_raises_transient_error(
        self, exc: httpx.RequestError, client: GoogleClient, mock_http: AsyncMock, caplog
    ) -> None:
        """GH #257: a network failure maps to ProviderTransientError so the chain
        falls through — the raw httpx error surfaced as HTTP 500."""
        mock_http.post.side_effect = exc

        with caplog.at_level("WARNING"), pytest.raises(ProviderTransientError) as exc_info:
            await client.validate_address("123 Main St")
        assert exc_info.value.provider == "google"
        assert exc_info.value.retry_after_seconds > 0
        assert exc_info.value.__cause__ is exc
        messages = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(type(exc).__name__ in m for m in messages)
        # The message is never logged: it can embed the request URL (CR 3).
        assert "Main St" not in caplog.text

    @pytest.mark.asyncio
    async def test_credential_refresh_transport_error_raises_transient_error(
        self, mock_http: AsyncMock, _default_guard: QuotaGuard
    ) -> None:
        """GH #257: an ADC token refresh that cannot reach its endpoint raises
        google-auth's own TransportError (it runs on ``requests``, not httpx)."""
        expired_creds = MagicMock()
        expired_creds.valid = False
        expired_creds.refresh.side_effect = google.auth.exceptions.TransportError("unreachable")
        client = GoogleClient(
            credentials=expired_creds, http_client=mock_http, quota_guard=_default_guard
        )

        with pytest.raises(ProviderTransientError) as exc_info:
            await client.validate_address("123 Main St")
        assert exc_info.value.provider == "google"
        mock_http.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_retryable_refresh_error_raises_transient_error(
        self, mock_http: AsyncMock, _default_guard: QuotaGuard, caplog
    ) -> None:
        """CR 2 (GH #257): a token-endpoint 5xx that google-auth's own retries
        could not clear is transient, like a USPS token-endpoint 5xx (GH-115)."""
        expired_creds = MagicMock()
        expired_creds.valid = False
        expired_creds.refresh.side_effect = google.auth.exceptions.RefreshError(
            "server_error: backend unavailable", retryable=True
        )
        client = GoogleClient(
            credentials=expired_creds, http_client=mock_http, quota_guard=_default_guard
        )

        with caplog.at_level("WARNING"), pytest.raises(ProviderTransientError) as exc_info:
            await client.validate_address("123 Main St")
        assert exc_info.value.provider == "google"
        assert exc_info.value.retry_after_seconds > 0
        mock_http.post.assert_not_called()
        assert "backend unavailable" not in caplog.text

    @pytest.mark.asyncio
    async def test_non_retryable_refresh_error_propagates(
        self, mock_http: AsyncMock, _default_guard: QuotaGuard
    ) -> None:
        """Bad or revoked credentials are operator action, not a fallback."""
        expired_creds = MagicMock()
        expired_creds.valid = False
        expired_creds.refresh.side_effect = google.auth.exceptions.RefreshError(
            "invalid_grant", retryable=False
        )
        client = GoogleClient(
            credentials=expired_creds, http_client=mock_http, quota_guard=_default_guard
        )

        with pytest.raises(google.auth.exceptions.RefreshError):
            await client.validate_address("123 Main St")
        mock_http.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_400_raises_provider_bad_request_error(
        self, client: GoogleClient, mock_http: AsyncMock
    ) -> None:
        """GH-114: Google 400 must be translated, not propagated as raw HTTPStatusError."""
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.status_code = 400
        bad_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "400 Bad Request", request=MagicMock(), response=bad_resp
        )
        mock_http.post.return_value = bad_resp
        with pytest.raises(ProviderBadRequestError) as exc_info:
            await client.validate_address("")
        assert exc_info.value.provider == "google"

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


# GH #127: non-US response with street + unit folded into one addressLines element.
GOOGLE_RESPONSE_INTERNATIONAL_FOLDED_UNIT = {
    "result": {
        "verdict": {"addressComplete": True, "validationGranularity": "PREMISE"},
        "address": {
            "postalAddress": {
                "addressLines": ["10 Downing St FLAT 1"],
                "locality": "London",
                "postalCode": "SW1A 2AA",
            }
        },
        "geocode": {},
    }
}

# Non-US response where Google splits the unit into its own addressLines element.
GOOGLE_RESPONSE_INTERNATIONAL_SEPARATE_UNIT = {
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

    def test_folded_unit_split_into_line_2(self) -> None:
        """GH #127: non-US path also recovers a folded unit into address_line_2."""
        result = GoogleClient._map_response_international(
            GOOGLE_RESPONSE_INTERNATIONAL_FOLDED_UNIT, secondary_address="FLAT 1"
        )
        assert result["address_line_1"] == "10 Downing St"
        assert result["address_line_2"] == "FLAT 1"

    def test_separate_line_2_not_overwritten(self) -> None:
        """When Google splits the unit into its own line, the sent unit must not clobber it."""
        result = GoogleClient._map_response_international(
            GOOGLE_RESPONSE_INTERNATIONAL_SEPARATE_UNIT, secondary_address="FLAT 1"
        )
        assert result["address_line_1"] == "Flat 1"
        assert result["address_line_2"] == "10 Downing St"


class TestMapResponseUsHasStatusKey:
    """_map_response (US path) must include 'status' so GoogleProvider can read it uniformly."""

    def test_confirmed_y_has_status(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_Y)
        assert result["status"] == "confirmed"

    def test_not_confirmed_n_has_status(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_N)
        assert result["status"] == "not_confirmed"

    def test_no_dpv_no_postal_address_has_undetermined_status(self) -> None:
        raw = {
            "result": {
                "verdict": {},
                "geocode": {},
                "uspsData": {"standardizedAddress": {}},
            }
        }
        result = GoogleClient._map_response(raw)
        assert result["status"] == "undetermined"

    def test_unknown_dpv_code_has_undetermined_status(self) -> None:
        raw = {
            "result": {
                "verdict": {},
                "geocode": {},
                "uspsData": {"standardizedAddress": {}, "dpvConfirmation": "X"},
            }
        }
        result = GoogleClient._map_response(raw)
        assert result["status"] == "undetermined"
        # Unknown codes are dropped: ValidationResult.dpv_match_code is a Literal.
        assert result["dpv_match_code"] is None

    def test_blank_dpv_code_treated_as_absent(self) -> None:
        raw = {
            "result": {
                "verdict": {},
                "geocode": {},
                "uspsData": {"standardizedAddress": {}, "dpvConfirmation": " "},
            }
        }
        result = GoogleClient._map_response(raw)
        assert result["status"] == "undetermined"
        assert result["dpv_match_code"] is None


# -- US postalAddress fallback (GH-114) ------------------------------------
# Captured live for input "Lynnwood City Hall, 44th Avenue West, Lynnwood, WA, USA"
# with enableUspsCass=True. CASS produced no DPV (dpvFootnote=A1M1) but Google
# still returned a populated result.address.postalAddress.
GOOGLE_RESPONSE_US_NO_DPV_RICH_POSTAL = {
    "result": {
        "verdict": {
            "inputGranularity": "PREMISE",
            "validationGranularity": "PREMISE",
            "geocodeGranularity": "PREMISE",
            "hasInferredComponents": True,
            "possibleNextAction": "FIX",
        },
        "address": {
            "formattedAddress": (
                "Lynnwood City Hall, 44th Avenue West, Lynnwood, WA 98036-5635, USA"
            ),
            "postalAddress": {
                "regionCode": "US",
                "postalCode": "98036-5635",
                "administrativeArea": "WA",
                "locality": "Lynnwood",
                "addressLines": ["Lynnwood City Hall", "44th Ave W"],
            },
            "missingComponentTypes": ["street_number"],
        },
        "geocode": {"location": {"latitude": 47.8253139, "longitude": -122.2936207}},
        "uspsData": {
            "standardizedAddress": {
                "firstAddressLine": "LYNNWOOD CITY HALL, 44TH AVENUE WEST, LYNNWOOD, WA, USA"
            },
            "dpvFootnote": "A1M1",
        },
    }
}


class TestMapResponseUsPostalFallback:
    """GH-114 regression: fall back to postalAddress when CASS produces no dpvConfirmation."""

    def test_status_derived_from_verdict_when_dpv_absent(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_US_NO_DPV_RICH_POSTAL)
        # validationGranularity=PREMISE, addressComplete falsy → "invalid"
        assert result["status"] == "invalid"

    def test_dpv_match_code_none_when_dpv_absent(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_US_NO_DPV_RICH_POSTAL)
        assert result["dpv_match_code"] is None

    def test_address_line_1_from_postal_address_lines(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_US_NO_DPV_RICH_POSTAL)
        assert result["address_line_1"] == "Lynnwood City Hall"

    def test_address_line_2_from_postal_address_lines(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_US_NO_DPV_RICH_POSTAL)
        assert result["address_line_2"] == "44th Ave W"

    def test_city_from_postal_locality(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_US_NO_DPV_RICH_POSTAL)
        assert result["city"] == "Lynnwood"

    def test_region_from_postal_administrative_area(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_US_NO_DPV_RICH_POSTAL)
        assert result["region"] == "WA"

    def test_postal_code_from_postal_address(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_US_NO_DPV_RICH_POSTAL)
        assert result["postal_code"] == "98036-5635"

    def test_latitude_preserved(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_US_NO_DPV_RICH_POSTAL)
        assert result["latitude"] == pytest.approx(47.8253139)

    def test_inferred_components_propagated(self) -> None:
        result = GoogleClient._map_response(GOOGLE_RESPONSE_US_NO_DPV_RICH_POSTAL)
        assert result["has_inferred_components"] is True

    def test_existing_cass_path_unchanged_when_dpv_present(self) -> None:
        """When dpvConfirmation is present, USPS standardized fields still win."""
        result = GoogleClient._map_response(GOOGLE_RESPONSE_Y)
        assert result["dpv_match_code"] == "Y"
        assert result["address_line_1"] == "123 MAIN ST"
        assert result["city"] == "SPRINGFIELD"
        assert result["postal_code"] == "62701-1234"


# -- US non-CASS folded-unit recovery (GH #127) ----------------------------
# Modeled on the live production capture for "9 BENNY DR LOT B, OKANOGAN, WA
# 98840": USPS fell through to Google, which hit the non-CASS path
# (dpvConfirmation absent) and echoed street + unit folded into a single
# postalAddress.addressLines element rather than as a separate line.
GOOGLE_RESPONSE_US_NO_DPV_FOLDED_UNIT = {
    "result": {
        "verdict": {
            "inputGranularity": "PREMISE",
            "validationGranularity": "PREMISE",
            "geocodeGranularity": "PREMISE",
        },
        "address": {
            "postalAddress": {
                "regionCode": "US",
                "postalCode": "98840",
                "administrativeArea": "WA",
                "locality": "Okanogan",
                "addressLines": ["9 BENNY DR LOT B"],
            },
        },
        "geocode": {"location": {"latitude": 48.36, "longitude": -119.58}},
        "uspsData": {"standardizedAddress": {}},
    }
}


class TestMapResponseNonCassFoldedUnit:
    """GH #127: recover the folded secondary unit into address_line_2 on the non-CASS path."""

    def test_folded_unit_split_into_line_2(self) -> None:
        result = GoogleClient._map_response(
            GOOGLE_RESPONSE_US_NO_DPV_FOLDED_UNIT, secondary_address="LOT B"
        )
        assert result["address_line_1"] == "9 BENNY DR"
        assert result["address_line_2"] == "LOT B"

    def test_split_is_case_insensitive_preserves_echoed_casing(self) -> None:
        result = GoogleClient._map_response(
            GOOGLE_RESPONSE_US_NO_DPV_FOLDED_UNIT, secondary_address="lot b"
        )
        assert result["address_line_1"] == "9 BENNY DR"
        assert result["address_line_2"] == "LOT B"

    def test_no_secondary_sent_leaves_unit_folded(self) -> None:
        """No unit was sent → nothing to split; line stays as Google returned it."""
        result = GoogleClient._map_response(GOOGLE_RESPONSE_US_NO_DPV_FOLDED_UNIT)
        assert result["address_line_1"] == "9 BENNY DR LOT B"
        assert result["address_line_2"] == ""

    def test_google_reformatted_unit_no_match_no_regression(self) -> None:
        """If Google's echo doesn't end with the unit we sent, leave it in line 1."""
        result = GoogleClient._map_response(
            GOOGLE_RESPONSE_US_NO_DPV_FOLDED_UNIT, secondary_address="STE 200"
        )
        assert result["address_line_1"] == "9 BENNY DR LOT B"
        assert result["address_line_2"] == ""

    def test_separate_line_2_not_overwritten(self) -> None:
        """When Google already returns the unit as a separate line, keep it untouched."""
        result = GoogleClient._map_response(
            GOOGLE_RESPONSE_US_NO_DPV_RICH_POSTAL, secondary_address="LOT B"
        )
        assert result["address_line_1"] == "Lynnwood City Hall"
        assert result["address_line_2"] == "44th Ave W"


class TestBlankDpvTakesVerdictPath:
    """CR 14 (GH #250): a blank dpvConfirmation is treated as absent, so a response
    that carries a postalAddress is read on the non-CASS verdict path — not the
    CASS branch (which used to yield ``unavailable`` from standardizedAddress)."""

    def test_blank_dpv_with_postal_address_uses_verdict(self) -> None:
        raw = copy.deepcopy(GOOGLE_RESPONSE_US_NO_DPV_RICH_POSTAL)
        raw["result"]["uspsData"] = {
            "dpvConfirmation": " ",
            "standardizedAddress": {
                "firstAddressLine": "CASS LINE MUST NOT BE USED",
                "city": "NOT LYNNWOOD",
                "state": "XX",
                "zipCode": "00000",
            },
        }

        result = GoogleClient._map_response(raw)

        # PREMISE granularity without addressComplete → invalid (_verdict_to_status)
        assert result["status"] == "invalid"
        assert result["dpv_match_code"] is None
        assert result["city"] == "Lynnwood"
        assert result["region"] == "WA"
        assert result["postal_code"] == "98036-5635"


_GOOGLE_LOGGER = "address_validator.services.validation.google_client"


def _us_response_with_usps(usps_data: dict) -> dict:
    """A minimal US response whose ``uspsData`` is *usps_data*."""
    return {"result": {"verdict": {}, "geocode": {}, "uspsData": usps_data}}


class TestUnexpectedUspsDataWarning:
    """GH #254: #250 made an unrecognised dpvConfirmation silent (dropped →
    ``undetermined``). Warn once per distinct value so a new code is noticed;
    likewise warn once when Google reports USPS processing suspended."""

    @staticmethod
    def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
        return [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]

    def test_unknown_dpv_code_warns_with_value_and_length(self, caplog) -> None:
        raw = _us_response_with_usps({"standardizedAddress": {}, "dpvConfirmation": "X"})
        with caplog.at_level("WARNING", logger=_GOOGLE_LOGGER):
            result = GoogleClient._map_response(raw)
        assert result["status"] == "undetermined"
        assert result["dpv_match_code"] is None
        msgs = self._warnings(caplog)
        assert len(msgs) == 1
        assert msgs[0].startswith("GoogleClient: ")
        assert "dpvConfirmation" in msgs[0]
        assert "'X'" in msgs[0]
        assert "len=1" in msgs[0]

    def test_unknown_dpv_code_warns_once_per_value(self, caplog) -> None:
        raw_x = _us_response_with_usps({"standardizedAddress": {}, "dpvConfirmation": "X"})
        raw_z = _us_response_with_usps({"standardizedAddress": {}, "dpvConfirmation": "Z"})
        with caplog.at_level("WARNING", logger=_GOOGLE_LOGGER):
            GoogleClient._map_response(raw_x)
            GoogleClient._map_response(raw_x)
            GoogleClient._map_response(raw_z)
            GoogleClient._map_response(raw_x)
        msgs = self._warnings(caplog)
        assert len(msgs) == 2
        assert "'X'" in msgs[0]
        assert "'Z'" in msgs[1]

    def test_two_char_unknown_value_is_logged_verbatim(self, caplog) -> None:
        raw = _us_response_with_usps({"standardizedAddress": {}, "dpvConfirmation": "YY"})
        with caplog.at_level("WARNING", logger=_GOOGLE_LOGGER):
            GoogleClient._map_response(raw)
        msgs = self._warnings(caplog)
        assert len(msgs) == 1
        assert "'YY'" in msgs[0]

    def test_long_unknown_value_is_not_logged_and_warns_once(self, caplog) -> None:
        # Longer than a code: could be address text, so only the length is
        # logged, and every long value shares one signature (bounded dedup set).
        raw_a = _us_response_with_usps(
            {"standardizedAddress": {}, "dpvConfirmation": "ABCDEFGHIJKLMNOP"}
        )
        raw_b = _us_response_with_usps(
            {"standardizedAddress": {}, "dpvConfirmation": "123 MAIN ST"}
        )
        with caplog.at_level("WARNING", logger=_GOOGLE_LOGGER):
            result = GoogleClient._map_response(raw_a)
            GoogleClient._map_response(raw_b)
        assert result["status"] == "undetermined"
        msgs = self._warnings(caplog)
        assert len(msgs) == 1
        assert "len=16" in msgs[0]
        assert "ABC" not in msgs[0]
        assert "MAIN" not in msgs[0]

    @pytest.mark.parametrize("dpv", ["Y", "D", "S", "N", " ", "", None])
    def test_documented_or_blank_dpv_does_not_warn(self, dpv: str | None, caplog) -> None:
        usps: dict = {"standardizedAddress": {}}
        if dpv is not None:
            usps["dpvConfirmation"] = dpv
        with caplog.at_level("WARNING", logger=_GOOGLE_LOGGER):
            GoogleClient._map_response(_us_response_with_usps(usps))
        assert self._warnings(caplog) == []

    def test_error_message_warns_once_without_its_text(self, caplog) -> None:
        raw = _us_response_with_usps(
            {
                "standardizedAddress": {},
                "cassProcessed": False,
                "errorMessage": "USPS processing suspended for 123 MAIN ST",
            }
        )
        with caplog.at_level("WARNING", logger=_GOOGLE_LOGGER):
            GoogleClient._map_response(raw)
            GoogleClient._map_response(raw)
        msgs = self._warnings(caplog)
        assert len(msgs) == 1
        assert "errorMessage" in msgs[0]
        assert "cassProcessed=False" in msgs[0]
        # The message text is undocumented free text — never logged.
        assert "suspended for" not in msgs[0]
        assert "MAIN" not in msgs[0]

    def test_non_bool_cass_processed_is_logged_by_type_only(self, caplog) -> None:
        # Documented as a bool; anything else could be free text, so only its
        # type name is logged (and the dedup key stays bounded).
        raw = _us_response_with_usps(
            {"standardizedAddress": {}, "cassProcessed": "123 MAIN ST", "errorMessage": "x"}
        )
        with caplog.at_level("WARNING", logger=_GOOGLE_LOGGER):
            GoogleClient._map_response(raw)
        msgs = self._warnings(caplog)
        assert len(msgs) == 1
        assert "cassProcessed=<str>" in msgs[0]
        assert "MAIN" not in msgs[0]

    def test_captured_no_dpv_response_does_not_warn(self, caplog) -> None:
        # Live capture (GH-114): no dpvConfirmation key, no errorMessage.
        with caplog.at_level("WARNING", logger=_GOOGLE_LOGGER):
            GoogleClient._map_response(GOOGLE_RESPONSE_US_NO_DPV_RICH_POSTAL)
        assert self._warnings(caplog) == []
