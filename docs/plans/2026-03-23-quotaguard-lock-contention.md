# QuotaGuard Lock Contention Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Release `QuotaGuard._lock` before `asyncio.sleep()` so concurrent waiters sleep in parallel instead of serializing.

**Architecture:** Replace the single `async with self._lock` block in `acquire()` with a deadline-bounded loop that holds the lock only during state reads/writes, releases before sleeping, and re-checks after waking. No new classes, no new files.

**Tech Stack:** Python asyncio, existing `QuotaGuard` internals

**Design doc:** `docs/plans/2026-03-23-quotaguard-lock-contention-design.md`
**Issue:** #56, #64

---

### Task 1: Refactor `acquire()` to release lock before sleep

**Files:**
- Modify: `src/address_validator/services/validation/_rate_limit.py:153-208`
- Test: `tests/unit/validation/test_rate_limit.py`

- [ ] **Step 1: Write the new `acquire()` implementation**

Replace the `acquire` method (lines 153–208) with:

```python
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
                self._tokens[i] = min(
                    float(window.limit), self._tokens[i] + elapsed * rate
                )
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
            if max_wait == 0:
                for i in range(len(self._windows)):
                    self._tokens[i] -= 1.0
                return

            # --- Wait would exceed deadline: reject ---
            if monotonic() + max_wait > deadline:
                raise ProviderAtCapacityError(self._provider_name)

            wait = max_wait

        # --- Lock released: sleep concurrently with other waiters ---
        await asyncio.sleep(wait)
        # Loop back to re-acquire lock and re-check token availability
```

Key differences from the old code:
- `while True` loop instead of linear flow
- Lock acquired/released each iteration (not held across sleep)
- `deadline` computed once at entry
- Fast path: `max_wait == 0` → consume and return inside the lock
- After sleep, loop back to re-check (tokens may have been consumed by another waiter)

- [ ] **Step 2: Adapt existing tests that mock sleep without advancing time**

Two tests mock `asyncio.sleep` (instant return) but don't advance `monotonic`. With the old code this was fine — sleep was inside the lock and re-refill ran immediately after. With the loop, the mocked sleep returns instantly, loop re-enters, refill finds ~0 elapsed, tokens still ~0, and it spins until the deadline expires.

Fix both tests by having the mock sleep side-effect set tokens directly (simulating refill):

In `test_soft_window_sleeps_when_tokens_exhausted`, update the sleep mock:

```python
@pytest.mark.asyncio
async def test_soft_window_sleeps_when_tokens_exhausted(self) -> None:
    guard = self._soft_guard(limit=1, duration_s=1.0, latency_budget_s=2.0)
    guard._tokens[0] = 0.0
    guard._last_refill[0] = time.monotonic()

    async def refilling_sleep(duration: float) -> None:
        # Simulate token refill that would happen during real sleep
        guard._tokens[0] = 1.0

    with patch(
        "address_validator.services.validation._rate_limit.asyncio.sleep",
        side_effect=refilling_sleep,
    ) as mock_sleep:
        await guard.acquire()

    mock_sleep.assert_called_once()
    sleep_time = mock_sleep.call_args[0][0]
    assert 0 < sleep_time <= 2.0
```

In `test_multi_window_wait_is_max_not_sum`, same pattern:

```python
@pytest.mark.asyncio
async def test_multi_window_wait_is_max_not_sum(self) -> None:
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

    async def refilling_sleep(duration: float) -> None:
        guard._tokens[0] = 1.0
        guard._tokens[1] = 1.0

    with patch(
        "address_validator.services.validation._rate_limit.asyncio.sleep",
        side_effect=refilling_sleep,
    ) as mock_sleep:
        await guard.acquire()

    mock_sleep.assert_called_once()
    sleep_time = mock_sleep.call_args[0][0]
    assert 0.45 <= sleep_time <= 0.6
```

- [ ] **Step 3: Run existing tests to verify nothing is broken**

Run: `uv run pytest tests/unit/validation/test_rate_limit.py -v --no-cov -x`

Expected: All 20 existing tests pass.

- [ ] **Step 4: Commit**

```bash
git add src/address_validator/services/validation/_rate_limit.py tests/unit/validation/test_rate_limit.py
git commit -m "#56 refactor: release QuotaGuard lock before asyncio.sleep

Replace single async-with-lock block with deadline-bounded loop.
Lock held only during state reads/writes, released before sleep.
Concurrent waiters now sleep in parallel instead of serializing.
Adapt two existing tests to set tokens in sleep side-effect."
```

---

### Task 2: Add concurrency test — parallel sleep

**Files:**
- Test: `tests/unit/validation/test_rate_limit.py`

- [ ] **Step 1: Write the failing test**

Add to `TestQuotaGuard`:

```python
@pytest.mark.asyncio
async def test_concurrent_acquires_sleep_in_parallel(self) -> None:
    """Two callers with empty bucket should both enter sleep concurrently."""
    guard = self._soft_guard(limit=2, duration_s=1.0, latency_budget_s=2.0)
    guard._tokens[0] = 0.0
    guard._last_refill[0] = time.monotonic()

    max_concurrent = 0
    active = 0

    _original_sleep = asyncio.sleep

    async def tracking_sleep(duration: float) -> None:
        nonlocal max_concurrent, active
        active += 1
        max_concurrent = max(max_concurrent, active)
        await _original_sleep(0)  # yield so second caller can enter sleep
        # Set tokens *after* yield so both callers are in sleep first
        guard._tokens[0] = float(guard._windows[0].limit)
        active -= 1

    with patch(
        "address_validator.services.validation._rate_limit.asyncio.sleep",
        side_effect=tracking_sleep,
    ):
        await asyncio.gather(guard.acquire(), guard.acquire())

    # Both callers should have slept concurrently (max_concurrent >= 2)
    assert max_concurrent >= 2, (
        f"Expected concurrent sleeps but max_concurrent={max_concurrent}"
    )
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/unit/validation/test_rate_limit.py::TestQuotaGuard::test_concurrent_acquires_sleep_in_parallel -v --no-cov`

