"""Unit tests for the provider factory (get_provider)."""

import pytest

import services.validation.cache_db as cache_db_module
import services.validation.factory as factory_module
from services.validation.cache_provider import CachingProvider
from services.validation.chain_provider import ChainProvider
from services.validation.factory import get_provider
from services.validation.google_provider import GoogleProvider
from services.validation.null_provider import NullProvider
from services.validation.usps_provider import USPSProvider


@pytest.fixture(autouse=True)
async def reset_singletons() -> None:
    """Reset module-level provider singletons between tests."""
    factory_module._usps_provider = None
    factory_module._google_provider = None
    factory_module._http_client = None
    factory_module._caching_provider = None
    await cache_db_module.close_db()
    yield
    factory_module._usps_provider = None
    factory_module._google_provider = None
    factory_module._http_client = None
    factory_module._caching_provider = None
    await cache_db_module.close_db()


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

    def test_usps_rate_limit_rps_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("USPS_RATE_LIMIT_RPS", "10.0")
        result = get_provider()
        assert isinstance(result, CachingProvider)
        usps: USPSProvider = result._inner  # type: ignore[assignment]
        assert usps._client._rate_limiter.rate == 10.0

    def test_google_rate_limit_rps_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "google")
        monkeypatch.setenv("GOOGLE_API_KEY", "gkey")
        monkeypatch.setenv("GOOGLE_RATE_LIMIT_RPS", "50.0")
        result = get_provider()
        assert isinstance(result, CachingProvider)
        google: GoogleProvider = result._inner  # type: ignore[assignment]
        assert google._client._rate_limiter.rate == 50.0

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
