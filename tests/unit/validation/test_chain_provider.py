"""Unit tests for ChainProvider — fallback logic and error handling."""

from unittest.mock import AsyncMock

import httpx
import pytest

from address_validator.core import warnings as warning_catalogue
from address_validator.models import (
    ComponentSet,
    StandardizeResponseV2,
    ValidateResponseV2,
    ValidationResult,
)
from address_validator.services.validation.chain_provider import ChainProvider
from address_validator.services.validation.errors import (
    ProviderAtCapacityError,
    ProviderBadRequestError,
    ProviderRateLimitedError,
    ProviderTransientError,
)
from address_validator.usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION

_CONFIRMED = ValidateResponseV2(
    country="US",
    validation=ValidationResult(status="confirmed", dpv_match_code="Y", provider="usps"),
)

_GOOGLE_CONFIRMED = ValidateResponseV2(
    country="US",
    validation=ValidationResult(status="confirmed", dpv_match_code="Y", provider="google"),
)


def _mock_provider(response: ValidateResponseV2) -> AsyncMock:
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
    async def test_falls_back_to_second_on_bad_request(self, std_address: object) -> None:
        primary = AsyncMock()
        primary.validate = AsyncMock(side_effect=ProviderBadRequestError("usps", detail="400"))
        secondary = _mock_provider(_GOOGLE_CONFIRMED)
        chain = ChainProvider(providers=[primary, secondary])

        result = await chain.validate(std_address)  # type: ignore[arg-type]
        assert result.validation.provider == "google"
        primary.validate.assert_awaited_once()
        secondary.validate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_bad_request_when_all_providers_reject(self, std_address: object) -> None:
        p1 = AsyncMock()
        p1.validate = AsyncMock(side_effect=ProviderBadRequestError("usps", detail="400"))
        p2 = AsyncMock()
        p2.validate = AsyncMock(side_effect=ProviderBadRequestError("google", detail="400"))
        chain = ChainProvider(providers=[p1, p2])

        with pytest.raises(ProviderBadRequestError) as exc_info:
            await chain.validate(std_address)  # type: ignore[arg-type]
        assert exc_info.value.provider == "all"

    @pytest.mark.asyncio
    async def test_rate_limited_then_bad_request_raises_rate_limited(
        self, std_address: object
    ) -> None:
        """Transient errors take precedence — caller can retry when capacity clears."""
        p1 = AsyncMock()
        p1.validate = AsyncMock(
            side_effect=ProviderRateLimitedError("usps", retry_after_seconds=2.0)
        )
        p2 = AsyncMock()
        p2.validate = AsyncMock(side_effect=ProviderBadRequestError("google", detail="400"))
        chain = ChainProvider(providers=[p1, p2])

        with pytest.raises(ProviderRateLimitedError) as exc_info:
            await chain.validate(std_address)  # type: ignore[arg-type]
        assert exc_info.value.provider == "all"
        assert exc_info.value.retry_after_seconds == 2.0

    @pytest.mark.asyncio
    async def test_bad_request_then_rate_limited_raises_rate_limited(
        self, std_address: object
    ) -> None:
        """Even if bad-request came first, transient error wins."""
        p1 = AsyncMock()
        p1.validate = AsyncMock(side_effect=ProviderBadRequestError("usps", detail="400"))
        p2 = AsyncMock()
        p2.validate = AsyncMock(
            side_effect=ProviderRateLimitedError("google", retry_after_seconds=5.0)
        )
        chain = ChainProvider(providers=[p1, p2])

        with pytest.raises(ProviderRateLimitedError) as exc_info:
            await chain.validate(std_address)  # type: ignore[arg-type]
        assert exc_info.value.provider == "all"
        assert exc_info.value.retry_after_seconds == 5.0

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

    @pytest.mark.asyncio
    async def test_falls_back_to_second_on_transient_error(self, std_address: object) -> None:
        """GH-115: ProviderTransientError (upstream 5xx) falls through like
        ProviderRateLimitedError and ProviderAtCapacityError."""
        primary = AsyncMock()
        primary.validate = AsyncMock(
            side_effect=ProviderTransientError("google", retry_after_seconds=1.0)
        )
        secondary = _mock_provider(_CONFIRMED)
        chain = ChainProvider(providers=[primary, secondary])

        result = await chain.validate(std_address)  # type: ignore[arg-type]
        assert result.validation.provider == "usps"
        primary.validate.assert_awaited_once()
        secondary.validate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_all_when_all_providers_transient(self, std_address: object) -> None:
        p1 = AsyncMock()
        p1.validate = AsyncMock(side_effect=ProviderTransientError("usps", retry_after_seconds=1.0))
        p2 = AsyncMock()
        p2.validate = AsyncMock(
            side_effect=ProviderTransientError("google", retry_after_seconds=2.5)
        )
        chain = ChainProvider(providers=[p1, p2])

        with pytest.raises(ProviderRateLimitedError) as exc_info:
            await chain.validate(std_address)  # type: ignore[arg-type]
        assert exc_info.value.provider == "all"
        assert exc_info.value.retry_after_seconds == 2.5

    @pytest.mark.asyncio
    async def test_transient_then_bad_request_raises_rate_limited(
        self, std_address: object
    ) -> None:
        """Transient (5xx) wins over bad-request — caller should retry later
        rather than treating the input as malformed."""
        p1 = AsyncMock()
        p1.validate = AsyncMock(side_effect=ProviderTransientError("google", retry_after_seconds=2))
        p2 = AsyncMock()
        p2.validate = AsyncMock(side_effect=ProviderBadRequestError("usps", detail="HTTP 401"))
        chain = ChainProvider(providers=[p1, p2])

        with pytest.raises(ProviderRateLimitedError):
            await chain.validate(std_address)  # type: ignore[arg-type]

    def test_supports_non_us_false_when_all_providers_false(self) -> None:
        p1 = AsyncMock()
        p1.supports_non_us = False
        p2 = AsyncMock()
        p2.supports_non_us = False
        chain = ChainProvider(providers=[p1, p2])
        assert chain.supports_non_us is False

    def test_supports_non_us_true_when_any_provider_true(self) -> None:
        p1 = AsyncMock()
        p1.supports_non_us = False
        p2 = AsyncMock()
        p2.supports_non_us = True
        chain = ChainProvider(providers=[p1, p2])
        assert chain.supports_non_us is True

    def test_supports_non_us_true_when_all_providers_true(self) -> None:
        p1 = AsyncMock()
        p1.supports_non_us = True
        p2 = AsyncMock()
        p2.supports_non_us = True
        chain = ChainProvider(providers=[p1, p2])
        assert chain.supports_non_us is True


