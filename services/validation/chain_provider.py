"""ChainProvider — tries providers in order, falling back on rate-limit errors.

Constructed by :func:`~services.validation.factory.get_provider` when
``VALIDATION_PROVIDER`` contains more than one comma-separated value.
Do not instantiate directly in application code.
"""

import logging

from models import StandardizeResponseV1, ValidateResponseV1
from services.validation.errors import ProviderRateLimitedError
from services.validation.protocol import ValidationProvider

logger = logging.getLogger(__name__)


class ChainProvider:
    """Tries each provider in order, falling back when one is rate-limited.

    On :class:`~services.validation.errors.ProviderRateLimitedError` from the
    current provider, the next provider in the chain is tried.  If all
    providers raise :class:`~services.validation.errors.ProviderRateLimitedError`,
    a final :class:`~services.validation.errors.ProviderRateLimitedError` with
    ``provider="all"`` is raised for the router to translate to HTTP 503.

    Any non-rate-limit exception (network error, unexpected 5xx, etc.) is
    re-raised immediately without trying further providers.

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

    async def validate(self, std: StandardizeResponseV1) -> ValidateResponseV1:
        for provider in self._providers:
            name = type(provider).__name__
            try:
                return await provider.validate(std)
            except ProviderRateLimitedError:
                logger.warning("ChainProvider: %s rate-limited, trying next provider", name)
        raise ProviderRateLimitedError("all")
