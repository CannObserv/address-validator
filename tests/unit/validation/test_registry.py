"""Unit tests for validation/registry.py — ProviderRegistry."""

import pytest

import address_validator.db.engine as cache_db_module
from address_validator.services.validation._rate_limit import FixedResetQuotaWindow
from address_validator.services.validation.cache_provider import CachingProvider
from address_validator.services.validation.chain_provider import ChainProvider
from address_validator.services.validation.config import ValidationConfig
from address_validator.services.validation.google_provider import GoogleProvider
from address_validator.services.validation.null_provider import NullProvider
from address_validator.services.validation.registry import ProviderRegistry
from address_validator.services.validation.usps_provider import USPSProvider


@pytest.fixture(autouse=True)
async def _cleanup_engine() -> None:
    """Reset the shared cache engine between tests."""
    await cache_db_module.close_engine()
    yield
    await cache_db_module.close_engine()


def _make_registry(monkeypatch: pytest.MonkeyPatch, **env: str) -> ProviderRegistry:
    """Set env vars via monkeypatch, then return a fresh ProviderRegistry."""
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return ProviderRegistry(ValidationConfig())


class TestGetProvider:
    def test_default_is_null(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VALIDATION_PROVIDER", raising=False)
        reg = ProviderRegistry(ValidationConfig())
        assert isinstance(reg.get_provider(), NullProvider)

    def test_none_keyword_gives_null(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(monkeypatch, VALIDATION_PROVIDER="none")
        assert isinstance(reg.get_provider(), NullProvider)

    def test_none_keyword_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(monkeypatch, VALIDATION_PROVIDER="NONE")
        assert isinstance(reg.get_provider(), NullProvider)

    def test_usps_gives_caching_usps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(
            monkeypatch,
            VALIDATION_PROVIDER="usps",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
        )
        result = reg.get_provider()
        assert isinstance(result, CachingProvider)
        assert isinstance(result._inner, USPSProvider)

    def test_usps_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(
            monkeypatch,
            VALIDATION_PROVIDER="USPS",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
        )
        result = reg.get_provider()
        assert isinstance(result, CachingProvider)
        assert isinstance(result._inner, USPSProvider)

    def test_provider_is_singleton(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(
            monkeypatch,
            VALIDATION_PROVIDER="usps",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
        )
        assert reg.get_provider() is reg.get_provider()

    def test_usps_missing_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.delenv("USPS_CONSUMER_KEY", raising=False)
        monkeypatch.delenv("USPS_CONSUMER_SECRET", raising=False)
        reg = ProviderRegistry(ValidationConfig())
        with pytest.raises(ValueError, match="USPS_CONSUMER_KEY"):
            reg.get_provider()

    def test_unknown_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(monkeypatch, VALIDATION_PROVIDER="smarty")
        with pytest.raises(ValueError, match="smarty"):
            reg.get_provider()

    def test_unknown_provider_error_mentions_google(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(monkeypatch, VALIDATION_PROVIDER="smarty")
        with pytest.raises(ValueError, match="google"):
            reg.get_provider()

    def test_google_gives_caching_google(
        self, monkeypatch: pytest.MonkeyPatch, mock_google_auth
    ) -> None:
        reg = _make_registry(monkeypatch, VALIDATION_PROVIDER="google")
        result = reg.get_provider()
        assert isinstance(result, CachingProvider)
        assert isinstance(result._inner, GoogleProvider)

    def test_google_case_insensitive(
        self, monkeypatch: pytest.MonkeyPatch, mock_google_auth
    ) -> None:
        reg = _make_registry(monkeypatch, VALIDATION_PROVIDER="GOOGLE")
        result = reg.get_provider()
        assert isinstance(result, CachingProvider)
        assert isinstance(result._inner, GoogleProvider)

    def test_google_singleton(self, monkeypatch: pytest.MonkeyPatch, mock_google_auth) -> None:
        reg = _make_registry(monkeypatch, VALIDATION_PROVIDER="google")
        assert reg.get_provider() is reg.get_provider()

    def test_null_returned_unwrapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VALIDATION_PROVIDER", raising=False)
        reg = ProviderRegistry(ValidationConfig())
        result = reg.get_provider()
        assert isinstance(result, NullProvider)
        assert not isinstance(result, CachingProvider)

    def test_chain_provider(self, monkeypatch: pytest.MonkeyPatch, mock_google_auth) -> None:
        reg = _make_registry(
            monkeypatch,
            VALIDATION_PROVIDER="usps,google",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
        )
        result = reg.get_provider()
        assert isinstance(result, CachingProvider)
        assert isinstance(result._inner, ChainProvider)

    def test_chain_usps_then_google(
        self, monkeypatch: pytest.MonkeyPatch, mock_google_auth
    ) -> None:
        reg = _make_registry(
            monkeypatch,
            VALIDATION_PROVIDER="usps,google",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
        )
        result = reg.get_provider()
        chain = result._inner
        assert isinstance(chain, ChainProvider)
        assert isinstance(chain._providers[0], USPSProvider)
        assert isinstance(chain._providers[1], GoogleProvider)

    def test_chain_google_then_usps(
        self, monkeypatch: pytest.MonkeyPatch, mock_google_auth
    ) -> None:
        reg = _make_registry(
            monkeypatch,
            VALIDATION_PROVIDER="google,usps",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
        )
        result = reg.get_provider()
        chain = result._inner
        assert isinstance(chain, ChainProvider)
        assert isinstance(chain._providers[0], GoogleProvider)
        assert isinstance(chain._providers[1], USPSProvider)

    def test_usps_rps_configures_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(
            monkeypatch,
            VALIDATION_PROVIDER="usps",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
            USPS_RATE_LIMIT_RPS="10.0",
        )
        result = reg.get_provider()
        usps: USPSProvider = result._inner  # type: ignore[assignment]
        guard = usps.client.quota_guard
        assert guard._windows[0].limit == 10

    def test_usps_daily_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(
            monkeypatch,
            VALIDATION_PROVIDER="usps",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
            USPS_DAILY_LIMIT="5000",
        )
        result = reg.get_provider()
        usps: USPSProvider = result._inner  # type: ignore[assignment]
        guard = usps.client.quota_guard
        assert guard._windows[1].limit == 5000

    def test_google_rpm(self, monkeypatch: pytest.MonkeyPatch, mock_google_auth) -> None:
        reg = _make_registry(
            monkeypatch,
            VALIDATION_PROVIDER="google",
            GOOGLE_RATE_LIMIT_RPM="10",
        )
        result = reg.get_provider()
        google: GoogleProvider = result._inner  # type: ignore[assignment]
        guard = google.client.quota_guard
        assert guard._windows[0].limit == 10

    def test_google_daily_limit(self, monkeypatch: pytest.MonkeyPatch, mock_google_auth) -> None:
        reg = _make_registry(
            monkeypatch,
            VALIDATION_PROVIDER="google",
            GOOGLE_DAILY_LIMIT="80",
        )
        result = reg.get_provider()
        google: GoogleProvider = result._inner  # type: ignore[assignment]
        guard = google.client.quota_guard
        assert guard._windows[1].limit == 80
        assert guard._windows[1].mode == "hard"

    def test_google_daily_window_is_fixed_reset(
        self, monkeypatch: pytest.MonkeyPatch, mock_google_auth
    ) -> None:
        reg = _make_registry(monkeypatch, VALIDATION_PROVIDER="google")
        result = reg.get_provider()
        google: GoogleProvider = result._inner  # type: ignore[assignment]
        guard = google.client.quota_guard
        assert isinstance(guard._windows[1], FixedResetQuotaWindow)

    def test_unknown_in_list_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(
            monkeypatch,
            VALIDATION_PROVIDER="usps,smarty",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
        )
        with pytest.raises(ValueError, match="smarty"):
            reg.get_provider()

    def test_none_mixed_with_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(
            monkeypatch,
            VALIDATION_PROVIDER="none,usps",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
        )
        result = reg.get_provider()
        assert isinstance(result, CachingProvider)
        assert isinstance(result._inner, USPSProvider)

    def test_ttl_default_30(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.delenv("VALIDATION_CACHE_TTL_DAYS", raising=False)
        reg = ProviderRegistry(ValidationConfig())
        result = reg.get_provider()
        assert isinstance(result, CachingProvider)
        assert result._ttl_days == 30

    def test_ttl_custom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(
            monkeypatch,
            VALIDATION_PROVIDER="usps",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
            VALIDATION_CACHE_TTL_DAYS="7",
        )
        result = reg.get_provider()
        assert isinstance(result, CachingProvider)
        assert result._ttl_days == 7

    def test_ttl_zero_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(
            monkeypatch,
            VALIDATION_PROVIDER="usps",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
            VALIDATION_CACHE_TTL_DAYS="0",
        )
        result = reg.get_provider()
        assert isinstance(result, CachingProvider)
        assert result._ttl_days == 0


class TestGetQuotaInfo:
    def test_empty_for_null(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VALIDATION_PROVIDER", raising=False)
        reg = ProviderRegistry(ValidationConfig())
        reg.get_provider()
        assert reg.get_quota_info() == []

    def test_usps_quota(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(
            monkeypatch,
            VALIDATION_PROVIDER="usps",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
        )
        reg.get_provider()
        info = reg.get_quota_info()
        assert len(info) == 1
        assert info[0]["provider"] == "usps"
        assert "remaining" in info[0]
        assert "limit" in info[0]

    def test_google_quota(self, monkeypatch: pytest.MonkeyPatch, mock_google_auth) -> None:
        reg = _make_registry(monkeypatch, VALIDATION_PROVIDER="google")
        reg.get_provider()
        info = reg.get_quota_info()
        assert len(info) == 1
        assert info[0]["provider"] == "google"
        assert "remaining" in info[0]
        assert "limit" in info[0]

    def test_chain_both_quotas(self, monkeypatch: pytest.MonkeyPatch, mock_google_auth) -> None:
        reg = _make_registry(
            monkeypatch,
            VALIDATION_PROVIDER="usps,google",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
        )
        reg.get_provider()
        info = reg.get_quota_info()
        assert len(info) == 2
        providers = {entry["provider"] for entry in info}
        assert providers == {"usps", "google"}
