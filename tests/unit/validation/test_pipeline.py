"""Unit tests for services/validation/pipeline.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from address_validator.core.errors import APIError
from address_validator.models import (
    ComponentSet,
    ParseResponseV1,
    StandardizeResponseV1,
    ValidateRequestV1,
)
from address_validator.services.libpostal_client import LibpostalUnavailableError
from address_validator.services.validation.pipeline import (
    build_non_us_std,
    run_non_us_pipeline_v1,
    run_non_us_pipeline_v2,
    run_us_pipeline,
)

# ---------------------------------------------------------------------------
# build_non_us_std
# ---------------------------------------------------------------------------


class TestBuildNonUsSd:
    def test_returns_standardized_address(self) -> None:
        comps = {
            "address_line_1": "10 Downing St",
            "city": "London",
            "postal_code": "SW1A 2AA",
        }
        result = build_non_us_std(comps, "GB")
        assert isinstance(result, StandardizeResponseV1)
        assert result.country == "GB"
        assert result.address_line_1 == "10 Downing St"
        assert result.city == "London"
        assert result.postal_code == "SW1A 2AA"

    def test_missing_fields_default_to_empty_string(self) -> None:
        result = build_non_us_std({"city": "Paris"}, "FR")
        assert result.address_line_1 == ""
        assert result.address_line_2 == ""
        assert result.region == ""
        assert result.postal_code == ""

    def test_spec_is_raw(self) -> None:
        result = build_non_us_std({"address_line_1": "1 Main St"}, "GB")
        assert result.components.spec == "raw"
        assert result.components.spec_version == "1"

    def test_components_values_are_verbatim(self) -> None:
        comps = {"address_line_1": "mixed Case", "city": "cityName"}
        result = build_non_us_std(comps, "GB")
        assert result.components.values == comps

    def test_standardized_field_is_built(self) -> None:
        result = build_non_us_std(
            {"address_line_1": "10 Downing St", "city": "London", "postal_code": "SW1A 2AA"},
            "GB",
        )
        assert "10 Downing St" in result.standardized
        assert "London" in result.standardized


# ---------------------------------------------------------------------------
# run_non_us_pipeline_v1
# ---------------------------------------------------------------------------


def _make_registry(supports_non_us: bool = True):
    provider = MagicMock()
    provider.supports_non_us = supports_non_us
    registry = MagicMock()
    registry.get_provider.return_value = provider
    return registry, provider


class TestRunNonUsPipelineV1:
    @pytest.mark.asyncio
    async def test_invalid_country_code_raises_422(self) -> None:
        registry, _ = _make_registry()
        req = ValidateRequestV1(address="1 Main St", country="XX")
        with pytest.raises(APIError) as exc_info:
            await run_non_us_pipeline_v1(req, registry)
        assert exc_info.value.status_code == 422
        assert exc_info.value.error == "invalid_country_code"

    @pytest.mark.asyncio
    async def test_raw_string_non_us_raises_422(self) -> None:
        registry, _ = _make_registry()
        req = ValidateRequestV1(address="10 Downing St", country="GB")
        with pytest.raises(APIError) as exc_info:
            await run_non_us_pipeline_v1(req, registry)
        assert exc_info.value.status_code == 422
        assert exc_info.value.error == "country_not_supported"

    @pytest.mark.asyncio
    async def test_provider_no_non_us_support_raises_422(self) -> None:
        registry, _ = _make_registry(supports_non_us=False)
        req = ValidateRequestV1(
            components={"address_line_1": "10 Downing St", "city": "London"},
            country="GB",
        )
        with pytest.raises(APIError) as exc_info:
            await run_non_us_pipeline_v1(req, registry)
        assert exc_info.value.status_code == 422
        assert exc_info.value.error == "country_not_supported"

    @pytest.mark.asyncio
    async def test_valid_non_us_components_returns_pipeline_result(self) -> None:
        registry, provider = _make_registry()
        comps = {"address_line_1": "10 Downing St", "city": "London", "postal_code": "SW1A 2AA"}
        req = ValidateRequestV1(components=comps, country="GB")
        std, raw_input, returned_provider = await run_non_us_pipeline_v1(req, registry)

        assert std.country == "GB"
        assert std.components.spec == "raw"
        assert raw_input is not None
        assert json.loads(raw_input) == comps
        assert returned_provider is provider

    @pytest.mark.asyncio
    async def test_error_message_mentions_us_only(self) -> None:
        registry, _ = _make_registry()
        req = ValidateRequestV1(address="10 Downing St", country="GB")
        with pytest.raises(APIError) as exc_info:
            await run_non_us_pipeline_v1(req, registry)
        assert "US" in exc_info.value.message


# ---------------------------------------------------------------------------
# run_non_us_pipeline_v2
# ---------------------------------------------------------------------------


class TestRunNonUsPipelineV2:
    @pytest.mark.asyncio
    async def test_invalid_country_code_raises_422(self) -> None:
        registry, _ = _make_registry()
        req = ValidateRequestV1(address="1 Main St", country="XX")
        with pytest.raises(APIError) as exc_info:
            await run_non_us_pipeline_v2(req, registry, libpostal_client=None)
        assert exc_info.value.status_code == 422
        assert exc_info.value.error == "invalid_country_code"

    @pytest.mark.asyncio
    async def test_non_ca_raw_string_raises_422(self) -> None:
        registry, _ = _make_registry()
        req = ValidateRequestV1(address="10 Downing St", country="GB")
        with pytest.raises(APIError) as exc_info:
            await run_non_us_pipeline_v2(req, registry, libpostal_client=None)
        assert exc_info.value.status_code == 422
        assert exc_info.value.error == "country_not_supported"

    @pytest.mark.asyncio
    async def test_error_message_mentions_us_and_ca(self) -> None:
        registry, _ = _make_registry()
        req = ValidateRequestV1(address="10 Downing St", country="GB")
        with pytest.raises(APIError) as exc_info:
            await run_non_us_pipeline_v2(req, registry, libpostal_client=None)
        assert "CA" in exc_info.value.message

    @pytest.mark.asyncio
    async def test_provider_no_non_us_support_raises_422(self) -> None:
        registry, _ = _make_registry(supports_non_us=False)
        req = ValidateRequestV1(
            components={"address_line_1": "10 Downing St", "city": "London"},
            country="GB",
        )
        with pytest.raises(APIError) as exc_info:
            await run_non_us_pipeline_v2(req, registry, libpostal_client=None)
        assert exc_info.value.status_code == 422
        assert exc_info.value.error == "country_not_supported"

    @pytest.mark.asyncio
    async def test_valid_non_us_components_returns_pipeline_result(self) -> None:
        registry, provider = _make_registry()
        comps = {"address_line_1": "10 Downing St", "city": "London"}
        req = ValidateRequestV1(components=comps, country="GB")
        std, raw_input, returned_provider = await run_non_us_pipeline_v2(
            req, registry, libpostal_client=None
        )

        assert std.country == "GB"
        assert std.components.spec == "raw"
        assert raw_input is not None
        assert json.loads(raw_input) == comps
        assert returned_provider is provider

    @pytest.mark.asyncio
    async def test_ca_raw_string_calls_libpostal(self) -> None:
        registry, provider = _make_registry()
        libpostal_client = AsyncMock()
        parse_response = ParseResponseV1(
            input="123 Main St, Toronto ON M5V 1A1",
            country="CA",
            components=ComponentSet(
                spec="iso-19160-4",
                spec_version="1",
                values={
                    "address_line_1": "123 Main St",
                    "city": "Toronto",
                    "region": "ON",
                    "postal_code": "M5V 1A1",
                },
            ),
            type="Street Address",
        )

        with patch(
            "address_validator.services.validation.pipeline.parse_address",
            new=AsyncMock(return_value=parse_response),
        ):
            req = ValidateRequestV1(address="123 Main St, Toronto ON M5V 1A1", country="CA")
            _std, raw_input, returned_provider = await run_non_us_pipeline_v2(
                req, registry, libpostal_client=libpostal_client
            )

        assert raw_input == "123 Main St, Toronto ON M5V 1A1"
        assert returned_provider is provider

    @pytest.mark.asyncio
    async def test_ca_raw_string_libpostal_unavailable_raises_503(self) -> None:
        registry, _ = _make_registry()
        libpostal_client = AsyncMock()

        with patch(
            "address_validator.services.validation.pipeline.parse_address",
            new=AsyncMock(side_effect=LibpostalUnavailableError("sidecar down")),
        ):
            req = ValidateRequestV1(address="123 Main St, Toronto ON M5V 1A1", country="CA")
            with pytest.raises(APIError) as exc_info:
                await run_non_us_pipeline_v2(req, registry, libpostal_client=libpostal_client)

        assert exc_info.value.status_code == 503
        assert exc_info.value.error == "parsing_unavailable"


# ---------------------------------------------------------------------------
# run_us_pipeline
# ---------------------------------------------------------------------------


class TestRunUsPipeline:
    @pytest.mark.asyncio
    async def test_raw_address_returns_std_and_raw_input(self) -> None:
        registry, provider = _make_registry()
        req = ValidateRequestV1(address="123 Main St, Springfield, IL 62701", country="US")
        std, raw_input, returned_provider = await run_us_pipeline(req, registry)

        assert std.country == "US"
        assert raw_input == "123 Main St, Springfield, IL 62701"
        assert returned_provider is provider

    @pytest.mark.asyncio
    async def test_components_raw_input_is_json(self) -> None:
        registry, _ = _make_registry()
        comps = {
            "address_number": "123",
            "street_name": "MAIN",
            "street_suffix": "ST",
            "city": "SPRINGFIELD",
            "region": "IL",
            "postal_code": "62701",
        }
        req = ValidateRequestV1(components=comps, country="US")
        _std, raw_input, _ = await run_us_pipeline(req, registry)

        assert raw_input is not None
        assert json.loads(raw_input) == comps

    @pytest.mark.asyncio
    async def test_default_profile_is_usps_pub28(self) -> None:
        """Default component_profile should translate via usps-pub28 (v1 behavior)."""
        registry, _ = _make_registry()
        comps = {
            "address_number": "123",
            "street_name": "MAIN",
            "street_suffix": "ST",
            "city": "SPRINGFIELD",
            "region": "IL",
            "postal_code": "62701",
        }
        req = ValidateRequestV1(components=comps, country="US")
        std, _, _ = await run_us_pipeline(req, registry)
        # USPS pipeline should produce a standardized city value
        assert std.city != ""

    @pytest.mark.asyncio
    async def test_iso_profile_accepted(self) -> None:
        registry, _ = _make_registry()
        req = ValidateRequestV1(
            components={"address_line_1": "123 Main St", "city": "Springfield"},
            country="US",
        )
        # Should not raise
        await run_us_pipeline(req, registry, component_profile="iso-19160-4")
