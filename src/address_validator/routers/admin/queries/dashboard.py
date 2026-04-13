"""Dashboard aggregate stats and sparkline queries."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import func

from address_validator.db.tables import (
    audit_daily_stats,
    audit_log,
)

from ._shared import (
    _API_ENDPOINT_FILTER,
    _from_archived,
    _from_live,
    _time_boundaries,
    is_error_expr,
    is_rate_limited_expr,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


async def get_dashboard_stats(engine: AsyncEngine) -> dict:
    """Fetch aggregate stats for the dashboard landing page."""
    tb = _time_boundaries()
    last_7d = tb["last_7d"]
    last_24h = tb["last_24h"]

    async with engine.connect() as conn:
        # Live counts
        row = (
            await conn.execute(
                _from_live(
                    [
                        func.count().label("total"),
                        func.count().filter(audit_log.c.timestamp >= last_24h).label("last_24h"),
                        func.count().filter(audit_log.c.timestamp >= last_7d).label("last_7d"),
                        func.count()
                        .filter(
                            is_error_expr(audit_log.c.status_code),
                            audit_log.c.timestamp >= last_24h,
                            _API_ENDPOINT_FILTER,
                        )
                        .label("errors_24h"),
                        func.count()
                        .filter(
                            is_rate_limited_expr(audit_log.c.status_code),
                            audit_log.c.timestamp >= last_24h,
                            _API_ENDPOINT_FILTER,
                        )
                        .label("rate_limited_24h"),
                        func.count()
                        .filter(
                            audit_log.c.timestamp >= last_24h,
                            _API_ENDPOINT_FILTER,
                        )
                        .label("api_24h"),
                    ],
                )
            )
        ).one()

        # Archived totals — only dates before earliest live row
        archived_total = (
            await conn.execute(
                _from_archived(
                    [func.coalesce(func.sum(audit_daily_stats.c.request_count), 0)],
                )
            )
        ).scalar()

        # Cache hit rate — live only, validate endpoint, last 7 days
        cache_row = (
            await conn.execute(
                _from_live(
                    [
                        func.count().filter(audit_log.c.cache_hit.is_(True)).label("hits"),
                        func.count().filter(audit_log.c.cache_hit.isnot(None)).label("total"),
                    ],
                    audit_log.c.endpoint == "/api/v1/validate",
                    audit_log.c.timestamp >= last_7d,
                )
            )
        ).one()

        # Live endpoint breakdown
        ep_rows = (
            await conn.execute(
                _from_live(
                    [
                        audit_log.c.endpoint,
                        func.count().label("total"),
                        func.count().filter(audit_log.c.timestamp >= last_24h).label("last_24h"),
                        func.count().filter(audit_log.c.timestamp >= last_7d).label("last_7d"),
                    ],
                ).group_by(audit_log.c.endpoint)
            )
        ).fetchall()

        # Archived endpoint breakdown
        archived_ep_rows = (
            await conn.execute(
                _from_archived(
                    [
                        audit_daily_stats.c.endpoint,
                        func.sum(audit_daily_stats.c.request_count).label("total"),
                    ],
                ).group_by(audit_daily_stats.c.endpoint)
            )
        ).fetchall()

    error_rate = (row.errors_24h / row.api_24h * 100) if row.api_24h > 0 else None
    cache_hit_rate = (cache_row.hits / cache_row.total * 100) if cache_row.total > 0 else None

    known = {
        "/api/v1/parse": "/parse",
        "/api/v1/standardize": "/standardize",
        "/api/v1/validate": "/validate",
    }
    breakdown: dict[str, dict[str, int]] = {
        "all": {},
        "7d": {},
        "24h": {},
    }

    # Live breakdown
    for ep_row in ep_rows:
        label = known.get(ep_row.endpoint, "other")
        periods = (("all", "total"), ("7d", "last_7d"), ("24h", "last_24h"))
        for period, col in periods:
            breakdown[period][label] = breakdown[period].get(label, 0) + ep_row._mapping[col]  # noqa: SLF001

    # Add archived totals to "all" period
    for ar_row in archived_ep_rows:
        label = known.get(ar_row.endpoint, "other")
        breakdown["all"][label] = breakdown["all"].get(label, 0) + ar_row.total

    return {
        "requests_24h": row.last_24h,
        "requests_7d": row.last_7d,
        "requests_all": row.total + archived_total,
        "error_rate": error_rate,
        "rate_limited_24h": row.rate_limited_24h,
        "cache_hit_rate": cache_hit_rate,
        "endpoint_breakdown": breakdown,
    }


async def get_sparkline_data(engine: AsyncEngine) -> dict[str, list[float]]:
    """Fetch time-bucketed values for dashboard sparklines.

    Returns a dict keyed by card name, each value a list of floats
    (zero-filled for missing buckets).
    """
    tb = _time_boundaries()
    now, today_start = tb["now"], tb["today"]
    start_30d = today_start - timedelta(days=29)
    start_7d = today_start - timedelta(days=6)
    # Truncate to start of hour. The current-hour bucket is partial (count so far this hour).
    start_24h = (now - timedelta(hours=23)).replace(minute=0, second=0, microsecond=0)

    day_bucket = func.date_trunc("day", audit_log.c.timestamp).label("bucket")
    hour_bucket = func.date_trunc("hour", audit_log.c.timestamp).label("bucket")

    async with engine.connect() as conn:
        # Daily request counts — last 30 days.
        daily_rows = (
            await conn.execute(
                _from_live(
                    [day_bucket, func.count().label("cnt")],
                    audit_log.c.timestamp >= start_30d,
                )
                .group_by(sa.literal_column("bucket"))
                .order_by(sa.literal_column("bucket"))
            )
        ).fetchall()

        # Hourly request counts — last 24 hours.
        hourly_rows = (
            await conn.execute(
                _from_live(
                    [hour_bucket, func.count().label("cnt")],
                    audit_log.c.timestamp >= start_24h,
                )
                .group_by(sa.literal_column("bucket"))
                .order_by(sa.literal_column("bucket"))
            )
        ).fetchall()

        # Daily cache hit rate — last 7 days (validate endpoint only).
        cache_rows = (
            await conn.execute(
                _from_live(
                    [
                        day_bucket,
                        func.count().filter(audit_log.c.cache_hit.is_(True)).label("hits"),
                        func.count().filter(audit_log.c.cache_hit.isnot(None)).label("total"),
                    ],
                    audit_log.c.endpoint == "/api/v1/validate",
                    audit_log.c.timestamp >= start_7d,
                )
                .group_by(sa.literal_column("bucket"))
                .order_by(sa.literal_column("bucket"))
            )
        ).fetchall()

        # Daily error rate — last 7 days (API endpoints only).
        error_rows = (
            await conn.execute(
                _from_live(
                    [
                        day_bucket,
                        func.count().filter(is_error_expr(audit_log.c.status_code)).label("errors"),
                        func.count().label("total"),
                    ],
                    _API_ENDPOINT_FILTER,
                    audit_log.c.timestamp >= start_7d,
                )
                .group_by(sa.literal_column("bucket"))
                .order_by(sa.literal_column("bucket"))
            )
        ).fetchall()

    # --- Zero-fill helper ---
    def _fill_daily(rows: list, start: object, days: int) -> list[float]:
        by_day = {r.bucket.date(): float(r.cnt) for r in rows}
        return [by_day.get((start + timedelta(days=i)).date(), 0.0) for i in range(days)]

    def _fill_hourly(rows: list, start: object, hours: int) -> list[float]:
        by_hour = {r.bucket: float(r.cnt) for r in rows}
        return [by_hour.get(start + timedelta(hours=i), 0.0) for i in range(hours)]

    def _fill_rate_daily(
        rows: list,
        start: object,
        days: int,
        num_col: str,
        den_col: str,
    ) -> list[float]:
        by_day: dict = {}
        for r in rows:
            mapping = r._mapping  # noqa: SLF001
            den = mapping[den_col]
            by_day[r.bucket.date()] = (mapping[num_col] / den * 100) if den > 0 else 0.0
        return [by_day.get((start + timedelta(days=i)).date(), 0.0) for i in range(days)]

    return {
        "requests_all": _fill_daily(daily_rows, start_30d, 30),
        "requests_7d": _fill_daily(daily_rows, start_7d, 7),
        "requests_24h": _fill_hourly(hourly_rows, start_24h, 24),
        "cache_hit_rate": _fill_rate_daily(cache_rows, start_7d, 7, "hits", "total"),
        "error_rate": _fill_rate_daily(error_rows, start_7d, 7, "errors", "total"),
    }
