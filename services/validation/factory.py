"""Provider factory -- reads env vars and returns the configured backend.

Environment variables
---------------------
VALIDATION_PROVIDER
    Which backend to use.  Accepted values (case-insensitive):

    ``none`` (default)
        :class:`~services.validation.null_provider.NullProvider` -- returns
        ``validation_status='unavailable'`` without any network calls.  Safe
        default for development and environments without API credentials.

    ``usps``
        :class:`~services.validation.usps_provider.USPSProvider` -- calls
        the USPS Addresses API v3.  Requires ``USPS_CONSUMER_KEY`` and
        ``USPS_CONSUMER_SECRET``.

USPS_CONSUMER_KEY
    OAuth2 client ID from the USPS Developer Portal.  Required when
    ``VALIDATION_PROVIDER=usps``.

USPS_CONSUMER_SECRET
    OAuth2 client secret.  Required when ``VALIDATION_PROVIDER=usps``.
"""

import logging
import os

import httpx

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


def _get_http_client() -> httpx.AsyncClient:
    global _http_client  # noqa: PLW0603
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _http_client


def _get_usps_provider(key: str, secret: str) -> USPSProvider:
    """Return the shared :class:`USPSProvider` singleton, creating it if needed."""
    global _usps_provider  # noqa: PLW0603
    if _usps_provider is None:
        _usps_provider = USPSProvider(
            client=USPSClient(
                consumer_key=key,
                consumer_secret=secret,
                http_client=_get_http_client(),
            )
        )
    return _usps_provider


def get_provider() -> ValidationProvider:
    """Return the configured :class:`ValidationProvider`.

    The USPS provider and its underlying HTTP client are module-level
    singletons so the token cache and rate-limiter state are shared
    across all requests.  NullProvider is stateless and is constructed
    cheaply on each call.
    """
    provider_name = os.environ.get("VALIDATION_PROVIDER", "none").strip().lower()

    if provider_name in ("none", ""):
        logger.debug("get_provider: using NullProvider")
        return NullProvider()

    if provider_name == "usps":
        key = os.environ.get("USPS_CONSUMER_KEY", "").strip()
        secret = os.environ.get("USPS_CONSUMER_SECRET", "").strip()
        if not key or not secret:
            raise ValueError(
                "USPS_CONSUMER_KEY and USPS_CONSUMER_SECRET must be set "
                "when VALIDATION_PROVIDER=usps"
            )
        logger.debug("get_provider: using USPSProvider")
        return _get_usps_provider(key, secret)

    raise ValueError(
        f"Unknown VALIDATION_PROVIDER value: '{provider_name}'. Supported values: 'none', 'usps'."
    )