Expected: PASS. With the old code (lock held during sleep), `max_concurrent` would be 1. With the new code, both callers sleep concurrently.

Note: If the monotonic mock is fragile, simplify — the key assertion is that `max_concurrent >= 2`. Adjust the mock strategy as needed to make tokens available after the sleep yield.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/validation/test_rate_limit.py
git commit -m "#56 test: verify concurrent acquires sleep in parallel"
```

---

### Task 3: Add test — token stolen between sleep and re-acquire

**Files:**
- Test: `tests/unit/validation/test_rate_limit.py`

- [ ] **Step 1: Write the failing test**

Add to `TestQuotaGuard`:

```python
@pytest.mark.asyncio
async def test_retry_when_token_stolen_after_sleep(self) -> None:
    """If another caller consumes the token while we sleep, loop retries."""
    guard = self._soft_guard(limit=1, duration_s=1.0, latency_budget_s=3.0)
    guard._tokens[0] = 0.0
    guard._last_refill[0] = time.monotonic()

    sleep_call_count = 0

    async def mock_sleep(duration: float) -> None:
        nonlocal sleep_call_count
        sleep_call_count += 1
        if sleep_call_count == 1:
            # Simulate token refill but then stolen by another caller
            guard._tokens[0] = 0.0
            # Advance last_refill so next iteration computes a fresh wait
            guard._last_refill[0] = time.monotonic()
        else:
            # Second sleep: let tokens refill normally
            guard._tokens[0] = 1.0

    with patch(
        "address_validator.services.validation._rate_limit.asyncio.sleep",
        side_effect=mock_sleep,
    ):
        await guard.acquire()

    # Should have slept twice (first attempt: token stolen; second: success)
    assert sleep_call_count == 2
    # Token consumed
    assert guard._tokens[0] == 0.0
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/unit/validation/test_rate_limit.py::TestQuotaGuard::test_retry_when_token_stolen_after_sleep -v --no-cov`

Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/unit/validation/test_rate_limit.py
git commit -m "#56 test: verify retry loop when token stolen after sleep"
```

---

### Task 4: Add test — deadline expiry across retries

**Files:**
- Test: `tests/unit/validation/test_rate_limit.py`

- [ ] **Step 1: Write the failing test**

Add to `TestQuotaGuard`:

```python
@pytest.mark.asyncio
async def test_deadline_expiry_across_retries(self) -> None:
    """Tokens always stolen after sleep → budget exhausted → ProviderAtCapacityError."""
    # rate=1/s, budget=1.5s — allows ~1 sleep of 1s, but not a second
    guard = self._soft_guard(limit=1, duration_s=1.0, latency_budget_s=1.5)
    guard._tokens[0] = 0.0
    guard._last_refill[0] = time.monotonic()

    # Use a controlled clock: each monotonic() call advances by a fixed step.
    # acquire() calls monotonic() at least 3 times per iteration:
    #   1. deadline = monotonic() + budget   (only first iteration)
    #   2. now = monotonic()                 (refill)
    #   3. monotonic() + max_wait > deadline (check)
    # We need the deadline check on the 2nd iteration to exceed the budget.
    base = time.monotonic()
    call_count = [0]

    def controlled_monotonic() -> float:
        call_count[0] += 1
        # Advance 0.4s per call — after ~4 calls (1.6s) exceeds 1.5s budget
        return base + call_count[0] * 0.4

    async def mock_sleep(duration: float) -> None:
        # Token never becomes available — always stolen
        guard._tokens[0] = 0.0

    with (
        patch(
            "address_validator.services.validation._rate_limit.asyncio.sleep",
            side_effect=mock_sleep,
        ),
        patch(
            "address_validator.services.validation._rate_limit.monotonic",
            side_effect=controlled_monotonic,
        ),
        pytest.raises(ProviderAtCapacityError),
    ):
        await guard.acquire()
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/unit/validation/test_rate_limit.py::TestQuotaGuard::test_deadline_expiry_across_retries -v --no-cov`

Expected: PASS — after one sleep, `monotonic() + max_wait > deadline` triggers the raise.

- [ ] **Step 3: Commit**

```bash
git add tests/unit/validation/test_rate_limit.py
git commit -m "#56 test: verify deadline expiry across retry iterations"
```

---

### Task 5: Run full test suite + lint

- [ ] **Step 1: Run ruff**

Run: `uv run ruff check src/address_validator/services/validation/_rate_limit.py tests/unit/validation/test_rate_limit.py`

Expected: No issues

- [ ] **Step 2: Run full rate-limit tests**

Run: `uv run pytest tests/unit/validation/test_rate_limit.py -v --no-cov`

Expected: All tests pass (existing + 3 new)

- [ ] **Step 3: Run full test suite with coverage**

Run: `uv run pytest`

Expected: All pass, coverage ≥ 80%

- [ ] **Step 4: Commit any lint fixes if needed**

```bash
git commit -am "#56 fix: lint cleanup"
```
