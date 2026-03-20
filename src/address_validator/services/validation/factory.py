"""Provider factory -- reads env vars and returns the configured backend.

Call :func:`validate_config` from the FastAPI lifespan startup hook to catch
misconfiguration at startup rather than on the first request.

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
        the Google Address Validation API.  Uses Application Default
        Credentials (ADC); no additional env var required.

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
    Maximum USPS API requests per second.  Must be a positive number.
    Defaults to ``5.0`` (free-tier documented limit).

GOOGLE_RATE_LIMIT_RPM
    Maximum Google API requests per minute.  Must be a positive integer.
    Defaults to ``5``.

GOOGLE_DAILY_LIMIT
    Maximum Google API requests per day (hard quota).  Must be a positive
    integer.  Defaults to ``160``.

USPS_DAILY_LIMIT
    Maximum USPS API requests per day.  Must be a positive integer.
    Defaults to ``10000``.

VALIDATION_LATENCY_BUDGET_S
    Maximum seconds a request may be queued waiting for rate-limit tokens
    before raising ProviderAtCapacityError.  Must be a positive number.
    Defaults to ``1.0``.

VALIDATION_CACHE_DSN
    PostgreSQL connection string for the validation cache.
    Example: postgresql+asyncpg://user:pass@localhost/address_validator
    Required when a non-null provider is configured.

VALIDATION_CACHE_TTL_DAYS
    Days before a cached result is treated as expired and re-validated via
    the live provider.  Default ``30``.  Set to ``0`` to disable expiry.
"""

import logging
import os

import httpx
from google.cloud import cloudquotas_v1, monitoring_v3

from address_validator.services.validation import cache_db
from address_validator.services.validation._rate_limit import (
    FixedResetQuotaWindow,
    QuotaGuard,
    QuotaWindow,
)
from address_validator.services.validation.cache_provider import CachingProvider
from address_validator.services.validation.chain_provider import ChainProvider
from address_validator.services.validation.gcp_auth import get_credentials, resolve_project_id
from address_validator.services.validation.gcp_quota_sync import (
    fetch_daily_limit,
    fetch_daily_usage,
)
from address_validator.services.validation.google_client import GoogleClient
from address_validator.services.validation.google_provider import GoogleProvider
from address_validator.services.validation.null_provider import NullProvider
from address_validator.services.validation.protocol import ValidationProvider
from address_validator.services.validation.usps_client import USPSClient
from address_validator.services.validation.usps_provider import USPSProvider

logger = logging.getLogger(__name__)

# Module-level singletons -- created once, shared across all requests.
# The USPSClient holds the token cache and rate-limiter state; discarding
# it on every request would defeat both.
_http_client: httpx.AsyncClient | None = None
_usps_provider: USPSProvider | None = None
_google_provider: GoogleProvider | None = None
_caching_provider: CachingProvider | None = None
_reconciliation_params: dict | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client  # noqa: PLW0603
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _http_client


def _get_usps_provider(
    key: str, secret: str, rps: float, daily_limit: int, latency_budget_s: float
) -> USPSProvider:
    global _usps_provider  # noqa: PLW0603
    if _usps_provider is None:
        logger.debug(
            "get_provider: creating USPSProvider singleton (%.1f rps, %d/day)", rps, daily_limit
        )
        guard = QuotaGuard(
            windows=[
                QuotaWindow(limit=int(rps), duration_s=1.0, mode="soft"),
                QuotaWindow(limit=daily_limit, duration_s=86_400.0, mode="soft"),
            ],
            latency_budget_s=latency_budget_s,
            provider_name="usps",
        )
        _usps_provider = USPSProvider(
            client=USPSClient(
                consumer_key=key,
                consumer_secret=secret,
                http_client=_get_http_client(),
                quota_guard=guard,
            )
        )
    return _usps_provider


