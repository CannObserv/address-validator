"""Sentinel exceptions for the validation provider layer."""


class ProviderRateLimitedError(Exception):
    """Raised by a provider client after all HTTP 429 retries are exhausted.

    :class:`~services.validation.chain_provider.ChainProvider` catches this to
    try the next provider in the chain.  If all providers raise it, the router
    catches the final instance and returns HTTP 429.

    Parameters
    ----------
    provider:
        Short name of the provider that was exhausted (e.g. ``"usps"``,
        ``"google"``, or ``"all"`` when the chain is exhausted).
    retry_after_seconds:
        How long (in seconds) the caller should wait before retrying.
        Set to the last backoff delay computed by the client.  Defaults to
        ``0.0`` when no delay information is available (e.g. synthetic errors
        raised in tests).
    """

    def __init__(self, provider: str, retry_after_seconds: float = 0.0) -> None:
        self.provider = provider
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Provider '{provider}' rate-limited after retries")


class ProviderAtCapacityError(Exception):
    """Raised by :class:`~services.validation._rate_limit.QuotaGuard` when a
    request cannot be dispatched within the configured latency budget, or when
    a hard quota window is exhausted.

    Semantically distinct from :class:`ProviderRateLimitedError`: this error
    means the request was *not sent* (local capacity decision), whereas
    ``ProviderRateLimitedError`` means the upstream API returned HTTP 429.

    :class:`~services.validation.chain_provider.ChainProvider` catches both
    and advances to the next provider.

    Parameters
    ----------
    provider:
        Short name of the provider (e.g. ``"usps"``, ``"google"``).
    retry_after_seconds:
        Hint for how long to wait before retrying.  Defaults to ``0.0``.
    """

    def __init__(self, provider: str, retry_after_seconds: float = 0.0) -> None:
        self.provider = provider
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Provider '{provider}' at local capacity")
