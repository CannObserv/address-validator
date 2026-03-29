# Factory Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `factory.py`'s 5 mutable globals and interleaved concerns with pydantic-settings config models and a `ProviderRegistry` class.

**Architecture:** Three new files — `config.py` (pydantic-settings env models), `registry.py` (singleton-free provider lifecycle), and updated tests. `factory.py` is deleted. `main.py` creates one `ProviderRegistry` in its lifespan and stores it on `app.state`. Consumers read from `app.state.registry` or receive it via dependency injection.

**Tech Stack:** pydantic-settings, FastAPI app.state, pytest

---

### Task 1: Add pydantic-settings dependency

**Files:**
- Modify: `pyproject.toml:6-22`

- [ ] **Step 1: Add pydantic-settings to dependencies**

In `pyproject.toml`, add `"pydantic-settings>=2.9,<3"` to the `dependencies` list after the `pydantic` entry.

- [ ] **Step 2: Install**

Run: `uv sync`
Expected: installs pydantic-settings and its deps without errors.

- [ ] **Step 3: Verify import**

Run: `uv run python -c "from pydantic_settings import BaseSettings; print('ok')"`
Expected: prints `ok`

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "#50 chore: add pydantic-settings dependency"
```

---

### Task 2: Create `validation/config.py` — pydantic-settings models

**Files:**
- Create: `src/address_validator/services/validation/config.py`
- Test: `tests/unit/validation/test_config.py`

These three `BaseSettings` subclasses replace the manual `_parse_*` functions in `factory.py`. Each class:
- Reads env vars via its `env_prefix`
- Uses `field_validator` to enforce the same business rules as the old `_parse_*` functions
- Raises `ValueError` with the same error messages tests expect (matching env var names)

**Important pydantic-settings behavior:** `BaseSettings` reads env vars at instantiation time. Fields with defaults are optional in the env. Fields without defaults are required — pydantic raises `ValidationError` (not `ValueError`) if missing. We need a `validate_config()` function that catches `ValidationError` and re-raises as `ValueError` with messages matching what tests expect.

- [ ] **Step 1: Write tests for USPSConfig**

Create `tests/unit/validation/test_config.py`:

```python
"""Unit tests for validation config models."""

import pytest

from address_validator.services.validation.config import (
    GoogleConfig,
    USPSConfig,
    ValidationConfig,
    validate_config,
)


