"""Per-provider stats queries."""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import func, select, union_all

from address_validator.db.tables import (
    audit_daily_stats,
    audit_log,
)

from ._shared import (
    _ARCHIVED_DATE_GUARD,
    _from_archived,
    _from_live,
    _sort_validation_statuses,
    _time_boundaries,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine


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

        # Status code distributions (live only, 24h + 7d)
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

        # Validation status distributions — live only (no archive column), 24h + 7d + all-time
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
                ).group_by(audit_log.c.validation_status)
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
                ).group_by(audit_log.c.validation_status)
            )
        ).fetchall()

        status_rows = (
            await conn.execute(
                _from_live(
                    [
                        audit_log.c.validation_status,
                        func.count().label("count"),
                    ],
                    audit_log.c.provider == provider_name,
                    audit_log.c.validation_status.isnot(None),
                ).group_by(audit_log.c.validation_status)
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


async def get_provider_daily_usage(engine: AsyncEngine) -> dict[str, int]:
    """Count audit_log rows for the current UTC day, grouped by provider.

    Returns a {provider_name: count} mapping. Providers with zero requests
    today are omitted. Rows with NULL provider are excluded.
    Fails open: returns {} on any exception.
    """
    tb = _time_boundaries()
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    _from_live(
                        [
                            audit_log.c.provider,
                            func.count().label("cnt"),
                        ],
                        audit_log.c.provider.isnot(None),
                        audit_log.c.timestamp >= tb["today"],
                    ).group_by(audit_log.c.provider)
                )
            ).fetchall()
    except Exception:
        return {}
    return {r.provider: r.cnt for r in rows}
