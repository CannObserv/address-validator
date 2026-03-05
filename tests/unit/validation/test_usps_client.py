"""Unit tests for the USPS v3 client (token caching, request shape, response mapping)."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

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
    "addressAdditionalInfo": {
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
            expires_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
        )
        assert not token.is_expired()

    def test_expired_when_in_past(self) -> None:
        token = USPSToken(
            access_token="x",
            expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
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
        assert mock_http.get.call_count == 1   # address call

    @pytest.mark.asyncio
    async def test_reuses_cached_token(
        self, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        mock_http.get.return_value = self._make_response(VALID_ADDRESS_RESPONSE)

        await client.validate_address("123 Main St", "Springfield", "IL")
        await client.validate_address("456 Oak Ave", "Chicago", "IL")

        assert mock_http.post.call_count == 1  # only one token fetch
        assert mock_http.get.call_count == 2

    @pytest.mark.asyncio
    async def test_refreshes_expired_token(
        self, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        expired = USPSToken(
            access_token="old",
            expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
        )
        client._token = expired

        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        mock_http.get.return_value = self._make_response(VALID_ADDRESS_RESPONSE)

        await client.validate_address("123 Main St", "Springfield", "IL")
        assert mock_http.post.call_count == 1  # refreshed

    @pytest.mark.asyncio
    async def test_maps_dpv_confirmation(
        self, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        mock_http.get.return_value = self._make_response(VALID_ADDRESS_RESPONSE)

        result = await client.validate_address("123 Main St", "Springfield", "IL")
        assert result["dpv_match_code"] == "Y"

    @pytest.mark.asyncio
    async def test_maps_zip_plus4(
        self, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        mock_http.get.return_value = self._make_response(VALID_ADDRESS_RESPONSE)

        result = await client.validate_address("123 Main St", "Springfield", "IL")
        assert result["zip_plus4"] == "1234"

    @pytest.mark.asyncio
    async def test_maps_corrected_address_components(
        self, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        mock_http.get.return_value = self._make_response(VALID_ADDRESS_RESPONSE)

        result = await client.validate_address("123 Main St", "Springfield", "IL")
        comps = result["corrected_components"]
        assert comps["address_line"] == "123 MAIN ST"
        assert comps["city"] == "SPRINGFIELD"
        assert comps["region"] == "IL"
        assert comps["postal_code"] == "62701"

    @pytest.mark.asyncio
    async def test_http_error_propagates(
        self, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        mock_http.post.return_value = self._make_response(TOKEN_RESPONSE)
        bad_resp = MagicMock(spec=httpx.Response)
        bad_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock()
        )
        mock_http.get.return_value = bad_resp

        with pytest.raises(httpx.HTTPStatusError):
            await client.validate_address("999 Fake St", "Nowhere", "IL")
