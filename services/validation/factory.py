"""Provider factory -- reads env vars and returns the configured backend.

Environment variables
---------------------
VALIDATION_PROVIDER
    Which backend(s) to use.  Accepts a single value or a comma-separated
    ordered list (first = primary, rest = fallbacks tried on rate-limit).

    ``none`` (default)
        :class:`~services.validation.null_provider.NullProvider` -- returns
        ``validation.status='unavailable'`` without any network calls.  Safe
        default for development and environments without API credentials.

    ``usps``
        :class:`~services.validation.usps_provider.USPSProvider` -- calls
        the USPS Addresses API v3.  Requires ``USPS_CONSUMER_KEY`` and
        ``USPS_CONSUMER_SECRET``.

    ``google``
        :class:`~services.validation.google_provider.GoogleProvider` -- calls
        the Google Address Validation API.  Requires ``GOOGLE_API_KEY``.

    ``usps,google``
        USPS primary with Google fallback.  When USPS returns HTTP 429 after
        all retries, Google is tried.  If both are exhausted the router
        returns HTTP 503.

USPS_CONSUMER_KEY
    OAuth2 client ID from the USPS Developer Portal.  Required when
    ``usps`` appears in ``VALIDATION_PROVIDER``.

USPS_CONSUMER_SECRET
    OAuth2 client secret.  Required when ``usps`` appears in
    ``VALIDATION_PROVIDER``.

USPS_RATE_LIMIT_RPS
    Maximum USPS API requests per second.  Defaults to ``5.0`` (free-tier
    documented limit).

GOOGLE_API_KEY
    API key from the Google Cloud Console.  Required when ``google`` appears
    in ``VALIDATION_PROVIDER``.

GOOGLE_RATE_LIMIT_RPS
    Maximum Google API requests per second.  Defaults to ``25.0`` (standard
    per-project quota).
"""

import logging
import os

import httpx

from services.validation import cache_db
from services.validation.cache_provider import CachingProvider
from services.validation.chain_provider import ChainProvider
from services.validation.google_client import GoogleClient
from services.validation.google_provider import GoogleProvider
from services.validation.null_provider import NullProvider
from services.validation.protocol import ValidationProvider
from services.validation.usps_client import USPSClient
from services.validation.usps_provider import USPSProvider

logger = logging.getLogger(__name__)

# Module-level singletons -- created once, shared across all requests.
# The USPSClient holds the token cache and rate-limiter state; discarding
# it on every request would defeat both.
_http_client: httpx.AsyncClient | None = None
_usps_provider: USPSProvider | None = None
_google_provider: GoogleProvider | None = None
_caching_provider: CachingProvider | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client  # noqa: PLW0603
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _http_client


def _get_usps_provider(key: str, secret: str, rate_limit_rps: float) -> USPSProvider:
    """Return the shared :class:`USPSProvider` singleton, creating it if needed."""
    global _usps_provider  # noqa: PLW0603
    if _usps_provider is None:
        _usps_provider = USPSProvider(
            client=USPSClient(
                consumer_key=key,
                consumer_secret=secret,
                http_client=_get_http_client(),
                rate_limit_rps=rate_limit_rps,
            )
        )
    return _usps_provider


def _get_google_provider(api_key: str, rate_limit_rps: float) -> GoogleProvider:
    """Return the shared :class:`GoogleProvider` singleton, creating it if needed."""
    global _google_provider  # noqa: PLW0603
    if _google_provider is None:
        _google_provider = GoogleProvider(
            client=GoogleClient(
                api_key=api_key,
                http_client=_get_http_client(),
                rate_limit_rps=rate_limit_rps,
            )
        )
    return _google_provider


def _get_caching_provider(inner: ValidationProvider) -> CachingProvider:
    """Return the shared :class:`CachingProvider` singleton wrapping *inner*."""
    global _caching_provider  # noqa: PLW0603
    if _caching_provider is None:
        _caching_provider = CachingProvider(inner=inner, get_db=cache_db.get_db)
    return _caching_provider


def _build_single_provider(name: str) -> ValidationProvider:
    """Instantiate a single named provider, reading credentials from env."""
    if name == "usps":
        key = os.environ.get("USPS_CONSUMER_KEY", "").strip()
        secret = os.environ.get("USPS_CONSUMER_SECRET", "").strip()
        if not key or not secret:
            raise ValueError(
                "USPS_CONSUMER_KEY and USPS_CONSUMER_SECRET must be set "
                "when 'usps' appears in VALIDATION_PROVIDER"
            )
        rps = float(os.environ.get("USPS_RATE_LIMIT_RPS", "5.0"))
        logger.debug("get_provider: building USPSProvider (%.1f rps)", rps)
        return _get_usps_provider(key, secret, rps)

    if name == "google":
        api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "GOOGLE_API_KEY must be set when 'google' appears in VALIDATION_PROVIDER"
            )
        rps = float(os.environ.get("GOOGLE_RATE_LIMIT_RPS", "25.0"))
        logger.debug("get_provider: building GoogleProvider (%.1f rps)", rps)
        return _get_google_provider(api_key, rps)

    raise ValueError(
        f"Unknown provider name: '{name}'. Supported values: 'none', 'usps', 'google'."
    )


def _resolve_provider() -> ValidationProvider:
    """Resolve the configured inner provider(s) from env vars."""
    provider_str = os.environ.get("VALIDATION_PROVIDER", "none").strip().lower()

    names = [n.strip() for n in provider_str.split(",") if n.strip() and n.strip() != "none"]

    if not names:
        logger.debug("get_provider: using NullProvider")
        return NullProvider()

    providers = [_build_single_provider(n) for n in names]

    if len(providers) == 1:
        return providers[0]

    logger.debug("get_provider: building ChainProvider with %d providers", len(providers))
    return ChainProvider(providers=providers)


def get_provider() -> ValidationProvider:
    """Return the configured :class:`ValidationProvider`.

    The USPS and Google providers and their underlying HTTP client are
    module-level singletons so the token cache and rate-limiter state are
    shared across all requests.  NullProvider is stateless and is constructed
    cheaply on each call.

    Non-null providers are wrapped in a :class:`CachingProvider` that checks
    the local SQLite validation cache before delegating to the real backend.
    NullProvider is returned unwrapped — it returns ``status="unavailable"``
    and caching its results provides no benefit.

    When ``VALIDATION_PROVIDER`` contains a comma-separated list (e.g.
    ``usps,google``), a :class:`~services.validation.chain_provider.ChainProvider`
    is used as the inner provider.  The caching layer wraps the chain, so a
    cache hit bypasses all providers.
    """
    inner = _resolve_provider()
    if isinstance(inner, NullProvider):
        return inner
    return _get_caching_provider(inner)
