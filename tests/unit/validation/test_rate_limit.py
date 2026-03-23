"""Unit tests for services/validation/_rate_limit.py."""

import time
from datetime import datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest

from address_validator.services.validation._rate_limit import (
    _RETRY_BASE_DELAY_S,
    FixedResetQuotaWindow,
    QuotaGuard,
    QuotaWindow,
    _parse_retry_after,
)
from address_validator.services.validation.errors import ProviderAtCapacityError


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
        with patch("address_validator.services.validation._rate_limit.asyncio.sleep") as mock_sleep:
            await guard.acquire()
        mock_sleep.assert_not_called()

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

        with (
            patch("address_validator.services.validation._rate_limit.asyncio.sleep") as mock_sleep,
            pytest.raises(ProviderAtCapacityError),
        ):
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
        guard = QuotaGuard(
            windows=[
                QuotaWindow(limit=1, duration_s=1.0, mode="soft"),
                QuotaWindow(limit=1, duration_s=1.0, mode="soft"),
            ],
            latency_budget_s=2.0,
            provider_name="test",
        )
        # Window 0: rate=1/s, tokens=0.5 → needs (1 - 0.5) / 1 = 0.5s
        # Window 1: rate=1/s, tokens=0.8 → needs (1 - 0.8) / 1 = 0.2s
        # Max = 0.5s; budget = 2.0s → should sleep ~0.5s, not ~0.7s (sum)
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

    @pytest.mark.asyncio
    async def test_tokens_replenish_over_time(self) -> None:
        # rate=10/s, tokens drained; simulate 0.5s elapsed → 5 tokens refilled
        guard = self._soft_guard(limit=10, duration_s=1.0, latency_budget_s=1.0)
        guard._tokens[0] = 0.0
        guard._last_refill[0] = time.monotonic() - 0.5  # 0.5s ago → +5 tokens

        with patch("address_validator.services.validation._rate_limit.asyncio.sleep") as mock_sleep:
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

    def test_accepts_fixed_reset_window(self) -> None:
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
        PT = ZoneInfo("America/Los_Angeles")
        guard = QuotaGuard(
            windows=[FixedResetQuotaWindow(limit=160, mode="hard")],
            provider_name="google",
        )
        # Drain tokens and simulate last reset was yesterday
        guard._tokens[0] = 0.0
        yesterday = datetime(2026, 3, 19, 23, 0, 0, tzinfo=PT)
        guard._last_reset = [yesterday]

        today = datetime(2026, 3, 20, 0, 1, 0, tzinfo=PT)
        patch_target = "address_validator.services.validation._rate_limit._now_in_tz"
        with patch(patch_target, return_value=today):
            await guard.acquire()
        # Tokens should have been reset to full, then 1 consumed
        assert guard._tokens[0] == 159.0


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
