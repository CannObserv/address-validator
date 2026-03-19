"""Unit tests for services/validation/errors.py."""

from services.validation.errors import ProviderAtCapacityError, ProviderRateLimitedError


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

    def test_retry_after_seconds_default(self) -> None:
        err = ProviderRateLimitedError("usps")
        assert err.retry_after_seconds == 0.0

    def test_retry_after_seconds_stored(self) -> None:
        err = ProviderRateLimitedError("usps", retry_after_seconds=4.5)
        assert err.retry_after_seconds == 4.5


class TestProviderAtCapacityError:
    def test_stores_provider_name(self) -> None:
        err = ProviderAtCapacityError("usps")
        assert err.provider == "usps"

    def test_str_contains_provider_name(self) -> None:
        err = ProviderAtCapacityError("google")
        assert "google" in str(err)

    def test_is_exception(self) -> None:
        assert isinstance(ProviderAtCapacityError("usps"), Exception)

    def test_retry_after_seconds_default(self) -> None:
        err = ProviderAtCapacityError("usps")
        assert err.retry_after_seconds == 0.0

    def test_retry_after_seconds_stored(self) -> None:
        err = ProviderAtCapacityError("usps", retry_after_seconds=1.5)
        assert err.retry_after_seconds == 1.5
