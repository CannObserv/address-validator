"""Unit tests for the NullProvider (no-op validation backend)."""

import pytest

from models import ValidateRequestV1
from services.validation.null_provider import NullProvider


class TestNullProvider:
    @pytest.fixture()
    def provider(self) -> NullProvider:
        return NullProvider()

    @pytest.mark.asyncio
    async def test_returns_unavailable_status(self, provider: NullProvider) -> None:
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation.status == "unavailable"

    @pytest.mark.asyncio
    async def test_provider_name_is_none(self, provider: NullProvider) -> None:
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation.provider is None

    @pytest.mark.asyncio
    async def test_dpv_match_code_is_none(self, provider: NullProvider) -> None:
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.validation.dpv_match_code is None

    @pytest.mark.asyncio
    async def test_address_fields_are_none(self, provider: NullProvider) -> None:
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.address_line_1 is None
        assert result.components is None
        assert result.validated is None

    @pytest.mark.asyncio
    async def test_lat_lng_are_none(self, provider: NullProvider) -> None:
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.latitude is None
        assert result.longitude is None

    @pytest.mark.asyncio
    async def test_warnings_is_empty(self, provider: NullProvider) -> None:
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_api_version_is_1(self, provider: NullProvider) -> None:
        req = ValidateRequestV1(address="123 Main St", city="Springfield", region="IL")
        result = await provider.validate(req)
        assert result.api_version == "1"
