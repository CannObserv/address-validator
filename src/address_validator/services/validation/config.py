"""Pydantic-settings models for validation provider configuration.

Replaces the manual ``_parse_*`` functions that were in ``factory.py``.
Each model reads env vars via its ``env_prefix`` at construction time.
"""

import logging
import os

from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from address_validator.services.validation.gcp_auth import get_credentials

logger = logging.getLogger(__name__)

_SUPPORTED_PROVIDERS = ("none", "usps", "google")


def settings_error(exc: ValidationError, prefix: str) -> ValueError:
    """Convert a pydantic ValidationError into a ValueError with env-var names.

    Missing required fields and value errors are re-expressed using the full
    ``{PREFIX}{FIELD}`` environment variable name so that error messages match
    the env-var naming convention used throughout the codebase.
    """
    lines: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(p) for p in error["loc"])
        env_var = f"{prefix}{loc.upper()}"
        msg = error["msg"]
        if error["type"] == "missing":
            lines.append(f"{env_var} is required but was not set")
        else:
            # field_validator messages already contain the full env var name
            lines.append(msg)
    return ValueError("; ".join(lines))


class USPSConfig(BaseSettings):
    """USPS provider credentials and rate-limit settings."""

    model_config = SettingsConfigDict(env_prefix="USPS_")

    consumer_key: str
    consumer_secret: str
    rate_limit_rps: float = 5.0
    daily_limit: int = 10000

    @field_validator("rate_limit_rps")
    @classmethod
    def _rps_at_least_one(cls, v: float) -> float:
        if v < 1:
            raise ValueError("USPS_RATE_LIMIT_RPS must be a number >= 1 (e.g. '5.0')")
        return v

    @field_validator("daily_limit")
    @classmethod
    def _daily_limit_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("USPS_DAILY_LIMIT must be a positive integer (e.g. '10000')")
        return v


class GoogleConfig(BaseSettings):
    """Google provider settings."""

    model_config = SettingsConfigDict(env_prefix="GOOGLE_")

    project_id: str | None = None
    rate_limit_rpm: int = 5
    daily_limit: int = 160
    quota_reconcile_interval_s: float = 900.0

    @field_validator("rate_limit_rpm")
    @classmethod
    def _rpm_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("GOOGLE_RATE_LIMIT_RPM must be a positive integer (e.g. '5')")
        return v

    @field_validator("daily_limit")
    @classmethod
    def _daily_limit_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("GOOGLE_DAILY_LIMIT must be a positive integer (e.g. '160')")
        return v

    @field_validator("quota_reconcile_interval_s")
    @classmethod
    def _interval_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(
                "GOOGLE_QUOTA_RECONCILE_INTERVAL_S must be a positive number (e.g. '900')"
            )
        return v


class ValidationConfig(BaseSettings):
    """Top-level validation settings (provider selection, cache, latency budget)."""

    model_config = SettingsConfigDict(env_prefix="VALIDATION_")

    provider: str = "none"
    latency_budget_s: float = 1.0
    cache_dsn: str = ""
    cache_ttl_days: int = 30

    @field_validator("latency_budget_s")
    @classmethod
    def _budget_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("VALIDATION_LATENCY_BUDGET_S must be a positive number (e.g. '1.0')")
        return v

    @field_validator("cache_ttl_days")
    @classmethod
    def _ttl_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError(
                "VALIDATION_CACHE_TTL_DAYS must be a non-negative integer "
                "(e.g. '30'); use 0 to disable expiry"
            )
        return v

    @property
    def provider_names(self) -> list[str]:
        """Parse CSV provider string into list, filtering out 'none'."""
        return [s for n in self.provider.split(",") if (s := n.strip().lower()) and s != "none"]


def _check_provider_config(name: str) -> None:
    """Validate credentials for a single provider name (no object construction)."""
    if name == "usps":
        try:
            USPSConfig()
        except ValidationError as exc:
            raise settings_error(exc, "USPS_") from None
    elif name == "google":
        try:
            GoogleConfig()
        except ValidationError as exc:
            raise settings_error(exc, "GOOGLE_") from None
        get_credentials()
    else:
        raise ValueError(
            f"Unknown provider name: '{name}'. "
            f"Supported values: {', '.join(repr(p) for p in _SUPPORTED_PROVIDERS)}."
        )


def validate_config() -> ValidationConfig | None:
    """Validate all provider configuration at startup.

    Reads env vars, checks required credentials are present, and validates
    rate-limit/budget/TTL values.  Raises :exc:`ValueError` on misconfiguration.

    Returns the :class:`ValidationConfig` when a real provider is configured,
    or ``None`` when provider is ``none``.
    """
    # Parse the provider string first — use a raw env read so that an invalid
    # cache_ttl_days value does not prevent us from checking the provider.
    raw_provider = os.environ.get("VALIDATION_PROVIDER", "none").strip().lower()
    names = [s for n in raw_provider.split(",") if (s := n.strip()) and s != "none"]

    if not names:
        logger.info("validate_config: provider=none")
        return None

    try:
        val_cfg = ValidationConfig()
    except ValidationError as exc:
        raise settings_error(exc, "VALIDATION_") from None

    for name in names:
        _check_provider_config(name)

    cache_dsn = val_cfg.cache_dsn.strip()
    if not cache_dsn:
        raise ValueError(
            "VALIDATION_CACHE_DSN must be set when a non-null validation provider is configured "
            "(e.g. 'postgresql+asyncpg://user:pass@localhost/address_validator')"
        )

    logger.info("validate_config: provider=%s ttl=%d days", ",".join(names), val_cfg.cache_ttl_days)
    return val_cfg
