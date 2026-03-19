"""Unit tests for the provider factory (get_provider, validate_config)."""

import logging

import pytest

import address_validator.services.validation.cache_db as cache_db_module
import address_validator.services.validation.factory as factory_module
from address_validator.services.validation.cache_provider import CachingProvider
from address_validator.services.validation.chain_provider import ChainProvider
from address_validator.services.validation.factory import get_provider, validate_config
from address_validator.services.validation.google_provider import GoogleProvider
from address_validator.services.validation.null_provider import NullProvider
from address_validator.services.validation.usps_provider import USPSProvider


@pytest.fixture(autouse=True)
async def reset_singletons() -> None:
    """Reset module-level provider singletons between tests."""
    factory_module._usps_provider = None
    factory_module._google_provider = None
    factory_module._http_client = None
    factory_module._caching_provider = None
    await cache_db_module.close_engine()
    yield
    factory_module._usps_provider = None
    factory_module._google_provider = None
    factory_module._http_client = None
    factory_module._caching_provider = None
    await cache_db_module.close_engine()


class TestGetProvider:
    def test_default_is_null(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VALIDATION_PROVIDER", raising=False)
        assert isinstance(get_provider(), NullProvider)

    def test_none_keyword_gives_null(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "none")
        assert isinstance(get_provider(), NullProvider)

    def test_none_keyword_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "NONE")
        assert isinstance(get_provider(), NullProvider)

    def test_usps_keyword_gives_usps_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        result = get_provider()
        assert isinstance(result, CachingProvider)
        assert isinstance(result._inner, USPSProvider)

    def test_usps_keyword_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "USPS")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        result = get_provider()
        assert isinstance(result, CachingProvider)
        assert isinstance(result._inner, USPSProvider)

    def test_usps_provider_is_singleton(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_provider() must return the same USPSProvider instance each call."""
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        assert get_provider() is get_provider()

    def test_usps_missing_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.delenv("USPS_CONSUMER_KEY", raising=False)
        monkeypatch.delenv("USPS_CONSUMER_SECRET", raising=False)
        with pytest.raises(ValueError, match="USPS_CONSUMER_KEY"):
            get_provider()

    def test_unknown_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "smarty")
        with pytest.raises(ValueError, match="smarty"):
            get_provider()

    def test_google_keyword_gives_google_provider(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "google")
        monkeypatch.setenv("GOOGLE_API_KEY", "my-key")
        result = get_provider()
        assert isinstance(result, CachingProvider)
        assert isinstance(result._inner, GoogleProvider)

    def test_google_keyword_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "GOOGLE")
        monkeypatch.setenv("GOOGLE_API_KEY", "my-key")
        result = get_provider()
        assert isinstance(result, CachingProvider)
        assert isinstance(result._inner, GoogleProvider)

    def test_google_provider_is_singleton(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "google")
        monkeypatch.setenv("GOOGLE_API_KEY", "my-key")
        assert get_provider() is get_provider()

    def test_google_missing_api_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "google")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
            get_provider()

    def test_unknown_provider_error_mentions_google(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "smarty")
        with pytest.raises(ValueError, match="google"):
            get_provider()

    # NullProvider is NOT wrapped in CachingProvider
    def test_null_provider_returned_unwrapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VALIDATION_PROVIDER", raising=False)
        result = get_provider()
        assert isinstance(result, NullProvider)
        assert not isinstance(result, CachingProvider)

    def test_usps_provider_wrapped_in_caching_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        result = get_provider()
        assert isinstance(result, CachingProvider)
        assert isinstance(result._inner, USPSProvider)

    def test_google_provider_wrapped_in_caching_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "google")
        monkeypatch.setenv("GOOGLE_API_KEY", "my-key")
        result = get_provider()
        assert isinstance(result, CachingProvider)
        assert isinstance(result._inner, GoogleProvider)

    def test_caching_provider_is_singleton(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        assert get_provider() is get_provider()

    def test_comma_separated_list_gives_chain_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps,google")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("GOOGLE_API_KEY", "gkey")
        result = get_provider()
        assert isinstance(result, CachingProvider)
        assert isinstance(result._inner, ChainProvider)

    def test_chain_contains_usps_then_google(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps,google")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("GOOGLE_API_KEY", "gkey")
        result = get_provider()
        assert isinstance(result, CachingProvider)
        chain = result._inner
        assert isinstance(chain, ChainProvider)
        assert isinstance(chain._providers[0], USPSProvider)
        assert isinstance(chain._providers[1], GoogleProvider)

    def test_chain_google_then_usps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "google,usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("GOOGLE_API_KEY", "gkey")
        result = get_provider()
        assert isinstance(result, CachingProvider)
        chain = result._inner
        assert isinstance(chain, ChainProvider)
        assert isinstance(chain._providers[0], GoogleProvider)
        assert isinstance(chain._providers[1], USPSProvider)

    def test_usps_rate_limit_rps_configures_per_second_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("USPS_RATE_LIMIT_RPS", "10.0")
        result = get_provider()
        assert isinstance(result, CachingProvider)
        usps: USPSProvider = result._inner  # type: ignore[assignment]
        guard = usps._client._rate_limiter
        assert guard._windows[0].limit == 10
        assert guard._windows[0].duration_s == 1.0

    def test_usps_daily_limit_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("USPS_DAILY_LIMIT", "5000")
        result = get_provider()
        usps: USPSProvider = result._inner  # type: ignore[assignment]
        guard = usps._client._rate_limiter
        assert guard._windows[1].limit == 5000
        assert guard._windows[1].duration_s == 86_400.0

    def test_google_rate_limit_rpm_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "google")
        monkeypatch.setenv("GOOGLE_API_KEY", "gkey")
        monkeypatch.setenv("GOOGLE_RATE_LIMIT_RPM", "10")
        result = get_provider()
        assert isinstance(result, CachingProvider)
        google: GoogleProvider = result._inner  # type: ignore[assignment]
        guard = google._client._rate_limiter
        assert guard._windows[0].limit == 10
        assert guard._windows[0].duration_s == 60.0

    def test_google_daily_limit_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "google")
        monkeypatch.setenv("GOOGLE_API_KEY", "gkey")
        monkeypatch.setenv("GOOGLE_DAILY_LIMIT", "80")
        result = get_provider()
        google: GoogleProvider = result._inner  # type: ignore[assignment]
        guard = google._client._rate_limiter
        assert guard._windows[1].limit == 80
        assert guard._windows[1].mode == "hard"

    def test_google_daily_window_is_hard(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "google")
        monkeypatch.setenv("GOOGLE_API_KEY", "gkey")
        result = get_provider()
        google: GoogleProvider = result._inner  # type: ignore[assignment]
        guard = google._client._rate_limiter
        assert guard._windows[1].mode == "hard"

    def test_unknown_provider_in_list_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps,smarty")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        with pytest.raises(ValueError, match="smarty"):
            get_provider()

    def test_none_mixed_with_valid_gives_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # "none" tokens are filtered out; "usps" remains
        monkeypatch.setenv("VALIDATION_PROVIDER", "none,usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        result = get_provider()
        assert isinstance(result, CachingProvider)
        assert isinstance(result._inner, USPSProvider)

    def test_ttl_days_default_is_30(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.delenv("VALIDATION_CACHE_TTL_DAYS", raising=False)
        result = get_provider()
        assert isinstance(result, CachingProvider)
        assert result._ttl_days == 30

    def test_ttl_days_env_var_passed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("VALIDATION_CACHE_TTL_DAYS", "7")
        result = get_provider()
        assert isinstance(result, CachingProvider)
        assert result._ttl_days == 7

    def test_ttl_days_zero_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("VALIDATION_CACHE_TTL_DAYS", "0")
        result = get_provider()
        assert isinstance(result, CachingProvider)
        assert result._ttl_days == 0

    def test_ttl_days_invalid_string_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("VALIDATION_CACHE_TTL_DAYS", "abc")
        with pytest.raises(ValueError, match="VALIDATION_CACHE_TTL_DAYS"):
            get_provider()

    def test_ttl_days_negative_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("VALIDATION_CACHE_TTL_DAYS", "-1")
        with pytest.raises(ValueError, match="VALIDATION_CACHE_TTL_DAYS"):
            get_provider()


class TestValidateConfig:
    def test_none_provider_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VALIDATION_PROVIDER", raising=False)
        validate_config()  # must not raise

    def test_none_keyword_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "none")
        validate_config()  # must not raise

    def test_usps_valid_creds_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("VALIDATION_CACHE_DSN", "postgresql+asyncpg://localhost/test")
        validate_config()  # must not raise

    def test_usps_missing_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.delenv("USPS_CONSUMER_KEY", raising=False)
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        with pytest.raises(ValueError, match="USPS_CONSUMER_KEY"):
            validate_config()

    def test_usps_missing_secret_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.delenv("USPS_CONSUMER_SECRET", raising=False)
        with pytest.raises(ValueError, match="USPS_CONSUMER_SECRET"):
            validate_config()

    def test_usps_invalid_rate_limit_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("USPS_RATE_LIMIT_RPS", "not-a-number")
        with pytest.raises(ValueError, match="USPS_RATE_LIMIT_RPS"):
            validate_config()

    def test_google_valid_key_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "google")
        monkeypatch.setenv("GOOGLE_API_KEY", "my-key")
        monkeypatch.setenv("VALIDATION_CACHE_DSN", "postgresql+asyncpg://localhost/test")
        validate_config()  # must not raise

    def test_google_missing_api_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "google")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
            validate_config()

    def test_unknown_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "smarty")
        with pytest.raises(ValueError, match="smarty"):
            validate_config()

    def test_chain_valid_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps,google")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("GOOGLE_API_KEY", "gkey")
        monkeypatch.setenv("VALIDATION_CACHE_DSN", "postgresql+asyncpg://localhost/test")
        validate_config()  # must not raise

    def test_chain_missing_google_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps,google")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        with pytest.raises(ValueError, match="GOOGLE_API_KEY"):
            validate_config()

    def test_invalid_ttl_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("VALIDATION_CACHE_DSN", "postgresql+asyncpg://localhost/test")
        monkeypatch.setenv("VALIDATION_CACHE_TTL_DAYS", "abc")
        with pytest.raises(ValueError, match="VALIDATION_CACHE_TTL_DAYS"):
            validate_config()

    def test_negative_ttl_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("VALIDATION_CACHE_DSN", "postgresql+asyncpg://localhost/test")
        monkeypatch.setenv("VALIDATION_CACHE_TTL_DAYS", "-1")
        with pytest.raises(ValueError, match="VALIDATION_CACHE_TTL_DAYS"):
            validate_config()

    def test_missing_cache_dsn_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.delenv("VALIDATION_CACHE_DSN", raising=False)
        with pytest.raises(ValueError, match="VALIDATION_CACHE_DSN"):
            validate_config()

    def test_none_provider_skips_ttl_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """TTL is only relevant for non-null providers; none should not validate it."""
        monkeypatch.setenv("VALIDATION_PROVIDER", "none")
        monkeypatch.setenv("VALIDATION_CACHE_TTL_DAYS", "abc")
        validate_config()  # must not raise — TTL irrelevant for NullProvider

    def test_logs_active_provider(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("VALIDATION_CACHE_DSN", "postgresql+asyncpg://localhost/test")
        with caplog.at_level(logging.INFO, logger="address_validator.services.validation.factory"):
            validate_config()
        assert any("usps" in r.message for r in caplog.records)

    def test_logs_none_provider(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.delenv("VALIDATION_PROVIDER", raising=False)

        with caplog.at_level(logging.INFO, logger="address_validator.services.validation.factory"):
            validate_config()
        assert any("none" in r.message for r in caplog.records)

    def test_usps_zero_rate_limit_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("USPS_RATE_LIMIT_RPS", "0")
        with pytest.raises(ValueError, match="USPS_RATE_LIMIT_RPS"):
            validate_config()

    def test_usps_negative_rate_limit_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("USPS_RATE_LIMIT_RPS", "-1.0")
        with pytest.raises(ValueError, match="USPS_RATE_LIMIT_RPS"):
            validate_config()

    def test_usps_sub_one_rate_limit_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("USPS_RATE_LIMIT_RPS", "0.5")
        with pytest.raises(ValueError, match="USPS_RATE_LIMIT_RPS"):
            validate_config()

    def test_invalid_latency_budget_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("VALIDATION_LATENCY_BUDGET_S", "not-a-number")
        with pytest.raises(ValueError, match="VALIDATION_LATENCY_BUDGET_S"):
            validate_config()

    def test_zero_latency_budget_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("VALIDATION_LATENCY_BUDGET_S", "0")
        with pytest.raises(ValueError, match="VALIDATION_LATENCY_BUDGET_S"):
            validate_config()

    def test_invalid_usps_daily_limit_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("USPS_DAILY_LIMIT", "abc")
        with pytest.raises(ValueError, match="USPS_DAILY_LIMIT"):
            validate_config()

    def test_zero_usps_daily_limit_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("USPS_DAILY_LIMIT", "0")
        with pytest.raises(ValueError, match="USPS_DAILY_LIMIT"):
            validate_config()

    def test_invalid_google_rpm_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "google")
        monkeypatch.setenv("GOOGLE_API_KEY", "gkey")
        monkeypatch.setenv("GOOGLE_RATE_LIMIT_RPM", "abc")
        with pytest.raises(ValueError, match="GOOGLE_RATE_LIMIT_RPM"):
            validate_config()

    def test_zero_google_rpm_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "google")
        monkeypatch.setenv("GOOGLE_API_KEY", "gkey")
        monkeypatch.setenv("GOOGLE_RATE_LIMIT_RPM", "0")
        with pytest.raises(ValueError, match="GOOGLE_RATE_LIMIT_RPM"):
            validate_config()

    def test_invalid_google_daily_limit_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "google")
        monkeypatch.setenv("GOOGLE_API_KEY", "gkey")
        monkeypatch.setenv("GOOGLE_DAILY_LIMIT", "abc")
        with pytest.raises(ValueError, match="GOOGLE_DAILY_LIMIT"):
            validate_config()

    def test_zero_google_daily_limit_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "google")
        monkeypatch.setenv("GOOGLE_API_KEY", "gkey")
        monkeypatch.setenv("GOOGLE_DAILY_LIMIT", "0")
        with pytest.raises(ValueError, match="GOOGLE_DAILY_LIMIT"):
            validate_config()


class TestGetProviderRpsGuard:
    """Positive-RPS guard is also enforced by get_provider / _build_single_provider."""

    def test_usps_zero_rate_limit_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("USPS_RATE_LIMIT_RPS", "0")
        with pytest.raises(ValueError, match="USPS_RATE_LIMIT_RPS"):
            get_provider()

    def test_usps_negative_rate_limit_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("USPS_RATE_LIMIT_RPS", "-1.0")
        with pytest.raises(ValueError, match="USPS_RATE_LIMIT_RPS"):
            get_provider()

    def test_usps_sub_one_rate_limit_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("USPS_RATE_LIMIT_RPS", "0.5")
        with pytest.raises(ValueError, match="USPS_RATE_LIMIT_RPS"):
            get_provider()
