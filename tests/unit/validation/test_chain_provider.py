"""Unit tests for ChainProvider — fallback logic and error handling."""

from unittest.mock import AsyncMock

import pytest

from address_validator.models import (
    ComponentSet,
    StandardizeResponseV1,
    ValidateResponseV1,
    ValidationResult,
)
from address_validator.services.validation.chain_provider import ChainProvider
from address_validator.services.validation.errors import (
    ProviderAtCapacityError,
    ProviderRateLimitedError,
)
from address_validator.usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION

_CONFIRMED = ValidateResponseV1(
    country="US",
    validation=ValidationResult(status="confirmed", dpv_match_code="Y", provider="usps"),
)

_GOOGLE_CONFIRMED = ValidateResponseV1(
    country="US",
    validation=ValidationResult(status="confirmed", dpv_match_code="Y", provider="google"),
)


def _mock_provider(response: ValidateResponseV1) -> AsyncMock:
    p = AsyncMock()
    p.validate = AsyncMock(return_value=response)
    return p


def _rate_limited_provider() -> AsyncMock:
    p = AsyncMock()
    p.validate = AsyncMock(side_effect=ProviderRateLimitedError("usps"))
    return p


class TestChainProvider:
    @pytest.mark.asyncio
    async def test_returns_first_provider_result(self, std_address: object) -> None:
        chain = ChainProvider(providers=[_mock_provider(_CONFIRMED)])
        result = await chain.validate(std_address)  # type: ignore[arg-type]
        assert result.validation.status == "confirmed"

    @pytest.mark.asyncio
    async def test_falls_back_to_second_on_rate_limit(self, std_address: object) -> None:
        primary = _rate_limited_provider()
        secondary = _mock_provider(_GOOGLE_CONFIRMED)
        chain = ChainProvider(providers=[primary, secondary])

        result = await chain.validate(std_address)  # type: ignore[arg-type]
        assert result.validation.provider == "google"
        primary.validate.assert_awaited_once()
        secondary.validate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_when_all_providers_rate_limited(self, std_address: object) -> None:
        p1 = _rate_limited_provider()
        p2 = AsyncMock()
        p2.validate = AsyncMock(side_effect=ProviderRateLimitedError("google"))
        chain = ChainProvider(providers=[p1, p2])

        with pytest.raises(ProviderRateLimitedError) as exc_info:
            await chain.validate(std_address)  # type: ignore[arg-type]
        assert exc_info.value.provider == "all"

    @pytest.mark.asyncio
    async def test_retry_after_propagated_from_last_provider(self, std_address: object) -> None:
        p1 = AsyncMock()
        p1.validate = AsyncMock(
            side_effect=ProviderRateLimitedError("usps", retry_after_seconds=2.0)
        )
        p2 = AsyncMock()
        p2.validate = AsyncMock(
            side_effect=ProviderRateLimitedError("google", retry_after_seconds=5.5)
        )
        chain = ChainProvider(providers=[p1, p2])

        with pytest.raises(ProviderRateLimitedError) as exc_info:
            await chain.validate(std_address)  # type: ignore[arg-type]
        assert exc_info.value.retry_after_seconds == 5.5

    @pytest.mark.asyncio
    async def test_non_rate_limit_error_propagates_immediately(self, std_address: object) -> None:
        p1 = AsyncMock()
        p1.validate = AsyncMock(side_effect=ValueError("unexpected"))
        p2 = _mock_provider(_CONFIRMED)
        chain = ChainProvider(providers=[p1, p2])

        with pytest.raises(ValueError, match="unexpected"):
            await chain.validate(std_address)  # type: ignore[arg-type]
        # p2 must NOT have been called
        p2.validate.assert_not_awaited()

    def test_empty_provider_list_raises(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            ChainProvider(providers=[])

    @pytest.mark.asyncio
    async def test_single_provider_no_fallback_needed(self, std_address: object) -> None:
        chain = ChainProvider(providers=[_mock_provider(_CONFIRMED)])
        result = await chain.validate(std_address)  # type: ignore[arg-type]
        assert result is _CONFIRMED

    @pytest.mark.asyncio
    async def test_falls_back_to_second_on_at_capacity(self, std_address: object) -> None:
        primary = AsyncMock()
        primary.validate = AsyncMock(side_effect=ProviderAtCapacityError("usps"))
        secondary = _mock_provider(_GOOGLE_CONFIRMED)
        chain = ChainProvider(providers=[primary, secondary])

        result = await chain.validate(std_address)  # type: ignore[arg-type]
        assert result.validation.provider == "google"
        primary.validate.assert_awaited_once()
        secondary.validate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_all_when_all_providers_at_capacity(self, std_address: object) -> None:
        p1 = AsyncMock()
        p1.validate = AsyncMock(side_effect=ProviderAtCapacityError("usps"))
        p2 = AsyncMock()
        p2.validate = AsyncMock(side_effect=ProviderAtCapacityError("google"))
        chain = ChainProvider(providers=[p1, p2])

        with pytest.raises(ProviderRateLimitedError) as exc_info:
            await chain.validate(std_address)  # type: ignore[arg-type]
        assert exc_info.value.provider == "all"

    @pytest.mark.asyncio
    async def test_retry_after_propagated_from_at_capacity_error(self, std_address: object) -> None:
        p1 = AsyncMock()
        p1.validate = AsyncMock(
            side_effect=ProviderAtCapacityError("usps", retry_after_seconds=0.5)
        )
        p2 = AsyncMock()
        p2.validate = AsyncMock(
            side_effect=ProviderAtCapacityError("google", retry_after_seconds=2.0)
        )
        chain = ChainProvider(providers=[p1, p2])

        with pytest.raises(ProviderRateLimitedError) as exc_info:
            await chain.validate(std_address)  # type: ignore[arg-type]
        assert exc_info.value.retry_after_seconds == 2.0

    @pytest.mark.asyncio
    async def test_at_capacity_mixed_with_rate_limited_propagates_last(
        self, std_address: object
    ) -> None:
        p1 = AsyncMock()
        p1.validate = AsyncMock(
            side_effect=ProviderAtCapacityError("usps", retry_after_seconds=0.1)
        )
        p2 = AsyncMock()
        p2.validate = AsyncMock(
            side_effect=ProviderRateLimitedError("google", retry_after_seconds=3.0)
        )
        chain = ChainProvider(providers=[p1, p2])

        with pytest.raises(ProviderRateLimitedError) as exc_info:
            await chain.validate(std_address)  # type: ignore[arg-type]
        assert exc_info.value.retry_after_seconds == 3.0

    @pytest.mark.asyncio
    async def test_raw_input_threaded_to_provider(self, std_address) -> None:
        """ChainProvider must forward raw_input to each sub-provider."""
        provider = _mock_provider(_CONFIRMED)
        chain = ChainProvider(providers=[provider])

        await chain.validate(std_address, raw_input="123 Main St, Springfield IL")

        provider.validate.assert_awaited_once_with(
            std_address, raw_input="123 Main St, Springfield IL"
        )

    @pytest.mark.asyncio
    async def test_raw_input_threaded_on_fallback(self, std_address) -> None:
        """raw_input is passed to the fallback provider, not lost on retry."""
        first = _rate_limited_provider()
        second = _mock_provider(_GOOGLE_CONFIRMED)
        chain = ChainProvider(providers=[first, second])

        await chain.validate(std_address, raw_input="456 Elm Ave")

        second.validate.assert_awaited_once_with(std_address, raw_input="456 Elm Ave")


@pytest.fixture()
def std_address():
    """Minimal StandardizeResponseV1 for use in ChainProvider tests."""
    return StandardizeResponseV1(
        address_line_1="123 MAIN ST",
        address_line_2="",
        city="SPRINGFIELD",
        region="IL",
        postal_code="62701",
        country="US",
        standardized="123 MAIN ST  SPRINGFIELD, IL 62701",
        components=ComponentSet(
            spec=USPS_PUB28_SPEC,
            spec_version=USPS_PUB28_SPEC_VERSION,
            values={"address_line_1": "123 MAIN ST"},
        ),
        warnings=[],
    )
