"""Unit tests for the NullProvider (no-op validation backend)."""

import pytest

from address_validator.models import ComponentSet, StandardizeResponseV1
from address_validator.services.validation.null_provider import NullProvider
from address_validator.usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION


def _make_std(country: str = "US") -> StandardizeResponseV1:
    return StandardizeResponseV1(
        address_line_1="123 MAIN ST",
        address_line_2="",
        city="SPRINGFIELD",
        region="IL",
        postal_code="62701",
        country=country,
        standardized="123 MAIN ST  SPRINGFIELD, IL 62701",
        components=ComponentSet(
            spec=USPS_PUB28_SPEC,
            spec_version=USPS_PUB28_SPEC_VERSION,
            values={"premise_number": "123", "thoroughfare_name": "MAIN"},
        ),
        warnings=[],
    )


class TestNullProvider:
    @pytest.fixture()
    def provider(self) -> NullProvider:
        return NullProvider()

    @pytest.mark.asyncio
    async def test_returns_unavailable_status(self, provider: NullProvider) -> None:
        result = await provider.validate(_make_std())
        assert result.validation.status == "unavailable"

    @pytest.mark.asyncio
    async def test_provider_name_is_none(self, provider: NullProvider) -> None:
        result = await provider.validate(_make_std())
        assert result.validation.provider is None

    @pytest.mark.asyncio
    async def test_dpv_match_code_is_none(self, provider: NullProvider) -> None:
        result = await provider.validate(_make_std())
        assert result.validation.dpv_match_code is None

    @pytest.mark.asyncio
    async def test_address_fields_are_none(self, provider: NullProvider) -> None:
        result = await provider.validate(_make_std())
        assert result.address_line_1 is None
        assert result.components is None
        assert result.validated is None

    @pytest.mark.asyncio
    async def test_lat_lng_are_none(self, provider: NullProvider) -> None:
        result = await provider.validate(_make_std())
        assert result.latitude is None
        assert result.longitude is None

    @pytest.mark.asyncio
    async def test_warnings_is_empty(self, provider: NullProvider) -> None:
        result = await provider.validate(_make_std())
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_api_version_is_1(self, provider: NullProvider) -> None:
        result = await provider.validate(_make_std())
        assert result.api_version == "1"

    @pytest.mark.asyncio
    async def test_country_passed_through(self, provider: NullProvider) -> None:
        result = await provider.validate(_make_std(country="US"))
        assert result.country == "US"

    @pytest.mark.asyncio
    async def test_validate_accepts_raw_input_kwarg(self, provider: NullProvider) -> None:
        """NullProvider.validate must accept raw_input without raising."""
        result = await provider.validate(_make_std(), raw_input="123 Main St, Springfield IL")
        assert result.validation.status == "unavailable"

    def test_supports_non_us_is_false(self, provider: NullProvider) -> None:
        assert provider.supports_non_us is False
