# Multi-Window Quota Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-window `_TokenBucket` with a `QuotaGuard` that enforces multiple quota windows per provider, queues requests up to a configurable latency budget, and enables `ChainProvider` to fall back to secondary providers when the primary is at local capacity — not only when the upstream returns HTTP 429.

**Architecture:** A new `QuotaGuard(windows, latency_budget_s)` class replaces `_TokenBucket` in both provider clients. Each `QuotaWindow` carries a limit, duration, and mode (`soft` = queue up to budget; `hard` = reject immediately). A new `ProviderAtCapacityError` is raised when the budget is exceeded or a hard window is exhausted; `ChainProvider` catches it alongside the existing `ProviderRateLimitedError`.

**Tech Stack:** Python 3.12, asyncio, pytest-asyncio, `unittest.mock.patch`

---

## File map

| File | Action | What changes |
|---|---|---|
| `services/validation/errors.py` | Modify | Add `ProviderAtCapacityError` |
| `services/validation/_rate_limit.py` | Modify | Add `QuotaWindow`, `QuotaGuard`; remove `_TokenBucket` (Task 7) |
| `services/validation/chain_provider.py` | Modify | Catch `ProviderAtCapacityError` alongside `ProviderRateLimitedError` |
| `services/validation/usps_client.py` | Modify | Accept `quota_guard: QuotaGuard`; remove `rate_limit_rps` param |
| `services/validation/google_client.py` | Modify | Accept `quota_guard: QuotaGuard`; remove `rate_limit_rps` param |
| `services/validation/factory.py` | Modify | Build `QuotaGuard` from new env vars; update `_parse_*_config`; update `validate_config` |
| `docs/VALIDATION-PROVIDERS.md` | Modify | Update env var table, rate limit description |
| `AGENTS.md` | Modify | Update sensitive areas table |
| `tests/unit/validation/test_errors.py` | Modify | Add `TestProviderAtCapacityError` |
| `tests/unit/validation/test_rate_limit.py` | Modify | Replace `TestTokenBucket` with `TestQuotaGuard` |
| `tests/unit/validation/test_chain_provider.py` | Modify | Add `ProviderAtCapacityError` fallback tests |
| `tests/unit/validation/test_usps_client.py` | Modify | Update fixture + replace rate-limiter type assertions |
| `tests/unit/validation/test_google_client.py` | Modify | Update fixture + replace rate-limiter type assertions |
| `tests/unit/validation/test_provider_factory.py` | Modify | Update for new env vars; remove `GOOGLE_RATE_LIMIT_RPS` tests |

---

## Task 1: `ProviderAtCapacityError`

**Files:**
- Modify: `services/validation/errors.py`
- Modify: `tests/unit/validation/test_errors.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/validation/test_errors.py`:

```python
from services.validation.errors import ProviderAtCapacityError


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
```

- [ ] **Step 2: Run tests — expect failure**

```
uv run pytest tests/unit/validation/test_errors.py::TestProviderAtCapacityError -v
```

Expected: `ImportError` — `ProviderAtCapacityError` not yet defined.

- [ ] **Step 3: Add `ProviderAtCapacityError` to `errors.py`**

Append after `ProviderRateLimitedError`:

```python
class ProviderAtCapacityError(Exception):
    """Raised by :class:`~services.validation._rate_limit.QuotaGuard` when a
    request cannot be dispatched within the configured latency budget, or when
    a hard quota window is exhausted.

    Semantically distinct from :class:`ProviderRateLimitedError`: this error
    means the request was *not sent* (local capacity decision), whereas
    ``ProviderRateLimitedError`` means the upstream API returned HTTP 429.

    :class:`~services.validation.chain_provider.ChainProvider` catches both
    and advances to the next provider.

    Parameters
    ----------
    provider:
        Short name of the provider (e.g. ``"usps"``, ``"google"``).
    retry_after_seconds:
        Hint for how long to wait before retrying.  Defaults to ``0.0``.
    """

    def __init__(self, provider: str, retry_after_seconds: float = 0.0) -> None:
        self.provider = provider
        self.retry_after_seconds = retry_after_seconds
        super().__init__(f"Provider '{provider}' at local capacity")
```

- [ ] **Step 4: Run tests — expect pass**

```
uv run pytest tests/unit/validation/test_errors.py -v
```

Expected: all pass.

- [ ] **Step 5: Lint**

```
uv run ruff check services/validation/errors.py tests/unit/validation/test_errors.py
```

- [ ] **Step 6: Commit**

```bash
git add services/validation/errors.py tests/unit/validation/test_errors.py
git commit -m "#38 feat: add ProviderAtCapacityError to errors.py"
```

---

## Task 2: `QuotaWindow` + `QuotaGuard`

**Files:**
- Modify: `services/validation/_rate_limit.py`
- Modify: `tests/unit/validation/test_rate_limit.py`

### Background

`QuotaGuard` replaces `_TokenBucket`. It holds a list of `QuotaWindow` descriptors and
maintains parallel `_tokens` / `_last_refill` state lists. `acquire()` runs inside a single
`asyncio.Lock` to serialise concurrent callers.

**Acquire logic (within the lock):**
1. Refill all windows: `tokens[i] = min(limit, tokens[i] + elapsed * (limit / duration_s))`
2. Check hard windows: if any `tokens[i] < 1` and `mode == "hard"`, raise `ProviderAtCapacityError`
3. Compute `max_wait = max((1 - tokens[i]) / rate_i for i where tokens[i] < 1, else 0)`
4. If `max_wait > latency_budget_s`, raise `ProviderAtCapacityError`
5. If `max_wait > 0`: sleep, then re-refill all windows (time elapsed during sleep)
6. Consume 1 token from each window: `tokens[i] -= 1.0`

`_TokenBucket` is **not** removed in this task — that happens in Task 7 after clients are
migrated. Both classes coexist temporarily.

