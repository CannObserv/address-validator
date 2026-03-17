"""Shared rate-limiting utilities for validation provider clients.

Provides:
- :class:`_TokenBucket` — async token-bucket rate limiter
- :func:`_parse_retry_after` — extracts backoff delay from a 429 response
- Retry constants: :data:`_RETRY_MAX`, :data:`_RETRY_BASE_DELAY_S`
"""

import asyncio
import random
from time import monotonic

import httpx

# HTTP status code for "Too Many Requests".
_HTTP_TOO_MANY_REQUESTS = 429

# Maximum number of retry attempts on HTTP 429 (not counting the initial try).
_RETRY_MAX = 3

# Base delay (seconds) for exponential backoff when no Retry-After header is present.
_RETRY_BASE_DELAY_S = 1.0

# Maximum jitter (seconds) added to exponential backoff to avoid thundering herd.
_RETRY_JITTER_S = 0.5


class _TokenBucket:
    """Minimal async token-bucket rate limiter.

    The :class:`asyncio.Lock` is created at instantiation time inside the
    instance so it is always bound to the correct running event loop.
    """

    def __init__(self, rate: float, capacity: float) -> None:
        self.rate = rate
        self.capacity = capacity
        self._tokens = capacity
        self._last_refill = monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = monotonic()
            elapsed = now - self._last_refill
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last_refill = now
            if self._tokens < 1:
                wait = (1 - self._tokens) / self.rate
                await asyncio.sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= 1.0


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
