# Multi-Window Quota Guard Design

**Date:** 2026-03-19

## Problem

The current `_TokenBucket` is a single-window, always-blocking per-second rate limiter. It
cannot express multi-window quotas (e.g. per-minute, per-day), cannot enforce a latency budget
that triggers chain fallback before a request is sent, and cannot protect a provider's daily
budget as a hard ceiling. Under burst traffic the bucket queues requests indefinitely, leading
to client-visible latency growth and no meaningful overflow to secondary providers.

## Goals

- Multi-window quota enforcement for each provider (per-second, per-minute, per-day, or any
  combination)
- Latency-bounded queue: queue requests up to a configurable budget (default 1 s); overflow to
  the next provider rather than accumulate unbounded latency
- Hard ceiling mode per window: some windows (e.g. Google daily) must never be exceeded
- General: applies to all current and future providers, not just the primary
- Minimal disruption to existing call sites (`acquire()` signature unchanged)

## Non-goals

- Round-robin or load-balanced routing across providers
- Per-client fairness / per-client rate limiting
- Persistent quota state across restarts (in-process tracking only; Google Cloud Quota API
  integration is a separate future task)
- Dynamic quota seeding from upstream APIs at startup

## Assumed provider limits

| Provider | Window | Limit | Source |
|---|---|---|---|
| USPS | per-second | 5 | Research doc (Jan 2025) — **must be confirmed at developer.usps.com before implementation** |
| USPS | per-day | 10,000 | Same — unconfirmed; treat as working assumption |
| Google | per-minute | 5 | Confirmed (current project quota) |
| Google | per-day | 160 | Confirmed (current project quota) |

A USPS per-hour window may also exist; if confirmed it should be added as a third USPS window.

---

## Design

### 1. New abstractions — `services/validation/_rate_limit.py`

#### `QuotaWindow`

```python
@dataclass
class QuotaWindow:
    limit: int           # max requests in the window
    duration_s: float    # window duration in seconds
    mode: Literal["soft", "hard"]
```

