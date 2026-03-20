"""GCP quota discovery and usage monitoring.

Provides:
- :func:`fetch_daily_limit` — reads the daily quota ceiling from Cloud Quotas API
- :func:`fetch_daily_usage` — reads today's consumption from Cloud Monitoring API
- :func:`reconcile_once` — adjusts a QuotaGuard's daily window based on Monitoring data
- :func:`run_reconciliation_loop` — periodic background reconciliation
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, time
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from google.cloud import cloudquotas_v1, monitoring_v3

    from address_validator.services.validation._rate_limit import QuotaGuard

logger = logging.getLogger(__name__)

_ADDRESS_VALIDATION_SERVICE = "addressvalidation.googleapis.com"
_VALIDATE_ADDRESS_METRIC = "addressvalidation.googleapis.com/validate_address_requests"
_PACIFIC = ZoneInfo("America/Los_Angeles")

# RPM=5 * 10 min lag → up to ~50 requests of explainable staleness
_STALENESS_THRESHOLD = 50

# Cloud Quotas API returns INT64_MAX when no explicit quota is set.
# Treat as "no limit discovered" so we fall back to the env var override.
_INT64_MAX = 2**63 - 1


def fetch_daily_limit(
    client: cloudquotas_v1.CloudQuotasClient,
    project_id: str,
) -> int | None:
    """Query Cloud Quotas API for the daily ValidateAddress limit.

    Filters for the ``validate_address_requests`` metric with
    ``refresh_interval == "day"`` (quota ID
    ``ValidateAddressRequestsPerDayPerProject``).  Other metrics on the
    same service (e.g. ``ProvideValidationFeedback``) are ignored.

    Returns the enforced daily quota value, or ``None`` if not found or on error.
    """
    parent = (
        f"projects/{project_id}/locations/global/services/{_ADDRESS_VALIDATION_SERVICE}"
    )
    try:
        for info in client.list_quota_infos(parent=parent):
            if (
                info.metric == _VALIDATE_ADDRESS_METRIC
                and info.refresh_interval == "day"
                and info.dimensions_infos
            ):
                value = int(info.dimensions_infos[0].details.value)
                if value >= _INT64_MAX:
                    logger.info(
                        "gcp_quota_sync: daily limit is INT64_MAX "
                        "(no explicit quota set), ignoring"
                    )
                    return None
                logger.info("gcp_quota_sync: discovered daily limit=%d from Cloud Quotas", value)
                return value
    except Exception:
        logger.warning(
            "gcp_quota_sync: failed to fetch daily limit from Cloud Quotas", exc_info=True
        )
    return None


def fetch_daily_usage(
    client: monitoring_v3.MetricServiceClient,
    project_id: str,
) -> int | None:
    """Query Cloud Monitoring for today's Address Validation API usage.

    Uses midnight Pacific Time as the start of the current day to match
    Google's quota reset boundary.

    Returns the usage count, or ``None`` if unavailable.
    """
    from google.cloud import monitoring_v3 as monitoring  # noqa: PLC0415

    now = datetime.now(_PACIFIC)
    midnight_pt = datetime.combine(now.date(), time.min, tzinfo=_PACIFIC)
    start_utc = midnight_pt.astimezone(UTC)
    end_utc = now.astimezone(UTC)

    interval = monitoring.TimeInterval(
        start_time=start_utc,
        end_time=end_utc,
    )

    try:
        results = client.list_time_series(
            request={
                "name": f"projects/{project_id}",
                "filter": (
                    'metric.type = "serviceruntime.googleapis.com/quota/allocation/usage"'
                    ' AND resource.type = "consumer_quota"'
                    f' AND resource.label.service = "{_ADDRESS_VALIDATION_SERVICE}"'
                ),
                "interval": interval,
                "view": monitoring.ListTimeSeriesRequest.TimeSeriesView.FULL,
            }
        )
        for series in results:
            if series.points:
                usage = series.points[0].value.int64_value
                logger.info("gcp_quota_sync: daily usage=%d from Cloud Monitoring", usage)
                return int(usage)
    except Exception:
        logger.warning(
            "gcp_quota_sync: failed to fetch daily usage from Cloud Monitoring", exc_info=True
        )
    return None


def reconcile_once(
    guard: QuotaGuard,
    daily_window_index: int,
    reported_usage: int,
) -> None:
    """Adjust the guard's daily window tokens based on Monitoring data.

    Only adjusts **downward** (when Monitoring reports higher usage than local).
    Logs a warning when Monitoring reports lower usage (possible lag).

    Drift within what ~10 min of traffic could produce is logged at DEBUG
    (normal Monitoring staleness).  Larger drift is logged at WARNING.
    """
    window = guard._windows[daily_window_index]  # noqa: SLF001
    current_tokens = guard._tokens[daily_window_index]  # noqa: SLF001
    local_usage = window.limit - current_tokens
    delta = reported_usage - local_usage

    if delta > 0:
        level = logging.DEBUG if delta <= _STALENESS_THRESHOLD else logging.WARNING
        logger.log(
            level,
            "gcp_quota_sync: quota drift detected — monitoring=%d local=%d, adjusting down by %d",
            reported_usage,
            int(local_usage),
            int(delta),
        )
        guard.adjust_tokens(daily_window_index, -delta)
    elif delta < 0:
        level = logging.DEBUG if abs(delta) <= _STALENESS_THRESHOLD else logging.WARNING
        logger.log(
            level,
            "gcp_quota_sync: quota drift — monitoring=%d local=%d, not adjusting up (possible lag)",
            reported_usage,
            int(local_usage),
        )


async def run_reconciliation_loop(
    guard: QuotaGuard,
    daily_window_index: int,
    monitoring_client: monitoring_v3.MetricServiceClient,
    project_id: str,
    interval_s: float = 900.0,
) -> None:
    """Background task: periodically reconcile local quota tracking.

    Runs until cancelled.  Errors on individual ticks are logged and skipped.
    """
    logger.info("gcp_quota_sync: reconciliation loop started (interval=%.0fs)", interval_s)
    while True:
        await asyncio.sleep(interval_s)
        try:
            usage = fetch_daily_usage(monitoring_client, project_id)
            if usage is not None:
                reconcile_once(guard, daily_window_index, usage)
            else:
                logger.debug("gcp_quota_sync: no usage data available, skipping reconciliation")
        except Exception:
            logger.exception("gcp_quota_sync: reconciliation tick failed")