- [ ] **Step 1: Write failing tests**

Replace the `TestTokenBucket` class in `tests/unit/validation/test_rate_limit.py` with the
new `TestQuotaGuard` class. Keep `TestParseRetryAfter` unchanged. Also update the imports.

Full replacement for `test_rate_limit.py`:

```python
"""Unit tests for services/validation/_rate_limit.py."""

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from services.validation._rate_limit import (
    _RETRY_BASE_DELAY_S,
    _parse_retry_after,
    QuotaGuard,
    QuotaWindow,
)
from services.validation.errors import ProviderAtCapacityError


class TestQuotaGuard:
    def _soft_guard(
        self,
        limit: int = 5,
        duration_s: float = 1.0,
        latency_budget_s: float = 2.0,
    ) -> QuotaGuard:
        return QuotaGuard(
            windows=[QuotaWindow(limit=limit, duration_s=duration_s, mode="soft")],
            latency_budget_s=latency_budget_s,
            provider_name="test",
        )

    def _hard_guard(
        self,
        limit: int = 160,
        duration_s: float = 86_400.0,
        latency_budget_s: float = 5.0,
    ) -> QuotaGuard:
        return QuotaGuard(
            windows=[QuotaWindow(limit=limit, duration_s=duration_s, mode="hard")],
            latency_budget_s=latency_budget_s,
            provider_name="test",
        )

    @pytest.mark.asyncio
    async def test_first_acquire_does_not_sleep(self) -> None:
        guard = self._soft_guard()
        with patch("services.validation._rate_limit.asyncio.sleep") as mock_sleep:
            await guard.acquire()
        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_soft_window_sleeps_when_tokens_exhausted(self) -> None:
        guard = self._soft_guard(limit=1, duration_s=1.0, latency_budget_s=2.0)
        guard._tokens[0] = 0.0
        guard._last_refill[0] = time.monotonic()

        with patch("services.validation._rate_limit.asyncio.sleep") as mock_sleep:
            await guard.acquire()

        mock_sleep.assert_called_once()
        sleep_time = mock_sleep.call_args[0][0]
        assert 0 < sleep_time <= 2.0

    @pytest.mark.asyncio
    async def test_soft_window_raises_when_wait_exceeds_budget(self) -> None:
        # rate = 1/1.0 = 1 token/s; tokens=0 → need 1s; budget=0.5s → raise
        guard = self._soft_guard(limit=1, duration_s=1.0, latency_budget_s=0.5)
        guard._tokens[0] = 0.0
        guard._last_refill[0] = time.monotonic()

        with pytest.raises(ProviderAtCapacityError) as exc_info:
            await guard.acquire()
        assert exc_info.value.provider == "test"

    @pytest.mark.asyncio
    async def test_hard_window_raises_immediately_when_exhausted(self) -> None:
        guard = self._hard_guard(limit=160, duration_s=86_400.0, latency_budget_s=999.0)
        guard._tokens[0] = 0.0
        guard._last_refill[0] = time.monotonic()

        with pytest.raises(ProviderAtCapacityError):
            await guard.acquire()

    @pytest.mark.asyncio
    async def test_hard_window_does_not_sleep_before_raising(self) -> None:
        guard = self._hard_guard()
        guard._tokens[0] = 0.0
        guard._last_refill[0] = time.monotonic()

        with patch("services.validation._rate_limit.asyncio.sleep") as mock_sleep:
            with pytest.raises(ProviderAtCapacityError):
                await guard.acquire()
        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_hard_exhausted_blocks_regardless_of_soft_capacity(self) -> None:
        # soft window has plenty of tokens; hard window is empty → still raises
        guard = QuotaGuard(
            windows=[
                QuotaWindow(limit=5, duration_s=1.0, mode="soft"),
                QuotaWindow(limit=160, duration_s=86_400.0, mode="hard"),
            ],
            latency_budget_s=5.0,
            provider_name="test",
        )
        guard._tokens[0] = 5.0  # soft: full
        guard._tokens[1] = 0.0  # hard: empty
        guard._last_refill[0] = time.monotonic()
        guard._last_refill[1] = time.monotonic()

        with pytest.raises(ProviderAtCapacityError):
            await guard.acquire()

    @pytest.mark.asyncio
    async def test_multi_window_wait_is_max_not_sum(self) -> None:
        # Window 0: rate=1/s, tokens=0.5 → needs 0.5s
        # Window 1: rate=1/s, tokens=0.8 → needs 0.2s
        # Max = 0.5s; budget = 2.0s → should sleep ~0.5s, not ~0.7s
        guard = QuotaGuard(
            windows=[
                QuotaWindow(limit=1, duration_s=1.0, mode="soft"),
                QuotaWindow(limit=1, duration_s=1.0, mode="soft"),
            ],
            latency_budget_s=2.0,
            provider_name="test",
        )
        guard._tokens[0] = 0.5
        guard._tokens[1] = 0.8
        now = time.monotonic()
        guard._last_refill[0] = now
        guard._last_refill[1] = now

        with patch("services.validation._rate_limit.asyncio.sleep") as mock_sleep:
            await guard.acquire()

        mock_sleep.assert_called_once()
        sleep_time = mock_sleep.call_args[0][0]
        # Should be ~0.5s (max), not ~0.7s (sum); allow floating point tolerance
        assert 0.45 <= sleep_time <= 0.6

    @pytest.mark.asyncio
    async def test_tokens_replenish_over_time(self) -> None:
        # rate=10/s, tokens drained; simulate 0.5s elapsed → 5 tokens refilled
        guard = self._soft_guard(limit=10, duration_s=1.0, latency_budget_s=1.0)
        guard._tokens[0] = 0.0
        guard._last_refill[0] = time.monotonic() - 0.5  # 0.5s ago → +5 tokens

        with patch("services.validation._rate_limit.asyncio.sleep") as mock_sleep:
            await guard.acquire()
        mock_sleep.assert_not_called()

    def test_starts_with_full_capacity(self) -> None:
        guard = self._soft_guard(limit=5)
        assert guard._tokens[0] == 5.0

    def test_provider_name_stored(self) -> None:
        guard = QuotaGuard(
            windows=[QuotaWindow(limit=5, duration_s=1.0, mode="soft")],
            provider_name="usps",
        )
        assert guard._provider_name == "usps"

    def test_multi_window_count_matches(self) -> None:
        guard = QuotaGuard(
            windows=[
                QuotaWindow(limit=5, duration_s=1.0, mode="soft"),
                QuotaWindow(limit=10_000, duration_s=86_400.0, mode="soft"),
            ],
            provider_name="test",
        )
        assert len(guard._windows) == 2
        assert len(guard._tokens) == 2
        assert len(guard._last_refill) == 2


class TestParseRetryAfter:
    def _make_response(self, headers: dict) -> httpx.Response:
        resp = MagicMock(spec=httpx.Response)
        resp.headers = headers
        return resp

    def test_reads_retry_after_integer(self) -> None:
        resp = self._make_response({"Retry-After": "30"})
        assert _parse_retry_after(resp, attempt=0) == 30.0

    def test_reads_retry_after_zero(self) -> None:
        resp = self._make_response({"Retry-After": "0"})
        assert _parse_retry_after(resp, attempt=0) == 0.0

    def test_falls_back_to_exponential_backoff_when_no_header(self) -> None:
        resp = self._make_response({})
        delay = _parse_retry_after(resp, attempt=0)
        assert delay >= _RETRY_BASE_DELAY_S
        assert delay < _RETRY_BASE_DELAY_S + 1.0

    def test_exponential_backoff_grows_with_attempt(self) -> None:
        resp = self._make_response({})
        delay2 = _parse_retry_after(resp, attempt=2)
        assert delay2 >= _RETRY_BASE_DELAY_S * 4

    def test_non_integer_retry_after_falls_back(self) -> None:
        resp = self._make_response({"Retry-After": "Wed, 21 Oct 2025 07:28:00 GMT"})
        delay = _parse_retry_after(resp, attempt=0)
        assert delay >= _RETRY_BASE_DELAY_S
```

