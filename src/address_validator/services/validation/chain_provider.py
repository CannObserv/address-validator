"""ChainProvider — tries providers in order, falling back on recoverable errors
and on ``undetermined`` answers.

Constructed by :class:`~services.validation.registry.ProviderRegistry` when
``VALIDATION_PROVIDER`` contains more than one comma-separated value.
Do not instantiate directly in application code.
"""

import logging

from address_validator.core import warnings as warning_catalogue
from address_validator.core.countries import US_POSTAL_COUNTRIES
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


class ChainProvider:
    """Tries each provider in order, falling back on recoverable errors.

    A provider with ``supports_non_us = False`` (USPS) is skipped for a country
    outside :data:`~core.countries.US_POSTAL_COUNTRIES` — it is neither asked
    nor counted as failed (GH #260).

    On any of the following errors from the current provider, the next
    provider in the chain is tried:

    * :class:`~services.validation.errors.ProviderRateLimitedError` (HTTP 429)
    * :class:`~services.validation.errors.ProviderAtCapacityError` (local quota)
    * :class:`~services.validation.errors.ProviderTransientError` (HTTP 5xx /
      unexpected non-2xx / failed request or credential refresh / unusable 2xx
      body — the clients wrap connect errors, timeouts and undecodable bodies,
      GH #257, and a 2xx body that is not a JSON object, GH #271)
    * :class:`~services.validation.errors.ProviderBadRequestError` (HTTP 400)

    An ``undetermined`` answer (HTTP 200, no determination — e.g. USPS blank
    DPV, GH #250) is a *soft* miss: it is held and the next provider is tried.
    With nothing held, the first determined answer wins. Once a US answer is
    held, only an answer with a DPV code replaces it: a verdict-only answer
    (Google US non-CASS ``invalid``/``not_found``, no DPV code) is a geocoder
    opinion, weaker than USPS's own no-determination (GH #258). Google's US
    non-CASS ``addressComplete`` is itself ``undetermined`` (GH #262), so with
    ``google,usps`` it is held and USPS is asked.
    Non-US answers never carry a DPV code, so any determined one replaces a
    held answer.
    If no provider determines the address, the first held ``undetermined``
    answer is returned — a 200 answer beats a 429 — and, when any provider
    failed transiently along the way, it carries
    :data:`~core.warnings.PROVIDER_FALLBACK_UNREACHABLE` so the client knows a
    retry may yield a determination (``CachingProvider`` does not cache it).

    When all providers fail:

    * If **any** provider raised a transient error (rate-limited / at-capacity
      / upstream 5xx / unreachable), a :class:`~services.validation.errors.ProviderRateLimitedError`
      with ``provider="all"`` is raised — the caller should retry later.
      Its ``retry_after_seconds`` is the *minimum* across the transient
      errors: the soonest any provider could answer, whatever the chain
      order (GH #270).
    * If **every** provider raised
      :class:`~services.validation.errors.ProviderBadRequestError`, a
      ``ProviderBadRequestError("all")`` is raised — the input itself is
      the problem, not transient capacity.

    Any other exception is re-raised immediately without trying further
    providers.  The list above is closed: the clients map every non-2xx
    response, every failed request (GH #257) and every 2xx body that is not
    a JSON object (GH #271) to one of those errors.  What they do not map
    still ends the chain as a 500 — e.g. a non-retryable Google
    credential-refresh failure (operator action), or a body whose nested
    field is not an object (GH #278).

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
        retry_after: float | None = None  # min across transient errors
        last_bad_request: ProviderBadRequestError | None = None
        held: ValidateResponseV2 | None = None
        unreachable = False  # any provider failed transiently
        for provider in self._eligible(std.country):
            name = type(provider).__name__
            try:
                result = await provider.validate(std, raw_input=raw_input)
            except (
                ProviderRateLimitedError,
                ProviderAtCapacityError,
                ProviderTransientError,
            ) as exc:
                retry_after = (
                    exc.retry_after_seconds
                    if retry_after is None
                    else min(retry_after, exc.retry_after_seconds)
                )
                unreachable = True
                logger.warning(
                    "ChainProvider: %s unavailable (%s, retry after %.0fs), trying next provider",
                    name,
                    type(exc).__name__,
                    exc.retry_after_seconds,
                )
            except ProviderBadRequestError as exc:
                last_bad_request = exc
                logger.warning(
                    "ChainProvider: %s unavailable (%s), trying next provider",
                    name,
                    type(exc).__name__,
                )
            else:
                if result.validation.status == UNDETERMINED:
                    logger.info("ChainProvider: %s undetermined", name)
                    if held is None:
                        held = result
                elif (
                    held is None
                    or std.country != "US"
                    or result.validation.dpv_match_code is not None
                ):
                    return result
                else:
                    # US verdict-only answer (no DPV code) — weaker than the held
                    # answer, so it does not replace it (GH #258). Non-US answers
                    # never carry a DPV code, so they are exempt.
                    logger.info(
                        "ChainProvider: %s answered %s without a DPV code, keeping undetermined",
                        name,
                        result.validation.status,
                    )
        return _exhausted(held, unreachable, retry_after, last_bad_request)

    def _eligible(self, country: str) -> list[ValidationProvider]:
        """Providers that serve *country*: US-only ones are dropped outside
        :data:`~core.countries.US_POSTAL_COUNTRIES` (GH #260)."""
        if country in US_POSTAL_COUNTRIES:
            return self._providers
        eligible = [p for p in self._providers if p.supports_non_us]
        if not eligible:
            # run_non_us_pipeline rejects this with a 422 before the chain is
            # reached; falling through to _exhausted would report it as a
            # retryable outage instead of the routing bug it is.
            raise ValueError(f"ChainProvider: no provider serves country {country!r}")
        logger.debug(
            "ChainProvider: skipping %d US-only provider(s) for country=%s",
            len(self._providers) - len(eligible),
            country,
        )
        return eligible


def _exhausted(
    held: ValidateResponseV2 | None,
    unreachable: bool,
    retry_after: float | None,
    last_bad_request: ProviderBadRequestError | None,
) -> ValidateResponseV2:
    """No provider gave a final answer: return the held answer, or raise."""
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
    if retry_after is not None:
        raise ProviderRateLimitedError("all", retry_after_seconds=retry_after)
    if last_bad_request is not None:
        raise ProviderBadRequestError("all", detail=last_bad_request.detail)
    raise ProviderRateLimitedError("all", retry_after_seconds=0.0)
