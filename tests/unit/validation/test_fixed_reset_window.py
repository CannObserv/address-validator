"""Unit tests for FixedResetQuotaWindow."""

from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from address_validator.services.validation._rate_limit import FixedResetQuotaWindow

PT = ZoneInfo("America/Los_Angeles")
_PATCH = "address_validator.services.validation._rate_limit._now_in_tz"


class TestFixedResetQuotaWindow:
    def test_is_frozen_dataclass(self) -> None:
        w = FixedResetQuotaWindow(limit=160, mode="hard")
        with pytest.raises(AttributeError):
            w.limit = 200  # type: ignore[misc]

    def test_limit_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="limit"):
            FixedResetQuotaWindow(limit=0, mode="hard")

    def test_default_timezone_is_pacific(self) -> None:
        w = FixedResetQuotaWindow(limit=160, mode="hard")
        assert w.timezone == PT

    def test_seconds_until_reset_near_midnight(self) -> None:
        # 11:59:00 PM PT → 60 seconds until midnight
        fake_now = datetime(2026, 3, 20, 23, 59, 0, tzinfo=PT)
        w = FixedResetQuotaWindow(limit=160, mode="hard")
        with patch(_PATCH, return_value=fake_now):
            assert w.seconds_until_reset() == pytest.approx(60.0, abs=1.0)

    def test_seconds_until_reset_at_start_of_day(self) -> None:
        # 12:00:01 AM PT → ~86399 seconds until next midnight
        fake_now = datetime(2026, 3, 20, 0, 0, 1, tzinfo=PT)
        w = FixedResetQuotaWindow(limit=160, mode="hard")
        with patch(_PATCH, return_value=fake_now):
            remaining = w.seconds_until_reset()
            assert 86_398 <= remaining <= 86_400

    def test_should_reset_true_after_midnight(self) -> None:
        w = FixedResetQuotaWindow(limit=160, mode="hard")
        # Last reset was yesterday
        yesterday = datetime(2026, 3, 19, 0, 0, 0, tzinfo=PT)
        now = datetime(2026, 3, 20, 0, 0, 1, tzinfo=PT)
        with patch(_PATCH, return_value=now):
            assert w.should_reset(yesterday) is True

    def test_should_reset_false_same_day(self) -> None:
        w = FixedResetQuotaWindow(limit=160, mode="hard")
        today_morning = datetime(2026, 3, 20, 8, 0, 0, tzinfo=PT)
        today_afternoon = datetime(2026, 3, 20, 14, 0, 0, tzinfo=PT)
        with patch(_PATCH, return_value=today_afternoon):
            assert w.should_reset(today_morning) is False

    def test_mode_soft_allowed(self) -> None:
        w = FixedResetQuotaWindow(limit=160, mode="soft")
        assert w.mode == "soft"

    def test_mode_hard_allowed(self) -> None:
        w = FixedResetQuotaWindow(limit=160, mode="hard")
        assert w.mode == "hard"
