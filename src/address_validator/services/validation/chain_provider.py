"""ChainProvider — tries providers in order, falling back on rate-limit errors.

Constructed by :class:`~services.validation.registry.ProviderRegistry` when
``VALIDATION_PROVIDER`` contains more than one comma-separated value.
Do not instantiate directly in application code.
"""

import logging

from address_validator.models import StandardizeResponseV1, ValidateResponseV1
from address_validator.services.validation.errors import (
    ProviderAtCapacityError,
    ProviderBadRequestError,
    ProviderRateLimitedError,
)
from address_validator.services.validation.protocol import ValidationProvider

logger = logging.getLogger(__name__)


class ChainProvider:
    """Tries each provider in order, falling back on rate-limit or capacity errors.

    On :class:`~services.validation.errors.ProviderRateLimitedError` or
    :class:`~services.validation.errors.ProviderAtCapacityError` from the
    current provider, the next provider in the chain is tried.  If all
    providers raise :class:`~services.validation.errors.ProviderRateLimitedError`
    or :class:`~services.validation.errors.ProviderAtCapacityError`,
    a final :class:`~services.validation.errors.ProviderRateLimitedError` with
    ``provider="all"`` is raised for the router to translate to HTTP 503.

    Any non-rate-limit, non-capacity exception (network error, unexpected 5xx,
    etc.) is re-raised immediately without trying further providers.

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

    async def validate(
        self, std: StandardizeResponseV1, *, raw_input: str | None = None
    ) -> ValidateResponseV1:
        _RecoverableError = (
            ProviderRateLimitedError,
            ProviderAtCapacityError,
            ProviderBadRequestError,
        )
        last_exc: (
            ProviderRateLimitedError | ProviderAtCapacityError | ProviderBadRequestError | None
        ) = None
        for provider in self._providers:
            name = type(provider).__name__
            try:
                return await provider.validate(std, raw_input=raw_input)
            except _RecoverableError as exc:
                last_exc = exc
                logger.warning(
                    "ChainProvider: %s unavailable (%s), trying next provider",
                    name,
                    type(exc).__name__,
                )
        if isinstance(last_exc, ProviderBadRequestError):
            raise ProviderBadRequestError("all", detail=last_exc.detail)
        retry_after = last_exc.retry_after_seconds if last_exc is not None else 0.0
        raise ProviderRateLimitedError("all", retry_after_seconds=retry_after)
