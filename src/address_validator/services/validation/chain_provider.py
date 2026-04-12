"""ChainProvider — tries providers in order, falling back on recoverable errors.

Constructed by :class:`~services.validation.registry.ProviderRegistry` when
``VALIDATION_PROVIDER`` contains more than one comma-separated value.
Do not instantiate directly in application code.
"""

import logging

from address_validator.models import StandardizedAddress, ValidateResponseV1
from address_validator.services.validation.errors import (
    ProviderAtCapacityError,
    ProviderBadRequestError,
    ProviderRateLimitedError,
)
from address_validator.services.validation.protocol import ValidationProvider

logger = logging.getLogger(__name__)


class ChainProvider:
    """Tries each provider in order, falling back on recoverable errors.

    On :class:`~services.validation.errors.ProviderRateLimitedError`,
    :class:`~services.validation.errors.ProviderAtCapacityError`, or
    :class:`~services.validation.errors.ProviderBadRequestError` from the
    current provider, the next provider in the chain is tried.

    When all providers fail:

    * If **any** provider raised a transient error (rate-limited / at-capacity),
      a :class:`~services.validation.errors.ProviderRateLimitedError` with
      ``provider="all"`` is raised — the caller should retry later.
    * If **every** provider raised
      :class:`~services.validation.errors.ProviderBadRequestError`, a
      ``ProviderBadRequestError("all")`` is raised — the input itself is
      the problem, not transient capacity.

    Any other exception (network error, unexpected 5xx, etc.) is re-raised
    immediately without trying further providers.

    Parameters
    ----------
    providers:
        Ordered list of :class:`~services.validation.protocol.ValidationProvider`
        instances.  Must contain at least one element.
    """

    def __init__(self, providers: list[ValidationProvider]) -> None:
        if not providers:
            raise ValueError("ChainProvider requires at least one provider")
        self._providers = providers

    @property
    def supports_non_us(self) -> bool:
        """True if any provider in the chain supports non-US address validation."""
        return any(p.supports_non_us for p in self._providers)

    async def validate(
        self, std: StandardizedAddress, *, raw_input: str | None = None
    ) -> ValidateResponseV1:
        last_transient: ProviderRateLimitedError | ProviderAtCapacityError | None = None
        last_bad_request: ProviderBadRequestError | None = None
        for provider in self._providers:
            name = type(provider).__name__
            try:
                return await provider.validate(std, raw_input=raw_input)
            except (ProviderRateLimitedError, ProviderAtCapacityError) as exc:
                last_transient = exc
                logger.warning(
                    "ChainProvider: %s unavailable (%s), trying next provider",
                    name,
                    type(exc).__name__,
                )
            except ProviderBadRequestError as exc:
                last_bad_request = exc
                logger.warning(
                    "ChainProvider: %s unavailable (%s), trying next provider",
                    name,
                    type(exc).__name__,
                )
        # Prefer transient error — caller can retry when capacity clears.
        if last_transient is not None:
            raise ProviderRateLimitedError(
                "all", retry_after_seconds=last_transient.retry_after_seconds
            )
        if last_bad_request is not None:
            raise ProviderBadRequestError("all", detail=last_bad_request.detail)
        raise ProviderRateLimitedError("all", retry_after_seconds=0.0)
