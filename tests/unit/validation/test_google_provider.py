"""Unit tests for GoogleProvider — validates request→response mapping."""

from unittest.mock import AsyncMock

import httpx
import pytest

from address_validator.models import ComponentSet, StandardizeResponseV1
from address_validator.services.validation.google_provider import GoogleProvider
from address_validator.usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION

# Flat dicts matching GoogleClient._map_response output
CLIENT_RESULT_Y = {
    "dpv_match_code": "Y",
    "status": "confirmed",
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
    "status": "not_confirmed",
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


def _make_std(
    address_line_1: str = "123 MAIN ST",
    city: str = "SPRINGFIELD",
    region: str = "IL",
    postal_code: str = "62701",
    country: str = "US",
) -> StandardizeResponseV1:
    return StandardizeResponseV1(
        address_line_1=address_line_1,
        address_line_2="",
        city=city,
        region=region,
        postal_code=postal_code,
        country=country,
        standardized=f"{address_line_1}  {city}, {region} {postal_code}",
        components=ComponentSet(
            spec=USPS_PUB28_SPEC,
            spec_version=USPS_PUB28_SPEC_VERSION,
            values={"premise_number": "123", "thoroughfare_name": "MAIN"},
        ),
        warnings=[],
    )


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
        result = await provider.validate(_make_std())
        assert result.validation.status == "confirmed"
        assert result.validation.dpv_match_code == "Y"

    @pytest.mark.asyncio
    async def test_dpv_s_sets_confirmed_missing_secondary(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = {
            **CLIENT_RESULT_Y,
            "dpv_match_code": "S",
            "status": "confirmed_missing_secondary",
        }
        result = await provider.validate(_make_std())
        assert result.validation.status == "confirmed_missing_secondary"

    @pytest.mark.asyncio
    async def test_dpv_d_sets_confirmed_bad_secondary(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = {
            **CLIENT_RESULT_Y,
            "dpv_match_code": "D",
            "status": "confirmed_bad_secondary",
        }
        result = await provider.validate(_make_std(address_line_1="123 MAIN ST APT 999"))
        assert result.validation.status == "confirmed_bad_secondary"

    @pytest.mark.asyncio
    async def test_dpv_n_sets_not_confirmed(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_N
        result = await provider.validate(_make_std(address_line_1="999 FAKE ST", city="NOWHERE"))
        assert result.validation.status == "not_confirmed"

    @pytest.mark.asyncio
    async def test_provider_field_is_google(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.validation.provider == "google"

    @pytest.mark.asyncio
    async def test_lat_lng_populated(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.latitude == pytest.approx(39.7817)
        assert result.longitude == pytest.approx(-89.6501)

    @pytest.mark.asyncio
    async def test_lat_lng_none_when_not_confirmed(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_N
        result = await provider.validate(_make_std(address_line_1="999 FAKE ST"))
        assert result.latitude is None
        assert result.longitude is None

    @pytest.mark.asyncio
    async def test_postal_code_with_zip_plus4(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.postal_code == "62701-1234"

    @pytest.mark.asyncio
    async def test_validated_string_built(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.validated == "123 MAIN ST  SPRINGFIELD, IL 62701-1234"

    @pytest.mark.asyncio
    async def test_components_spec_is_usps_pub28(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.components is not None
        assert result.components.spec == "usps-pub28"

    @pytest.mark.asyncio
    async def test_components_contains_vacant(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.components is not None
        assert result.components.values.get("vacant") == "N"

    @pytest.mark.asyncio
    async def test_no_components_when_not_confirmed(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_N
        result = await provider.validate(_make_std(address_line_1="999 FAKE ST"))
        assert result.components is None
        assert result.validated is None

    @pytest.mark.asyncio
    async def test_inferred_components_warning(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_WITH_WARNINGS
        result = await provider.validate(_make_std())
        assert any("inferred" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_replaced_components_warning(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_WITH_WARNINGS
        result = await provider.validate(_make_std())
        assert any("replaced" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_unconfirmed_components_warning(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_WITH_WARNINGS
        result = await provider.validate(_make_std())
        assert any("unconfirmed" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_no_warnings_when_all_false(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std())
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_client_called_with_standardized_fields(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        """Provider must forward std fields (not raw user input) to the Google client."""
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        std = _make_std(
            address_line_1="123 MAIN ST", city="SPRINGFIELD", region="IL", postal_code="62701"
        )
        await provider.validate(std)
        mock_client.validate_address.assert_called_once_with(
            street_address="123 MAIN ST",
            city="SPRINGFIELD",
            state="IL",
            zip_code="62701",
            country="US",
        )

    @pytest.mark.asyncio
    async def test_http_error_raises(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        mock_client.validate_address.side_effect = httpx.TimeoutException("timeout")
        with pytest.raises(httpx.TimeoutException):
            await provider.validate(_make_std())

    @pytest.mark.asyncio
    async def test_validate_accepts_raw_input_kwarg(
        self, provider: GoogleProvider, mock_client: AsyncMock
    ) -> None:
        """GoogleProvider.validate must accept raw_input without raising."""
        mock_client.validate_address.return_value = CLIENT_RESULT_Y
        result = await provider.validate(_make_std(), raw_input="123 Main St, Springfield IL")
        assert result.validation.status == "confirmed"

    def test_supports_non_us_is_true(self, provider: GoogleProvider) -> None:
        assert provider.supports_non_us is True


CLIENT_RESULT_INTERNATIONAL_CONFIRMED = {
    "dpv_match_code": None,
    "status": "confirmed",
    "address_line_1": "10 Downing St",
    "address_line_2": "",
    "city": "London",
    "region": "",
    "postal_code": "SW1A 2AA",
    "vacant": None,
    "latitude": 51.5033,
    "longitude": -0.1276,
    "has_inferred_components": False,
    "has_replaced_components": False,
    "has_unconfirmed_components": False,
}

CLIENT_RESULT_INTERNATIONAL_NOT_FOUND = {
    "dpv_match_code": None,
    "status": "not_found",
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


def _make_gb_std(
    address_line_1: str = "10 Downing St",
    city: str = "London",
    postal_code: str = "SW1A 2AA",
    country: str = "GB",
) -> StandardizeResponseV1:
    return StandardizeResponseV1(
        address_line_1=address_line_1,
        address_line_2="",
        city=city,
        region="",
        postal_code=postal_code,
        country=country,
        standardized=f"{address_line_1}  {city} {postal_code}",
        components=ComponentSet(
            spec="raw",
            spec_version="1",
            values={"address_line_1": address_line_1, "city": city, "postal_code": postal_code},
        ),
    )


class TestGoogleProviderNonUS:
    @pytest.mark.asyncio
    async def test_non_us_confirmed(self) -> None:
        client = AsyncMock()
        client.validate_address = AsyncMock(return_value=CLIENT_RESULT_INTERNATIONAL_CONFIRMED)
        provider = GoogleProvider(client)
        result = await provider.validate(_make_gb_std())
        assert result.validation.status == "confirmed"
        assert result.country == "GB"

    @pytest.mark.asyncio
    async def test_non_us_not_found(self) -> None:
        client = AsyncMock()
        client.validate_address = AsyncMock(return_value=CLIENT_RESULT_INTERNATIONAL_NOT_FOUND)
        provider = GoogleProvider(client)
        result = await provider.validate(_make_gb_std())
        assert result.validation.status == "not_found"

    @pytest.mark.asyncio
    async def test_non_us_passes_country_to_client(self) -> None:
        client = AsyncMock()
        client.validate_address = AsyncMock(return_value=CLIENT_RESULT_INTERNATIONAL_CONFIRMED)
        provider = GoogleProvider(client)
        await provider.validate(_make_gb_std(country="GB"))
        client.validate_address.assert_awaited_once()
        call_kwargs = client.validate_address.call_args[1]
        assert call_kwargs["country"] == "GB"

    @pytest.mark.asyncio
    async def test_non_us_no_dpv_match_code_in_result(self) -> None:
        client = AsyncMock()
        client.validate_address = AsyncMock(return_value=CLIENT_RESULT_INTERNATIONAL_CONFIRMED)
        provider = GoogleProvider(client)
        result = await provider.validate(_make_gb_std())
        assert result.validation.dpv_match_code is None

    @pytest.mark.asyncio
    async def test_non_us_components_spec_is_raw(self) -> None:
        client = AsyncMock()
        client.validate_address = AsyncMock(return_value=CLIENT_RESULT_INTERNATIONAL_CONFIRMED)
        provider = GoogleProvider(client)
        result = await provider.validate(_make_gb_std())
        assert result.components is not None
        assert result.components.spec == "raw"

    @pytest.mark.asyncio
    async def test_non_us_invalid_status(self) -> None:
        client = AsyncMock()
        client.validate_address = AsyncMock(
            return_value={**CLIENT_RESULT_INTERNATIONAL_NOT_FOUND, "status": "invalid"}
        )
        provider = GoogleProvider(client)
        result = await provider.validate(_make_gb_std())
        assert result.validation.status == "invalid"
