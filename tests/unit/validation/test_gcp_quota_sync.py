"""Unit tests for GCP quota sync — limit discovery and usage monitoring."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from address_validator.services.validation._rate_limit import (
    FixedResetQuotaWindow,
    QuotaGuard,
    QuotaWindow,
)
from address_validator.services.validation.gcp_quota_sync import (
    fetch_daily_limit,
    fetch_daily_usage,
    reconcile_once,
    run_reconciliation_loop,
)

_VALIDATE_METRIC = "addressvalidation.googleapis.com/validate_address_requests"
_FEEDBACK_METRIC = "addressvalidation.googleapis.com/provide_validation_feedback_requests"


def _make_quota_info(metric, refresh_interval, value):
    info = MagicMock()
    info.metric = metric
    info.refresh_interval = refresh_interval
    info.dimensions_infos = [MagicMock()]
    info.dimensions_infos[0].details.value = value
    return info


class TestFetchDailyLimit:
    def test_extracts_daily_limit_from_validate_address_metric(self) -> None:
        mock_client = MagicMock()
        mock_client.list_quota_infos.return_value = [
            _make_quota_info(_FEEDBACK_METRIC, "day", 2**63 - 1),
            _make_quota_info(_VALIDATE_METRIC, "minute", 5),
            _make_quota_info(_VALIDATE_METRIC, "day", 160),
        ]
        result = fetch_daily_limit(mock_client, "my-project")
        assert result == 160

    def test_ignores_feedback_metric(self) -> None:
        mock_client = MagicMock()
        mock_client.list_quota_infos.return_value = [
            _make_quota_info(_FEEDBACK_METRIC, "day", 999),
        ]
        result = fetch_daily_limit(mock_client, "my-project")
        assert result is None

    def test_returns_none_when_no_daily_quota_found(self) -> None:
        mock_client = MagicMock()
        mock_client.list_quota_infos.return_value = [
            _make_quota_info(_VALIDATE_METRIC, "minute", 5),
        ]
        result = fetch_daily_limit(mock_client, "my-project")
        assert result is None

    def test_returns_none_on_api_error(self) -> None:
        mock_client = MagicMock()
        mock_client.list_quota_infos.side_effect = Exception("API error")
        result = fetch_daily_limit(mock_client, "my-project")
        assert result is None

    def test_returns_none_when_int64_max(self) -> None:
        mock_client = MagicMock()
        mock_client.list_quota_infos.return_value = [
            _make_quota_info(_VALIDATE_METRIC, "day", 2**63 - 1),
        ]
        result = fetch_daily_limit(mock_client, "my-project")
        assert result is None

    def test_queries_correct_service(self) -> None:
        mock_client = MagicMock()
        mock_client.list_quota_infos.return_value = []
        fetch_daily_limit(mock_client, "my-project")
        call_kwargs = mock_client.list_quota_infos.call_args.kwargs
        assert "addressvalidation.googleapis.com" in call_kwargs["parent"]


class TestFetchDailyUsage:
    def test_returns_usage_count(self) -> None:
        mock_client = MagicMock()
        point = MagicMock()
        point.value.int64_value = 47
        series = MagicMock()
        series.points = [point]
        mock_client.list_time_series.return_value = [series]
        result = fetch_daily_usage(mock_client, "my-project")
        assert result == 47

    def test_returns_none_when_no_data(self) -> None:
        mock_client = MagicMock()
        mock_client.list_time_series.return_value = []
        result = fetch_daily_usage(mock_client, "my-project")
        assert result is None

    def test_returns_none_on_api_error(self) -> None:
        mock_client = MagicMock()
        mock_client.list_time_series.side_effect = Exception("API error")
        result = fetch_daily_usage(mock_client, "my-project")
        assert result is None

    def test_skips_series_with_no_points(self) -> None:
        """Series with empty points list should be skipped, returning None."""
        mock_client = MagicMock()
        empty_series = MagicMock()
        empty_series.points = []
        mock_client.list_time_series.return_value = [empty_series]
        assert fetch_daily_usage(mock_client, "my-project") is None


class TestReconcileOnce:
    def _make_guard(self, daily_limit: int = 160, used: int = 0) -> QuotaGuard:
        guard = QuotaGuard(
            windows=[
                QuotaWindow(limit=5, duration_s=60.0, mode="soft"),
                FixedResetQuotaWindow(limit=daily_limit, mode="hard"),
            ],
            provider_name="google",
        )
        guard._tokens[1] = float(daily_limit - used)
        return guard

    def test_adjusts_down_when_monitoring_higher(self) -> None:
        guard = self._make_guard(daily_limit=160, used=40)
        reconcile_once(guard, daily_window_index=1, reported_usage=60)
        assert guard._tokens[1] == 100.0

    def test_no_adjust_up_when_monitoring_lower(self) -> None:
        guard = self._make_guard(daily_limit=160, used=40)
        reconcile_once(guard, daily_window_index=1, reported_usage=20)
        assert guard._tokens[1] == 120.0

    def test_no_change_when_equal(self) -> None:
        guard = self._make_guard(daily_limit=160, used=40)
        reconcile_once(guard, daily_window_index=1, reported_usage=40)
        assert guard._tokens[1] == 120.0

    def test_does_not_go_below_zero(self) -> None:
        guard = self._make_guard(daily_limit=160, used=150)
        reconcile_once(guard, daily_window_index=1, reported_usage=200)
        assert guard._tokens[1] == 0.0


class TestRunReconciliationLoop:
    async def test_cancellation_exits_cleanly(self) -> None:
        guard = QuotaGuard(
            windows=[
                QuotaWindow(limit=5, duration_s=60.0, mode="soft"),
                FixedResetQuotaWindow(limit=160, mode="hard"),
            ],
            provider_name="google",
        )
        task = asyncio.create_task(
            run_reconciliation_loop(
                guard=guard,
                daily_window_index=1,
                monitoring_client=MagicMock(),
                project_id="test",
                interval_s=3600,
            )
        )
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    async def test_loop_iterations_handle_all_branches(self) -> None:
        """Exercise the per-tick body: usage int, usage None, and raised exception.

        The loop body has three branches (reconcile, debug-skip, except). All
        three must be hit so the reconciliation loop is robust against
        Monitoring outages and stale data.
        """
        guard = QuotaGuard(
            windows=[
                QuotaWindow(limit=5, duration_s=60.0, mode="soft"),
                FixedResetQuotaWindow(limit=160, mode="hard"),
            ],
            provider_name="google",
        )
        guard._tokens[1] = 120.0

        fetch_results: list = [50, None, Exception("boom")]
        fetch_calls = 0

        def _fake_fetch(_client, _project):
            nonlocal fetch_calls
            result = fetch_results[fetch_calls]
            fetch_calls += 1
            if isinstance(result, Exception):
                raise result
            return result

        # Stop the loop after the third tick so the test never hangs.
        async def _fake_sleep(_seconds):
            if fetch_calls >= len(fetch_results):
                raise asyncio.CancelledError

        with (
            patch(
                "address_validator.services.validation.gcp_quota_sync.fetch_daily_usage",
                side_effect=_fake_fetch,
            ),
            patch(
                "address_validator.services.validation.gcp_quota_sync.asyncio.sleep",
                side_effect=_fake_sleep,
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await run_reconciliation_loop(
                guard=guard,
                daily_window_index=1,
                monitoring_client=MagicMock(),
                project_id="test",
                interval_s=0.0,
            )

        assert fetch_calls == 3
        # First tick reported usage=50 vs local=40 → adjust down by 10.
        assert guard._tokens[1] == 110.0
