"""ChainProvider — tries providers in order, falling back on recoverable errors
and on ``undetermined`` answers.

Constructed by :class:`~services.validation.registry.ProviderRegistry` when
``VALIDATION_PROVIDER`` contains more than one comma-separated value.
Do not instantiate directly in application code.
"""

import logging

import httpx

from address_validator.core import warnings as warning_catalogue
from address_validator.core.validation_status import UNDETERMINED
from address_validator.models import StandardizedAddress, ValidateResponseV2
from address_validator.services.validation.errors import (
    ProviderAtCapacityError,
    ProviderBadRequestError,
    ProviderRateLimitedError,
    ProviderTransientError,
)
from address_validator.services.validation.protocol import ValidationProvider

logger = logging.getLogger(__name__)

_TransientErr = ProviderRateLimitedError | ProviderAtCapacityError | ProviderTransientError


class ChainProvider:
    """Tries each provider in order, falling back on recoverable errors.

    On any of the following errors from the current provider, the next
    provider in the chain is tried:

    * :class:`~services.validation.errors.ProviderRateLimitedError` (HTTP 429)
    * :class:`~services.validation.errors.ProviderAtCapacityError` (local quota)
    * :class:`~services.validation.errors.ProviderTransientError` (HTTP 5xx /
      unexpected non-2xx)
    * :class:`~services.validation.errors.ProviderBadRequestError` (HTTP 400)

    An ``undetermined`` answer (HTTP 200, no determination — e.g. USPS blank
    DPV, GH #250) is a *soft* miss: it is held and the next provider is tried.
    The first determined answer wins. If no provider determines the address,
    the first held ``undetermined`` answer is returned — a 200 answer beats a
    429 — and, when any provider failed transiently along the way, it carries
    :data:`~core.warnings.PROVIDER_FALLBACK_UNREACHABLE` so the client knows a
    retry may yield a determination (``CachingProvider`` does not cache it).

    When all providers fail:

    * If **any** provider raised a transient error (rate-limited / at-capacity
      / upstream 5xx), a :class:`~services.validation.errors.ProviderRateLimitedError`
      with ``provider="all"`` is raised — the caller should retry later.
    * If **every** provider raised
      :class:`~services.validation.errors.ProviderBadRequestError`, a
      ``ProviderBadRequestError("all")`` is raised — the input itself is
      the problem, not transient capacity.

    Any other exception (network error, programming bug, etc.) is re-raised
    immediately without trying further providers — except a network error
    (``httpx.TransportError``) raised after an ``undetermined`` answer is held,
    which counts as transient so the held 200 answer is still returned.

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
    ) -> ValidateResponseV2:
        last_transient: _TransientErr | None = None
        last_bad_request: ProviderBadRequestError | None = None
        held: ValidateResponseV2 | None = None
        unreachable = False  # any provider failed transiently or at the transport layer
        for provider in self._providers:
            name = type(provider).__name__
            try:
                result = await provider.validate(std, raw_input=raw_input)
            except (
                ProviderRateLimitedError,
                ProviderAtCapacityError,
                ProviderTransientError,
            ) as exc:
                last_transient = exc
                unreachable = True
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
            except httpx.TransportError as exc:
                # Network failure (connect error, timeout) — not wrapped by the
                # clients. With nothing held it propagates as before; once a 200
                # answer is held it must not turn that answer into a 500 (GH #250).
                if held is None:
                    raise
                unreachable = True
                logger.warning(
                    "ChainProvider: %s unreachable (%s), keeping undetermined answer",
                    name,
                    type(exc).__name__,
                )
            else:
                if result.validation.status != UNDETERMINED:
                    return result
                logger.info("ChainProvider: %s undetermined", name)
                if held is None:
                    held = result
        if held is not None:
            if unreachable:
                return held.model_copy(
                    update={
                        "warnings": [
                            *held.warnings,
                            warning_catalogue.PROVIDER_FALLBACK_UNREACHABLE,
                        ]
                    }
                )
            return held
        # Prefer transient error — caller can retry when capacity clears.
        if last_transient is not None:
            raise ProviderRateLimitedError(
                "all", retry_after_seconds=last_transient.retry_after_seconds
            )
        if last_bad_request is not None:
            raise ProviderBadRequestError("all", detail=last_bad_request.detail)
        raise ProviderRateLimitedError("all", retry_after_seconds=0.0)
