"""Shared helpers used across admin query modules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import sqlalchemy as sa
from sqlalchemy import func, select

from address_validator.db.tables import (
    ERROR_STATUS_MIN,
    RATE_LIMITED_STATUS,
    audit_daily_stats,
    audit_log,
)
from address_validator.routers.admin._config import VS_META

if TYPE_CHECKING:
    from sqlalchemy import ColumnElement, Select

# ---------------------------------------------------------------------------
# Shared expressions
# ---------------------------------------------------------------------------

# Single source of truth for the v1/v2 API endpoint surface. Maps each audit
# path to its friendly display label. Both v1 and v2 paths map to the same
# label so historical audit_log rows from before the v1 removal (#117) still
# surface. Ordering is significant — it drives display order in consumers.
_API_ENDPOINT_LABELS = {
    "/api/v1/parse": "/parse",
    "/api/v1/standardize": "/standardize",
    "/api/v1/validate": "/validate",
    "/api/v2/parse": "/parse",
    "/api/v2/standardize": "/standardize",
    "/api/v2/validate": "/validate",
}
_API_ENDPOINTS = tuple(_API_ENDPOINT_LABELS)
_API_ENDPOINT_FILTER = audit_log.c.endpoint.in_(_API_ENDPOINTS)

# ---------------------------------------------------------------------------
# Validation status helpers
# ---------------------------------------------------------------------------

_VS_CANONICAL_ORDER = tuple(VS_META.keys())


def _sort_validation_statuses(vs_dict: dict) -> dict:
    """Return vs_dict with keys in canonical display order.

    Unknown statuses sort after the known statuses, alphabetically among themselves.
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


def is_error_expr(status_code_col: ColumnElement) -> ColumnElement:
    """True for response status codes counted as errors.

    Rate-limited (429) responses are excluded — rate limiting is traffic
    control, not failure. Callers should surface 429 separately.
    """
    return sa.and_(
        status_code_col >= ERROR_STATUS_MIN,
        status_code_col != RATE_LIMITED_STATUS,
    )


def is_rate_limited_expr(status_code_col: ColumnElement) -> ColumnElement:
    """True for 429 Too Many Requests responses."""
    return status_code_col == RATE_LIMITED_STATUS