@pytest.fixture()
def std_address():
    """Minimal StandardizeResponseV2 for use in ChainProvider tests."""
    return StandardizeResponseV2(
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


# -- GH #250: undetermined is a soft miss — try the next provider ------------

_USPS_UNDETERMINED = ValidateResponseV2(
    country="US",
    address_line_1="301 E HARBOR AVE",
    validation=ValidationResult(status="undetermined", provider="usps"),
)

_GOOGLE_UNDETERMINED = ValidateResponseV2(
    country="US",
    validation=ValidationResult(status="undetermined", provider="google"),
)


def _raising_provider(exc: Exception) -> AsyncMock:
    p = AsyncMock()
    p.validate = AsyncMock(side_effect=exc)
    return p


class TestChainUndetermined:
    @pytest.mark.asyncio
    async def test_undetermined_falls_through_to_next_provider(self, std_address: object) -> None:
        primary = _mock_provider(_USPS_UNDETERMINED)
        secondary = _mock_provider(_GOOGLE_CONFIRMED)
        chain = ChainProvider(providers=[primary, secondary])

        result = await chain.validate(std_address)  # type: ignore[arg-type]

        assert result.validation.provider == "google"
        assert result.validation.status == "confirmed"
        secondary.validate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_all_undetermined_returns_first_answer(self, std_address: object) -> None:
        chain = ChainProvider(
            providers=[_mock_provider(_USPS_UNDETERMINED), _mock_provider(_GOOGLE_UNDETERMINED)]
        )

        result = await chain.validate(std_address)  # type: ignore[arg-type]

        assert result.validation.status == "undetermined"
        assert result.validation.provider == "usps"
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_single_provider_undetermined_returned(self, std_address: object) -> None:
        chain = ChainProvider(providers=[_mock_provider(_USPS_UNDETERMINED)])

        result = await chain.validate(std_address)  # type: ignore[arg-type]

        assert result.validation.status == "undetermined"
        assert result.warnings == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "exc",
        [
            ProviderRateLimitedError("google", retry_after_seconds=5.0),
            ProviderAtCapacityError("google", retry_after_seconds=5.0),
            ProviderTransientError("google", retry_after_seconds=5.0),
        ],
    )
    async def test_fallback_transient_returns_undetermined_with_warning(
        self, exc: Exception, std_address: object
    ) -> None:
        """A 200 answer beats a 429: the undetermined answer is returned, flagged
        so the client (and the cache) know a retry may yield a determination."""
        chain = ChainProvider(
            providers=[_mock_provider(_USPS_UNDETERMINED), _raising_provider(exc)]
        )

        result = await chain.validate(std_address)  # type: ignore[arg-type]

        assert result.validation.status == "undetermined"
        assert result.validation.provider == "usps"
        assert result.warnings == [warning_catalogue.PROVIDER_FALLBACK_UNREACHABLE]

    @pytest.mark.asyncio
    async def test_transient_before_undetermined_also_warns(self, std_address: object) -> None:
        chain = ChainProvider(
            providers=[
                _raising_provider(ProviderRateLimitedError("usps")),
                _mock_provider(_GOOGLE_UNDETERMINED),
            ]
        )

        result = await chain.validate(std_address)  # type: ignore[arg-type]

        assert result.validation.provider == "google"
        assert result.warnings == [warning_catalogue.PROVIDER_FALLBACK_UNREACHABLE]

    @pytest.mark.asyncio
    async def test_fallback_bad_request_returns_undetermined_without_warning(
        self, std_address: object
    ) -> None:
        """A 400 is an answer about the input, not an outage — no retry hint."""
        chain = ChainProvider(
            providers=[
                _mock_provider(_USPS_UNDETERMINED),
                _raising_provider(ProviderBadRequestError("google", detail="HTTP 400")),
            ]
        )

        result = await chain.validate(std_address)  # type: ignore[arg-type]

        assert result.validation.status == "undetermined"
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_undetermined_preserves_existing_warnings(self, std_address: object) -> None:
        held = _USPS_UNDETERMINED.model_copy(update={"warnings": ["existing"]})
        chain = ChainProvider(
            providers=[_mock_provider(held), _raising_provider(ProviderRateLimitedError("google"))]
        )

        result = await chain.validate(std_address)  # type: ignore[arg-type]

        assert result.warnings == ["existing", warning_catalogue.PROVIDER_FALLBACK_UNREACHABLE]


class TestChainTransportErrors:
    """CR 9 (GH #250): a network failure from a fallback provider must not turn a
    held 200 answer into a 500; with nothing held it still propagates."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("exc", [httpx.ConnectError("boom"), httpx.ReadTimeout("slow")])
    async def test_fallback_transport_error_returns_held_with_warning(
        self, exc: Exception, std_address: object
    ) -> None:
        chain = ChainProvider(
            providers=[_mock_provider(_USPS_UNDETERMINED), _raising_provider(exc)]
        )

        result = await chain.validate(std_address)  # type: ignore[arg-type]

        assert result.validation.status == "undetermined"
        assert result.validation.provider == "usps"
        assert result.warnings == [warning_catalogue.PROVIDER_FALLBACK_UNREACHABLE]

    @pytest.mark.asyncio
    async def test_transport_error_with_nothing_held_propagates(self, std_address: object) -> None:
        chain = ChainProvider(
            providers=[
                _raising_provider(httpx.ConnectError("boom")),
                _mock_provider(_GOOGLE_CONFIRMED),
            ]
        )

        with pytest.raises(httpx.ConnectError):
            await chain.validate(std_address)  # type: ignore[arg-type]
