"""Shared rate-limiting utilities for validation provider clients.

Provides:
- :class:`QuotaWindow` — descriptor for a single quota constraint
- :class:`QuotaGuard` — multi-window async rate limiter
- :func:`_parse_retry_after` — extracts backoff delay from a 429 response
- :func:`_json_object` — decodes a 2xx body, mapping an unusable one to a typed error
- :class:`_FieldReader` — reads a body's fields, mapping a wrong-typed one to a typed error
- Retry constants: :data:`_RETRY_MAX`, :data:`_RETRY_BASE_DELAY_S`
"""

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from time import monotonic
from typing import Any, Literal, NoReturn
from zoneinfo import ZoneInfo

import httpx

from address_validator.services.validation.errors import (
    ProviderAtCapacityError,
    ProviderBadRequestError,
    ProviderTransientError,
)

# HTTP status code for "Bad Request" — provider rejected the input.
_HTTP_BAD_REQUEST = 400

# HTTP status codes treated as operator-action-required auth failures
# (expired creds, missing IAM role, project disabled).
_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403

# HTTP status code for "Too Many Requests".
_HTTP_TOO_MANY_REQUESTS = 429

# HTTP server-error range — upstream service failure; treated as transient.
_HTTP_SERVER_ERROR_MIN = 500
_HTTP_SERVER_ERROR_MAX = 599

