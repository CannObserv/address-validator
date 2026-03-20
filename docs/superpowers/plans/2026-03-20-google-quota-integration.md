# Google Cloud Quota Integration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate Cloud Quotas and Cloud Monitoring APIs to auto-discover limits, seed daily quota usage on boot, and periodically reconcile — while migrating Google auth from API key to ADC.

**Architecture:** Three new modules (`gcp_auth.py`, `gcp_quota_sync.py`, `_rate_limit.py` extension) layered beneath the existing factory. `gcp_auth.py` handles ADC + project ID resolution. `gcp_quota_sync.py` wraps both GCP client libraries and exposes `fetch_daily_limit()`, `fetch_daily_usage()`, and an async `run_reconciliation_loop()`. The factory wires these into provider creation; the lifespan manages the background task.

**Tech Stack:** `google-cloud-quotas`, `google-cloud-monitoring`, `google-auth`, Python `zoneinfo` (stdlib)

**Design doc:** `docs/plans/2026-03-20-google-quota-integration-design.md`

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `src/address_validator/services/validation/gcp_auth.py` | ADC credential loading, project ID resolution |
| Create | `src/address_validator/services/validation/gcp_quota_sync.py` | Cloud Quotas + Monitoring API calls, reconciliation loop |
| Create | `tests/unit/validation/test_gcp_auth.py` | Tests for gcp_auth |
| Create | `tests/unit/validation/test_gcp_quota_sync.py` | Tests for gcp_quota_sync |
| Create | `tests/unit/validation/test_fixed_reset_window.py` | Tests for FixedResetQuotaWindow |
| Modify | `src/address_validator/services/validation/_rate_limit.py` | Add `FixedResetQuotaWindow`, add `adjust_tokens()` to `QuotaGuard` |
| Modify | `src/address_validator/services/validation/google_client.py:46-54,88-94` | Replace API key auth with ADC bearer token |
| Modify | `src/address_validator/services/validation/factory.py:138-161,211-228,244-259,262-275` | Replace `_parse_google_config` with ADC, wire quota sync |
| Modify | `src/address_validator/main.py:49-54` | Start/stop reconciliation task in lifespan |
| Modify | `pyproject.toml:6-17` | Add google-cloud-quotas, google-cloud-monitoring, google-auth deps |
| Modify | `tests/unit/validation/test_provider_factory.py` | Update Google tests: remove API key refs, mock ADC |
| Modify | `tests/unit/validation/test_google_client.py:15,46-54,218-223` | Replace API key with ADC bearer token |
| Modify | `tests/unit/validation/test_rate_limit.py` | Add tests for adjust_tokens method |
| Modify | `docs/VALIDATION-PROVIDERS.md` | Update auth, env vars, quota sync docs |

---

### Task 1: Add Dependencies

**Files:**
- Modify: `pyproject.toml:6-17`

- [ ] **Step 1: Add google-cloud-quotas, google-cloud-monitoring, google-auth**

```bash
uv add "google-cloud-quotas>=0.5,<1" "google-cloud-monitoring>=2.0,<3" "google-auth>=2.0,<3"
```

- [ ] **Step 2: Verify installation**

Run: `uv run python -c "import google.cloud.quotas_v1; import google.cloud.monitoring_v3; import google.auth; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "#42 chore: add google-cloud-quotas, google-cloud-monitoring, google-auth deps"
```

---

### Task 2: FixedResetQuotaWindow

Add a wall-clock-aligned daily window that resets at midnight Pacific Time. Google-specific — USPS keeps its rolling `QuotaWindow`.

**Files:**
- Modify: `src/address_validator/services/validation/_rate_limit.py:33-61`
- Create: `tests/unit/validation/test_fixed_reset_window.py`

- [ ] **Step 1: Write failing tests for FixedResetQuotaWindow**

Create `tests/unit/validation/test_fixed_reset_window.py`:

```python
"""Unit tests for FixedResetQuotaWindow."""

from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from address_validator.services.validation._rate_limit import FixedResetQuotaWindow

PT = ZoneInfo("America/Los_Angeles")


class TestFixedResetQuotaWindow:
    def test_is_frozen_dataclass(self) -> None:
        w = FixedResetQuotaWindow(limit=160, mode="hard")
        with pytest.raises(AttributeError):
            w.limit = 200  # type: ignore[misc]

    def test_limit_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="limit"):
            FixedResetQuotaWindow(limit=0, mode="hard")

    def test_default_timezone_is_pacific(self) -> None:
        w = FixedResetQuotaWindow(limit=160, mode="hard")
        assert w.timezone == PT

    def test_seconds_until_reset_near_midnight(self) -> None:
        # 11:59:00 PM PT → 60 seconds until midnight
        fake_now = datetime(2026, 3, 20, 23, 59, 0, tzinfo=PT)
        w = FixedResetQuotaWindow(limit=160, mode="hard")
        with patch("address_validator.services.validation._rate_limit._now_in_tz", return_value=fake_now):
            assert w.seconds_until_reset() == pytest.approx(60.0, abs=1.0)

    def test_seconds_until_reset_at_start_of_day(self) -> None:
        # 12:00:01 AM PT → ~86399 seconds until next midnight
        fake_now = datetime(2026, 3, 20, 0, 0, 1, tzinfo=PT)
        w = FixedResetQuotaWindow(limit=160, mode="hard")
        with patch("address_validator.services.validation._rate_limit._now_in_tz", return_value=fake_now):
            remaining = w.seconds_until_reset()
            assert 86_398 <= remaining <= 86_400

    def test_should_reset_true_after_midnight(self) -> None:
        w = FixedResetQuotaWindow(limit=160, mode="hard")
        # Last reset was yesterday
        yesterday = datetime(2026, 3, 19, 0, 0, 0, tzinfo=PT)
        now = datetime(2026, 3, 20, 0, 0, 1, tzinfo=PT)
        with patch("address_validator.services.validation._rate_limit._now_in_tz", return_value=now):
            assert w.should_reset(yesterday) is True

    def test_should_reset_false_same_day(self) -> None:
        w = FixedResetQuotaWindow(limit=160, mode="hard")
        today_morning = datetime(2026, 3, 20, 8, 0, 0, tzinfo=PT)
        today_afternoon = datetime(2026, 3, 20, 14, 0, 0, tzinfo=PT)
        with patch("address_validator.services.validation._rate_limit._now_in_tz", return_value=today_afternoon):
            assert w.should_reset(today_morning) is False

    def test_mode_soft_allowed(self) -> None:
        w = FixedResetQuotaWindow(limit=160, mode="soft")
        assert w.mode == "soft"

    def test_mode_hard_allowed(self) -> None:
        w = FixedResetQuotaWindow(limit=160, mode="hard")
        assert w.mode == "hard"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/validation/test_fixed_reset_window.py -v --no-cov -x`