def _get_google_provider(rpm: int, daily_limit: int, latency_budget_s: float) -> GoogleProvider:
    global _google_provider  # noqa: PLW0603
    global _reconciliation_params  # noqa: PLW0603
    if _google_provider is None:
        logger.debug(
            "get_provider: creating GoogleProvider singleton (%d rpm, %d/day)", rpm, daily_limit
        )
        credentials, adc_project = get_credentials()
        project_id = resolve_project_id(adc_project)

        if project_id:
            try:
                quotas_client = cloudquotas_v1.CloudQuotasClient(credentials=credentials)
                discovered = fetch_daily_limit(quotas_client, project_id)
                if discovered is not None:
                    daily_limit = discovered
            except Exception:
                logger.warning(
                    "get_provider: Cloud Quotas API unavailable, using configured limit=%d",
                    daily_limit,
                )
        else:
            logger.warning(
                "get_provider: GCP project ID not resolved — quota sync features disabled"
            )

        guard = QuotaGuard(
            windows=[
                QuotaWindow(limit=rpm, duration_s=60.0, mode="soft"),
                FixedResetQuotaWindow(limit=daily_limit, mode="hard"),
            ],
            latency_budget_s=latency_budget_s,
            provider_name="google",
        )

        monitoring_client = None
        if project_id:
            try:
                monitoring_client = monitoring_v3.MetricServiceClient(credentials=credentials)
                usage = fetch_daily_usage(monitoring_client, project_id)
                if usage is not None and usage > 0:
                    guard.adjust_tokens(1, -usage)
                    logger.info(
                        "get_provider: seeded daily quota from Monitoring"
                        " (used=%d, remaining=%d)",
                        usage,
                        daily_limit - usage,
                    )
            except Exception:
                monitoring_client = None
                logger.warning(
                    "get_provider: Cloud Monitoring API unavailable, starting with full bucket"
                )

        if project_id and monitoring_client:
            interval_s = float(os.environ.get("GOOGLE_QUOTA_RECONCILE_INTERVAL_S", "900"))
            _reconciliation_params = {
                "guard": guard,
                "daily_window_index": 1,
                "monitoring_client": monitoring_client,
                "project_id": project_id,
                "interval_s": interval_s,
            }

        _google_provider = GoogleProvider(
            client=GoogleClient(
                credentials=credentials,
                http_client=_get_http_client(),
                quota_guard=guard,
            )
        )
    return _google_provider


def get_reconciliation_params() -> dict | None:
    """Return reconciliation loop parameters if Google provider is active."""
    return _reconciliation_params


def _get_caching_provider(inner: ValidationProvider) -> CachingProvider:
    """Return the shared :class:`CachingProvider` singleton wrapping *inner*."""
    global _caching_provider  # noqa: PLW0603
    if _caching_provider is None:
        try:
            ttl_days = int(os.environ.get("VALIDATION_CACHE_TTL_DAYS", "30"))
        except ValueError:
            raise ValueError(
                "VALIDATION_CACHE_TTL_DAYS must be a non-negative integer "
                "(e.g. '30'); use 0 to disable expiry"
            ) from None
        if ttl_days < 0:
            raise ValueError(
                "VALIDATION_CACHE_TTL_DAYS must be a non-negative integer "
                "(e.g. '30'); use 0 to disable expiry"
            )
        logger.debug("get_provider: cache TTL=%d days (0=disabled)", ttl_days)
        _caching_provider = CachingProvider(
            inner=inner, get_engine=cache_db.get_engine, ttl_days=ttl_days
        )
    return _caching_provider


def _parse_usps_config() -> tuple[str, str, float, int]:
    """Read, validate, and return ``(key, secret, rps, daily_limit)``."""
    key = os.environ.get("USPS_CONSUMER_KEY", "").strip()
    secret = os.environ.get("USPS_CONSUMER_SECRET", "").strip()
    if not key or not secret:
        raise ValueError(
            "USPS_CONSUMER_KEY and USPS_CONSUMER_SECRET must be set "
            "when 'usps' appears in VALIDATION_PROVIDER"
        )
    try:
        rps = float(os.environ.get("USPS_RATE_LIMIT_RPS", "5.0"))
    except ValueError:
        raise ValueError("USPS_RATE_LIMIT_RPS must be a number >= 1 (e.g. '5.0')") from None
    if rps < 1:
        raise ValueError("USPS_RATE_LIMIT_RPS must be a number >= 1 (e.g. '5.0')")
    try:
        daily_limit = int(os.environ.get("USPS_DAILY_LIMIT", "10000"))
    except ValueError:
        raise ValueError("USPS_DAILY_LIMIT must be a positive integer (e.g. '10000')") from None
    if daily_limit <= 0:
        raise ValueError("USPS_DAILY_LIMIT must be a positive integer (e.g. '10000')")
    return key, secret, rps, daily_limit


def _parse_google_config() -> tuple[int, int]:
    """Read, validate, and return ``(rpm, daily_limit)``."""
    try:
        rpm = int(os.environ.get("GOOGLE_RATE_LIMIT_RPM", "5"))
    except ValueError:
        raise ValueError("GOOGLE_RATE_LIMIT_RPM must be a positive integer (e.g. '5')") from None
    if rpm <= 0:
        raise ValueError("GOOGLE_RATE_LIMIT_RPM must be a positive integer (e.g. '5')")
    try:
        daily_limit = int(os.environ.get("GOOGLE_DAILY_LIMIT", "160"))
    except ValueError:
        raise ValueError("GOOGLE_DAILY_LIMIT must be a positive integer (e.g. '160')") from None
    if daily_limit <= 0:
        raise ValueError("GOOGLE_DAILY_LIMIT must be a positive integer (e.g. '160')")
    return rpm, daily_limit