- [ ] **Step 2: Run tests — expect failure**

```
uv run pytest tests/unit/validation/test_rate_limit.py::TestQuotaGuard -v
```

Expected: `ImportError` — `QuotaGuard` and `QuotaWindow` not yet defined.

- [ ] **Step 3: Add `QuotaWindow` and `QuotaGuard` to `_rate_limit.py`**

Add after the existing imports and constants (before `_TokenBucket`). Add the new imports at the top:

```python
from dataclasses import dataclass
from typing import Literal
```

Add a top-level import for `ProviderAtCapacityError` **at the bottom of the imports block** in
`_rate_limit.py`. `errors.py` has zero imports, so there is no circular dependency:

```python
from services.validation.errors import ProviderAtCapacityError
```

Then add the new classes:

```python
@dataclass
class QuotaWindow:
    """Describes one quota constraint for a :class:`QuotaGuard`.

    Parameters
    ----------
    limit:
        Maximum number of requests allowed in *duration_s* seconds.
    duration_s:
        Window duration in seconds (e.g. ``1.0`` for per-second,
        ``60.0`` for per-minute, ``86400.0`` for per-day).
    mode:
        ``"soft"`` — queue the request by sleeping up to the guard's
        ``latency_budget_s``; raise :class:`ProviderAtCapacityError` if the
        wait would exceed the budget.
        ``"hard"`` — raise :class:`ProviderAtCapacityError` immediately when
        the window is exhausted; never sleep.
    """

    limit: int
    duration_s: float
    mode: Literal["soft", "hard"]


class QuotaGuard:
    """Multi-window async rate limiter with a latency budget.

    Replaces :class:`_TokenBucket`.  Each :class:`QuotaWindow` is backed by a
    token bucket (``rate = limit / duration_s``, ``capacity = limit``).  On
    service start the bucket is full (optimistic — does not know mid-period
    usage across restarts).

    ``acquire()`` is the sole entry point.  It:

    1. Refills all windows based on elapsed time.
    2. Raises :class:`~services.validation.errors.ProviderAtCapacityError`
       immediately if any ``"hard"`` window has no token.
    3. Computes the maximum wait across all windows that need one.
    4. Raises if that wait exceeds ``latency_budget_s``.
    5. Sleeps the required wait, re-refills, then consumes one token from
       every window.

    Parameters
    ----------
    windows:
        Ordered list of quota constraints applied simultaneously.
    latency_budget_s:
        Maximum seconds a request may be held in queue before
        :class:`~services.validation.errors.ProviderAtCapacityError` is raised.
        Default ``1.0``.
    provider_name:
        Included in raised exceptions for logging context.
    """

    def __init__(
        self,
        windows: list[QuotaWindow],
        latency_budget_s: float = 1.0,
        provider_name: str = "",
    ) -> None:
        self._windows = windows
        self._latency_budget_s = latency_budget_s
        self._provider_name = provider_name
        self._tokens: list[float] = [float(w.limit) for w in windows]
        self._last_refill: list[float] = [monotonic() for _ in windows]
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Acquire one token from every window, blocking up to the latency budget."""
        async with self._lock:
            # --- Refill all windows ---
            now = monotonic()
            for i, window in enumerate(self._windows):
                rate = window.limit / window.duration_s
                elapsed = now - self._last_refill[i]
                self._tokens[i] = min(float(window.limit), self._tokens[i] + elapsed * rate)
                self._last_refill[i] = now

            # --- Hard windows: reject immediately if exhausted ---
            for i, window in enumerate(self._windows):
                if window.mode == "hard" and self._tokens[i] < 1:
                    raise ProviderAtCapacityError(self._provider_name)

            # --- Soft windows: compute max wait ---
            max_wait = 0.0
            for i, window in enumerate(self._windows):
                if self._tokens[i] < 1:
                    rate = window.limit / window.duration_s
                    wait = (1 - self._tokens[i]) / rate
                    max_wait = max(max_wait, wait)

            if max_wait > self._latency_budget_s:
                raise ProviderAtCapacityError(self._provider_name)

            # --- Sleep if needed, then re-refill ---
            if max_wait > 0:
                await asyncio.sleep(max_wait)
                now = monotonic()
                for i, window in enumerate(self._windows):
                    rate = window.limit / window.duration_s
                    elapsed = now - self._last_refill[i]
                    self._tokens[i] = min(
                        float(window.limit), self._tokens[i] + elapsed * rate
                    )
                    self._last_refill[i] = now

            # --- Consume from all windows ---
            for i in range(len(self._windows)):
                self._tokens[i] -= 1.0
```