class TestUSPSConfig:
    def test_valid_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        cfg = USPSConfig()
        assert cfg.consumer_key == "key"
        assert cfg.consumer_secret == "secret"
        assert cfg.rate_limit_rps == 5.0
        assert cfg.daily_limit == 10000

    def test_custom_rps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("USPS_RATE_LIMIT_RPS", "10.0")
        cfg = USPSConfig()
        assert cfg.rate_limit_rps == 10.0

    def test_rps_below_one_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("USPS_RATE_LIMIT_RPS", "0.5")
        with pytest.raises(ValueError, match="USPS_RATE_LIMIT_RPS"):
            USPSConfig()

    def test_zero_rps_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("USPS_RATE_LIMIT_RPS", "0")
        with pytest.raises(ValueError, match="USPS_RATE_LIMIT_RPS"):
            USPSConfig()

    def test_negative_rps_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("USPS_RATE_LIMIT_RPS", "-1.0")
        with pytest.raises(ValueError, match="USPS_RATE_LIMIT_RPS"):
            USPSConfig()

    def test_zero_daily_limit_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("USPS_DAILY_LIMIT", "0")
        with pytest.raises(ValueError, match="USPS_DAILY_LIMIT"):
            USPSConfig()

    def test_custom_daily_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
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
        """provider_names parses CSV into lowercase list, filtering 'none'."""
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
        validate_config()  # must not raise

    def test_usps_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("VALIDATION_CACHE_DSN", "postgresql+asyncpg://localhost/test")
        validate_config()  # must not raise

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
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        with pytest.raises(ValueError, match="USPS_CONSUMER_KEY"):
            validate_config()

    def test_unknown_provider_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "smarty")
        with pytest.raises(ValueError, match="smarty"):
            validate_config()

    def test_google_valid(
        self, monkeypatch: pytest.MonkeyPatch, mock_google_auth
    ) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "google")
        monkeypatch.setenv("VALIDATION_CACHE_DSN", "postgresql+asyncpg://localhost/test")
        validate_config()  # must not raise

    def test_none_skips_ttl_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "none")
        monkeypatch.setenv("VALIDATION_CACHE_TTL_DAYS", "abc")
        validate_config()  # must not raise

    def test_logs_active_provider(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "usps")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        monkeypatch.setenv("VALIDATION_CACHE_DSN", "postgresql+asyncpg://localhost/test")
        import logging

        with caplog.at_level(
            logging.INFO, logger="address_validator.services.validation.config"
        ):
            validate_config()
        assert any("usps" in r.message for r in caplog.records)

    def test_logs_none_provider(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.delenv("VALIDATION_PROVIDER", raising=False)
        import logging

        with caplog.at_level(
            logging.INFO, logger="address_validator.services.validation.config"
        ):
            validate_config()
        assert any("none" in r.message for r in caplog.records)
```

Note: `mock_google_auth` is currently defined in the factory test file. We'll need it in a shared conftest. For now, add it locally to this test file:

```python
from unittest.mock import MagicMock, patch

@pytest.fixture()
def mock_google_auth():
    """Patch get_credentials to return fake credentials."""
    creds = MagicMock()
    creds.token = "fake-token"
    creds.valid = True
    with patch(
        "address_validator.services.validation.gcp_auth.google.auth.default"
    ) as mock_default:
        mock_default.return_value = (creds, "fake-project")
        yield mock_default
```

- [ ] **Step 2: Run tests — expect FAIL (module doesn't exist)**

Run: `uv run pytest tests/unit/validation/test_config.py -x --no-cov`
Expected: `ModuleNotFoundError: No module named 'address_validator.services.validation.config'`

- [ ] **Step 3: Implement config.py**

Create `src/address_validator/services/validation/config.py`:

```python
"""Pydantic-settings models for validation provider configuration.

Replaces the manual ``_parse_*`` functions that were in ``factory.py``.
Each model reads env vars via its ``env_prefix`` at construction time.
"""

import logging

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from address_validator.services.validation.gcp_auth import get_credentials

logger = logging.getLogger(__name__)

_SUPPORTED_PROVIDERS = ("none", "usps", "google")


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
            raise ValueError(
                "VALIDATION_LATENCY_BUDGET_S must be a positive number (e.g. '1.0')"
            )
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
        return [
            s
            for n in self.provider.split(",")
            if (s := n.strip().lower()) and s != "none"
        ]


def _check_provider_config(name: str) -> None:
    """Validate credentials for a single provider name (no object construction)."""
    if name == "usps":
        USPSConfig()
    elif name == "google":
        GoogleConfig()
        get_credentials()
    else:
        raise ValueError(
            f"Unknown provider name: '{name}'. "
            f"Supported values: {', '.join(repr(p) for p in _SUPPORTED_PROVIDERS)}."
        )


def validate_config() -> None:
    """Validate all provider configuration at startup.

    Reads env vars, checks required credentials are present, and validates
    rate-limit/budget/TTL values.  Raises :exc:`ValueError` on misconfiguration.
    """
    try:
        val_cfg = ValidationConfig()
    except Exception as exc:
        raise ValueError(str(exc)) from None

    names = val_cfg.provider_names

    if not names:
        logger.info("validate_config: provider=none")
        return

    for name in names:
        _check_provider_config(name)

    cache_dsn = val_cfg.cache_dsn.strip()
    if not cache_dsn:
        raise ValueError(
            "VALIDATION_CACHE_DSN must be set when a non-null validation provider is configured "
            "(e.g. 'postgresql+asyncpg://user:pass@localhost/address_validator')"
        )

    logger.info(
        "validate_config: provider=%s ttl=%d days", ",".join(names), val_cfg.cache_ttl_days
    )
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `uv run pytest tests/unit/validation/test_config.py -x --no-cov`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/services/validation/config.py tests/unit/validation/test_config.py
git commit -m "#50 feat: add pydantic-settings config models for validation providers"
```

---

### Task 3: Create `validation/registry.py` — ProviderRegistry class

**Files:**
- Create: `src/address_validator/services/validation/registry.py`
- Test: `tests/unit/validation/test_registry.py`

The `ProviderRegistry` holds all provider state on an instance (no globals). It replaces `_get_http_client`, `_get_usps_provider`, `_get_google_provider`, `_get_caching_provider`, and `_resolve_provider` from `factory.py`.

Key differences from `factory.py`:
- State lives on `self` — no `global` statements
- Config objects are passed to `__init__` (not read from env at call time)
- Google wiring split into `_discover_google_quota` and `_setup_reconciliation`
- `get_quota_info()` public method replaces the private-poking in `_config.py`
- `close()` method for HTTP client cleanup

- [ ] **Step 1: Write tests for ProviderRegistry**

Create `tests/unit/validation/test_registry.py`:

```python
"""Unit tests for ProviderRegistry."""

from unittest.mock import MagicMock, patch

import pytest

import address_validator.services.validation.cache_db as cache_db_module
from address_validator.services.validation._rate_limit import FixedResetQuotaWindow
from address_validator.services.validation.cache_provider import CachingProvider
from address_validator.services.validation.chain_provider import ChainProvider
from address_validator.services.validation.config import (
    GoogleConfig,
    USPSConfig,
    ValidationConfig,
)
from address_validator.services.validation.google_provider import GoogleProvider
from address_validator.services.validation.null_provider import NullProvider
from address_validator.services.validation.registry import ProviderRegistry
from address_validator.services.validation.usps_provider import USPSProvider


@pytest.fixture(autouse=True)
async def _cleanup_engine() -> None:
    yield
    await cache_db_module.close_engine()


@pytest.fixture()
def mock_google_auth():
    """Patch get_credentials to return fake credentials."""
    creds = MagicMock()
    creds.token = "fake-token"
    creds.valid = True
    with patch(
        "address_validator.services.validation.gcp_auth.google.auth.default"
    ) as mock_default:
        mock_default.return_value = (creds, "fake-project")
        yield mock_default


def _make_registry(
    monkeypatch: pytest.MonkeyPatch,
    provider: str = "none",
    **env_overrides: str,
) -> ProviderRegistry:
    """Helper to build a ProviderRegistry from env vars."""
    monkeypatch.setenv("VALIDATION_PROVIDER", provider)
    for k, v in env_overrides.items():
        monkeypatch.setenv(k, v)
    val_cfg = ValidationConfig()
    return ProviderRegistry(val_cfg)


class TestGetProvider:
    def test_default_is_null(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VALIDATION_PROVIDER", raising=False)
        reg = ProviderRegistry(ValidationConfig())
        assert isinstance(reg.get_provider(), NullProvider)

    def test_none_keyword_gives_null(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(monkeypatch, provider="none")
        assert isinstance(reg.get_provider(), NullProvider)

    def test_none_keyword_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "NONE")
        reg = ProviderRegistry(ValidationConfig())
        assert isinstance(reg.get_provider(), NullProvider)

    def test_usps_gives_caching_usps(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(
            monkeypatch,
            provider="usps",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
        )
        result = reg.get_provider()
        assert isinstance(result, CachingProvider)
        assert isinstance(result._inner, USPSProvider)

    def test_usps_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "USPS")
        monkeypatch.setenv("USPS_CONSUMER_KEY", "key")
        monkeypatch.setenv("USPS_CONSUMER_SECRET", "secret")
        reg = ProviderRegistry(ValidationConfig())
        result = reg.get_provider()
        assert isinstance(result, CachingProvider)
        assert isinstance(result._inner, USPSProvider)

    def test_provider_is_singleton(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(
            monkeypatch,
            provider="usps",
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
        reg = _make_registry(monkeypatch, provider="smarty")
        with pytest.raises(ValueError, match="smarty"):
            reg.get_provider()

    def test_unknown_provider_error_mentions_google(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reg = _make_registry(monkeypatch, provider="smarty")
        with pytest.raises(ValueError, match="google"):
            reg.get_provider()

    def test_google_gives_caching_google(
        self, monkeypatch: pytest.MonkeyPatch, mock_google_auth
    ) -> None:
        reg = _make_registry(monkeypatch, provider="google")
        result = reg.get_provider()
        assert isinstance(result, CachingProvider)
        assert isinstance(result._inner, GoogleProvider)

    def test_google_case_insensitive(
        self, monkeypatch: pytest.MonkeyPatch, mock_google_auth
    ) -> None:
        monkeypatch.setenv("VALIDATION_PROVIDER", "GOOGLE")
        reg = ProviderRegistry(ValidationConfig())
        result = reg.get_provider()
        assert isinstance(result, CachingProvider)
        assert isinstance(result._inner, GoogleProvider)

    def test_google_singleton(
        self, monkeypatch: pytest.MonkeyPatch, mock_google_auth
    ) -> None:
        reg = _make_registry(monkeypatch, provider="google")
        assert reg.get_provider() is reg.get_provider()

    def test_null_returned_unwrapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VALIDATION_PROVIDER", raising=False)
        reg = ProviderRegistry(ValidationConfig())
        result = reg.get_provider()
        assert isinstance(result, NullProvider)
        assert not isinstance(result, CachingProvider)

    def test_chain_provider(
        self, monkeypatch: pytest.MonkeyPatch, mock_google_auth
    ) -> None:
        reg = _make_registry(
            monkeypatch,
            provider="usps,google",
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
            provider="usps,google",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
        )
        result = reg.get_provider()
        chain = result._inner
        assert isinstance(chain._providers[0], USPSProvider)
        assert isinstance(chain._providers[1], GoogleProvider)

    def test_chain_google_then_usps(
        self, monkeypatch: pytest.MonkeyPatch, mock_google_auth
    ) -> None:
        reg = _make_registry(
            monkeypatch,
            provider="google,usps",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
        )
        result = reg.get_provider()
        chain = result._inner
        assert isinstance(chain._providers[0], GoogleProvider)
        assert isinstance(chain._providers[1], USPSProvider)

    def test_usps_rps_configures_window(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(
            monkeypatch,
            provider="usps",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
            USPS_RATE_LIMIT_RPS="10.0",
        )
        result = reg.get_provider()
        usps: USPSProvider = result._inner  # type: ignore[assignment]
        guard = usps._client._rate_limiter
        assert guard._windows[0].limit == 10
        assert guard._windows[0].duration_s == 1.0

    def test_usps_daily_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(
            monkeypatch,
            provider="usps",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
            USPS_DAILY_LIMIT="5000",
        )
        result = reg.get_provider()
        usps: USPSProvider = result._inner  # type: ignore[assignment]
        guard = usps._client._rate_limiter
        assert guard._windows[1].limit == 5000
        assert guard._windows[1].duration_s == 86_400.0

    def test_google_rpm(
        self, monkeypatch: pytest.MonkeyPatch, mock_google_auth
    ) -> None:
        reg = _make_registry(
            monkeypatch,
            provider="google",
            GOOGLE_RATE_LIMIT_RPM="10",
        )
        result = reg.get_provider()
        google: GoogleProvider = result._inner  # type: ignore[assignment]
        guard = google._client._rate_limiter
        assert guard._windows[0].limit == 10
        assert guard._windows[0].duration_s == 60.0

    def test_google_daily_limit(
        self, monkeypatch: pytest.MonkeyPatch, mock_google_auth
    ) -> None:
        reg = _make_registry(monkeypatch, provider="google", GOOGLE_DAILY_LIMIT="80")
        result = reg.get_provider()
        google: GoogleProvider = result._inner  # type: ignore[assignment]
        guard = google._client._rate_limiter
        assert guard._windows[1].limit == 80
        assert guard._windows[1].mode == "hard"

    def test_google_daily_window_is_fixed_reset(
        self, monkeypatch: pytest.MonkeyPatch, mock_google_auth
    ) -> None:
        reg = _make_registry(monkeypatch, provider="google")
        result = reg.get_provider()
        google: GoogleProvider = result._inner  # type: ignore[assignment]
        guard = google._client._rate_limiter
        assert isinstance(guard._windows[1], FixedResetQuotaWindow)

    def test_unknown_in_list_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(
            monkeypatch,
            provider="usps,smarty",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
        )
        with pytest.raises(ValueError, match="smarty"):
            reg.get_provider()

    def test_none_mixed_with_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(
            monkeypatch,
            provider="none,usps",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
        )
        result = reg.get_provider()
        assert isinstance(result, CachingProvider)
        assert isinstance(result._inner, USPSProvider)

    def test_ttl_default_30(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VALIDATION_CACHE_TTL_DAYS", raising=False)
        reg = _make_registry(
            monkeypatch,
            provider="usps",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
        )
        result = reg.get_provider()
        assert isinstance(result, CachingProvider)
        assert result._ttl_days == 30

    def test_ttl_custom(self, monkeypatch: pytest.MonkeyPatch) -> None:
        reg = _make_registry(
            monkeypatch,
            provider="usps",
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
            provider="usps",
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
            provider="usps",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
        )
        reg.get_provider()
        quota = reg.get_quota_info()
        assert len(quota) == 1
        assert quota[0]["provider"] == "usps"
        assert "remaining" in quota[0]
        assert "limit" in quota[0]

    def test_google_quota(
        self, monkeypatch: pytest.MonkeyPatch, mock_google_auth
    ) -> None:
        reg = _make_registry(monkeypatch, provider="google")
        reg.get_provider()
        quota = reg.get_quota_info()
        assert len(quota) == 1
        assert quota[0]["provider"] == "google"

    def test_chain_both_quotas(
        self, monkeypatch: pytest.MonkeyPatch, mock_google_auth
    ) -> None:
        reg = _make_registry(
            monkeypatch,
            provider="usps,google",
            USPS_CONSUMER_KEY="key",
            USPS_CONSUMER_SECRET="secret",
        )
        reg.get_provider()
        quota = reg.get_quota_info()
        assert len(quota) == 2
        providers = {q["provider"] for q in quota}
        assert providers == {"usps", "google"}
```

- [ ] **Step 2: Run tests — expect FAIL**

Run: `uv run pytest tests/unit/validation/test_registry.py -x --no-cov`
Expected: `ModuleNotFoundError: No module named 'address_validator.services.validation.registry'`

- [ ] **Step 3: Implement registry.py**

Create `src/address_validator/services/validation/registry.py`:

```python
"""ProviderRegistry — singleton-free provider lifecycle management.

Replaces the module-level globals in the former ``factory.py``.  A single
instance is created in the FastAPI lifespan and stored on ``app.state``.
"""

import logging

import httpx

from address_validator.services.validation import cache_db
from address_validator.services.validation._rate_limit import (
    FixedResetQuotaWindow,
    QuotaGuard,
    QuotaWindow,
)
from address_validator.services.validation.cache_provider import CachingProvider
from address_validator.services.validation.chain_provider import ChainProvider
from address_validator.services.validation.config import (
    GoogleConfig,
    USPSConfig,
    ValidationConfig,
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

_SUPPORTED_PROVIDERS = ("none", "usps", "google")


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
            if not hasattr(prov, "_client") or not hasattr(prov._client, "_rate_limiter"):
                continue
            guard = prov._client._rate_limiter
            if len(guard._windows) > 1:
                quota.append(
                    {
                        "provider": name,
                        "remaining": int(guard._tokens[1]),
                        "limit": guard._windows[1].limit,
                    }
                )
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
            return self._build_usps_provider(USPSConfig(), budget)
        if name == "google":
            return self._build_google_provider(GoogleConfig(), budget)
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

    def _build_google_provider(
        self, cfg: GoogleConfig, latency_budget_s: float
    ) -> GoogleProvider:
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
                logger.info(
                    "get_provider: seeded daily quota from Monitoring (used=%d, remaining=%d)",
                    usage,
                    guard._windows[1].limit - usage,
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
            get_engine=cache_db.get_engine,
            ttl_days=self._config.cache_ttl_days,
        )
```

- [ ] **Step 4: Run tests — expect PASS**

Run: `uv run pytest tests/unit/validation/test_registry.py -x --no-cov`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/services/validation/registry.py tests/unit/validation/test_registry.py
git commit -m "#50 feat: add ProviderRegistry class replacing factory globals"
```

---

### Task 4: Wire registry into main.py and validate router

**Files:**
- Modify: `src/address_validator/main.py:24-30,63-90`
- Modify: `src/address_validator/routers/v1/validate.py:45-46,114`

The lifespan now creates a `ProviderRegistry` instance and stores it on `app.state`. The validate router reads from `request.app.state.registry` instead of calling the old `get_provider()` function.

- [ ] **Step 1: Update main.py lifespan**

In `src/address_validator/main.py`, replace the factory imports (lines 25-29) with:

```python
from address_validator.services.validation.cache_db import close_engine
from address_validator.services.validation.config import validate_config
from address_validator.services.validation.registry import ProviderRegistry
from address_validator.services.validation.config import ValidationConfig
```

Replace the lifespan function (lines 63-90) with:

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan context — validate config on startup, close DB on shutdown."""
    validate_config()

    config = ValidationConfig()
    registry = ProviderRegistry(config)

    # Eagerly construct provider singletons so quota sync wiring runs at boot
    registry.get_provider()
    app.state.registry = registry

    # Start reconciliation background task if Google provider is active
    reconciliation_task = None
    params = registry.get_reconciliation_params()
    if params:
        reconciliation_task = asyncio.create_task(
            run_reconciliation_loop(**params),
            name="google-quota-reconciliation",
        )

    yield

    # Cancel reconciliation task on shutdown
    if reconciliation_task is not None:
        reconciliation_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reconciliation_task

    await registry.close()
    await close_engine()
```

- [ ] **Step 2: Update validate router**

In `src/address_validator/routers/v1/validate.py`:

Replace the import (line 45):
```python
from address_validator.services.validation.factory import get_provider
```
with:
```python
from fastapi import Request as _Request
```

Add `request: _Request` parameter to the handler function signature (line 87):
```python
async def validate_address_v1(req: ValidateRequestV1, request: _Request) -> ValidateResponseV1:
```

Note: `Request` is already imported on line 37 as a FastAPI dependency. However, looking more carefully at the imports — `Request` is NOT imported in validate.py. We need to add it. Actually, we can just use `fastapi.Request` — but we need to import it. Let's update the import section.

Replace line 45:
```python
from address_validator.services.validation.factory import get_provider
```

And replace line 114:
```python
    provider = get_provider()
```
with:
```python
    provider = request.app.state.registry.get_provider()
```

We need to add `Request` to the imports. The file already imports from `fastapi` on line 37:
```python
from fastapi import APIRouter, Depends
```
Change to:
```python
from fastapi import APIRouter, Depends, Request
```

And add `request: Request` to the handler signature on line 87:
```python
async def validate_address_v1(req: ValidateRequestV1, request: Request) -> ValidateResponseV1:
```

- [ ] **Step 3: Run existing tests to verify nothing breaks**

Run: `uv run pytest tests/integration/test_lifespan.py tests/unit/test_validate_router.py -x --no-cov`
Expected: PASS. The lifespan tests use `TestClient(app)` which triggers the lifespan. The validate router tests patch `get_provider` — these will need updating since the patch target changed.

If validate router tests fail due to patching: update the patch target in `tests/unit/test_validate_router.py` from `"address_validator.routers.v1.validate.get_provider"` to use `request.app.state.registry.get_provider`. The simplest approach is to patch `app.state.registry`:

In `tests/unit/test_validate_router.py`, for each test that patches `get_provider`, change:
```python
with patch("address_validator.routers.v1.validate.get_provider", return_value=...):
```
to set up the mock on `app.state`:
```python
from address_validator.main import app
# In each test:
app.state.registry = MagicMock()
app.state.registry.get_provider.return_value = ...
```

Actually — let's read the full test file first to understand the pattern.

- [ ] **Step 4: Update validate router tests**

Read `tests/unit/test_validate_router.py` and update all `patch("address_validator.routers.v1.validate.get_provider", ...)` calls. The pattern becomes:

```python
# At module level or in a fixture:
from unittest.mock import MagicMock, PropertyMock

# In each test, replace patch context managers with app.state.registry mock:
mock_registry = MagicMock()
mock_registry.get_provider.return_value = _make_null_provider(NULL_RESPONSE)
app.state.registry = mock_registry
# then make the request
```

Since `client` is session-scoped, we need to be careful. Use `monkeypatch.setattr(app.state, "registry", mock_registry)` or set/restore in each test.

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest --no-cov -x`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/main.py src/address_validator/routers/v1/validate.py tests/unit/test_validate_router.py
git commit -m "#50 refactor: wire ProviderRegistry into lifespan and validate router"
```

---

### Task 5: Update admin dashboard to use registry

**Files:**
- Modify: `src/address_validator/routers/admin/_config.py:1-53`
- Modify: `src/address_validator/routers/admin/dashboard.py:7,51`
- Modify: `src/address_validator/routers/admin/providers.py:9,53`

The admin `_config.py` currently reaches into `factory._usps_provider` and `factory._google_provider` (private attributes with `# noqa: SLF001` suppressions). Replace with a call to `registry.get_quota_info()`, reading the registry from `request.app.state`.

- [ ] **Step 1: Rewrite `_config.py` get_quota_info to accept registry**

Replace `get_quota_info()` in `_config.py` with a version that takes a `request` parameter:

```python
from fastapi import Request

def get_quota_info(request: Request) -> list[dict]:
    """Read current quota state from the provider registry."""
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        return []
    return registry.get_quota_info()
```

Remove the `from address_validator.services.validation import factory` import.

- [ ] **Step 2: Update dashboard.py**

In `dashboard.py`, line 51, change:
```python
"quota": get_quota_info(),
```
to:
```python
"quota": get_quota_info(request),
```

- [ ] **Step 3: Update providers.py**

In `providers.py`, line 53, change:
```python
for q in get_quota_info():
```
to:
```python
for q in get_quota_info(request):
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest --no-cov -x`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/address_validator/routers/admin/_config.py src/address_validator/routers/admin/dashboard.py src/address_validator/routers/admin/providers.py
git commit -m "#50 refactor: admin dashboard reads quota from registry instead of factory globals"
```

---

### Task 6: Delete factory.py and old tests

**Files:**
- Delete: `src/address_validator/services/validation/factory.py`
- Delete: `tests/unit/validation/test_provider_factory.py`

- [ ] **Step 1: Search for remaining factory imports**

Run: `grep -r "from.*factory import\|import.*factory" src/ tests/` and verify no remaining references.

If any remain (e.g. docstrings in other modules referencing `factory`), update them to reference `registry` or `config` as appropriate.

- [ ] **Step 2: Delete factory.py**

```bash
git rm src/address_validator/services/validation/factory.py
```

- [ ] **Step 3: Delete old factory test file**

```bash
git rm tests/unit/validation/test_provider_factory.py
```

- [ ] **Step 4: Update docstring references**

Several modules reference `factory.get_provider` in docstrings:
- `src/address_validator/services/validation/usps_provider.py`
- `src/address_validator/services/validation/usps_client.py`
- `src/address_validator/services/validation/google_client.py`
- `src/address_validator/services/validation/google_provider.py`
- `src/address_validator/services/validation/chain_provider.py`
- `src/address_validator/routers/v1/validate.py` (module docstring line 29)

Update these references from `factory.get_provider` to `registry.ProviderRegistry`.

- [ ] **Step 5: Run full test suite + lint**

Run: `uv run pytest --no-cov -x && uv run ruff check .`
Expected: all pass, no lint errors.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "#50 refactor: delete factory.py, migrate all references to config + registry"
```

---

### Task 7: Final verification — full test suite with coverage

**Files:** None (verification only)

- [ ] **Step 1: Run full test suite with coverage**

Run: `uv run pytest`
Expected: all tests pass, coverage >= 80% (baseline ~93%).

- [ ] **Step 2: Run linter**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: clean.

- [ ] **Step 3: Verify service starts**

Run: `sudo systemctl restart address-validator && sleep 2 && curl -s http://localhost:8000/api/v1/health | python3 -m json.tool`
Expected: `{"status": "healthy", ...}`

- [ ] **Step 4: Final commit (if any fixups needed)**

Only if Steps 1-3 surfaced issues.

---

### Task 8: Update AGENTS.md sensitive areas table

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Update sensitive areas**

In the `Sensitive areas` table in `AGENTS.md`:
- Replace the `factory.py` entries with equivalent entries for `config.py` and `registry.py`
- Update the description to reference the new module structure

The old entry:
```
| `src/address_validator/services/validation/factory.py` | Module-level singletons ... |
```

New entries:
```
| `src/address_validator/services/validation/config.py` | `validate_config()` is called from the lifespan startup hook and raises `ValueError` on misconfiguration; pydantic-settings validators enforce business rules — changes affect all env-var parsing |
| `src/address_validator/services/validation/registry.py` | `ProviderRegistry` owns provider lifecycle — `_build_google_provider` mixes credential resolution, quota discovery, monitoring, and reconciliation wiring; `get_quota_info()` exposes internal guard state to admin dashboard |
```

- [ ] **Step 2: Commit**

```bash
git add AGENTS.md
git commit -m "#50 docs: update AGENTS.md sensitive areas for config + registry"
```
