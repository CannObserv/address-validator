"""Unit tests for validation/config.py — pydantic-settings config models."""

import logging

import pytest

from address_validator.services.validation.config import (
    GoogleConfig,
    USPSConfig,
    ValidationConfig,
    validate_config,
)


class TestUSPSConfig:
    def test_valid_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USPS_CONSUMER_KEY", "mykey")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "mysecret")
        monkeypatch.delenv("USPS_RATE_LIMIT_RPS", raising=False)
        monkeypatch.delenv("USPS_DAILY_LIMIT", raising=False)
        cfg = USPSConfig()
        assert cfg.consumer_key == "mykey"
        assert cfg.consumer_secret == "mysecret"
        assert cfg.rate_limit_rps == 5.0
        assert cfg.daily_limit == 10000

    def test_custom_rps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USPS_CONSUMER_KEY", "k")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "s")
        monkeypatch.setenv("USPS_RATE_LIMIT_RPS", "10.0")
        cfg = USPSConfig()
        assert cfg.rate_limit_rps == 10.0

    def test_rps_below_one_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USPS_CONSUMER_KEY", "k")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "s")
        monkeypatch.setenv("USPS_RATE_LIMIT_RPS", "0.5")
        with pytest.raises(ValueError, match="USPS_RATE_LIMIT_RPS"):
            USPSConfig()

    def test_zero_rps_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USPS_CONSUMER_KEY", "k")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "s")
        monkeypatch.setenv("USPS_RATE_LIMIT_RPS", "0")
        with pytest.raises(ValueError, match="USPS_RATE_LIMIT_RPS"):
            USPSConfig()

    def test_negative_rps_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USPS_CONSUMER_KEY", "k")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "s")
        monkeypatch.setenv("USPS_RATE_LIMIT_RPS", "-1.0")
        with pytest.raises(ValueError, match="USPS_RATE_LIMIT_RPS"):
            USPSConfig()

    def test_zero_daily_limit_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USPS_CONSUMER_KEY", "k")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "s")
        monkeypatch.setenv("USPS_DAILY_LIMIT", "0")
        with pytest.raises(ValueError, match="USPS_DAILY_LIMIT"):
            USPSConfig()

    def test_custom_daily_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USPS_CONSUMER_KEY", "k")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "s")
        monkeypatch.setenv("USPS_DAILY_LIMIT", "5000")
        cfg = USPSConfig()
        assert cfg.daily_limit == 5000


class TestGoogleConfig:
    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GOOGLE_PROJECT_ID", raising=False)
        monkeypatch.delenv("GOOGLE_RATE_LIMIT_RPM", raising=False)
        monkeypatch.delenv("GOOGLE_DAILY_LIMIT", raising=False)
        monkeypatch.delenv("GOOGLE_QUOTA_RECONCILE_INTERVAL_S", raising=False)
        cfg = GoogleConfig()
        assert cfg.project_id is None
        assert cfg.rate_limit_rpm == 5
        assert cfg.daily_limit == 160
        assert cfg.quota_reconcile_interval_s == 900.0

    def test_custom_rpm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_RATE_LIMIT_RPM", "10")
        cfg = GoogleConfig()
        assert cfg.rate_limit_rpm == 10

    def test_zero_rpm_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_RATE_LIMIT_RPM", "0")
        with pytest.raises(ValueError, match="GOOGLE_RATE_LIMIT_RPM"):
            GoogleConfig()

    def test_zero_daily_limit_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_DAILY_LIMIT", "0")
        with pytest.raises(ValueError, match="GOOGLE_DAILY_LIMIT"):
            GoogleConfig()

    def test_zero_reconcile_interval_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_QUOTA_RECONCILE_INTERVAL_S", "0")
        with pytest.raises(ValueError, match="GOOGLE_QUOTA_RECONCILE_INTERVAL_S"):
            GoogleConfig()

    def test_project_id_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_PROJECT_ID", "my-project")
        cfg = GoogleConfig()
        assert cfg.project_id == "my-project"


class TestValidationConfig:
    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VALIDATION_PROVIDER", raising=False)
        monkeypatch.delenv("VALIDATION_LATENCY_BUDGET_S", raising=False)
        monkeypatch.delenv("VALIDATION_CACHE_DSN", raising=False)
        monkeypatch.delenv("VALIDATION_CACHE_TTL_DAYS", raising=False)
        cfg = ValidationConfig()
        assert cfg.provider == "none"
        assert cfg.latency_budget_s == 1.0
        assert cfg.cache_dsn == ""
        assert cfg.cache_ttl_days == 30

    def test_zero_latency_budget_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_LATENCY_BUDGET_S", "0")
        with pytest.raises(ValueError, match="VALIDATION_LATENCY_BUDGET_S"):
            ValidationConfig()

    def test_negative_ttl_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_CACHE_TTL_DAYS", "-1")
        with pytest.raises(ValueError, match="VALIDATION_CACHE_TTL_DAYS"):
            ValidationConfig()

    def test_zero_ttl_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_CACHE_TTL_DAYS", "0")
        cfg = ValidationConfig()
        assert cfg.cache_ttl_days == 0

    def test_provider_names(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps,google")
        cfg = ValidationConfig()
        assert cfg.provider_names == ["usps", "google"]

    def test_provider_names_none_filtered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "none,usps")
        cfg = ValidationConfig()
        assert cfg.provider_names == ["usps"]

    def test_provider_names_all_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "none")
        cfg = ValidationConfig()
        assert cfg.provider_names == []

    def test_provider_names_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "USPS")
        cfg = ValidationConfig()
        assert cfg.provider_names == ["usps"]


class TestValidateConfig:
    def test_none_provider_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VALIDATION_PROVIDER", raising=False)
        result = validate_config()
        assert result is None

    def test_usps_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("VALIDATION_CACHE_DSN", "postgresql+asyncpg://localhost/test")
        result = validate_config()
        assert isinstance(result, ValidationConfig)

    def test_missing_cache_dsn_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.delenv("VALIDATION_CACHE_DSN", raising=False)
        with pytest.raises(ValueError, match="VALIDATION_CACHE_DSN"):
            validate_config()

    def test_missing_usps_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.delenv("USPS_CONSUMER_KEY", raising=False)
        monkeypatch.delenv("USPS_CONSUMER_SECRET", raising=False)
        with pytest.raises(ValueError, match="USPS_CONSUMER_KEY"):
            validate_config()

    def test_unknown_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "smarty")
        with pytest.raises(ValueError, match="smarty"):
            validate_config()

    def test_google_valid(self, monkeypatch: pytest.MonkeyPatch, mock_google_auth) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "google")
        monkeypatch.setenv("VALIDATION_CACHE_DSN", "postgresql+asyncpg://localhost/test")
        result = validate_config()
        assert isinstance(result, ValidationConfig)

    def test_none_skips_ttl_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "none")
        monkeypatch.setenv("VALIDATION_CACHE_TTL_DAYS", "abc")
        result = validate_config()
        assert result is None

    def test_logs_active_provider(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("VALIDATION_CACHE_DSN", "postgresql+asyncpg://localhost/test")
        with caplog.at_level(logging.INFO, logger="address_validator.services.validation.config"):
            validate_config()
        assert any("usps" in r.message for r in caplog.records)

    def test_logs_none_provider(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.delenv("VALIDATION_PROVIDER", raising=False)
        with caplog.at_level(logging.INFO, logger="address_validator.services.validation.config"):
            validate_config()
        assert any("none" in r.message for r in caplog.records)