- [ ] **Step 4: Run tests — expect pass**

```
uv run pytest tests/unit/validation/test_rate_limit.py -v
```

Expected: all pass.

- [ ] **Step 5: Lint**

```
uv run ruff check services/validation/_rate_limit.py tests/unit/validation/test_rate_limit.py
```

- [ ] **Step 6: Commit**

```bash
git add services/validation/_rate_limit.py tests/unit/validation/test_rate_limit.py
git commit -m "#38 feat: add QuotaWindow and QuotaGuard to _rate_limit.py"
```

---

## Task 3: `ChainProvider` catches `ProviderAtCapacityError`

**Files:**
- Modify: `services/validation/chain_provider.py`
- Modify: `tests/unit/validation/test_chain_provider.py`

- [ ] **Step 1: Write failing tests**

Add these test methods to `TestChainProvider` in `test_chain_provider.py`. Also update the
imports block at the top to include `ProviderAtCapacityError`:

```python
# Add to imports:
from services.validation.errors import ProviderAtCapacityError, ProviderRateLimitedError
```

New test methods to add inside `TestChainProvider`:

```python
    @pytest.mark.asyncio
    async def test_falls_back_to_second_on_at_capacity(self, std_address: object) -> None:
        primary = AsyncMock()
        primary.validate = AsyncMock(side_effect=ProviderAtCapacityError("usps"))
        secondary = _mock_provider(_GOOGLE_CONFIRMED)
        chain = ChainProvider(providers=[primary, secondary])

        result = await chain.validate(std_address)  # type: ignore[arg-type]
        assert result.validation.provider == "google"
        primary.validate.assert_awaited_once()
        secondary.validate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_all_when_all_providers_at_capacity(self, std_address: object) -> None:
        p1 = AsyncMock()
        p1.validate = AsyncMock(side_effect=ProviderAtCapacityError("usps"))
        p2 = AsyncMock()
        p2.validate = AsyncMock(side_effect=ProviderAtCapacityError("google"))
        chain = ChainProvider(providers=[p1, p2])

        with pytest.raises(ProviderRateLimitedError) as exc_info:
            await chain.validate(std_address)  # type: ignore[arg-type]
        assert exc_info.value.provider == "all"

    @pytest.mark.asyncio
    async def test_retry_after_propagated_from_at_capacity_error(
        self, std_address: object
    ) -> None:
        p1 = AsyncMock()
        p1.validate = AsyncMock(
            side_effect=ProviderAtCapacityError("usps", retry_after_seconds=0.5)
        )
        p2 = AsyncMock()
        p2.validate = AsyncMock(
            side_effect=ProviderAtCapacityError("google", retry_after_seconds=2.0)
        )
        chain = ChainProvider(providers=[p1, p2])

        with pytest.raises(ProviderRateLimitedError) as exc_info:
            await chain.validate(std_address)  # type: ignore[arg-type]
        assert exc_info.value.retry_after_seconds == 2.0

    @pytest.mark.asyncio
    async def test_at_capacity_mixed_with_rate_limited_propagates_last(
        self, std_address: object
    ) -> None:
        p1 = AsyncMock()
        p1.validate = AsyncMock(
            side_effect=ProviderAtCapacityError("usps", retry_after_seconds=0.1)
        )
        p2 = AsyncMock()
        p2.validate = AsyncMock(
            side_effect=ProviderRateLimitedError("google", retry_after_seconds=3.0)
        )
        chain = ChainProvider(providers=[p1, p2])

        with pytest.raises(ProviderRateLimitedError) as exc_info:
            await chain.validate(std_address)  # type: ignore[arg-type]
        assert exc_info.value.retry_after_seconds == 3.0
```

- [ ] **Step 2: Run tests — expect failure**

```
uv run pytest tests/unit/validation/test_chain_provider.py -v -k "at_capacity"
```

Expected: `FAILED` — `ChainProvider` does not yet catch `ProviderAtCapacityError`.

- [ ] **Step 3: Update `chain_provider.py`**

Update the imports block and the `except` clause in `validate()`:

```python
# At top of file, update import:
from services.validation.errors import ProviderAtCapacityError, ProviderRateLimitedError
```

In `validate()`, change both the variable declaration and the except clause. The `last_exc`
annotation must accept both error types since either may be stored:

From:
```python
        last_exc: ProviderRateLimitedError | None = None
        for provider in self._providers:
            ...
            except ProviderRateLimitedError as exc:
                last_exc = exc
                logger.warning("ChainProvider: %s rate-limited, trying next provider", name)
```

To:
```python
        last_exc: ProviderRateLimitedError | ProviderAtCapacityError | None = None
        for provider in self._providers:
            ...
            except (ProviderRateLimitedError, ProviderAtCapacityError) as exc:
                last_exc = exc
                logger.warning(
                    "ChainProvider: %s at capacity or rate-limited, trying next provider", name
                )
```

