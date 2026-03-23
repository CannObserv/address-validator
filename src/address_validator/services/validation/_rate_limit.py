"""Shared rate-limiting utilities for validation provider clients.

Provides:
- :class:`QuotaWindow` — descriptor for a single quota constraint
- :class:`QuotaGuard` — multi-window async rate limiter
- :func:`_parse_retry_after` — extracts backoff delay from a 429 response
- Retry constants: :data:`_RETRY_MAX`, :data:`_RETRY_BASE_DELAY_S`
"""

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Literal
from zoneinfo import ZoneInfo

import httpx

from address_validator.services.validation.errors import ProviderAtCapacityError

# HTTP status code for "Too Many Requests".
_HTTP_TOO_MANY_REQUESTS = 429

# Maximum number of retry attempts on HTTP 429 (not counting the initial try).
_RETRY_MAX = 3

# Base delay (seconds) for exponential backoff when no Retry-After header is present.
_RETRY_BASE_DELAY_S = 1.0

# Maximum jitter (seconds) added to exponential backoff to avoid thundering herd.
_RETRY_JITTER_S = 0.5

# Sub-nanosecond threshold below which a computed wait is treated as zero.
# Prevents floating-point dust from triggering a sleep + re-acquire cycle.
_WAIT_EPSILON_S = 1e-9


@dataclass(frozen=True)
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

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError(f"QuotaWindow.limit must be positive, got {self.limit}")
        if self.duration_s <= 0:
            raise ValueError(f"QuotaWindow.duration_s must be positive, got {self.duration_s}")


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

    def should_reset(self, last_reset: datetime) -> bool:
        """Return True if *last_reset* was on a different calendar day than now."""
        now = _now_in_tz(self.timezone)
        return now.date() != last_reset.date()


class QuotaGuard:
    """Multi-window async rate limiter with a latency budget.

    Each :class:`QuotaWindow` is backed by a token bucket (``rate = limit / duration_s``,
    ``capacity = limit``).  On service start the bucket is full (optimistic — does
    not know mid-period usage across restarts).

    ``acquire()`` is the sole entry point.  It:

    1. Refills all windows based on elapsed time.
    2. Raises :class:`~services.validation.errors.ProviderAtCapacityError`
       immediately if any ``"hard"`` window has no token.
    3. Computes the maximum wait across all windows that need one.
    4. Raises if that wait exceeds ``latency_budget_s``.
    5. Releases lock, sleeps the required wait, re-acquires lock, then
       re-checks token availability and consumes one token from every
       window (loops if needed).

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
        windows: list[QuotaWindow | FixedResetQuotaWindow],
        latency_budget_s: float = 1.0,
        provider_name: str = "",
    ) -> None:
        self._windows = windows
        self._latency_budget_s = latency_budget_s
        self._provider_name = provider_name
        self._tokens: list[float] = [float(w.limit) for w in windows]
        self._last_refill: list[float] = [monotonic() for _ in windows]
        self._last_reset: list[datetime | None] = [
            _now_in_tz(w.timezone) if isinstance(w, FixedResetQuotaWindow) else None
            for w in windows
        ]
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:  # noqa: PLR0912
        """Acquire one token from every window, blocking up to the latency budget."""
        deadline = monotonic() + self._latency_budget_s

        while True:
            async with self._lock:
                # --- Fixed-reset windows: reset at day boundary ---
                for i, window in enumerate(self._windows):
                    if (
                        isinstance(window, FixedResetQuotaWindow)
                        and self._last_reset[i] is not None
                        and window.should_reset(self._last_reset[i])
                    ):
                        self._tokens[i] = float(window.limit)
                        self._last_reset[i] = _now_in_tz(window.timezone)

                # --- Refill all windows ---
                now = monotonic()
                for i, window in enumerate(self._windows):
                    if isinstance(window, FixedResetQuotaWindow):
                        continue
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
                    if isinstance(window, FixedResetQuotaWindow):
                        continue
                    if self._tokens[i] < 1:
                        rate = window.limit / window.duration_s
                        wait = (1 - self._tokens[i]) / rate
                        max_wait = max(max_wait, wait)

                # --- No wait needed: consume and return ---
                if max_wait < _WAIT_EPSILON_S:
                    for i in range(len(self._windows)):
                        self._tokens[i] -= 1.0
                    return

                # --- Wait would exceed deadline: reject ---
                if now + max_wait > deadline:
                    raise ProviderAtCapacityError(self._provider_name)

                wait = max_wait

            # --- Lock released: sleep concurrently with other waiters ---
            await asyncio.sleep(wait)
            # Loop back to re-acquire lock and re-check token availability

    def adjust_tokens(self, window_index: int, delta: float) -> None:
        """Adjust the token count for a specific window by *delta*.

        Clamps the result to ``[0, window.limit]``.  Intended for
        reconciliation — call under external synchronisation if needed.
        """
        window = self._windows[window_index]  # raises IndexError if out of range
        self._tokens[window_index] = max(
            0.0, min(float(window.limit), self._tokens[window_index] + delta)
        )

    _DAILY_WINDOW_INDEX = 1

    def get_daily_quota_state(self) -> dict | None:
        """Return remaining/limit for the daily window, or None if no daily window."""
        if len(self._windows) <= self._DAILY_WINDOW_INDEX:
            return None
        idx = self._DAILY_WINDOW_INDEX
        return {
            "remaining": int(self._tokens[idx]),
            "limit": self._windows[idx].limit,
        }


def _parse_retry_after(response: httpx.Response, attempt: int) -> float:
    """Return the number of seconds to wait before retrying after a 429.

    Reads the ``Retry-After`` header when present (integer seconds only).
    Falls back to exponential backoff with jitter:
    ``base * 2^attempt + uniform(0, jitter)``.

    Parameters
    ----------
    response:
        The HTTP 429 response from the provider.
    attempt:
        Zero-based attempt index (0 = first retry, 1 = second, ...).
    """
    retry_after = response.headers.get("Retry-After", "").strip()
    if retry_after.isdigit():
        return float(retry_after)
    return _RETRY_BASE_DELAY_S * (2**attempt) + random.uniform(0, _RETRY_JITTER_S)  # noqa: S311
