"""ProviderRegistry — singleton-free provider lifecycle management.

Replaces the module-level globals in the former ``factory.py``.  A single
instance is created in the FastAPI lifespan and stored on ``app.state``.
"""

import logging

import httpx
from pydantic import ValidationError

from address_validator.db import engine as db_engine
from address_validator.services.validation._rate_limit import (
    FixedResetQuotaWindow,
    QuotaGuard,
    QuotaWindow,
)
from address_validator.services.validation.cache_provider import CachingProvider
from address_validator.services.validation.chain_provider import ChainProvider
from address_validator.services.validation.config import (
    _SUPPORTED_PROVIDERS,
    GoogleConfig,
    USPSConfig,
    ValidationConfig,
    settings_error,
)
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


class ProviderRegistry:
    """Manages provider construction and lifecycle.

    All state lives on the instance — no module-level globals.
    """

    def __init__(self, config: ValidationConfig) -> None:
        self._config = config
        self._provider: ValidationProvider | None = None
        self._http_client: httpx.AsyncClient | None = None
        self._usps_provider: USPSProvider | None = None
        self._google_provider: GoogleProvider | None = None
        self._reconciliation_params: dict | None = None

    def get_provider(self) -> ValidationProvider:
        """Return the configured provider (lazy singleton on this instance)."""
        if self._provider is None:
            self._provider = self._build_provider()
        return self._provider

    def get_reconciliation_params(self) -> dict | None:
        """Return reconciliation loop parameters if Google provider is active."""
        return self._reconciliation_params

    def get_quota_info(self) -> list[dict]:
        """Return current quota state for each active provider."""
        quota: list[dict] = []
        for name, prov in [("usps", self._usps_provider), ("google", self._google_provider)]:
            if prov is None:
                continue
            state = prov.client.quota_guard.get_daily_quota_state()
            if state:
                quota.append({"provider": name, **state})
        return quota

    async def close(self) -> None:
        """Close the shared HTTP client."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    # -- Private construction methods ------------------------------------------

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(15.0, connect=5.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._http_client

    def _build_provider(self) -> ValidationProvider:
        names = self._config.provider_names
        if not names:
            logger.debug("get_provider: using NullProvider")
            return NullProvider()

        providers = [self._build_single_provider(n) for n in names]

        if len(providers) == 1:
            inner = providers[0]
        else:
            logger.debug("get_provider: building ChainProvider with %d providers", len(providers))
            inner = ChainProvider(providers=providers)

        return self._build_caching_provider(inner)

    def _build_single_provider(self, name: str) -> ValidationProvider:
        budget = self._config.latency_budget_s
        if name == "usps":
            try:
                cfg = USPSConfig()
            except ValidationError as exc:
                raise settings_error(exc, "USPS_") from None
            return self._build_usps_provider(cfg, budget)
        if name == "google":
            try:
                cfg = GoogleConfig()
            except ValidationError as exc:
                raise settings_error(exc, "GOOGLE_") from None
            return self._build_google_provider(cfg, budget)
        raise ValueError(
            f"Unknown provider name: '{name}'. "
            f"Supported values: {', '.join(repr(p) for p in _SUPPORTED_PROVIDERS)}."
        )

    def _build_usps_provider(self, cfg: USPSConfig, latency_budget_s: float) -> USPSProvider:
        if self._usps_provider is not None:
            return self._usps_provider
        logger.debug(
            "get_provider: creating USPSProvider singleton (%.1f rps, %d/day)",
            cfg.rate_limit_rps,
            cfg.daily_limit,
        )
        guard = QuotaGuard(
            windows=[
                QuotaWindow(limit=int(cfg.rate_limit_rps), duration_s=1.0, mode="soft"),
                QuotaWindow(limit=cfg.daily_limit, duration_s=86_400.0, mode="soft"),
            ],
            latency_budget_s=latency_budget_s,
            provider_name="usps",
        )
        self._usps_provider = USPSProvider(
            client=USPSClient(
                consumer_key=cfg.consumer_key,
                consumer_secret=cfg.consumer_secret,
                http_client=self._get_http_client(),
                quota_guard=guard,
            )
        )
        return self._usps_provider

    def _build_google_provider(self, cfg: GoogleConfig, latency_budget_s: float) -> GoogleProvider:
        if self._google_provider is not None:
            return self._google_provider

        logger.debug(
            "get_provider: creating GoogleProvider singleton (%d rpm, %d/day)",
            cfg.rate_limit_rpm,
            cfg.daily_limit,
        )
        credentials, adc_project = get_credentials()
        project_id = resolve_project_id(adc_project)

        daily_limit = self._discover_google_quota(credentials, project_id, cfg)

        guard = QuotaGuard(
            windows=[
                QuotaWindow(limit=cfg.rate_limit_rpm, duration_s=60.0, mode="soft"),
                FixedResetQuotaWindow(limit=daily_limit, mode="hard"),
            ],
            latency_budget_s=latency_budget_s,
            provider_name="google",
        )

        self._setup_reconciliation(guard, credentials, project_id, cfg)

        self._google_provider = GoogleProvider(
            client=GoogleClient(
                credentials=credentials,
                http_client=self._get_http_client(),
                quota_guard=guard,
            )
        )
        return self._google_provider

    def _discover_google_quota(
        self, credentials: object, project_id: str | None, cfg: GoogleConfig
    ) -> int:
        """Discover daily limit from Cloud Quotas API, falling back to config."""
        daily_limit = cfg.daily_limit
        if project_id:
            try:
                from google.cloud import cloudquotas_v1  # noqa: PLC0415

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
        return daily_limit

    def _setup_reconciliation(
        self,
        guard: QuotaGuard,
        credentials: object,
        project_id: str | None,
        cfg: GoogleConfig,
    ) -> None:
        """Wire up monitoring client and reconciliation params."""
        if not project_id:
            return

        monitoring_client = None
        try:
            from google.cloud import monitoring_v3  # noqa: PLC0415

            monitoring_client = monitoring_v3.MetricServiceClient(credentials=credentials)
            usage = fetch_daily_usage(monitoring_client, project_id)
            if usage is not None and usage > 0:
                guard.adjust_tokens(1, -usage)
                state = guard.get_daily_quota_state()
                daily_limit = state["limit"] if state else 0
                logger.info(
                    "get_provider: seeded daily quota from Monitoring (used=%d, remaining=%d)",
                    usage,
                    daily_limit - usage,
                )
        except Exception:
            monitoring_client = None
            logger.warning(
                "get_provider: Cloud Monitoring API unavailable, starting with full bucket"
            )

        if monitoring_client:
            interval_s = cfg.quota_reconcile_interval_s
            self._reconciliation_params = {
                "guard": guard,
                "daily_window_index": 1,
                "monitoring_client": monitoring_client,
                "project_id": project_id,
                "interval_s": interval_s,
            }

    def _build_caching_provider(self, inner: ValidationProvider) -> CachingProvider:
        """Wrap *inner* in a CachingProvider."""
        logger.debug("get_provider: cache TTL=%d days (0=disabled)", self._config.cache_ttl_days)
        return CachingProvider(
            inner=inner,
            get_engine=db_engine.get_engine,
            ttl_days=self._config.cache_ttl_days,
        )