- [ ] **Step 4: Run all chain provider tests — expect pass**

```
uv run pytest tests/unit/validation/test_chain_provider.py -v
```

Expected: all pass.

- [ ] **Step 5: Lint**

```
uv run ruff check services/validation/chain_provider.py tests/unit/validation/test_chain_provider.py
```

- [ ] **Step 6: Commit**

```bash
git add services/validation/chain_provider.py tests/unit/validation/test_chain_provider.py
git commit -m "#38 feat: ChainProvider catches ProviderAtCapacityError for chain fallback"
```

---

## Task 4: `USPSClient` accepts `QuotaGuard`

**Files:**
- Modify: `services/validation/usps_client.py`
- Modify: `tests/unit/validation/test_usps_client.py`

- [ ] **Step 1: Write failing tests**

In `test_usps_client.py`, update the `client` fixture and replace `test_accepts_custom_rate_limit_rps`:

Update the import block — remove any `_TokenBucket` reference (there is none in this file already).
Add `QuotaGuard, QuotaWindow` to the rate limit import:

```python
from services.validation._rate_limit import _RETRY_MAX, QuotaGuard, QuotaWindow
```

Replace the `client` fixture inside `TestUSPSClient`:

```python
    @pytest.fixture()
    def _default_guard(self) -> QuotaGuard:
        return QuotaGuard(
            windows=[QuotaWindow(limit=5, duration_s=1.0, mode="soft")],
            latency_budget_s=1.0,
            provider_name="usps",
        )

    @pytest.fixture()
    def client(self, mock_http: AsyncMock, _default_guard: QuotaGuard) -> USPSClient:
        return USPSClient(
            consumer_key="key",
            consumer_secret="secret",
            http_client=mock_http,
            quota_guard=_default_guard,
        )
```

Replace `test_accepts_custom_rate_limit_rps` with:

```python
    def test_accepts_quota_guard(self, mock_http: AsyncMock) -> None:
        guard = QuotaGuard(
            windows=[QuotaWindow(limit=10, duration_s=1.0, mode="soft")],
            provider_name="usps",
        )
        client = USPSClient(
            consumer_key="key",
            consumer_secret="secret",
            http_client=mock_http,
            quota_guard=guard,
        )
        assert client._rate_limiter is guard

    @pytest.mark.asyncio
    async def test_at_capacity_raises_before_http_call(
        self, client: USPSClient, mock_http: AsyncMock
    ) -> None:
        """QuotaGuard raising ProviderAtCapacityError must prevent any HTTP call."""
        from services.validation.errors import ProviderAtCapacityError

        with patch.object(
            client._rate_limiter,
            "acquire",
            side_effect=ProviderAtCapacityError("usps"),
        ):
            with pytest.raises(ProviderAtCapacityError):
                await client.validate_address("123 Main St", "Springfield", "IL")

        mock_http.get.assert_not_called()
        mock_http.post.assert_not_called()
```

- [ ] **Step 2: Run tests — expect failure**

```
uv run pytest tests/unit/validation/test_usps_client.py -v
```

Expected: `TypeError` — `USPSClient.__init__` does not yet accept `quota_guard`.

- [ ] **Step 3: Update `usps_client.py`**

Update the import block — replace:
```python
from services.validation._rate_limit import (
    _HTTP_TOO_MANY_REQUESTS,
    _RETRY_MAX,
    _parse_retry_after,
    _TokenBucket,
)
```
with:
```python
from services.validation._rate_limit import (
    _HTTP_TOO_MANY_REQUESTS,
    _RETRY_MAX,
    QuotaGuard,
    _parse_retry_after,
)
```

Replace the `__init__` signature and rate limiter construction:

```python
    def __init__(
        self,
        consumer_key: str,
        consumer_secret: str,
        http_client: httpx.AsyncClient,
        quota_guard: QuotaGuard,
    ) -> None:
        self._consumer_key = consumer_key
        self._consumer_secret = consumer_secret
        self._http = http_client
        self._token: USPSToken | None = None
        self._token_lock = asyncio.Lock()
        self._rate_limiter = quota_guard
```

Remove the `_DEFAULT_RATE_LIMIT_RPS` module constant and its docstring reference if present.
Remove the `rate_limit_rps` parameter from the class docstring.

- [ ] **Step 4: Run tests — expect pass**

```
uv run pytest tests/unit/validation/test_usps_client.py -v
```

Expected: all pass.

- [ ] **Step 5: Lint**

```
uv run ruff check services/validation/usps_client.py tests/unit/validation/test_usps_client.py
```

- [ ] **Step 6: Commit**

```bash
git add services/validation/usps_client.py tests/unit/validation/test_usps_client.py
git commit -m "#38 refactor: USPSClient accepts QuotaGuard instead of rate_limit_rps"
```

---

## Task 5: `GoogleClient` accepts `QuotaGuard`

**Files:**
- Modify: `services/validation/google_client.py`
- Modify: `tests/unit/validation/test_google_client.py`

- [ ] **Step 1: Write failing tests**

In `test_google_client.py`:

Update the import block — replace:
```python
from services.validation._rate_limit import _RETRY_MAX, _TokenBucket
```
with:
```python
from services.validation._rate_limit import _RETRY_MAX, QuotaGuard, QuotaWindow
```

Replace the `client` fixture in `TestGoogleClientValidateAddress`:

```python
    @pytest.fixture()
    def _default_guard(self) -> QuotaGuard:
        return QuotaGuard(
            windows=[
                QuotaWindow(limit=5, duration_s=60.0, mode="soft"),
                QuotaWindow(limit=160, duration_s=86_400.0, mode="hard"),
            ],
            latency_budget_s=1.0,
            provider_name="google",
        )

    @pytest.fixture()
    def client(self, mock_http: AsyncMock, _default_guard: QuotaGuard) -> GoogleClient:
        return GoogleClient(api_key=API_KEY, http_client=mock_http, quota_guard=_default_guard)
```

