"""Unit tests for GoogleProvider — validates request→response mapping."""

from unittest.mock import AsyncMock

import httpx
import pytest

from models import ValidateRequestV1
from services.validation.google_provider import GoogleProvider

# Flat dicts matching GoogleClient._map_response output
CLIENT_RESULT_Y = {
    "dpv_match_code": "Y",
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

CLIENT_RESULT_WITH_WARNINGS = {
    **CLIENT_RESULT_Y,
    "has_inferred_components": True,
    "has_replaced_components": True,
    "has_unconfirmed_components": True,
}


class TestGoogleProvider:
    @pytest.fixture()
    def mock_client(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture()
    def provider(self, mock_client: AsyncMock) -> GoogleProvider:
        p = GoogleProvider.__new__(GoogleProvider)
        p._client = mock_client
        return p

    @pytest.mark.asyncio
    async def test_dpv_y_sets_confirmed(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation.status == "confirmed"
        assert result.validation.dpv_match_code == "Y"

    @pytest.mark.asyncio
    async def test_dpv_s_sets_confirmed_missing_secondary(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = {**CLIENT_RESULT_Y, "dpv_match_code": "S"}
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation.status == "confirmed_missing_secondary"

    @pytest.mark.asyncio
    async def test_dpv_d_sets_confirmed_bad_secondary(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = {**CLIENT_RESULT_Y, "dpv_match_code": "D"}
        req = ValidateRequestV1(address="123 Main St Apt 999", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation.status == "confirmed_bad_secondary"

    @pytest.mark.asyncio
    async def test_dpv_n_sets_not_confirmed(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_N
        req = ValidateRequestV1(address="999 Fake St", city="Nowhere", region="IL")
        result = await provider.validate(req)
        assert result.validation.status == "not_confirmed"

    @pytest.mark.asyncio
    async def test_provider_field_is_google(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation.provider == "google"

    @pytest.mark.asyncio
    async def test_lat_lng_populated(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.latitude == pytest.approx(39.7817)
        assert result.longitude == pytest.approx(-89.6501)

    @pytest.mark.asyncio
    async def test_lat_lng_none_when_not_confirmed(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_N
        req = ValidateRequestV1(address="999 Fake St", city="Nowhere", region="IL")
        result = await provider.validate(req)
        assert result.latitude is None
        assert result.longitude is None

    @pytest.mark.asyncio
    async def test_postal_code_with_zip_plus4(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.postal_code == "62701-1234"

    @pytest.mark.asyncio
    async def test_validated_string_built(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validated == "123 MAIN ST  SPRINGFIELD, IL 62701-1234"

    @pytest.mark.asyncio
    async def test_components_spec_is_usps_pub28(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.components is not None
        assert result.components.spec == "usps-pub28"

    @pytest.mark.asyncio
    async def test_components_contains_vacant(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.components is not None
        assert result.components.values.get("vacant") == "N"

    @pytest.mark.asyncio
    async def test_no_components_when_not_confirmed(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_N
        req = ValidateRequestV1(address="999 Fake St")
        result = await provider.validate(req)
        assert result.components is None
        assert result.validated is None

    @pytest.mark.asyncio
    async def test_inferred_components_warning(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_WITH_WARNINGS
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert any("inferred" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_replaced_components_warning(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_WITH_WARNINGS
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert any("replaced" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_unconfirmed_components_warning(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_WITH_WARNINGS
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert any("unconfirmed" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_no_warnings_when_all_false(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_http_error_raises(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.side_effect = httpx.TimeoutException("timeout")
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        with pytest.raises(httpx.TimeoutException):
            await provider.validate(req)
