"""Unit tests for USPSProvider — validates request→response mapping."""

from unittest.mock import AsyncMock

import httpx
import pytest

from models import ValidateRequestV1
from services.validation.usps_provider import USPSProvider

CLIENT_RESULT_Y = {
    "dpv_match_code": "Y",
    "zip_plus4": "1234",
    "vacant": "N",
    "corrected_components": {
        "address_line": "123 MAIN ST",
        "secondary_address": "",
        "city": "SPRINGFIELD",
        "region": "IL",
        "postal_code": "62701",
    },
}

CLIENT_RESULT_N = {
    "dpv_match_code": "N",
    "zip_plus4": None,
    "vacant": None,
    "corrected_components": None,
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
        assert result.validation_status == "confirmed"
        assert result.dpv_match_code == "Y"

    @pytest.mark.asyncio
    async def test_dpv_s_sets_confirmed_missing_secondary(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = {**CLIENT_RESULT_Y, "dpv_match_code": "S"}
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation_status == "confirmed_missing_secondary"

    @pytest.mark.asyncio
    async def test_dpv_d_sets_confirmed_bad_secondary(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = {**CLIENT_RESULT_Y, "dpv_match_code": "D"}
        req = ValidateRequestV1(address="123 Main St Apt 999", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation_status == "confirmed_bad_secondary"

    @pytest.mark.asyncio
    async def test_dpv_n_sets_not_confirmed(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_N
        req = ValidateRequestV1(address="999 Fake St", city="Nowhere", region="IL")
        result = await provider.validate(req)
        assert result.validation_status == "not_confirmed"

    @pytest.mark.asyncio
    async def test_provider_field_is_usps(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.provider == "usps"

    @pytest.mark.asyncio
    async def test_zip_plus4_surfaced(self, provider: USPSProvider, mock_client: AsyncMock) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.zip_plus4 == "1234"

    @pytest.mark.asyncio
    async def test_corrected_components_surfaced(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.corrected_components is not None
        assert result.corrected_components["city"] == "SPRINGFIELD"

    @pytest.mark.asyncio
    async def test_http_error_raises(self, provider: USPSProvider, mock_client: AsyncMock) -> None:
        mock_client.validate_address.side_effect = httpx.TimeoutException("timeout")
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        with pytest.raises(httpx.TimeoutException):
            await provider.validate(req)
