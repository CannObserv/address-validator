"""Unit tests for the provider factory (get_provider)."""

import pytest

import services.validation.factory as factory_module
from services.validation.factory import get_provider
from services.validation.null_provider import NullProvider
from services.validation.usps_provider import USPSProvider


@pytest.fixture(autouse=True)
def reset_usps_singleton() -> None:
    """Reset the module-level USPSProvider singleton between tests.

    ``_get_usps_provider`` caches the instance after first creation;
    without this fixture, tests that create a USPS provider would
    affect subsequent tests that expect a fresh instance.
    """
    factory_module._usps_provider = None
    yield
    factory_module._usps_provider = None


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

    def test_usps_keyword_gives_usps_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        assert isinstance(get_provider(), USPSProvider)

    def test_usps_keyword_case_insensitive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "USPS")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        assert isinstance(get_provider(), USPSProvider)

    def test_usps_provider_is_singleton(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_provider() must return the same USPSProvider instance each call."""
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        assert get_provider() is get_provider()

    def test_usps_missing_key_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.delenv("USPS_CONSUMER_KEY", raising=False)
        monkeypatch.delenv("USPS_CONSUMER_SECRET", raising=False)
        with pytest.raises(ValueError, match="USPS_CONSUMER_KEY"):
            get_provider()

    def test_unknown_provider_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "smarty")
        with pytest.raises(ValueError, match="smarty"):
            get_provider()
