"""Unit tests for GCP quota sync — limit discovery and usage monitoring."""

from unittest.mock import MagicMock

from address_validator.services.validation._rate_limit import (
    FixedResetQuotaWindow,
    QuotaGuard,
    QuotaWindow,
)
from address_validator.services.validation.gcp_quota_sync import (
    fetch_daily_limit,
    fetch_daily_usage,
    reconcile_once,
)


class TestFetchDailyLimit:
    def test_extracts_daily_limit_from_quota_infos(self) -> None:
        mock_client = MagicMock()
        daily_info = MagicMock()
        daily_info.refresh_interval = "day"
        daily_info.dimensions_infos = [MagicMock()]
        daily_info.dimensions_infos[0].details.value = 200
        minute_info = MagicMock()
        minute_info.refresh_interval = "minute"
        mock_client.list_quota_infos.return_value = [minute_info, daily_info]
        result = fetch_daily_limit(mock_client, "my-project")
        assert result == 200

    def test_returns_none_when_no_daily_quota_found(self) -> None:
        mock_client = MagicMock()
        minute_info = MagicMock()
        minute_info.refresh_interval = "minute"
        mock_client.list_quota_infos.return_value = [minute_info]
        result = fetch_daily_limit(mock_client, "my-project")
        assert result is None

    def test_returns_none_on_api_error(self) -> None:
        mock_client = MagicMock()
        mock_client.list_quota_infos.side_effect = Exception("API error")
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