Expected: FAIL — `FixedResetQuotaWindow` not defined

- [ ] **Step 3: Implement FixedResetQuotaWindow**

Add to `src/address_validator/services/validation/_rate_limit.py` after the existing `QuotaWindow` class (after line 61):

```python
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_PACIFIC = ZoneInfo("America/Los_Angeles")


def _now_in_tz(tz: ZoneInfo) -> datetime:
    """Return the current wall-clock time in *tz*.  Extracted for test mocking."""
    return datetime.now(tz)


@dataclass(frozen=True)
class FixedResetQuotaWindow:
    """Daily quota window that resets at midnight in a fixed timezone.

    Unlike :class:`QuotaWindow` which uses a rolling token-bucket duration,
    this window resets to full capacity when the wall-clock day changes in the
    configured timezone.  Designed for Google Cloud quotas that reset at
    midnight Pacific Time.

    Parameters
    ----------
    limit:
        Maximum requests allowed per calendar day.
    mode:
        ``"soft"`` or ``"hard"`` — same semantics as :class:`QuotaWindow`.
    timezone:
        Timezone for the daily reset boundary.  Defaults to
        ``America/Los_Angeles`` (Pacific Time).
    """

    limit: int
    mode: Literal["soft", "hard"]
    timezone: ZoneInfo = _PACIFIC

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError(f"FixedResetQuotaWindow.limit must be positive, got {self.limit}")

    def seconds_until_reset(self) -> float:
        """Seconds remaining until the next midnight in this window's timezone."""
        now = _now_in_tz(self.timezone)
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        next_midnight = midnight + timedelta(days=1)
        return (next_midnight - now).total_seconds()

    def should_reset(self, last_reset: datetime) -> bool:
        """Return True if *last_reset* was on a different calendar day than now."""
        now = _now_in_tz(self.timezone)
        return now.date() != last_reset.date()
```

Add `from datetime import timedelta` to the existing datetime import.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/validation/test_fixed_reset_window.py -v --no-cov -x`
Expected: All PASS

- [ ] **Step 5: Run ruff**

Run: `uv run ruff check src/address_validator/services/validation/_rate_limit.py`
Expected: Clean

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/services/validation/_rate_limit.py tests/unit/validation/test_fixed_reset_window.py
git commit -m "#42 feat: add FixedResetQuotaWindow for wall-clock daily reset"
```

---

### Task 3: QuotaGuard — adjust_tokens and FixedResetQuotaWindow Support

Extend `QuotaGuard` to accept `FixedResetQuotaWindow` alongside `QuotaWindow`, and add `adjust_tokens()` for reconciliation.

**Files:**
- Modify: `src/address_validator/services/validation/_rate_limit.py:63-144`
- Modify: `tests/unit/validation/test_rate_limit.py`

- [ ] **Step 1: Write failing tests for adjust_tokens**

Append to `tests/unit/validation/test_rate_limit.py` in class `TestQuotaGuard`:

```python
    def test_adjust_tokens_decreases_tokens(self) -> None:
        guard = self._soft_guard(limit=100, duration_s=86_400.0)
        guard.adjust_tokens(0, -30)
        assert guard._tokens[0] == 70.0

    def test_adjust_tokens_does_not_go_below_zero(self) -> None:
        guard = self._soft_guard(limit=100, duration_s=86_400.0)
        guard.adjust_tokens(0, -200)
        assert guard._tokens[0] == 0.0

    def test_adjust_tokens_does_not_exceed_limit(self) -> None:
        guard = self._soft_guard(limit=100, duration_s=86_400.0)
        guard._tokens[0] = 50.0
        guard.adjust_tokens(0, 100)
        assert guard._tokens[0] == 100.0

    def test_adjust_tokens_raises_for_invalid_index(self) -> None:
        guard = self._soft_guard(limit=100, duration_s=86_400.0)
        with pytest.raises(IndexError):
            guard.adjust_tokens(5, -10)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/validation/test_rate_limit.py::TestQuotaGuard::test_adjust_tokens_decreases_tokens -v --no-cov -x`
Expected: FAIL — `QuotaGuard` has no `adjust_tokens`

- [ ] **Step 3: Implement adjust_tokens**

Add to `QuotaGuard` class in `_rate_limit.py` after the `acquire` method (after line 144):

```python
    def adjust_tokens(self, window_index: int, delta: float) -> None:
        """Adjust the token count for a specific window by *delta*.

        Clamps the result to ``[0, window.limit]``.  Intended for
        reconciliation — call under external synchronisation if needed.

        Parameters
        ----------
        window_index:
            Index into the windows list.
        delta:
            Positive to add tokens, negative to remove.
        """
        window = self._windows[window_index]  # raises IndexError if out of range
        self._tokens[window_index] = max(
            0.0, min(float(window.limit), self._tokens[window_index] + delta)
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/validation/test_rate_limit.py -v --no-cov -x`
Expected: All PASS

- [ ] **Step 5: Write failing tests for FixedResetQuotaWindow in QuotaGuard**

Append to `tests/unit/validation/test_rate_limit.py` in class `TestQuotaGuard`:

