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
    """

    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__(f"Provider '{provider}' rate-limited after retries")