Replace `test_has_rate_limiter` and `test_accepts_custom_rate_limit_rps` with:

```python
    def test_accepts_quota_guard(self, mock_http: AsyncMock) -> None:
        guard = QuotaGuard(
            windows=[QuotaWindow(limit=5, duration_s=60.0, mode="soft")],
            provider_name="google",
        )
        client = GoogleClient(api_key=API_KEY, http_client=mock_http, quota_guard=guard)
        assert client._rate_limiter is guard

    @pytest.mark.asyncio
    async def test_at_capacity_raises_before_http_call(
        self, client: GoogleClient, mock_http: AsyncMock
    ) -> None:
        """QuotaGuard raising ProviderAtCapacityError must prevent any HTTP call."""
        from services.validation.errors import ProviderAtCapacityError

        with patch.object(
            client._rate_limiter,
            "acquire",
            side_effect=ProviderAtCapacityError("google"),
        ):
            with pytest.raises(ProviderAtCapacityError):
                await client.validate_address("123 Main St")

        mock_http.post.assert_not_called()
```

- [ ] **Step 2: Run tests — expect failure**

```
uv run pytest tests/unit/validation/test_google_client.py -v
```

Expected: `TypeError` — `GoogleClient.__init__` does not yet accept `quota_guard`.

- [ ] **Step 3: Update `google_client.py`**

Update the import block — replace:
```python
from services.validation._rate_limit import (
    _HTTP_TOO_MANY_REQUESTS,
    _RETRY_MAX,
    _parse_retry_after,
    _TokenBucket,
)
```
with:
```python
from services.validation._rate_limit import (
    _HTTP_TOO_MANY_REQUESTS,
    _RETRY_MAX,
    QuotaGuard,
    _parse_retry_after,
)
```

Replace the `__init__` signature and rate limiter construction:

```python
    def __init__(
        self,
        api_key: str,
        http_client: httpx.AsyncClient,
        quota_guard: QuotaGuard,
    ) -> None:
        self._api_key = api_key
        self._http = http_client
        self._rate_limiter = quota_guard
```

Remove the `_DEFAULT_RATE_LIMIT_RPS` module constant and the `rate_limit_rps` parameter from
the class docstring.

- [ ] **Step 4: Run tests — expect pass**

```
uv run pytest tests/unit/validation/test_google_client.py -v
```

Expected: all pass.

- [ ] **Step 5: Lint**

```
uv run ruff check services/validation/google_client.py tests/unit/validation/test_google_client.py
```

- [ ] **Step 6: Commit**

```bash
git add services/validation/google_client.py tests/unit/validation/test_google_client.py
git commit -m "#38 refactor: GoogleClient accepts QuotaGuard instead of rate_limit_rps"
```

---

## Task 6: `factory.py` — new env vars and `QuotaGuard` construction

**Files:**
- Modify: `services/validation/factory.py`
- Modify: `tests/unit/validation/test_provider_factory.py`

This task wires everything together. The factory reads new env vars, builds `QuotaGuard`
instances, and passes them to the clients.

### New env var summary

| Variable | Type | Default | Provider |
|---|---|---|---|
| `USPS_DAILY_LIMIT` | positive int | `10000` | USPS |
| `GOOGLE_RATE_LIMIT_RPM` | positive int | `5` | Google (replaces `GOOGLE_RATE_LIMIT_RPS`) |
| `GOOGLE_DAILY_LIMIT` | positive int | `160` | Google |
| `VALIDATION_LATENCY_BUDGET_S` | positive float | `1.0` | shared |

`GOOGLE_RATE_LIMIT_RPS` is removed. Any deployment using it must migrate to
`GOOGLE_RATE_LIMIT_RPM`.

- [ ] **Step 1: Write failing tests**

The existing factory tests that reference `_rate_limiter.rate` or `GOOGLE_RATE_LIMIT_RPS`
will break. Address them in the same step as writing new tests.

In `test_provider_factory.py`:

Add `QuotaGuard, QuotaWindow` to imports (no new import needed — we only introspect via
`client._rate_limiter`).

**Replace** `test_usps_rate_limit_rps_env_var` (in `TestGetProvider`):

```python
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
```

**Replace** `test_google_rate_limit_rps_env_var`:

```python
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
```

Add new `validate_config` tests at the end of `TestValidateConfig`:

```python
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
```

**Remove** the four tests in `TestGetProviderRpsGuard` that reference `GOOGLE_RATE_LIMIT_RPS`
or check `_rate_limiter.rate` (that attribute no longer exists on `QuotaGuard`):

```python
# Remove from TestGetProviderRpsGuard — GOOGLE_RATE_LIMIT_RPS no longer exists:
#   test_google_zero_rate_limit_raises
#   test_google_negative_rate_limit_raises

# Remove from TestGetProviderRpsGuard — _rate_limiter.rate no longer exists on QuotaGuard:
#   test_usps_zero_rate_limit_raises      (replace with version below)
#   test_usps_negative_rate_limit_raises  (replace with version below)
```

**Replace** those two USPS tests with versions that verify the error is raised without
inspecting the removed `.rate` attribute:

```python
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
```

And in `TestValidateConfig`, remove the three tests that reference `GOOGLE_RATE_LIMIT_RPS`:
```python
#   test_google_invalid_rate_limit_raises  (references GOOGLE_RATE_LIMIT_RPS)
#   test_google_zero_rate_limit_raises     (references GOOGLE_RATE_LIMIT_RPS)
#   test_google_negative_rate_limit_raises (references GOOGLE_RATE_LIMIT_RPS)
```

- [ ] **Step 2: Run tests — expect failure**

```
uv run pytest tests/unit/validation/test_provider_factory.py -v
```

Expected: failures on new tests; removed tests gone.

- [ ] **Step 3: Update `factory.py`**