```python
    def test_accepts_fixed_reset_window(self) -> None:
        from address_validator.services.validation._rate_limit import FixedResetQuotaWindow
        guard = QuotaGuard(
            windows=[
                QuotaWindow(limit=5, duration_s=60.0, mode="soft"),
                FixedResetQuotaWindow(limit=160, mode="hard"),
            ],
            provider_name="google",
        )
        assert len(guard._windows) == 2
        assert guard._tokens[1] == 160.0

    @pytest.mark.asyncio
    async def test_fixed_reset_window_resets_at_midnight(self) -> None:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from address_validator.services.validation._rate_limit import FixedResetQuotaWindow

        PT = ZoneInfo("America/Los_Angeles")
        guard = QuotaGuard(
            windows=[FixedResetQuotaWindow(limit=160, mode="hard")],
            provider_name="google",
        )
        # Drain tokens and simulate last refill was yesterday
        guard._tokens[0] = 0.0
        yesterday = datetime(2026, 3, 19, 23, 0, 0, tzinfo=PT)
        guard._last_reset = [yesterday]

        today = datetime(2026, 3, 20, 0, 1, 0, tzinfo=PT)
        with patch("address_validator.services.validation._rate_limit._now_in_tz", return_value=today):
            await guard.acquire()
        # Tokens should have been reset to full, then 1 consumed
        assert guard._tokens[0] == 159.0
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `uv run pytest tests/unit/validation/test_rate_limit.py::TestQuotaGuard::test_fixed_reset_window_resets_at_midnight -v --no-cov -x`
Expected: FAIL

- [ ] **Step 7: Update QuotaGuard to support FixedResetQuotaWindow**

Modify `QuotaGuard.__init__` to accept a union type and track last reset timestamps. Modify `acquire()` to check for fixed-reset windows and reset tokens at day boundary.

In `_rate_limit.py`, update the `QuotaGuard` class:

1. Change the `windows` type hint:
```python
    def __init__(
        self,
        windows: list[QuotaWindow | FixedResetQuotaWindow],
        latency_budget_s: float = 1.0,
        provider_name: str = "",
    ) -> None:
```

2. Add `_last_reset` tracking in `__init__` (after `_last_refill`):
```python
        self._last_reset: list[datetime | None] = [
            _now_in_tz(w.timezone) if isinstance(w, FixedResetQuotaWindow) else None
            for w in windows
        ]
```

3. At the start of `acquire()`, before the existing refill loop (line 109), add fixed-reset logic:
```python
            # --- Fixed-reset windows: reset at day boundary ---
            for i, window in enumerate(self._windows):
                if isinstance(window, FixedResetQuotaWindow) and self._last_reset[i] is not None:
                    if window.should_reset(self._last_reset[i]):
                        self._tokens[i] = float(window.limit)
                        self._last_reset[i] = _now_in_tz(window.timezone)
```

4. In the refill loop, skip `FixedResetQuotaWindow` instances (they don't use token-bucket refill):
```python
            for i, window in enumerate(self._windows):
                if isinstance(window, FixedResetQuotaWindow):
                    continue
                rate = window.limit / window.duration_s
                elapsed = now - self._last_refill[i]
                self._tokens[i] = min(float(window.limit), self._tokens[i] + elapsed * rate)
                self._last_refill[i] = now
```

5. Same skip in the post-sleep re-refill block (lines 136-140).

6. Same skip in the soft-window wait computation loop (lines 122-127) — `FixedResetQuotaWindow` has no `duration_s`, so attempting to compute `rate = window.limit / window.duration_s` would crash:
```python
            max_wait = 0.0
            for i, window in enumerate(self._windows):
                if isinstance(window, FixedResetQuotaWindow):
                    continue
                if self._tokens[i] < 1:
                    rate = window.limit / window.duration_s
                    wait = (1 - self._tokens[i]) / rate
                    max_wait = max(max_wait, wait)
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/unit/validation/test_rate_limit.py -v --no-cov -x`
Expected: All PASS

- [ ] **Step 9: Run full test suite**

Run: `uv run pytest --no-cov -x`
Expected: All PASS

- [ ] **Step 10: Run ruff**

Run: `uv run ruff check src/address_validator/services/validation/_rate_limit.py`
Expected: Clean

- [ ] **Step 11: Commit**

```bash
git add src/address_validator/services/validation/_rate_limit.py tests/unit/validation/test_rate_limit.py
git commit -m "#42 feat: add adjust_tokens and FixedResetQuotaWindow support to QuotaGuard"
```

---

### Task 4: GCP Auth Module

ADC credential loading and project ID resolution.

**Files:**
- Create: `src/address_validator/services/validation/gcp_auth.py`
- Create: `tests/unit/validation/test_gcp_auth.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/validation/test_gcp_auth.py`:

```python
"""Unit tests for GCP ADC credential loading and project ID resolution."""

from unittest.mock import MagicMock, patch

import pytest

from address_validator.services.validation.gcp_auth import get_credentials, resolve_project_id


class TestGetCredentials:
    @patch("address_validator.services.validation.gcp_auth.google.auth.default")
    def test_returns_credentials_and_project(self, mock_default) -> None:
        mock_creds = MagicMock()
        mock_default.return_value = (mock_creds, "my-project")
        creds, project = get_credentials()
        assert creds is mock_creds
        assert project == "my-project"

    @patch("address_validator.services.validation.gcp_auth.google.auth.default")
    def test_requests_cloud_platform_scope(self, mock_default) -> None:
        mock_default.return_value = (MagicMock(), "proj")
        get_credentials()
        mock_default.assert_called_once_with(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )

    @patch("address_validator.services.validation.gcp_auth.google.auth.default")
    def test_propagates_auth_error(self, mock_default) -> None:
        from google.auth.exceptions import DefaultCredentialsError
        mock_default.side_effect = DefaultCredentialsError("no creds")
        with pytest.raises(DefaultCredentialsError):
            get_credentials()