# Default retry-after hint when a transient error has no explicit Retry-After
# header.  Kept small — the chain provider only uses it to populate the
# response header when the chain is fully exhausted.
_TRANSIENT_DEFAULT_RETRY_AFTER_S = 1.0

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

    def seconds_until_reset(self) -> float:
        """Return the seconds until the next midnight in the configured timezone.

        Measured in elapsed time (via timestamps), not wall-clock time, so a day
        with a DST change counts its real 23 or 25 hours.
        """
        now = _now_in_tz(self.timezone)
        midnight = datetime.combine(now.date() + timedelta(days=1), time(), tzinfo=self.timezone)
        return midnight.timestamp() - now.timestamp()


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

    Either raise carries ``retry_after_seconds``: the time until every window
    holds a token again — the longest wait, or for a ``FixedResetQuotaWindow``
    the time until its reset (GH #270).

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

    async def acquire(self) -> None:
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

                # --- Soft windows: compute max wait ---
                max_wait = 0.0
                for i, window in enumerate(self._windows):
                    if not isinstance(window, FixedResetQuotaWindow):
                        max_wait = max(max_wait, self._wait_for_token(i))

                # --- Hard windows: reject immediately if exhausted ---
                hard_waits = [
                    self._wait_for_token(i)
                    for i, window in enumerate(self._windows)
                    if window.mode == "hard" and self._tokens[i] < 1
                ]
                if hard_waits:
                    raise ProviderAtCapacityError(
                        self._provider_name, retry_after_seconds=max(max_wait, *hard_waits)
                    )

                # --- No wait needed: consume and return ---
                if max_wait < _WAIT_EPSILON_S:
                    for i in range(len(self._windows)):
                        self._tokens[i] -= 1.0
                    return

                # --- Wait would exceed deadline: reject ---
                if now + max_wait > deadline:
                    raise ProviderAtCapacityError(self._provider_name, retry_after_seconds=max_wait)

                wait = max_wait

            # --- Lock released: sleep concurrently with other waiters ---
            await asyncio.sleep(wait)
            # Loop back to re-acquire lock and re-check token availability

    def _wait_for_token(self, i: int) -> float:
        """Return the seconds until window *i* holds a whole token (0 if it does).

        Call after the refill step, under the lock.
        """
        if self._tokens[i] >= 1:
            return 0.0
        window = self._windows[i]
        if isinstance(window, FixedResetQuotaWindow):
            return window.seconds_until_reset()
        return (1 - self._tokens[i]) / (window.limit / window.duration_s)

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
        """Return remaining/limit for the daily window, or None if no daily window.

        ``remaining`` is the current token-bucket balance — *not* requests
        made today. The admin dashboard surfaces audit-derived
        ``requests_today`` to users; this field is retained for tests and
        for any future operator-facing burst-headroom indicator.
        """
        if len(self._windows) <= self._DAILY_WINDOW_INDEX:
            return None
        idx = self._DAILY_WINDOW_INDEX
        return {
            "remaining": int(self._tokens[idx]),
            "limit": self._windows[idx].limit,
        }


def _raise_for_unexpected_status(
    exc: httpx.HTTPStatusError,
    *,
    provider: str,
    logger: logging.Logger,
) -> NoReturn:
    """Map a non-2xx response outside the 400/429 paths to a typed provider error.

    Callers handle HTTP 400 (``ProviderBadRequestError``) and 429
    (``ProviderRateLimitedError``) directly; this helper covers everything
    else so raw :class:`httpx.HTTPStatusError` never escapes the client:

    * **401 / 403** — credentials/IAM problem.  Logged at ``ERROR`` (operator
      action required) and raised as ``ProviderBadRequestError`` so the
      chain provider falls through to the next provider instead of taking
      the whole service down.
    * **5xx** — upstream outage; raised as ``ProviderTransientError`` so the
      chain falls through, mirroring the 429 path.
    * **Anything else** — also mapped to ``ProviderTransientError`` and
      logged so we can spot new failure modes.

    Never returns.
    """
    status = exc.response.status_code
    if status in (_HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN):
        logger.error(
            "%s provider returned HTTP %d — operator action required "
            "(rotate credentials or verify IAM permissions)",
            provider,
            status,
        )
        raise ProviderBadRequestError(provider, detail=f"HTTP {status}") from exc
    if _HTTP_SERVER_ERROR_MIN <= status <= _HTTP_SERVER_ERROR_MAX:
        logger.warning("%s provider returned HTTP %d (upstream outage)", provider, status)
        raise ProviderTransientError(
            provider, retry_after_seconds=_TRANSIENT_DEFAULT_RETRY_AFTER_S
        ) from exc
    logger.warning("%s provider returned unexpected HTTP %d", provider, status)
    raise ProviderTransientError(
        provider, retry_after_seconds=_TRANSIENT_DEFAULT_RETRY_AFTER_S
    ) from exc


def _raise_for_unusable_body(
    reason: str,
    *,
    provider: str,
    logger: logging.Logger,
    cause: Exception | None = None,
) -> NoReturn:
    """Map a 2xx response whose body cannot be used to ``ProviderTransientError``.

    The body-layer counterpart of :func:`_raise_for_request_error`.  A gateway
    or captive-proxy page served with HTTP 200 is the plausible cause — an
    upstream fault, so transient: bad-request would end an all-providers-failed
    chain blaming the input (GH #271).

    *reason* must be a fixed string.  Never the body or a decoder's message:
    provider bodies carry the address, and the USPS token body the secret.
    *cause*, when given, is chained as ``__cause__`` like the sibling helpers.
    """
    logger.warning("%s provider returned %s", provider, reason)
    raise ProviderTransientError(
        provider, retry_after_seconds=_TRANSIENT_DEFAULT_RETRY_AFTER_S
    ) from cause


def _json_object(
    resp: httpx.Response,
    *,
    provider: str,
    logger: logging.Logger,
) -> dict[str, Any]:
    """Return a 2xx response's body as a JSON object, else raise ``ProviderTransientError``.

    The clients' mappers call ``.get`` on the top level, so a list, string or
    null would raise ``AttributeError`` and 500 the request (GH #271).  Follows
    ``libpostal_client`` (#239): ``json.JSONDecodeError`` and
    ``UnicodeDecodeError`` are both ``ValueError``.  The shape is logged by
    type name only.
    """
    try:
        raw = resp.json()
    except ValueError as exc:
        _raise_for_unusable_body("a non-JSON body", provider=provider, logger=logger, cause=exc)
    if not isinstance(raw, dict):
        _raise_for_unusable_body(
            f"an unexpected JSON shape ({type(raw).__name__})", provider=provider, logger=logger
        )
    return raw


class _FieldReader:
    """Typed reads of a decoded provider body's fields (GH #278).

    :func:`_json_object` checks the top level only.  A nested field of the wrong
    JSON type — ``"address": []``, a numeric ``ZIPCode`` — raised
    ``AttributeError`` (a 500) or gave a wrong answer.  Each read returns the
    field when it has the expected type and its default when it is absent or
    ``null``; any other value raises ``ProviderTransientError`` via
    :func:`_raise_for_unusable_body`.  The log names the field and its JSON type,
    never the value: provider bodies carry the address.
    """

    def __init__(self, provider: str, logger: logging.Logger) -> None:
        self._provider = provider
        self._logger = logger

    def _wrong_type(self, field: str, value: object) -> NoReturn:
        _raise_for_unusable_body(
            f"a wrong-typed field ({field}: {type(value).__name__})",
            provider=self._provider,
            logger=self._logger,
        )

    def obj(self, container: dict[str, Any], key: str) -> dict[str, Any]:
        value = container.get(key)
        if value is None:
            return {}
        if not isinstance(value, dict):
            self._wrong_type(key, value)
        return value

    def text(self, container: dict[str, Any], key: str) -> str:
        value = container.get(key)
        if value is None:
            return ""
        if not isinstance(value, str):
            self._wrong_type(key, value)
        return value

    def text_list(self, container: dict[str, Any], key: str) -> list[str]:
        value = container.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            self._wrong_type(key, value)
        for item in value:
            if not isinstance(item, str):
                self._wrong_type(f"{key}[]", item)
        return value

    def flag(self, container: dict[str, Any], key: str) -> bool:
        value = container.get(key)
        if value is None:
            return False
        if not isinstance(value, bool):
            self._wrong_type(key, value)
        return value

    def number(self, container: dict[str, Any], key: str) -> float | None:
        value = container.get(key)
        # bool is an int subclass, so it would pass the number check.
        if value is not None and (isinstance(value, bool) or not isinstance(value, int | float)):
            self._wrong_type(key, value)
        return value


def _raise_for_request_error(
    exc: Exception,
    *,
    provider: str,
    logger: logging.Logger,
) -> NoReturn:
    """Map a failed request (no usable HTTP response) to ``ProviderTransientError``.

    The request-layer counterpart of :func:`_raise_for_unexpected_status`:
    a raw :class:`httpx.RequestError` — connect error, timeout, protocol
    error, undecodable body — or google-auth's own ``TransportError`` from a
    credential refresh never escapes the client, so the chain provider falls
    through to the next provider (GH #257).  ``RequestError``, not
    ``TransportError``, for the reason ``libpostal_client`` gives (#239).

    Logs the exception type only — its message is not ours to vet for PII.
    """
    logger.warning("%s provider request failed (%s)", provider, type(exc).__name__)
    raise ProviderTransientError(
        provider, retry_after_seconds=_TRANSIENT_DEFAULT_RETRY_AFTER_S
    ) from exc


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