Internally backed by a token bucket: `rate = limit / duration_s`, `capacity = limit`. Bucket
starts full (optimistic — service doesn't know mid-period usage on startup).

- `"soft"` — queue the request by sleeping up to the provider's `latency_budget_s`; raise
  `ProviderAtCapacityError` if the required wait would exceed the budget
- `"hard"` — raise `ProviderAtCapacityError` immediately when the window is exhausted; never
  sleep

#### `QuotaGuard`

```python
class QuotaGuard:
    def __init__(self, windows: list[QuotaWindow], latency_budget_s: float = 1.0) -> None: ...
    async def acquire(self) -> None: ...
```

Replaces `_TokenBucket` in both provider clients. `acquire()` holds a single `asyncio.Lock`
across all windows and executes atomically:

1. Refill all window token buckets based on elapsed time
2. Compute required wait per window: `max(0, (1 − tokens) / rate)`
3. If any `hard` window requires a wait > 0: raise `ProviderAtCapacityError` immediately
4. If `max(all waits) > latency_budget_s`: raise `ProviderAtCapacityError`
5. Sleep `max(all waits)`, then consume 1 token from each window

The call site in both clients remains `await self._rate_limiter.acquire()` — no change.

#### `ProviderAtCapacityError`

```python
class ProviderAtCapacityError(Exception):
    def __init__(self, provider: str, retry_after_seconds: float = 0.0) -> None: ...
```

Semantically distinct from `ProviderRateLimitedError`:

| Exception | Meaning |
|---|---|
| `ProviderRateLimitedError` | Request was sent; upstream returned HTTP 429 after all retries |
| `ProviderAtCapacityError` | Request was not sent; local quota guard refused to dispatch |

Both surface as HTTP 429 + `Retry-After` to the client when all providers are exhausted.

`_TokenBucket` is removed once both clients have been migrated. The existing `_parse_retry_after`
and retry constants are unchanged.

---

### 2. Client changes

Both `USPSClient` and `GoogleClient` replace their `_TokenBucket` field with a `QuotaGuard`.
Construction moves to `factory.py`; clients accept a `QuotaGuard` rather than building it
internally. The `acquire()` call site is unchanged in both retry loops.

**USPS default windows:**

```python
[
    QuotaWindow(limit=usps_rate_limit_rps,  duration_s=1.0,     mode="soft"),  # per-second
    QuotaWindow(limit=usps_daily_limit,     duration_s=86_400.0, mode="soft"),  # per-day
]
```

The per-day window is `"soft"` — USPS's 10 K/day budget is generous relative to observed
traffic, and queuing briefly is preferable to abandoning USPS prematurely.

**Google default windows:**

```python
[
    QuotaWindow(limit=google_rate_limit_rpm, duration_s=60.0,    mode="soft"),  # per-minute
    QuotaWindow(limit=google_daily_limit,    duration_s=86_400.0, mode="hard"),  # per-day
]
```

The per-day window is `"hard"` — 160/day is a strict budget; once exhausted, Google must not
receive further requests until the next day.

`GOOGLE_RATE_LIMIT_RPS` (current env var) is removed; `GOOGLE_RATE_LIMIT_RPM` replaces it.

---

### 3. New env vars

| Variable | Default | Notes |
|---|---|---|
| `VALIDATION_LATENCY_BUDGET_S` | `1.0` | Shared latency budget passed to every `QuotaGuard` |
| `USPS_DAILY_LIMIT` | `10000` | USPS per-day window limit |
| `GOOGLE_RATE_LIMIT_RPM` | `5` | Google per-minute window limit; replaces `GOOGLE_RATE_LIMIT_RPS` |
| `GOOGLE_DAILY_LIMIT` | `160` | Google per-day hard ceiling |

`USPS_RATE_LIMIT_RPS` is retained. `GOOGLE_RATE_LIMIT_RPS` is removed (breaking env var change
— document in release notes).

`validate_config()` gains checks:
- `VALIDATION_LATENCY_BUDGET_S > 0`
- `USPS_DAILY_LIMIT`, `GOOGLE_RATE_LIMIT_RPM`, `GOOGLE_DAILY_LIMIT` are positive integers

---

### 4. `ChainProvider` changes

`ChainProvider.validate()` catches both error types:

```python
except (ProviderRateLimitedError, ProviderAtCapacityError) as exc:
    last_exc = exc
    logger.warning("ChainProvider: %s at capacity or rate-limited, trying next provider", name)
```

The final raise is unchanged: `ProviderRateLimitedError("all", retry_after_seconds=last_exc.retry_after_seconds)`.
The router does not need to know about `ProviderAtCapacityError`.

---

### 5. AGENTS.md sensitive areas update

`services/validation/_rate_limit.py` and `factory.py` entries in the sensitive areas table
should be updated to mention `QuotaGuard`, `QuotaWindow`, and `VALIDATION_LATENCY_BUDGET_S`.

---

## Test strategy

- **`QuotaGuard` unit tests** (mocked `monotonic`):
  - Soft window queues up to budget, raises `ProviderAtCapacityError` beyond it
  - Hard window raises immediately on exhaustion regardless of budget
  - Multi-window: hard exhaustion blocks even when soft window has capacity
  - Multi-window: soft wait is `max` across all soft windows, not sum
  - Concurrent `acquire()` calls serialise correctly under the lock

- **Client tests**: `ProviderAtCapacityError` is raised (no HTTP call made) when budget would
  be exceeded; existing 429-retry tests continue to pass

- **`ChainProvider` tests**: fallback triggers on both `ProviderRateLimitedError` and
  `ProviderAtCapacityError`; `ProviderRateLimitedError("all")` raised when all providers
  exhausted

- **`validate_config()` tests**: invalid `VALIDATION_LATENCY_BUDGET_S` raises `ValueError`;
  invalid daily/RPM limits raise `ValueError`

Coverage baseline ~93% — new code has clear unit-testable boundaries; floor must be maintained.

---

## Known limitation

`QuotaGuard` starts each window at full capacity on service start/restart. If the service
restarts mid-day after consuming part of a daily budget, the tracker resets to the full limit.
This is acceptable for now. The deferred Google Cloud Quota API integration would seed the
per-day counter from actual remaining quota at startup.