class TestResolveProjectId:
    def test_env_var_takes_precedence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_PROJECT_ID", "env-project")
        result = resolve_project_id(adc_project="adc-project")
        assert result == "env-project"

    def test_falls_back_to_adc_project(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GOOGLE_PROJECT_ID", raising=False)
        result = resolve_project_id(adc_project="adc-project")
        assert result == "adc-project"

    def test_returns_none_when_neither_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GOOGLE_PROJECT_ID", raising=False)
        result = resolve_project_id(adc_project=None)
        assert result is None

    def test_strips_whitespace_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_PROJECT_ID", "  my-project  ")
        result = resolve_project_id(adc_project=None)
        assert result == "my-project"

    def test_empty_env_var_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_PROJECT_ID", "  ")
        result = resolve_project_id(adc_project="adc-project")
        assert result == "adc-project"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/validation/test_gcp_auth.py -v --no-cov -x`
Expected: FAIL — module not found

- [ ] **Step 3: Implement gcp_auth.py**

Create `src/address_validator/services/validation/gcp_auth.py`:

```python
"""GCP Application Default Credentials loading and project ID resolution.

Provides:
- :func:`get_credentials` — loads ADC with cloud-platform scope
- :func:`resolve_project_id` — env var → ADC → None fallback chain
"""

from __future__ import annotations

import logging
import os

import google.auth
from google.auth.credentials import Credentials

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


def get_credentials() -> tuple[Credentials, str | None]:
    """Load Application Default Credentials with cloud-platform scope.

    Returns
    -------
    (credentials, project)
        The ADC credentials and the associated project ID (may be ``None``
        if not discoverable from the credential source).

    Raises
    ------
    google.auth.exceptions.DefaultCredentialsError
        If no valid credentials are found.
    """
    credentials, project = google.auth.default(scopes=_SCOPES)
    logger.debug("gcp_auth: loaded ADC credentials (project=%s)", project)
    return credentials, project


def resolve_project_id(adc_project: str | None) -> str | None:
    """Resolve the GCP project ID via env var → ADC fallback.

    Parameters
    ----------
    adc_project:
        Project ID returned by :func:`get_credentials`.  Used as fallback
        when ``GOOGLE_PROJECT_ID`` env var is unset or empty.

    Returns
    -------
    str | None
        The resolved project ID, or ``None`` if neither source provides one.
    """
    env_project = os.environ.get("GOOGLE_PROJECT_ID", "").strip()
    if env_project:
        logger.debug("gcp_auth: project ID from GOOGLE_PROJECT_ID env var: %s", env_project)
        return env_project

    if adc_project:
        logger.debug("gcp_auth: project ID from ADC: %s", adc_project)
        return adc_project

    logger.warning("gcp_auth: could not resolve GCP project ID from env or ADC")
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/validation/test_gcp_auth.py -v --no-cov -x`
Expected: All PASS

- [ ] **Step 5: Run ruff**

Run: `uv run ruff check src/address_validator/services/validation/gcp_auth.py`
Expected: Clean

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/services/validation/gcp_auth.py tests/unit/validation/test_gcp_auth.py
git commit -m "#42 feat: add GCP auth module for ADC credential loading"
```

---

### Task 5: GCP Quota Sync Module

Cloud Quotas API (daily limit discovery) and Cloud Monitoring API (usage query) wrapped in a reconciliation loop.

**Files:**
- Create: `src/address_validator/services/validation/gcp_quota_sync.py`
- Create: `tests/unit/validation/test_gcp_quota_sync.py`

- [ ] **Step 1: Write failing tests for fetch_daily_limit**

Create `tests/unit/validation/test_gcp_quota_sync.py`:

```python
"""Unit tests for GCP quota sync — limit discovery and usage monitoring."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from address_validator.services.validation.gcp_quota_sync import (
    fetch_daily_limit,
    fetch_daily_usage,
)


class TestFetchDailyLimit:
    def test_extracts_daily_limit_from_quota_infos(self) -> None:
        mock_client = MagicMock()
        # Simulate QuotaInfo with refreshInterval="day"
        daily_info = MagicMock()
        daily_info.refresh_interval = "day"
        daily_info.dimensions_infos = [MagicMock()]
        daily_info.dimensions_infos[0].details.value = 200

        minute_info = MagicMock()
        minute_info.refresh_interval = "minute"

        mock_client.list_quota_infos.return_value = [minute_info, daily_info]

        result = fetch_daily_limit(mock_client, "my-project")
        assert result == 200

    def test_returns_none_when_no_daily_quota_found(self) -> None:
        mock_client = MagicMock()
        minute_info = MagicMock()
        minute_info.refresh_interval = "minute"
        mock_client.list_quota_infos.return_value = [minute_info]

        result = fetch_daily_limit(mock_client, "my-project")
        assert result is None

    def test_returns_none_on_api_error(self) -> None:
        mock_client = MagicMock()
        mock_client.list_quota_infos.side_effect = Exception("API error")
        result = fetch_daily_limit(mock_client, "my-project")
        assert result is None

    def test_queries_correct_service(self) -> None:
        mock_client = MagicMock()
        mock_client.list_quota_infos.return_value = []
        fetch_daily_limit(mock_client, "my-project")
        call_args = mock_client.list_quota_infos.call_args
        parent = call_args.kwargs.get("parent") or call_args[0][0].parent
        assert "addressvalidation.googleapis.com" in str(parent)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/validation/test_gcp_quota_sync.py::TestFetchDailyLimit -v --no-cov -x`
Expected: FAIL — module not found

- [ ] **Step 3: Write failing tests for fetch_daily_usage**

Append to `tests/unit/validation/test_gcp_quota_sync.py`:

```python
class TestFetchDailyUsage:
    def test_returns_usage_count(self) -> None:
        mock_client = MagicMock()
        # Simulate a time series with points
        point = MagicMock()
        point.value.int64_value = 47
        series = MagicMock()
        series.points = [point]
        mock_client.list_time_series.return_value = [series]

        result = fetch_daily_usage(mock_client, "my-project")
        assert result == 47

    def test_returns_none_when_no_data(self) -> None:
        mock_client = MagicMock()
        mock_client.list_time_series.return_value = []
        result = fetch_daily_usage(mock_client, "my-project")
        assert result is None

    def test_returns_none_on_api_error(self) -> None:
        mock_client = MagicMock()
        mock_client.list_time_series.side_effect = Exception("API error")
        result = fetch_daily_usage(mock_client, "my-project")
        assert result is None
```

- [ ] **Step 4: Write failing tests for reconcile_once**

Append to `tests/unit/validation/test_gcp_quota_sync.py`:

```python
from address_validator.services.validation._rate_limit import (
    FixedResetQuotaWindow,
    QuotaGuard,
    QuotaWindow,
)
from address_validator.services.validation.gcp_quota_sync import reconcile_once


class TestReconcileOnce:
    def _make_guard(self, daily_limit: int = 160, used: int = 0) -> QuotaGuard:
        guard = QuotaGuard(
            windows=[
                QuotaWindow(limit=5, duration_s=60.0, mode="soft"),
                FixedResetQuotaWindow(limit=daily_limit, mode="hard"),
            ],
            provider_name="google",
        )
        guard._tokens[1] = float(daily_limit - used)
        return guard

    def test_adjusts_down_when_monitoring_higher(self) -> None:
        guard = self._make_guard(daily_limit=160, used=40)
        # Monitoring says 60 used — 20 more than we think
        reconcile_once(guard, daily_window_index=1, reported_usage=60)
        # tokens should be 160 - 60 = 100
        assert guard._tokens[1] == 100.0

    def test_no_adjust_up_when_monitoring_lower(self) -> None:
        guard = self._make_guard(daily_limit=160, used=40)
        # Monitoring says only 20 used — lower than our 40
        reconcile_once(guard, daily_window_index=1, reported_usage=20)
        # tokens should stay at 120 (160 - 40), not increase
        assert guard._tokens[1] == 120.0

    def test_no_change_when_equal(self) -> None:
        guard = self._make_guard(daily_limit=160, used=40)
        reconcile_once(guard, daily_window_index=1, reported_usage=40)
        assert guard._tokens[1] == 120.0

    def test_does_not_go_below_zero(self) -> None:
        guard = self._make_guard(daily_limit=160, used=150)
        # Monitoring says 200 used (impossible but guard is safe)
        reconcile_once(guard, daily_window_index=1, reported_usage=200)
        assert guard._tokens[1] == 0.0
```

- [ ] **Step 5: Implement gcp_quota_sync.py**

Create `src/address_validator/services/validation/gcp_quota_sync.py`:

```python
"""GCP quota discovery and usage monitoring.

Provides:
- :func:`fetch_daily_limit` — reads the daily quota ceiling from Cloud Quotas API
- :func:`fetch_daily_usage` — reads today's consumption from Cloud Monitoring API
- :func:`reconcile_once` — adjusts a QuotaGuard's daily window based on Monitoring data
- :func:`run_reconciliation_loop` — periodic background reconciliation
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from google.cloud import monitoring_v3, quotas_v1

from address_validator.services.validation._rate_limit import QuotaGuard

if TYPE_CHECKING:
    from google.auth.credentials import Credentials

logger = logging.getLogger(__name__)

_ADDRESS_VALIDATION_SERVICE = "addressvalidation.googleapis.com"
_PACIFIC = ZoneInfo("America/Los_Angeles")


def fetch_daily_limit(
    client: quotas_v1.CloudQuotasClient,
    project_id: str,
) -> int | None:
    """Query Cloud Quotas API for the daily limit on Address Validation.

    Returns the enforced daily quota value, or ``None`` if not found or on error.
    """
    parent = f"projects/{project_id}/locations/global/services/{_ADDRESS_VALIDATION_SERVICE}"
    try:
        for info in client.list_quota_infos(parent=parent):
            if info.refresh_interval == "day":
                if info.dimensions_infos:
                    value = info.dimensions_infos[0].details.value
                    logger.info("gcp_quota_sync: discovered daily limit=%d from Cloud Quotas", value)
                    return int(value)
    except Exception:
        logger.warning("gcp_quota_sync: failed to fetch daily limit from Cloud Quotas", exc_info=True)
    return None


def fetch_daily_usage(
    client: monitoring_v3.MetricServiceClient,
    project_id: str,
) -> int | None:
    """Query Cloud Monitoring for today's Address Validation API usage.

    Uses midnight Pacific Time as the start of the current day to match
    Google's quota reset boundary.

    Returns the usage count, or ``None`` if unavailable.
    """
    now = datetime.now(_PACIFIC)
    midnight_pt = datetime.combine(now.date(), time.min, tzinfo=_PACIFIC)
    # Convert to UTC for the Monitoring API
    start_utc = midnight_pt.astimezone(timezone.utc)
    end_utc = now.astimezone(timezone.utc)

    interval = monitoring_v3.TimeInterval(
        start_time=start_utc,
        end_time=end_utc,
    )

    try:
        results = client.list_time_series(
            request={
                "name": f"projects/{project_id}",
                "filter": (
                    'metric.type = "serviceruntime.googleapis.com/quota/allocation/usage"'
                    ' AND resource.type = "consumer_quota"'
                    f' AND resource.label.service = "{_ADDRESS_VALIDATION_SERVICE}"'
                ),
                "interval": interval,
                "view": monitoring_v3.ListTimeSeriesRequest.TimeSeriesView.FULL,
            }
        )
        for series in results:
            if series.points:
                usage = series.points[0].value.int64_value
                logger.info("gcp_quota_sync: daily usage=%d from Cloud Monitoring", usage)
                return int(usage)
    except Exception:
        logger.warning("gcp_quota_sync: failed to fetch daily usage from Cloud Monitoring", exc_info=True)
    return None


def reconcile_once(
    guard: QuotaGuard,
    daily_window_index: int,
    reported_usage: int,
) -> None:
    """Adjust the guard's daily window tokens based on Monitoring data.

    Only adjusts **downward** (when Monitoring reports higher usage than local).
    Logs a warning when Monitoring reports lower usage (possible lag).

    Drift within what ~10 min of traffic could produce is logged at DEBUG
    (normal Monitoring staleness).  Larger drift is logged at WARNING.
    """
    window = guard._windows[daily_window_index]
    current_tokens = guard._tokens[daily_window_index]
    local_usage = window.limit - current_tokens
    delta = reported_usage - local_usage

    # RPM=5 * 10 min lag → up to ~50 requests of explainable staleness
    _STALENESS_THRESHOLD = 50

    if delta > 0:
        level = logging.DEBUG if delta <= _STALENESS_THRESHOLD else logging.WARNING
        logger.log(
            level,
            "gcp_quota_sync: quota drift detected — monitoring=%d local=%d, adjusting down by %d",
            reported_usage,
            int(local_usage),
            int(delta),
        )
        guard.adjust_tokens(daily_window_index, -delta)
    elif delta < 0:
        level = logging.DEBUG if abs(delta) <= _STALENESS_THRESHOLD else logging.WARNING
        logger.log(
            level,
            "gcp_quota_sync: quota drift — monitoring=%d local=%d, not adjusting up (possible lag)",
            reported_usage,
            int(local_usage),
        )


async def run_reconciliation_loop(
    guard: QuotaGuard,
    daily_window_index: int,
    monitoring_client: monitoring_v3.MetricServiceClient,
    project_id: str,
    interval_s: float = 900.0,
) -> None:
    """Background task: periodically reconcile local quota tracking.

    Runs until cancelled.  Errors on individual ticks are logged and skipped.
    """
    logger.info("gcp_quota_sync: reconciliation loop started (interval=%.0fs)", interval_s)
    while True:
        await asyncio.sleep(interval_s)
        try:
            usage = fetch_daily_usage(monitoring_client, project_id)
            if usage is not None:
                reconcile_once(guard, daily_window_index, usage)
            else:
                logger.debug("gcp_quota_sync: no usage data available, skipping reconciliation")
        except Exception:
            logger.exception("gcp_quota_sync: reconciliation tick failed")
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/validation/test_gcp_quota_sync.py -v --no-cov -x`
Expected: All PASS

- [ ] **Step 7: Run ruff**

Run: `uv run ruff check src/address_validator/services/validation/gcp_quota_sync.py`
Expected: Clean

- [ ] **Step 8: Commit**

```bash
git add src/address_validator/services/validation/gcp_quota_sync.py tests/unit/validation/test_gcp_quota_sync.py
git commit -m "#42 feat: add GCP quota sync module for limit discovery and usage reconciliation"
```

---

### Task 6: Migrate GoogleClient from API Key to ADC

Replace the `api_key` constructor parameter with ADC credentials. Use bearer token auth instead of query parameter.

**Files:**
- Modify: `src/address_validator/services/validation/google_client.py:46-54,88-94`
- Modify: `tests/unit/validation/test_google_client.py`

- [ ] **Step 1: Update test fixtures — replace API key with mock credentials**

In `tests/unit/validation/test_google_client.py`, replace the `API_KEY` constant and update `TestGoogleClientValidateAddress`:

Replace line 15 (`API_KEY = "test-api-key"`) and update the `client` fixture:

```python
# Remove: API_KEY = "test-api-key"

# In TestGoogleClientValidateAddress, update fixtures:
    @pytest.fixture
    def mock_credentials(self):
        from unittest.mock import MagicMock
        creds = MagicMock()
        creds.token = "test-bearer-token"
        creds.valid = True
        return creds

    @pytest.fixture
    def client(self, mock_http, _default_guard, mock_credentials):
        return GoogleClient(
            credentials=mock_credentials,
            http_client=mock_http,
            quota_guard=_default_guard,
        )
```

- [ ] **Step 2: Update test_sends_api_key → test_sends_bearer_token**

Replace the `test_sends_api_key` test:

```python
    async def test_sends_bearer_token(self, client, mock_http) -> None:
        mock_http.post.return_value = self._make_response(GOOGLE_RESPONSE_Y)
        await client.validate_address("123 Main St")
        call_kwargs = mock_http.post.call_args
        headers = call_kwargs.kwargs.get("headers", {})
        assert headers.get("Authorization") == "Bearer test-bearer-token"
```

- [ ] **Step 3: Update test_posts_to_correct_url — remove params key check**

Ensure the test no longer asserts `params={"key": ...}`:

```python
    async def test_posts_to_correct_url(self, client, mock_http) -> None:
        mock_http.post.return_value = self._make_response(GOOGLE_RESPONSE_Y)
        await client.validate_address("123 Main St")
        url = mock_http.post.call_args[0][0]
        assert url == "https://addressvalidation.googleapis.com/v1:validateAddress"
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/unit/validation/test_google_client.py -v --no-cov -x`
Expected: FAIL — GoogleClient constructor signature mismatch

- [ ] **Step 5: Update GoogleClient implementation**

Modify `src/address_validator/services/validation/google_client.py`:

1. Update imports — add `google.auth.credentials`:
```python
from google.auth.credentials import Credentials
from google.auth.transport.requests import Request as AuthRequest
```

2. Update constructor (lines 46-54):
```python
    def __init__(
        self,
        credentials: Credentials,
        http_client: httpx.AsyncClient,
        quota_guard: QuotaGuard,
    ) -> None:
        self._credentials = credentials
        self._http = http_client
        self._rate_limiter = quota_guard
```

3. Add a method to get a fresh token:
```python
    def _get_auth_headers(self) -> dict[str, str]:
        """Return Authorization header with a fresh bearer token."""
        if not self._credentials.valid:
            self._credentials.refresh(AuthRequest())
        return {"Authorization": f"Bearer {self._credentials.token}"}
```

4. Update the HTTP POST call (lines 88-95) — replace `params={"key": self._api_key}` with `headers=self._get_auth_headers()`:
```python
            resp = await self._http.post(
                _VALIDATE_URL,
                headers=self._get_auth_headers(),
                json={
                    "address": {"addressLines": address_lines},
                    "enableUspsCass": True,
                },
            )
```

5. Remove the `_api_key` attribute entirely. Update the module docstring to reflect ADC auth.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/unit/validation/test_google_client.py -v --no-cov -x`
Expected: All PASS

- [ ] **Step 7: Run ruff**

Run: `uv run ruff check src/address_validator/services/validation/google_client.py`
Expected: Clean

- [ ] **Step 8: Commit**

```bash
git add src/address_validator/services/validation/google_client.py tests/unit/validation/test_google_client.py
git commit -m "#42 feat: migrate GoogleClient from API key to ADC bearer token auth"
```

---

### Task 7: Update Factory — ADC Auth + Quota Sync Wiring

Replace `_parse_google_config` with ADC-based config. Wire quota sync into provider creation.

**Files:**
- Modify: `src/address_validator/services/validation/factory.py:138-161,211-228,244-275`
- Modify: `tests/unit/validation/test_provider_factory.py`

- [ ] **Step 1: Update factory tests — replace GOOGLE_API_KEY with ADC mocking**

In `tests/unit/validation/test_provider_factory.py`, every test that sets `GOOGLE_API_KEY` needs updating. The pattern:

Before:
```python
monkeypatch.setenv("GOOGLE_API_KEY", "my-key")
```

After — mock ADC at the factory module level:
```python
# Add a fixture to the test file:
@pytest.fixture(autouse=True)
def mock_adc(monkeypatch):
    """Mock ADC so Google provider tests don't need real credentials."""
    mock_creds = MagicMock()
    mock_creds.token = "test-token"
    mock_creds.valid = True
    with patch(
        "address_validator.services.validation.factory.get_credentials",
        return_value=(mock_creds, "test-project"),
    ) as mock_get:
        yield mock_get
```

Update all Google-related tests to:
- Remove `monkeypatch.setenv("GOOGLE_API_KEY", ...)` lines
- Remove `monkeypatch.delenv("GOOGLE_API_KEY", ...)` lines
- Tests for `test_google_missing_api_key_raises` → becomes `test_google_missing_adc_raises` (mock ADC to raise `DefaultCredentialsError`)
- Tests in `TestValidateConfig` similarly updated

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/validation/test_provider_factory.py -v --no-cov -x`
Expected: FAIL — factory still expects `GOOGLE_API_KEY`

- [ ] **Step 3: Update _parse_google_config to use ADC**

Replace `_parse_google_config` (lines 211-228) in `factory.py`:

```python
def _parse_google_config() -> tuple[int, int]:
    """Read, validate, and return ``(rpm, daily_limit)``.

    Credentials come from ADC (validated separately).  ``GOOGLE_DAILY_LIMIT``
    is an optional override — when the Cloud Quotas API is available at
    provider construction time, the discovered limit takes precedence.
    """
    try:
        rpm = int(os.environ.get("GOOGLE_RATE_LIMIT_RPM", "5"))
    except ValueError:
        raise ValueError("GOOGLE_RATE_LIMIT_RPM must be a positive integer (e.g. '5')") from None
    if rpm <= 0:
        raise ValueError("GOOGLE_RATE_LIMIT_RPM must be a positive integer (e.g. '5')")
    try:
        daily_limit = int(os.environ.get("GOOGLE_DAILY_LIMIT", "160"))
    except ValueError:
        raise ValueError("GOOGLE_DAILY_LIMIT must be a positive integer (e.g. '160')") from None
    if daily_limit <= 0:
        raise ValueError("GOOGLE_DAILY_LIMIT must be a positive integer (e.g. '160')")
    return rpm, daily_limit
```

- [ ] **Step 4: Update _get_google_provider to use ADC + quota sync**

Replace `_get_google_provider` (lines 138-161):

```python
def _get_google_provider(
    rpm: int, daily_limit: int, latency_budget_s: float
) -> GoogleProvider:
    global _google_provider  # noqa: PLW0603
    if _google_provider is None:
        from address_validator.services.validation.gcp_auth import get_credentials, resolve_project_id
        from address_validator.services.validation.gcp_quota_sync import (
            fetch_daily_limit,
            fetch_daily_usage,
        )

        credentials, adc_project = get_credentials()
        project_id = resolve_project_id(adc_project)

        # Auto-discover daily limit from Cloud Quotas API
        if project_id:
            try:
                from google.cloud import quotas_v1
                quotas_client = quotas_v1.CloudQuotasClient(credentials=credentials)
                discovered = fetch_daily_limit(quotas_client, project_id)
                if discovered is not None:
                    daily_limit = discovered
            except Exception:
                logger.warning("get_provider: Cloud Quotas API unavailable, using configured limit=%d", daily_limit)

        if not project_id:
            logger.warning("get_provider: GCP project ID not resolved — quota sync features disabled, using env var defaults")

        logger.debug(
            "get_provider: creating GoogleProvider singleton (%d rpm, %d/day)", rpm, daily_limit
        )

        from address_validator.services.validation._rate_limit import FixedResetQuotaWindow
        guard = QuotaGuard(
            windows=[
                QuotaWindow(limit=rpm, duration_s=60.0, mode="soft"),
                FixedResetQuotaWindow(limit=daily_limit, mode="hard"),
            ],
            latency_budget_s=latency_budget_s,
            provider_name="google",
        )

        # Seed daily bucket from Cloud Monitoring
        monitoring_client = None
        if project_id:
            try:
                from google.cloud import monitoring_v3
                monitoring_client = monitoring_v3.MetricServiceClient(credentials=credentials)
                usage = fetch_daily_usage(monitoring_client, project_id)
                if usage is not None and usage > 0:
                    guard.adjust_tokens(1, -usage)
                    logger.info("get_provider: seeded daily quota from Monitoring (used=%d, remaining=%d)", usage, daily_limit - usage)
            except Exception:
                monitoring_client = None
                logger.warning("get_provider: Cloud Monitoring API unavailable, starting with full bucket")

        _google_provider = GoogleProvider(
            client=GoogleClient(
                credentials=credentials,
                http_client=_get_http_client(),
                quota_guard=guard,
            )
        )
    return _google_provider
```

- [ ] **Step 5: Update _check_provider_config for google**

In `_check_provider_config` (line 254), replace the google branch:

```python
    elif name == "google":
        _parse_google_config()
        # ADC validation: attempt to load credentials
        from address_validator.services.validation.gcp_auth import get_credentials
        get_credentials()
```

- [ ] **Step 6: Update _build_single_provider for google**

In `_build_single_provider` (lines 269-271):

```python
    if name == "google":
        rpm, daily_limit = _parse_google_config()
        return _get_google_provider(rpm, daily_limit, budget)
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/unit/validation/test_provider_factory.py -v --no-cov -x`
Expected: All PASS

- [ ] **Step 8: Run full test suite**

Run: `uv run pytest --no-cov -x`
Expected: All PASS

- [ ] **Step 9: Run ruff**

Run: `uv run ruff check src/address_validator/services/validation/factory.py`
Expected: Clean

- [ ] **Step 10: Commit**

```bash
git add src/address_validator/services/validation/factory.py tests/unit/validation/test_provider_factory.py
git commit -m "#42 feat: migrate factory from GOOGLE_API_KEY to ADC with quota sync wiring"
```

---

### Task 8: Reconciliation Background Task in Lifespan

Start/stop the reconciliation loop in the FastAPI lifespan.

**Files:**
- Modify: `src/address_validator/services/validation/factory.py`
- Modify: `src/address_validator/main.py:49-54`

- [ ] **Step 1: Add get_reconciliation_params to factory**

Add a function to `factory.py` that returns what the lifespan needs to start the loop (or `None` if Google provider isn't configured):

```python
# Module-level state for reconciliation
_reconciliation_params: dict | None = None


def get_reconciliation_params() -> dict | None:
    """Return reconciliation loop parameters if Google provider is active.

    Returns None when Google is not configured or project ID is unavailable.
    Called by the lifespan after get_provider() to set up the background task.
    """
    return _reconciliation_params
```

Populate `_reconciliation_params` inside `_get_google_provider` (after seeding the bucket, before creating the provider), storing `guard`, `daily_window_index`, `monitoring_client`, `project_id`, and `interval_s`:

```python
        # Store reconciliation params for the lifespan background task
        global _reconciliation_params  # noqa: PLW0603
        if project_id and monitoring_client:
            interval_s = float(os.environ.get("GOOGLE_QUOTA_RECONCILE_INTERVAL_S", "900"))
            _reconciliation_params = {
                "guard": guard,
                "daily_window_index": 1,
                "monitoring_client": monitoring_client,
                "project_id": project_id,
                "interval_s": interval_s,
            }
```

- [ ] **Step 2: Update lifespan in main.py**

Modify `main.py` lifespan (lines 49-54):

```python
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """FastAPI lifespan context — validate config on startup, close DB on shutdown."""
    import asyncio

    from address_validator.services.validation.factory import get_provider, get_reconciliation_params

    validate_config()

    # Eagerly construct provider singletons so quota sync wiring runs at boot
    # (get_provider is normally lazy — called on first request).  This ensures
    # _reconciliation_params is populated before we check it below.
    get_provider()

    # Start reconciliation background task if Google provider is active
    reconciliation_task = None
    params = get_reconciliation_params()
    if params:
        from address_validator.services.validation.gcp_quota_sync import run_reconciliation_loop
        reconciliation_task = asyncio.create_task(
            run_reconciliation_loop(**params),
            name="google-quota-reconciliation",
        )

    yield

    # Cancel reconciliation task on shutdown
    if reconciliation_task is not None:
        reconciliation_task.cancel()
        try:
            await reconciliation_task
        except asyncio.CancelledError:
            pass

    await close_engine()
```

- [ ] **Step 3: Write test for lifespan reconciliation startup**

Add to a new test or append to existing lifespan tests:

```python
# In an appropriate test file (e.g., tests/unit/test_main.py or test_provider_factory.py)

async def test_lifespan_starts_reconciliation_when_google_active(monkeypatch):
    """Reconciliation background task is created when Google provider is configured."""
    from unittest.mock import patch, MagicMock, AsyncMock
    import asyncio

    mock_params = {
        "guard": MagicMock(),
        "daily_window_index": 1,
        "monitoring_client": MagicMock(),
        "project_id": "test-project",
        "interval_s": 900.0,
    }

    with (
        patch("address_validator.services.validation.factory.validate_config"),
        patch("address_validator.services.validation.factory.get_reconciliation_params", return_value=mock_params),
        patch("address_validator.services.validation.gcp_quota_sync.run_reconciliation_loop", new_callable=AsyncMock) as mock_loop,
        patch("address_validator.services.validation.cache_db.close_engine", new_callable=AsyncMock),
    ):
        from address_validator.main import lifespan, app
        async with lifespan(app):
            # Give the task a moment to start
            await asyncio.sleep(0.01)
        mock_loop.assert_called_once_with(**mock_params)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ -v --no-cov -x -k "reconciliation"`
Expected: PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest --no-cov -x`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/address_validator/main.py src/address_validator/services/validation/factory.py tests/
git commit -m "#42 feat: wire reconciliation background task into FastAPI lifespan"
```

---

### Task 9: Update Documentation

**Files:**
- Modify: `docs/VALIDATION-PROVIDERS.md`
- Modify: `AGENTS.md` (env var table)

- [ ] **Step 1: Update VALIDATION-PROVIDERS.md**

Update the Google Provider section to reflect:
- Auth: ADC (service account) replacing API key
- Required IAM roles
- New env vars: `GOOGLE_PROJECT_ID`, `GOOGLE_QUOTA_RECONCILE_INTERVAL_S`
- Removed env var: `GOOGLE_API_KEY`
- `GOOGLE_DAILY_LIMIT` now optional override (auto-discovered from Cloud Quotas API)
- Daily window: fixed midnight PT reset instead of rolling 86400s
- Boot sequence: limit discovery → usage seeding
- Periodic reconciliation behavior
- Update the "Dynamic Quota Querying" section (lines 71-73) — no longer just client-side

- [ ] **Step 2: Update AGENTS.md env var table**

Update the env var table in `AGENTS.md`:
- Remove `GOOGLE_API_KEY`
- Add `GOOGLE_PROJECT_ID`
- Add `GOOGLE_QUOTA_RECONCILE_INTERVAL_S`
- Update `GOOGLE_DAILY_LIMIT` description to note auto-discovery
- Add note about ADC requirements under Authentication section

- [ ] **Step 3: Update sensitive areas table in AGENTS.md**

Add entries for new modules:
- `gcp_auth.py` — ADC credentials, project ID resolution
- `gcp_quota_sync.py` — quota discovery, usage monitoring, reconciliation loop
- `_rate_limit.py` — mention `FixedResetQuotaWindow` and `adjust_tokens`

- [ ] **Step 4: Commit**

```bash
git add docs/VALIDATION-PROVIDERS.md AGENTS.md
git commit -m "#42 docs: update provider docs and AGENTS.md for ADC auth and quota sync"
```

---

### Task 10: Final Integration Test + Cleanup

**Files:**
- All modified files

- [ ] **Step 1: Run full test suite with coverage**

Run: `uv run pytest`
Expected: All PASS, coverage >= 80%

- [ ] **Step 2: Run ruff on all changed files**

Run: `uv run ruff check .`
Expected: Clean

- [ ] **Step 3: Run ruff format**

Run: `uv run ruff format .`
Expected: Clean or reformatted

- [ ] **Step 4: Verify no references to GOOGLE_API_KEY remain in source**

Run: `grep -r "GOOGLE_API_KEY" src/ tests/ --include="*.py"`
Expected: No matches (except possibly in migration/changelog references)

- [ ] **Step 5: Commit any final cleanup**

```bash
git add -A
git commit -m "#42 chore: final cleanup for Google quota integration"
```
