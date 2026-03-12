"""Unit tests for the USPS v3 client (token caching, request shape, response mapping)."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from services.validation.usps_client import USPSClient, USPSToken

TOKEN_RESPONSE = {
    "access_token": "tok-abc",
    "token_type": "Bearer",
    "expires_in": 3600,
}

VALID_ADDRESS_RESPONSE = {
    "address": {
        "streetAddress": "123 MAIN ST",
        "city": "SPRINGFIELD",
        "state": "IL",
        "ZIPCode": "62701",
        "ZIPPlus4": "1234",
    },
    "additionalInfo": {
        "DPVConfirmation": "Y",
        "vacant": "N",
        "business": "N",
        "carrierRoute": "C001",
        "deliveryPoint": "23",
        "DPVCMRA": "N",
    },
}


class TestUSPSToken:
    def test_not_expired_when_fresh(self) -> None:
        token = USPSToken(
            access_token="x",
            expires_at=datetime(2099, 1, 1, tzinfo=UTC),
        )
        assert not token.is_expired()

    def test_expired_when_in_past(self) -> None:
        token = USPSToken(
            access_token="x",
            expires_at=datetime(2000, 1, 1, tzinfo=UTC),
        )
        assert token.is_expired()


class TestUSPSClient:
    @pytest.fixture()
    def mock_http(self) -> AsyncMock:
        return AsyncMock(spec=httpx.AsyncClient)

    @pytest.fixture()
    def client(self, mock_http: AsyncMock) -> USPSClient:
        return USPSClient(
            consumer_key="key",
            consumer_secret="secret",
            http_client=mock_http,
        )

    def _make_response(self, json_data: dict, status_code: int = 200) -> MagicMock:
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        resp.json.return_value = json_data
        resp.raise_for_status = MagicMock()
        return resp

    @pytest.mark.asyncio
    async def test_fetches_token_on_first_call(
        self, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        mock_http.get.return_value = self._make_response(VALID_ADDRESS_RESPONSE)

        await client.validate_address(
            street_address="123 Main St",
            city="Springfield",
            state="IL",
        )
        assert mock_http.post.call_count == 1  # token fetch
        assert mock_http.get.call_count == 1  # address call

    @pytest.mark.asyncio
    async def test_reuses_cached_token(self, client: USPSClient, mock_http: AsyncMock) -> None:
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        mock_http.get.return_value = self._make_response(VALID_ADDRESS_RESPONSE)

        await client.validate_address("123 Main St", "Springfield", "IL")
        await client.validate_address("456 Oak Ave", "Chicago", "IL")

        assert mock_http.post.call_count == 1  # only one token fetch
        assert mock_http.get.call_count == 2

    @pytest.mark.asyncio
    async def test_refreshes_expired_token(self, client: USPSClient, mock_http: AsyncMock) -> None:
        expired = USPSToken(
            access_token="old",
            expires_at=datetime(2000, 1, 1, tzinfo=UTC),
        )
        client._token = expired

        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        mock_http.get.return_value = self._make_response(VALID_ADDRESS_RESPONSE)

        await client.validate_address("123 Main St", "Springfield", "IL")
        assert mock_http.post.call_count == 1  # refreshed

    @pytest.mark.asyncio
    async def test_maps_dpv_confirmation(self, client: USPSClient, mock_http: AsyncMock) -> None:
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        mock_http.get.return_value = self._make_response(VALID_ADDRESS_RESPONSE)

        result = await client.validate_address("123 Main St", "Springfield", "IL")
        assert result["dpv_match_code"] == "Y"

    @pytest.mark.asyncio
    async def test_maps_flat_address_fields(self, client: USPSClient, mock_http: AsyncMock) -> None:
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        mock_http.get.return_value = self._make_response(VALID_ADDRESS_RESPONSE)

        result = await client.validate_address("123 Main St", "Springfield", "IL")
        assert result["address_line_1"] == "123 MAIN ST"
        assert result["city"] == "SPRINGFIELD"
        assert result["region"] == "IL"
        assert result["postal_code"] == "62701-1234"
        assert result["vacant"] == "N"
        assert result["dpv_match_code"] == "Y"
        assert "corrected_components" not in result
        assert "zip_plus4" not in result

    @pytest.mark.asyncio
    async def test_concurrent_requests_fetch_token_once(
        self, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        """Concurrent calls on a cold client must issue exactly one token fetch.

        Verifies the _token_lock prevents the check-then-act race where
        multiple coroutines see an empty/expired token and all race to
        refresh it.
        """
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        mock_http.get.return_value = self._make_response(VALID_ADDRESS_RESPONSE)

        results = await asyncio.gather(
            client.validate_address("123 Main St", "Springfield", "IL"),
            client.validate_address("456 Oak Ave", "Chicago", "IL"),
            client.validate_address("789 Pine Rd", "Peoria", "IL"),
        )
        assert len(results) == 3
        assert mock_http.post.call_count == 1  # single token fetch despite 3 concurrent calls

    @pytest.mark.asyncio
    async def test_http_error_propagates(self, client: USPSClient, mock_http: AsyncMock) -> None:
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock()
        )
        mock_http.get.return_value = bad_resp

        with pytest.raises(httpx.HTTPStatusError):
            await client.validate_address("999 Fake St", "Nowhere", "IL")


class TestMapResponse:
    def test_map_response_merges_zip_plus4(self) -> None:
        raw = {
            "address": {
                "streetAddress": "123 MAIN ST",
                "city": "SPRINGFIELD",
                "state": "IL",
                "ZIPCode": "62701",
                "ZIPPlus4": "1234",
            },
            "additionalInfo": {"DPVConfirmation": "Y", "vacant": "N"},
        }
        result = USPSClient._map_response(raw)
        assert result["postal_code"] == "62701-1234"

    def test_map_response_without_zip_plus4(self) -> None:
        raw = {
            "address": {
                "streetAddress": "123 MAIN ST",
                "city": "SPRINGFIELD",
                "state": "IL",
                "ZIPCode": "62701",
            },
            "additionalInfo": {"DPVConfirmation": "Y", "vacant": "N"},
        }
        result = USPSClient._map_response(raw)
        assert result["postal_code"] == "62701"

    def test_map_response_secondary_address(self) -> None:
        raw = {
            "address": {
                "streetAddress": "123 MAIN ST",
                "secondaryAddress": "APT 4",
                "city": "SPRINGFIELD",
                "state": "IL",
                "ZIPCode": "62701",
            },
            "additionalInfo": {"DPVConfirmation": "S"},
        }
        result = USPSClient._map_response(raw)
        assert result["address_line_2"] == "APT 4"

    def test_map_response_vacant_surfaced(self) -> None:
        raw = {
            "address": {
                "streetAddress": "123 MAIN ST",
                "city": "X",
                "state": "IL",
                "ZIPCode": "62701",
            },
            "additionalInfo": {"DPVConfirmation": "Y", "vacant": "Y"},
        }
        result = USPSClient._map_response(raw)
        assert result["vacant"] == "Y"

    def test_map_response_no_street_returns_empty_address_line_1(self) -> None:
        raw = {"address": {}, "additionalInfo": {"DPVConfirmation": "N"}}
        result = USPSClient._map_response(raw)
        assert result["address_line_1"] == ""
