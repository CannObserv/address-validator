# QuotaGuard.acquire() — Remove Head-of-Line Blocking

**Issue:** #56
**Date:** 2026-03-23

## Problem

`QuotaGuard.acquire()` holds `self._lock` for the entire duration of
`asyncio.sleep()` when a soft window needs waiting. Under concurrent load,
this serializes all callers through the sleep — even though tokens would be
available after a single refill period.

With a per-second bucket (e.g. USPS, limit=5), the Nth concurrent caller
waits ~N × sleep_time. The 1s latency budget caps the damage, but throughput
drops to ~1 req/sleep-cycle instead of the bucket's sustained rate.

## Approach

Release the lock before sleeping; re-acquire after; re-check in a loop.

```
deadline = monotonic() + latency_budget_s

loop:
  async with self._lock:
    refill all windows
    check hard windows → raise if exhausted
    compute max_wait across soft windows
    if no wait needed → consume tokens, return
    if now + max_wait > deadline → raise ProviderAtCapacityError
    wait = max_wait

  await asyncio.sleep(wait)    # lock released — concurrent waiters sleep in parallel
```

### Key properties

- Lock held only during state reads/writes — never during sleep
- Multiple waiters sleep concurrently for the same refill period
- After waking, loop re-acquires lock and re-checks (another caller may have consumed)
- Deadline computed once; each iteration checks remaining budget
- Hard windows still reject immediately (unchanged)
- `FixedResetQuotaWindow` reset logic stays inside the lock (unchanged)

## What doesn't change

- `adjust_tokens()`, `get_daily_quota_state()` — untouched
- `QuotaWindow`, `FixedResetQuotaWindow` dataclasses — untouched
- `_parse_retry_after` — untouched
- Public API surface — identical

## Test plan

- Adapt existing tests (sleep mock still works — loop does one iteration)
- New: concurrent callers sleep in parallel, not serially (`asyncio.gather` + mock tracking)
- New: retry loop re-checks after wake — token stolen between sleep and re-acquire triggers retry
- New: deadline expiry across retries → raises `ProviderAtCapacityError`

## Edge cases

- Token stolen between sleep and re-acquire → loop retries
- Budget exactly equals one wait → succeeds (strict `>`, not `>=`)
- Zero wait → no sleep, immediate consume (fast path)
- All hard windows → sleep path never entered (unchanged)
