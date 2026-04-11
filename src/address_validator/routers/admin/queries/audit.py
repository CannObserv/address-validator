"""Audit log browsing queries."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import func, select

from address_validator.db.tables import audit_log, query_patterns

if TYPE_CHECKING:
    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncEngine


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
        conditions.append(
            audit_log.c.endpoint.in_(
                [
                    f"/api/v1/{endpoint}",
                    f"/api/v2/{endpoint}",
                ]
            )
        )
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