Add to the top-level imports:

```python
from services.validation._rate_limit import QuotaGuard, QuotaWindow
```

**Replace `_parse_usps_config`** — new return type includes `daily_limit`:

```python
def _parse_usps_config() -> tuple[str, str, float, int]:
    """Read, validate, and return ``(key, secret, rps, daily_limit)``."""
    key = os.environ.get("USPS_CONSUMER_KEY", "").strip()
    secret = os.environ.get("USPS_CONSUMER_SECRET", "").strip()
    if not key or not secret:
        raise ValueError(
            "USPS_CONSUMER_KEY and USPS_CONSUMER_SECRET must be set "
            "when 'usps' appears in VALIDATION_PROVIDER"
        )
    try:
        rps = float(os.environ.get("USPS_RATE_LIMIT_RPS", "5.0"))
    except ValueError:
        raise ValueError("USPS_RATE_LIMIT_RPS must be a positive number (e.g. '5.0')") from None
    if rps <= 0:
        raise ValueError("USPS_RATE_LIMIT_RPS must be a positive number (e.g. '5.0')")
    try:
        daily_limit = int(os.environ.get("USPS_DAILY_LIMIT", "10000"))
    except ValueError:
        raise ValueError("USPS_DAILY_LIMIT must be a positive integer (e.g. '10000')") from None
    if daily_limit <= 0:
        raise ValueError("USPS_DAILY_LIMIT must be a positive integer (e.g. '10000')")
    return key, secret, rps, daily_limit
```

**Replace `_parse_google_config`** — returns `rpm` and `daily_limit`, drops `rps`:

```python
def _parse_google_config() -> tuple[str, int, int]:
    """Read, validate, and return ``(api_key, rpm, daily_limit)``."""
    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not api_key:
        raise ValueError(
            "GOOGLE_API_KEY must be set when 'google' appears in VALIDATION_PROVIDER"
        )
    try:
        rpm = int(os.environ.get("GOOGLE_RATE_LIMIT_RPM", "5"))
    except ValueError:
        raise ValueError(
            "GOOGLE_RATE_LIMIT_RPM must be a positive integer (e.g. '5')"
        ) from None
    if rpm <= 0:
        raise ValueError("GOOGLE_RATE_LIMIT_RPM must be a positive integer (e.g. '5')")
    try:
        daily_limit = int(os.environ.get("GOOGLE_DAILY_LIMIT", "160"))
    except ValueError:
        raise ValueError(
            "GOOGLE_DAILY_LIMIT must be a positive integer (e.g. '160')"
        ) from None
    if daily_limit <= 0:
        raise ValueError("GOOGLE_DAILY_LIMIT must be a positive integer (e.g. '160')")
    return api_key, rpm, daily_limit
```

**Add `_parse_latency_budget`**:

```python
def _parse_latency_budget() -> float:
    """Read and validate ``VALIDATION_LATENCY_BUDGET_S``."""
    try:
        budget = float(os.environ.get("VALIDATION_LATENCY_BUDGET_S", "1.0"))
    except ValueError:
        raise ValueError(
            "VALIDATION_LATENCY_BUDGET_S must be a positive number (e.g. '1.0')"
        ) from None
    if budget <= 0:
        raise ValueError(
            "VALIDATION_LATENCY_BUDGET_S must be a positive number (e.g. '1.0')"
        )
    return budget
```

**Replace `_get_usps_provider`**:

```python
def _get_usps_provider(
    key: str, secret: str, rps: float, daily_limit: int, latency_budget_s: float
) -> USPSProvider:
    global _usps_provider  # noqa: PLW0603
    if _usps_provider is None:
        logger.debug(
            "get_provider: creating USPSProvider singleton (%.1f rps, %d/day)", rps, daily_limit
        )
        guard = QuotaGuard(
            windows=[
                QuotaWindow(limit=int(rps), duration_s=1.0, mode="soft"),
                QuotaWindow(limit=daily_limit, duration_s=86_400.0, mode="soft"),
            ],
            latency_budget_s=latency_budget_s,
            provider_name="usps",
        )
        _usps_provider = USPSProvider(
            client=USPSClient(
                consumer_key=key,
                consumer_secret=secret,
                http_client=_get_http_client(),
                quota_guard=guard,
            )
        )
    return _usps_provider
```

**Replace `_get_google_provider`**:

```python
def _get_google_provider(
    api_key: str, rpm: int, daily_limit: int, latency_budget_s: float
) -> GoogleProvider:
    global _google_provider  # noqa: PLW0603
    if _google_provider is None:
        logger.debug(
            "get_provider: creating GoogleProvider singleton (%d rpm, %d/day)", rpm, daily_limit
        )
        guard = QuotaGuard(
            windows=[
                QuotaWindow(limit=rpm, duration_s=60.0, mode="soft"),
                QuotaWindow(limit=daily_limit, duration_s=86_400.0, mode="hard"),
            ],
            latency_budget_s=latency_budget_s,
            provider_name="google",
        )
        _google_provider = GoogleProvider(
            client=GoogleClient(
                api_key=api_key,
                http_client=_get_http_client(),
                quota_guard=guard,
            )
        )
    return _google_provider
```

**Replace `_build_single_provider`** (update the calls):

```python
def _build_single_provider(name: str) -> ValidationProvider:
    budget = _parse_latency_budget()
    if name == "usps":
        key, secret, rps, daily_limit = _parse_usps_config()
        return _get_usps_provider(key, secret, rps, daily_limit, budget)
    if name == "google":
        api_key, rpm, daily_limit = _parse_google_config()
        return _get_google_provider(api_key, rpm, daily_limit, budget)
    raise ValueError(
        f"Unknown provider name: '{name}'. Supported values: 'none', 'usps', 'google'."
    )
```

**Update `validate_config`** — add budget and new per-provider checks after the existing
per-provider loop:

```python
    if not names:
        logger.info("validate_config: provider=none")
        return

    for name in names:
        _check_provider_config(name)

    # Validate shared latency budget
    _parse_latency_budget()

    ttl_str = os.environ.get("VALIDATION_CACHE_TTL_DAYS", "30")
    # ... rest unchanged
```

Update the module docstring to document the four new env vars and note that
`GOOGLE_RATE_LIMIT_RPS` has been removed.

- [ ] **Step 4: Run all factory tests — expect pass**

```
uv run pytest tests/unit/validation/test_provider_factory.py -v
```

Expected: all pass.

- [ ] **Step 5: Run full suite**

```
uv run pytest --no-cov -x
```

Expected: all pass.

- [ ] **Step 6: Lint**

```
uv run ruff check services/validation/factory.py tests/unit/validation/test_provider_factory.py
```

- [ ] **Step 7: Commit**

```bash
git add services/validation/factory.py tests/unit/validation/test_provider_factory.py
git commit -m "#38 feat: factory builds QuotaGuard from new env vars; drop GOOGLE_RATE_LIMIT_RPS"
```

---

## Task 7: Remove `_TokenBucket`

`_TokenBucket` is now unused. Remove it and clean up the last references.

**Files:**
- Modify: `services/validation/_rate_limit.py`

- [ ] **Step 1: Verify nothing imports `_TokenBucket`**

```
uv run grep -r "_TokenBucket" services/ tests/
```

Expected: no results (Tasks 4 and 5 removed all imports).

- [ ] **Step 2: Delete `_TokenBucket` from `_rate_limit.py`**

Remove the entire `_TokenBucket` class and its docstring.

- [ ] **Step 3: Run full suite**

```
uv run pytest --no-cov -x
```

Expected: all pass.

- [ ] **Step 4: Lint**

```
uv run ruff check services/validation/_rate_limit.py
```

- [ ] **Step 5: Commit**

```bash
git add services/validation/_rate_limit.py
git commit -m "#38 refactor: remove _TokenBucket, superseded by QuotaGuard"
```

---

## Task 8: Docs and coverage check

**Files:**
- Modify: `docs/VALIDATION-PROVIDERS.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Update `VALIDATION-PROVIDERS.md`**

Replace the "Rate limit env vars" section:

```markdown
## Rate limit and quota env vars

| Variable | Default | Notes |
|---|---|---|
| `USPS_RATE_LIMIT_RPS` | `5.0` | USPS per-second window limit |
| `USPS_DAILY_LIMIT` | `10000` | USPS per-day window limit (soft — queues up to latency budget) |
| `GOOGLE_RATE_LIMIT_RPM` | `5` | Google per-minute window limit (soft) |
| `GOOGLE_DAILY_LIMIT` | `160` | Google per-day hard ceiling — never exceeded |
| `VALIDATION_LATENCY_BUDGET_S` | `1.0` | Max seconds a request may queue before overflowing to the next provider |

`GOOGLE_RATE_LIMIT_RPS` has been removed. Rename to `GOOGLE_RATE_LIMIT_RPM` in
`/etc/address-validator/env`.
```

Update the USPS and Google provider bullets to reflect `QuotaGuard` rather than "token bucket":

```markdown
- Rate limit: multi-window quota guard — per-second soft window (default 5 req/s), per-day soft
  window (default 10 000/day). Configurable via `USPS_RATE_LIMIT_RPS` and `USPS_DAILY_LIMIT`.
```

```markdown
- Rate limit: multi-window quota guard — per-minute soft window (default 5 req/min), per-day
  hard ceiling (default 160/day). Configurable via `GOOGLE_RATE_LIMIT_RPM` and
  `GOOGLE_DAILY_LIMIT`.
```

Update the "Fallback chain internals" section to mention `ProviderAtCapacityError`:

```markdown
`ChainProvider` catches both `ProviderRateLimitedError` (upstream HTTP 429 after retries) and
`ProviderAtCapacityError` (local quota exhausted before sending) and delegates to the next
provider.
```

- [ ] **Step 2: Update `AGENTS.md` sensitive areas table**

Add one new row for `_rate_limit.py`, and **replace** (not append) the existing `factory.py` row:

New row to add:
```markdown
| `services/validation/_rate_limit.py` | `QuotaGuard` and `QuotaWindow` — `acquire()` holds the single lock across all windows; changes to the refill/consume logic affect every provider |
```

Replace the existing `factory.py` row with:
```markdown
| `services/validation/factory.py` | Module-level singletons (`_usps_provider`, `_google_provider`, `_http_client`, `_caching_provider`) — reset to `None` in test fixtures; `validate_config()` is called from the lifespan startup hook and raises `ValueError` on misconfiguration; `_parse_latency_budget()`, `_parse_usps_config()`, `_parse_google_config()` — adding a new `QuotaWindow` or changing enforcement mode requires updating factory construction and `validate_config()` in sync |
```

- [ ] **Step 3: Run full suite with coverage**

```
uv run pytest
```

Expected: all pass, coverage ≥ 80% (baseline ~93% — should hold or improve).

- [ ] **Step 4: Lint**

```
uv run ruff check .
```

- [ ] **Step 5: Commit**

```bash
git add docs/VALIDATION-PROVIDERS.md AGENTS.md
git commit -m "#38 docs: update VALIDATION-PROVIDERS and AGENTS for QuotaGuard"
```

---

## Final verification

- [ ] Run `uv run pytest` — all pass, coverage ≥ 80%
- [ ] Run `uv run ruff check .` — clean
- [ ] Confirm `GOOGLE_RATE_LIMIT_RPS` does not appear anywhere in `services/` or `tests/`

```
grep -r "GOOGLE_RATE_LIMIT_RPS" services/ tests/ docs/VALIDATION-PROVIDERS.md
```

Expected: no results (only the removal note in the docs is acceptable).