def _parse_latency_budget() -> float:
    """Read and validate ``VALIDATION_LATENCY_BUDGET_S``."""
    try:
        budget = float(os.environ.get("VALIDATION_LATENCY_BUDGET_S", "1.0"))
    except ValueError:
        raise ValueError(
            "VALIDATION_LATENCY_BUDGET_S must be a positive number (e.g. '1.0')"
        ) from None
    if budget <= 0:
        raise ValueError("VALIDATION_LATENCY_BUDGET_S must be a positive number (e.g. '1.0')")
    return budget


def _check_provider_config(name: str) -> None:
    """Raise :exc:`ValueError` if env-var credentials for *name* are absent or malformed.

    Validates credentials and rate-limit values without constructing any
    objects or making network calls.  Called by :func:`validate_config` at
    startup; :func:`_build_single_provider` calls the provider-specific
    parsers directly to avoid re-reading env vars.
    """
    if name == "usps":
        _parse_usps_config()
    elif name == "google":
        _parse_google_config()
        get_credentials()  # validates ADC is available
    else:
        raise ValueError(
            f"Unknown provider name: '{name}'. Supported values: 'none', 'usps', 'google'."
        )


def _build_single_provider(name: str) -> ValidationProvider:
    """Instantiate a single named provider, reading credentials from env."""
    budget = _parse_latency_budget()
    if name == "usps":
        key, secret, rps, daily_limit = _parse_usps_config()
        return _get_usps_provider(key, secret, rps, daily_limit, budget)

    if name == "google":
        rpm, daily_limit = _parse_google_config()
        return _get_google_provider(rpm, daily_limit, budget)

    raise ValueError(
        f"Unknown provider name: '{name}'. Supported values: 'none', 'usps', 'google'."
    )


def _resolve_provider() -> ValidationProvider:
    """Resolve the configured inner provider(s) from env vars."""
    provider_str = os.environ.get("VALIDATION_PROVIDER", "none").strip().lower()

    names = [s for n in provider_str.split(",") if (s := n.strip()) and s != "none"]

    if not names:
        logger.debug("get_provider: using NullProvider")
        return NullProvider()

    providers = [_build_single_provider(n) for n in names]

    if len(providers) == 1:
        return providers[0]

    logger.debug("get_provider: building ChainProvider with %d providers", len(providers))
    return ChainProvider(providers=providers)


def validate_config() -> None:
    """Validate provider configuration from env vars without making network calls.

    Reads the same env vars as :func:`get_provider` and checks that all
    required credentials are present and well-formed.  Logs at INFO which
    provider is active.  Raises :exc:`ValueError` on misconfiguration so the
    FastAPI lifespan hook can surface the error at startup rather than on the
    first request.
    """
    provider_str = os.environ.get("VALIDATION_PROVIDER", "none").strip().lower()
    names = [s for n in provider_str.split(",") if (s := n.strip()) and s != "none"]

    if not names:
        logger.info("validate_config: provider=none")
        return

    for name in names:
        _check_provider_config(name)

    _parse_latency_budget()

    cache_dsn = os.environ.get("VALIDATION_CACHE_DSN", "").strip()
    if not cache_dsn:
        raise ValueError(
            "VALIDATION_CACHE_DSN must be set when a non-null validation provider is configured "
            "(e.g. 'postgresql+asyncpg://user:pass@localhost/address_validator')"
        )

    ttl_str = os.environ.get("VALIDATION_CACHE_TTL_DAYS", "30")
    try:
        ttl_days = int(ttl_str)
    except ValueError:
        raise ValueError(
            "VALIDATION_CACHE_TTL_DAYS must be a non-negative integer "
            "(e.g. '30'); use 0 to disable expiry"
        ) from None
    if ttl_days < 0:
        raise ValueError(
            "VALIDATION_CACHE_TTL_DAYS must be a non-negative integer "
            "(e.g. '30'); use 0 to disable expiry"
        )

    logger.info("validate_config: provider=%s ttl=%d days", ",".join(names), ttl_days)


def get_provider() -> ValidationProvider:
    """Return the configured :class:`ValidationProvider`.

    The USPS and Google providers and their underlying HTTP client are
    module-level singletons so the token cache and rate-limiter state are
    shared across all requests.  NullProvider is stateless and is constructed
    cheaply on each call.

    Non-null providers are wrapped in a :class:`CachingProvider` that checks
    the PostgreSQL validation cache before delegating to the real backend.
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
