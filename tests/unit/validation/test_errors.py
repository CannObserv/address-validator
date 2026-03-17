"""Unit tests for services/validation/errors.py."""

from services.validation.errors import ProviderRateLimitedError


class TestProviderRateLimitedError:
    def test_stores_provider_name(self) -> None:
        err = ProviderRateLimitedError("usps")
        assert err.provider == "usps"

    def test_str_contains_provider_name(self) -> None:
        err = ProviderRateLimitedError("google")
        assert "google" in str(err)

    def test_is_exception(self) -> None:
        err = ProviderRateLimitedError("all")
        assert isinstance(err, Exception)

    def test_all_sentinel(self) -> None:
        err = ProviderRateLimitedError("all")
        assert err.provider == "all"
