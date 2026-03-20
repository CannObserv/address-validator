"""Shared SQL query helpers for admin dashboard views."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


async def get_dashboard_stats(engine: AsyncEngine) -> dict:
    """Fetch aggregate stats for the dashboard landing page."""
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())

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
                        COUNT(*) FILTER (WHERE timestamp >= :week) AS week,
                        COUNT(*) FILTER (
                            WHERE status_code >= 400 AND timestamp >= :today
                            AND {_API_ENDPOINT_FILTER}
                        ) AS errors_today,
                        COUNT(*) FILTER (
                            WHERE timestamp >= :today
                            AND {_API_ENDPOINT_FILTER}
                        ) AS api_today
                    FROM audit_log
                """),  # noqa: S608
                {"today": today_start, "week": week_start},
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
                """),
            )
        ).one()

        ep_rows = (
            await conn.execute(
                text("""
                    SELECT
                        endpoint,
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE timestamp >= :today) AS today,
                        COUNT(*) FILTER (WHERE timestamp >= :week) AS week
                    FROM audit_log
                    GROUP BY endpoint
                """),
                {"today": today_start, "week": week_start},
            )
        ).fetchall()

    error_rate = (row.errors_today / row.api_today * 100) if row.api_today > 0 else None
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
    }
    for ep_row in ep_rows:
        label = known.get(ep_row.endpoint, "other")
        for period, col in (("all", "total"), ("week", "week"), ("today", "today")):
            breakdown[period][label] = breakdown[period].get(label, 0) + ep_row._mapping[col]  # noqa: SLF001

    return {
        "requests_today": row.today,
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
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=today_start.weekday())

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("""
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE timestamp >= :today) AS today,
                        COUNT(*) FILTER (WHERE timestamp >= :week) AS week,
                        COUNT(*) FILTER (WHERE status_code >= 400) AS errors,
                        AVG(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS avg_latency
                    FROM audit_log
                    WHERE endpoint = :endpoint
                """),
                {"today": today_start, "week": week_start, "endpoint": endpoint_path},
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
        "today": row.today,
        "week": row.week,
        "error_rate": error_rate,
        "avg_latency_ms": round(row.avg_latency) if row.avg_latency else None,
        "status_codes": {r.status_code: r.count for r in status_rows},
    }


async def get_provider_stats(engine: AsyncEngine, provider_name: str) -> dict:
    """Fetch stats for a specific validation provider."""
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("""
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE timestamp >= :today) AS today,
                        COUNT(*) FILTER (WHERE cache_hit = true) AS cache_hits,
                        COUNT(*) FILTER (WHERE cache_hit IS NOT NULL) AS cache_total
                    FROM audit_log
                    WHERE provider = :provider
                """),
                {"today": today_start, "provider": provider_name},
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
        "today": row.today,
        "cache_hit_rate": cache_hit_rate,
        "validation_statuses": {r.validation_status: r.count for r in status_rows},
    }
