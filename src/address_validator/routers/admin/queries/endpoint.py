"""Per-endpoint stats queries."""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import func, select, union_all

from address_validator.db.tables import (
    ERROR_STATUS_MIN,
    audit_daily_stats,
    audit_log,
)

from ._shared import _ARCHIVED_DATE_GUARD, _from_archived, _from_live, _time_boundaries

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


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
