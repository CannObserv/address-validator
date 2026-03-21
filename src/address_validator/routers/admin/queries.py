"""Shared SQL query helpers for admin dashboard views."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


def _time_boundaries() -> dict[str, datetime]:
    """Compute reusable time boundaries for dashboard queries."""
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "now": now,
        "today": today_start,
        "last_24h": now - timedelta(hours=24),
        "week": today_start - timedelta(days=today_start.weekday()),
    }


async def get_dashboard_stats(engine: AsyncEngine) -> dict:
    """Fetch aggregate stats for the dashboard landing page."""
    tb = _time_boundaries()
    today_start = tb["today"]
    week_start = tb["week"]
    last_24h = tb["last_24h"]

    # SAFETY: endpoint literals are hardcoded, not user-supplied.
    _API_ENDPOINT_FILTER = (
        "endpoint IN ('/api/v1/parse', '/api/v1/standardize', '/api/v1/validate')"
    )

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(f"""
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE timestamp >= :today) AS today,
                        COUNT(*) FILTER (WHERE timestamp >= :last_24h) AS last_24h,
                        COUNT(*) FILTER (WHERE timestamp >= :week) AS week,
                        COUNT(*) FILTER (
                            WHERE status_code >= 400 AND timestamp >= :last_24h
                            AND {_API_ENDPOINT_FILTER}
                        ) AS errors_24h,
                        COUNT(*) FILTER (
                            WHERE timestamp >= :last_24h
                            AND {_API_ENDPOINT_FILTER}
                        ) AS api_24h
                    FROM audit_log
                """),  # noqa: S608
                {"today": today_start, "last_24h": last_24h, "week": week_start},
            )
        ).one()

        cache_row = (
            await conn.execute(
                text("""
                    SELECT
                        COUNT(*) FILTER (WHERE cache_hit = true) AS hits,
                        COUNT(*) FILTER (WHERE cache_hit IS NOT NULL) AS total
                    FROM audit_log
                    WHERE endpoint = '/api/v1/validate'
                        AND timestamp >= :week
                """),
                {"week": week_start},
            )
        ).one()

        ep_rows = (
            await conn.execute(
                text("""
                    SELECT
                        endpoint,
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE timestamp >= :today) AS today,
                        COUNT(*) FILTER (WHERE timestamp >= :last_24h) AS last_24h,
                        COUNT(*) FILTER (WHERE timestamp >= :week) AS week
                    FROM audit_log
                    GROUP BY endpoint
                """),
                {"today": today_start, "last_24h": last_24h, "week": week_start},
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
        "week": {},
        "today": {},
        "24h": {},
    }
    for ep_row in ep_rows:
        label = known.get(ep_row.endpoint, "other")
        periods = (("all", "total"), ("week", "week"), ("today", "today"), ("24h", "last_24h"))
        for period, col in periods:
            breakdown[period][label] = breakdown[period].get(label, 0) + ep_row._mapping[col]  # noqa: SLF001

    return {
        "requests_today": row.today,
        "requests_24h": row.last_24h,
        "requests_week": row.week,
        "requests_all": row.total,
        "error_rate": error_rate,
        "cache_hit_rate": cache_hit_rate,
        "endpoint_breakdown": breakdown,
    }


async def get_audit_rows(
    engine: AsyncEngine,
    *,
    page: int = 1,
    per_page: int = 50,
    endpoint: str | None = None,
    provider: str | None = None,
    client_ip: str | None = None,
    status_min: int | None = None,
) -> tuple[list[dict], int]:
    """Fetch paginated, filtered audit_log rows. Returns (rows, total_count)."""
    conditions = []
    params: dict = {}

    if endpoint:
        conditions.append("endpoint = :endpoint")
        params["endpoint"] = f"/api/v1/{endpoint}"
    if provider:
        conditions.append("provider = :provider")
        params["provider"] = provider
    if client_ip:
        conditions.append("client_ip = :client_ip")
        params["client_ip"] = client_ip
    if status_min:
        conditions.append("status_code >= :status_min")
        params["status_min"] = status_min

    # SAFETY: conditions list contains only hardcoded column/operator literals;
    # all user-supplied values go through :parameterized placeholders in params dict.
    where = " AND ".join(conditions) if conditions else "1=1"

    async with engine.connect() as conn:
        count_row = (
            await conn.execute(
                text(f"SELECT COUNT(*) FROM audit_log WHERE {where}"),  # noqa: S608
                params,
            )
        ).one()
        total = count_row[0]

        params["limit"] = per_page
        params["offset"] = (page - 1) * per_page
        result = await conn.execute(
            text(f"""
                SELECT id, timestamp, request_id, client_ip, method, endpoint,
                       status_code, latency_ms, provider, validation_status,
                       cache_hit, error_detail
                FROM audit_log
                WHERE {where}
                ORDER BY timestamp DESC
                LIMIT :limit OFFSET :offset
            """),  # noqa: S608
            params,
        )
        rows = [dict(r._mapping) for r in result]  # noqa: SLF001

    return rows, total


async def get_endpoint_stats(engine: AsyncEngine, endpoint_name: str) -> dict:
    """Fetch stats for a specific endpoint."""
    endpoint_path = f"/api/v1/{endpoint_name}"
    tb = _time_boundaries()

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("""
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE timestamp >= :last_24h) AS last_24h,
                        COUNT(*) FILTER (WHERE timestamp >= :week) AS week,
                        COUNT(*) FILTER (WHERE status_code >= 400) AS errors,
                        AVG(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS avg_latency
                    FROM audit_log
                    WHERE endpoint = :endpoint
                """),
                {"last_24h": tb["last_24h"], "week": tb["week"], "endpoint": endpoint_path},
            )
        ).one()

        status_rows = (
            await conn.execute(
                text("""
                    SELECT status_code, COUNT(*) AS count
                    FROM audit_log
                    WHERE endpoint = :endpoint
                    GROUP BY status_code
                    ORDER BY status_code
                """),
                {"endpoint": endpoint_path},
            )
        ).fetchall()

    error_rate = (row.errors / row.total * 100) if row.total > 0 else None
    return {
        "total": row.total,
        "last_24h": row.last_24h,
        "week": row.week,
        "error_rate": error_rate,
        "avg_latency_ms": round(row.avg_latency) if row.avg_latency else None,
        "status_codes": {r.status_code: r.count for r in status_rows},
    }


async def get_provider_stats(engine: AsyncEngine, provider_name: str) -> dict:
    """Fetch stats for a specific validation provider."""
    tb = _time_boundaries()

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("""
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE timestamp >= :last_24h) AS last_24h,
                        COUNT(*) FILTER (WHERE cache_hit = true
                            AND timestamp >= :week) AS cache_hits,
                        COUNT(*) FILTER (WHERE cache_hit IS NOT NULL
                            AND timestamp >= :week) AS cache_total
                    FROM audit_log
                    WHERE provider = :provider
                """),
                {"last_24h": tb["last_24h"], "week": tb["week"], "provider": provider_name},
            )
        ).one()

        status_rows = (
            await conn.execute(
                text("""
                    SELECT validation_status, COUNT(*) AS count
                    FROM audit_log
                    WHERE provider = :provider AND validation_status IS NOT NULL
                    GROUP BY validation_status
                    ORDER BY count DESC
                """),
                {"provider": provider_name},
            )
        ).fetchall()

    cache_hit_rate = (row.cache_hits / row.cache_total * 100) if row.cache_total > 0 else None
    return {
        "total": row.total,
        "last_24h": row.last_24h,
        "cache_hit_rate": cache_hit_rate,
        "validation_statuses": {r.validation_status: r.count for r in status_rows},
    }


async def get_sparkline_data(engine: AsyncEngine) -> dict[str, list[float]]:
    """Fetch time-bucketed values for dashboard sparklines.

    Returns a dict keyed by card name, each value a list of floats
    (zero-filled for missing buckets).
    """
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_30d = today_start - timedelta(days=29)
    start_7d = today_start - timedelta(days=6)
    start_24h = now - timedelta(hours=23)
    # Truncate to start of hour. The current-hour bucket is partial (count so far this hour).
    start_24h = start_24h.replace(minute=0, second=0, microsecond=0)

    async with engine.connect() as conn:
        # Daily request counts — last 30 days.
        daily_rows = (
            await conn.execute(
                text("""
                    SELECT date_trunc('day', timestamp) AS bucket, COUNT(*) AS cnt
                    FROM audit_log
                    WHERE timestamp >= :start
                    GROUP BY bucket ORDER BY bucket
                """),
                {"start": start_30d},
            )
        ).fetchall()

        # Hourly request counts — last 24 hours.
        hourly_rows = (
            await conn.execute(
                text("""
                    SELECT date_trunc('hour', timestamp) AS bucket, COUNT(*) AS cnt
                    FROM audit_log
                    WHERE timestamp >= :start
                    GROUP BY bucket ORDER BY bucket
                """),
                {"start": start_24h},
            )
        ).fetchall()

        # Daily cache hit rate — last 7 days (validate endpoint only).
        cache_rows = (
            await conn.execute(
                text("""
                    SELECT
                        date_trunc('day', timestamp) AS bucket,
                        COUNT(*) FILTER (WHERE cache_hit = true) AS hits,
                        COUNT(*) FILTER (WHERE cache_hit IS NOT NULL) AS total
                    FROM audit_log
                    WHERE endpoint = '/api/v1/validate' AND timestamp >= :start
                    GROUP BY bucket ORDER BY bucket
                """),
                {"start": start_7d},
            )
        ).fetchall()

        # Daily error rate — last 7 days (API endpoints only).
        error_rows = (
            await conn.execute(
                text("""
                    SELECT
                        date_trunc('day', timestamp) AS bucket,
                        COUNT(*) FILTER (WHERE status_code >= 400) AS errors,
                        COUNT(*) AS total
                    FROM audit_log
                    WHERE endpoint IN (
                        '/api/v1/parse', '/api/v1/standardize', '/api/v1/validate'
                    ) AND timestamp >= :start
                    GROUP BY bucket ORDER BY bucket
                """),
                {"start": start_7d},
            )
        ).fetchall()

    # --- Zero-fill helper ---
    def _fill_daily(rows: list, start: datetime, days: int) -> list[float]:
        by_day = {r.bucket.date(): float(r.cnt) for r in rows}
        return [by_day.get((start + timedelta(days=i)).date(), 0.0) for i in range(days)]

    def _fill_hourly(rows: list, start: datetime, hours: int) -> list[float]:
        by_hour = {r.bucket: float(r.cnt) for r in rows}
        return [by_hour.get(start + timedelta(hours=i), 0.0) for i in range(hours)]

    def _fill_rate_daily(
        rows: list,
        start: datetime,
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
        "requests_week": _fill_daily(daily_rows, start_7d, 7),
        "requests_24h": _fill_hourly(hourly_rows, start_24h, 24),
        "cache_hit_rate": _fill_rate_daily(cache_rows, start_7d, 7, "hits", "total"),
        "error_rate": _fill_rate_daily(error_rows, start_7d, 7, "errors", "total"),
    }
