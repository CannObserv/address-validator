"""Unit tests for USPSProvider — validates request→response mapping."""

from unittest.mock import AsyncMock

import httpx
import pytest

from models import ValidateRequestV1
from services.validation.usps_provider import USPSProvider

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
        assert result.validation.status == "confirmed"
        assert result.validation.dpv_match_code == "Y"

    @pytest.mark.asyncio
    async def test_dpv_s_sets_confirmed_missing_secondary(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = {**CLIENT_RESULT_Y, "dpv_match_code": "S"}
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation.status == "confirmed_missing_secondary"

    @pytest.mark.asyncio
    async def test_dpv_d_sets_confirmed_bad_secondary(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = {**CLIENT_RESULT_Y, "dpv_match_code": "D"}
        req = ValidateRequestV1(address="123 Main St Apt 999", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation.status == "confirmed_bad_secondary"

    @pytest.mark.asyncio
    async def test_dpv_n_sets_not_confirmed(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_N
        req = ValidateRequestV1(address="999 Fake St", city="Nowhere", region="IL")
        result = await provider.validate(req)
        assert result.validation.status == "not_confirmed"

    @pytest.mark.asyncio
    async def test_provider_field_is_usps(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation.provider == "usps"

    @pytest.mark.asyncio
    async def test_postal_code_with_zip_plus4(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.postal_code == "62701-1234"

    @pytest.mark.asyncio
    async def test_address_lines_populated(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.address_line_1 == "123 MAIN ST"
        assert result.city == "SPRINGFIELD"
        assert result.region == "IL"

    @pytest.mark.asyncio
    async def test_components_contains_vacant(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.components is not None
        assert result.components.values.get("vacant") == "N"

    @pytest.mark.asyncio
    async def test_components_spec_is_usps_pub28(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.components is not None
        assert result.components.spec == "usps-pub28"

    @pytest.mark.asyncio
    async def test_validated_string_built(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validated == "123 MAIN ST  SPRINGFIELD, IL 62701-1234"

    @pytest.mark.asyncio
    async def test_lat_lng_are_none(self, provider: USPSProvider, mock_client: AsyncMock) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.latitude is None
        assert result.longitude is None

    @pytest.mark.asyncio
    async def test_warnings_is_empty(self, provider: USPSProvider, mock_client: AsyncMock) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_not_confirmed_has_no_components(
        self, provider: USPSProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_N
        req = ValidateRequestV1(address="999 Fake St", city="Nowhere", region="IL")
        result = await provider.validate(req)
        assert result.components is None
        assert result.validated is None

    @pytest.mark.asyncio
    async def test_http_error_raises(self, provider: USPSProvider, mock_client: AsyncMock) -> None:
        mock_client.validate_address.side_effect = httpx.TimeoutException("timeout")
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        with pytest.raises(httpx.TimeoutException):
            await provider.validate(req)
