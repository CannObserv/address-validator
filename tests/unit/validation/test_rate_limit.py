"""Unit tests for services/validation/_rate_limit.py."""

import time
from unittest.mock import MagicMock, patch

import httpx
import pytest

from services.validation._rate_limit import (
    _RETRY_BASE_DELAY_S,
    _parse_retry_after,
    _TokenBucket,
)


class TestTokenBucket:
    @pytest.mark.asyncio
    async def test_first_acquire_does_not_sleep(self) -> None:
        bucket = _TokenBucket(rate=10.0, capacity=10.0)
        with patch("services.validation._rate_limit.asyncio.sleep") as mock_sleep:
            await bucket.acquire()
        mock_sleep.assert_not_called()

    @pytest.mark.asyncio
    async def test_acquire_past_capacity_sleeps(self) -> None:
        bucket = _TokenBucket(rate=1.0, capacity=1.0)
        # Drain the bucket
        await bucket.acquire()
        # Next acquire should sleep
        with patch("services.validation._rate_limit.asyncio.sleep") as mock_sleep:
            await bucket.acquire()
        mock_sleep.assert_called_once()
        sleep_time = mock_sleep.call_args[0][0]
        assert sleep_time > 0

    @pytest.mark.asyncio
    async def test_tokens_replenish_over_time(self) -> None:
        bucket = _TokenBucket(rate=10.0, capacity=10.0)
        # Drain bucket manually
        bucket._tokens = 0.0
        # Simulate elapsed time by moving _last_refill back
        bucket._last_refill = time.monotonic() - 0.5  # 0.5s → 5 tokens refilled
        with patch("services.validation._rate_limit.asyncio.sleep") as mock_sleep:
            await bucket.acquire()
        mock_sleep.assert_not_called()

    def test_custom_rate_respected(self) -> None:
        bucket = _TokenBucket(rate=25.0, capacity=25.0)
        assert bucket.rate == 25.0
        assert bucket.capacity == 25.0
        assert bucket._tokens == 25.0


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
        # base * 2^0 = base, plus jitter
        assert delay >= _RETRY_BASE_DELAY_S
        assert delay < _RETRY_BASE_DELAY_S + 1.0  # jitter cap

    def test_exponential_backoff_grows_with_attempt(self) -> None:
        resp = self._make_response({})
        delay2 = _parse_retry_after(resp, attempt=2)
        # attempt=2 → base * 4; verify it's at least that
        assert delay2 >= _RETRY_BASE_DELAY_S * 4

    def test_non_integer_retry_after_falls_back(self) -> None:
        resp = self._make_response({"Retry-After": "Wed, 21 Oct 2025 07:28:00 GMT"})
        delay = _parse_retry_after(resp, attempt=0)
        # Non-integer date format should fall back to backoff
        assert delay >= _RETRY_BASE_DELAY_S
