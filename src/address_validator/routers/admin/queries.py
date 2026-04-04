"""Shared SQL query helpers for admin dashboard views.

All queries use SQLAlchemy Core expressions — no raw SQL f-strings.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import func, select, union_all

from address_validator.db.tables import (
    ERROR_STATUS_MIN,
    audit_daily_stats,
    audit_log,
    query_patterns,
)

if TYPE_CHECKING:
    from sqlalchemy import ColumnElement, Select
    from sqlalchemy.ext.asyncio import AsyncEngine

# ---------------------------------------------------------------------------
# Shared expressions
# ---------------------------------------------------------------------------

_API_ENDPOINTS = ("/api/v1/parse", "/api/v1/standardize", "/api/v1/validate")
_API_ENDPOINT_FILTER = audit_log.c.endpoint.in_(_API_ENDPOINTS)

# ---------------------------------------------------------------------------
# Validation status helpers
# ---------------------------------------------------------------------------

_VS_CANONICAL_ORDER = (
    "confirmed",
    "confirmed_missing_secondary",
    "confirmed_bad_secondary",
    "not_confirmed",
)


def _sort_validation_statuses(vs_dict: dict) -> dict:
    """Return vs_dict with keys in canonical display order.

    Unknown statuses sort after the known four, alphabetically among themselves.
    """
    priority = {vs: i for i, vs in enumerate(_VS_CANONICAL_ORDER)}
    return dict(
        sorted(
            vs_dict.items(),
            key=lambda kv: (priority.get(kv[0], len(_VS_CANONICAL_ORDER)), kv[0]),
        )
    )


# Date guard: restrict audit_daily_stats to dates before the earliest live row,
# avoiding double-counting when --backfill has populated rollups for recent dates.
_ARCHIVED_DATE_GUARD = (
    audit_daily_stats.c.date
    < select(
        func.coalesce(
            func.min(sa.cast(audit_log.c.timestamp, sa.Date)),
            func.current_date(),
        )
    ).scalar_subquery()
)


def _time_boundaries() -> dict[str, datetime]:
    """Compute reusable time boundaries for dashboard queries."""
    now = datetime.now(UTC)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "now": now,
        "today": today_start,
        "last_24h": now - timedelta(hours=24),
        "last_7d": now - timedelta(days=7),
    }


# ---------------------------------------------------------------------------
# Composable helpers
# ---------------------------------------------------------------------------


def _from_live(columns: list, *where: ColumnElement) -> Select:
    """Build a SELECT from audit_log with optional WHERE clauses."""
    stmt = select(*columns).select_from(audit_log)
    for cond in where:
        stmt = stmt.where(cond)
    return stmt


def _from_archived(columns: list, *where: ColumnElement) -> Select:
    """Build a SELECT from audit_daily_stats with the date guard baked in."""
    stmt = select(*columns).select_from(audit_daily_stats).where(_ARCHIVED_DATE_GUARD)
    for cond in where:
        stmt = stmt.where(cond)
    return stmt


# ---------------------------------------------------------------------------
# Dashboard stats
# ---------------------------------------------------------------------------


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
                            audit_log.c.status_code >= ERROR_STATUS_MIN,
                            audit_log.c.timestamp >= last_24h,
                            _API_ENDPOINT_FILTER,
                        )
                        .label("errors_24h"),
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
        "cache_hit_rate": cache_hit_rate,
        "endpoint_breakdown": breakdown,
    }


# ---------------------------------------------------------------------------
# Audit log browsing
# ---------------------------------------------------------------------------


async def get_audit_rows(
    engine: AsyncEngine,
    *,
    page: int = 1,
    per_page: int = 50,
    endpoint: str | None = None,
    provider: str | None = None,
    client_ip: str | None = None,
    status_min: int | None = None,
    status_codes: list[int] | None = None,
    validation_statuses: list[str] | None = None,
    raw_input: str | None = None,
) -> tuple[list[dict], int]:
    """Fetch paginated, filtered audit_log rows. Returns (rows, total_count)."""
    conditions: list[ColumnElement] = []

    if endpoint:
        conditions.append(audit_log.c.endpoint == f"/api/v1/{endpoint}")
    if provider:
        conditions.append(audit_log.c.provider == provider)
    if client_ip:
        conditions.append(audit_log.c.client_ip == client_ip)
    if status_min:
        conditions.append(audit_log.c.status_code >= status_min)
    if status_codes:
        conditions.append(audit_log.c.status_code.in_(status_codes))
    if validation_statuses:
        conditions.append(audit_log.c.validation_status.in_(validation_statuses))
    if raw_input:
        conditions.append(query_patterns.c.raw_input.ilike(f"%{raw_input}%"))

    joined = audit_log.outerjoin(
        query_patterns,
        audit_log.c.pattern_key == query_patterns.c.pattern_key,
    )

    async with engine.connect() as conn:
        count_stmt = select(func.count()).select_from(joined)
        for cond in conditions:
            count_stmt = count_stmt.where(cond)
        total = (await conn.execute(count_stmt)).scalar()

        row_stmt = select(
            audit_log.c.id,
            audit_log.c.timestamp,
            audit_log.c.request_id,
            audit_log.c.client_ip,
            audit_log.c.method,
            audit_log.c.endpoint,
            audit_log.c.status_code,
            audit_log.c.latency_ms,
            audit_log.c.provider,
            audit_log.c.validation_status,
            audit_log.c.cache_hit,
            audit_log.c.error_detail,
            query_patterns.c.raw_input,
        ).select_from(joined)
        for cond in conditions:
            row_stmt = row_stmt.where(cond)
        row_stmt = (
            row_stmt.order_by(audit_log.c.timestamp.desc())
            .limit(per_page)
            .offset((page - 1) * per_page)
        )
        result = await conn.execute(row_stmt)
        rows = [dict(r._mapping) for r in result]  # noqa: SLF001

    return rows, total


# ---------------------------------------------------------------------------
# Per-endpoint stats
# ---------------------------------------------------------------------------


async def get_endpoint_stats(engine: AsyncEngine, endpoint_name: str) -> dict:
    """Fetch stats for a specific endpoint."""
    endpoint_path = f"/api/v1/{endpoint_name}"
    tb = _time_boundaries()

    async with engine.connect() as conn:
        # Live stats
        row = (
            await conn.execute(
                _from_live(
                    [
                        func.count().label("total"),
                        func.count()
                        .filter(audit_log.c.timestamp >= tb["last_24h"])
                        .label("last_24h"),
                        func.count()
                        .filter(audit_log.c.timestamp >= tb["last_7d"])
                        .label("last_7d"),
                        func.count()
                        .filter(audit_log.c.status_code >= ERROR_STATUS_MIN)
                        .label("errors"),
                        func.avg(audit_log.c.latency_ms)
                        .filter(audit_log.c.latency_ms.isnot(None))
                        .label("avg_latency"),
                    ],
                    audit_log.c.endpoint == endpoint_path,
                )
            )
        ).one()

        # Archived totals for this endpoint
        archived = (
            await conn.execute(
                _from_archived(
                    [
                        func.coalesce(func.sum(audit_daily_stats.c.request_count), 0).label(
                            "total"
                        ),
                        func.coalesce(func.sum(audit_daily_stats.c.error_count), 0).label("errors"),
                    ],
                    audit_daily_stats.c.endpoint == endpoint_path,
                )
            )
        ).one()

        # Live + archived status code distribution
        live_status = (
            select(
                audit_log.c.status_code,
                sa.cast(func.count(), sa.Integer).label("cnt"),
            )
            .where(audit_log.c.endpoint == endpoint_path)
            .group_by(audit_log.c.status_code)
        )
        archived_status = (
            select(
                audit_daily_stats.c.status_code,
                sa.cast(func.sum(audit_daily_stats.c.request_count), sa.Integer).label("cnt"),
            )
            .where(
                audit_daily_stats.c.endpoint == endpoint_path,
                _ARCHIVED_DATE_GUARD,
            )
            .group_by(audit_daily_stats.c.status_code)
        )
        combined = union_all(live_status, archived_status).subquery("combined")
        status_rows = (
            await conn.execute(
                select(
                    combined.c.status_code,
                    sa.cast(func.sum(combined.c.cnt), sa.Integer).label("count"),
                )
                .group_by(combined.c.status_code)
                .order_by(combined.c.status_code)
            )
        ).fetchall()

        # Per-window status code distributions (live only)
        live_status_24h_rows = (
            await conn.execute(
                select(
                    audit_log.c.status_code,
                    sa.cast(func.count(), sa.Integer).label("cnt"),
                )
                .where(
                    audit_log.c.endpoint == endpoint_path,
                    audit_log.c.timestamp >= tb["last_24h"],
                )
                .group_by(audit_log.c.status_code)
            )
        ).fetchall()

        live_status_7d_rows = (
            await conn.execute(
                select(
                    audit_log.c.status_code,
                    sa.cast(func.count(), sa.Integer).label("cnt"),
                )
                .where(
                    audit_log.c.endpoint == endpoint_path,
                    audit_log.c.timestamp >= tb["last_7d"],
                )
                .group_by(audit_log.c.status_code)
            )
        ).fetchall()

    total = row.total + archived.total
    errors = row.errors + archived.errors
    error_rate = (errors / total * 100) if total > 0 else None
    return {
        "total": total,
        "last_24h": row.last_24h,
        "last_7d": row.last_7d,
        "error_rate": error_rate,
        "avg_latency_ms": round(row.avg_latency) if row.avg_latency else None,
        "status_codes_all": {r.status_code: r.count for r in status_rows},
        "status_codes_24h": {r.status_code: r.cnt for r in live_status_24h_rows},
        "status_codes_7d": {r.status_code: r.cnt for r in live_status_7d_rows},
    }


# ---------------------------------------------------------------------------
# Per-provider stats
# ---------------------------------------------------------------------------


async def get_provider_stats(engine: AsyncEngine, provider_name: str) -> dict:
    """Fetch stats for a specific validation provider."""
    tb = _time_boundaries()

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                _from_live(
                    [
                        func.count().label("total"),
                        func.count()
                        .filter(audit_log.c.timestamp >= tb["last_24h"])
                        .label("last_24h"),
                        func.count()
                        .filter(audit_log.c.timestamp >= tb["last_7d"])
                        .label("last_7d"),
                        func.count()
                        .filter(
                            audit_log.c.cache_hit.is_(True),
                            audit_log.c.timestamp >= tb["last_7d"],
                        )
                        .label("cache_hits"),
                        func.count()
                        .filter(
                            audit_log.c.cache_hit.isnot(None),
                            audit_log.c.timestamp >= tb["last_7d"],
                        )
                        .label("cache_total"),
                    ],
                    audit_log.c.provider == provider_name,
                )
            )
        ).one()

        # Archived total for this provider
        archived_total = (
            await conn.execute(
                _from_archived(
                    [func.coalesce(func.sum(audit_daily_stats.c.request_count), 0)],
                    audit_daily_stats.c.provider == provider_name,
                )
            )
        ).scalar()

        # Validation status distributions — live all-time (no archive column)
        status_rows = (
            await conn.execute(
                _from_live(
                    [
                        audit_log.c.validation_status,
                        func.count().label("count"),
                    ],
                    audit_log.c.provider == provider_name,
                    audit_log.c.validation_status.isnot(None),
                )
                .group_by(audit_log.c.validation_status)
                .order_by(func.count().desc())
            )
        ).fetchall()

        # Status code distributions (live only, 24h)
        live_status_24h_rows = (
            await conn.execute(
                select(
                    audit_log.c.status_code,
                    sa.cast(func.count(), sa.Integer).label("cnt"),
                )
                .where(
                    audit_log.c.provider == provider_name,
                    audit_log.c.timestamp >= tb["last_24h"],
                )
                .group_by(audit_log.c.status_code)
            )
        ).fetchall()

        live_status_7d_rows = (
            await conn.execute(
                select(
                    audit_log.c.status_code,
                    sa.cast(func.count(), sa.Integer).label("cnt"),
                )
                .where(
                    audit_log.c.provider == provider_name,
                    audit_log.c.timestamp >= tb["last_7d"],
                )
                .group_by(audit_log.c.status_code)
            )
        ).fetchall()

        vs_7d_rows = (
            await conn.execute(
                _from_live(
                    [
                        audit_log.c.validation_status,
                        func.count().label("count"),
                    ],
                    audit_log.c.provider == provider_name,
                    audit_log.c.validation_status.isnot(None),
                    audit_log.c.timestamp >= tb["last_7d"],
                )
                .group_by(audit_log.c.validation_status)
                .order_by(func.count().desc())
            )
        ).fetchall()

        # Status code distributions (live + archived, all-time)
        live_status_all = (
            select(
                audit_log.c.status_code,
                sa.cast(func.count(), sa.Integer).label("cnt"),
            )
            .where(audit_log.c.provider == provider_name)
            .group_by(audit_log.c.status_code)
        )
        archived_status_all = (
            select(
                audit_daily_stats.c.status_code,
                sa.cast(func.sum(audit_daily_stats.c.request_count), sa.Integer).label("cnt"),
            )
            .where(
                audit_daily_stats.c.provider == provider_name,
                _ARCHIVED_DATE_GUARD,
            )
            .group_by(audit_daily_stats.c.status_code)
        )
        combined_status = union_all(live_status_all, archived_status_all).subquery(
            "combined_status"
        )
        status_all_rows = (
            await conn.execute(
                select(
                    combined_status.c.status_code,
                    sa.cast(func.sum(combined_status.c.cnt), sa.Integer).label("count"),
                )
                .group_by(combined_status.c.status_code)
                .order_by(combined_status.c.status_code)
            )
        ).fetchall()

        # Validation status distributions (live only, 24h)
        vs_24h_rows = (
            await conn.execute(
                _from_live(
                    [
                        audit_log.c.validation_status,
                        func.count().label("count"),
                    ],
                    audit_log.c.provider == provider_name,
                    audit_log.c.validation_status.isnot(None),
                    audit_log.c.timestamp >= tb["last_24h"],
                )
                .group_by(audit_log.c.validation_status)
                .order_by(func.count().desc())
            )
        ).fetchall()

    cache_hit_rate = (row.cache_hits / row.cache_total * 100) if row.cache_total > 0 else None
    return {
        "total": row.total + archived_total,
        "last_24h": row.last_24h,
        "last_7d": row.last_7d,
        "cache_hit_rate": cache_hit_rate,
        "status_codes_24h": {r.status_code: r.cnt for r in live_status_24h_rows},
        "status_codes_7d": {r.status_code: r.cnt for r in live_status_7d_rows},
        "status_codes_all": {r.status_code: r.count for r in status_all_rows},
        "validation_statuses_all": _sort_validation_statuses(
            {r.validation_status: r.count for r in status_rows}
        ),
        "validation_statuses_24h": _sort_validation_statuses(
            {r.validation_status: r.count for r in vs_24h_rows}
        ),
        "validation_statuses_7d": _sort_validation_statuses(
            {r.validation_status: r.count for r in vs_7d_rows}
        ),
    }


# ---------------------------------------------------------------------------
# Sparkline data
# ---------------------------------------------------------------------------


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
                        func.count()
                        .filter(audit_log.c.status_code >= ERROR_STATUS_MIN)
                        .label("errors"),
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
        "requests_7d": _fill_daily(daily_rows, start_7d, 7),
        "requests_24h": _fill_hourly(hourly_rows, start_24h, 24),
        "cache_hit_rate": _fill_rate_daily(cache_rows, start_7d, 7, "hits", "total"),
        "error_rate": _fill_rate_daily(error_rows, start_7d, 7, "errors", "total"),
    }
