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
from time import monotonic
from typing import Literal

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
                    self._tokens[i] = min(float(window.limit), self._tokens[i] + elapsed * rate)
                    self._last_refill[i] = now

            # --- Consume from all windows ---
            for i in range(len(self._windows)):
                self._tokens[i] -= 1.0


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
