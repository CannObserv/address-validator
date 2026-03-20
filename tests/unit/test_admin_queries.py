"""Tests for admin dashboard SQL query helpers."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from address_validator.routers.admin.queries import (
    get_audit_rows,
    get_dashboard_stats,
    get_endpoint_stats,
    get_provider_stats,
)


async def _seed_rows(engine: AsyncEngine) -> None:
    """Insert sample audit_log rows for testing."""
    now = datetime.now(UTC)
    rows = [
        {
            "ts": now,
            "ip": "1.2.3.4",
            "method": "POST",
            "ep": "/api/v1/validate",
            "status": 200,
            "provider": "usps",
            "vs": "confirmed",
            "cache": True,
        },
        {
            "ts": now,
            "ip": "1.2.3.4",
            "method": "POST",
            "ep": "/api/v1/validate",
            "status": 200,
            "provider": "usps",
            "vs": "confirmed",
            "cache": False,
        },
        {
            "ts": now,
            "ip": "5.6.7.8",
            "method": "POST",
            "ep": "/api/v1/parse",
            "status": 200,
            "provider": None,
            "vs": None,
            "cache": None,
        },
        {
            "ts": now,
            "ip": "5.6.7.8",
            "method": "POST",
            "ep": "/api/v1/parse",
            "status": 400,
            "provider": None,
            "vs": None,
            "cache": None,
        },
    ]
    async with engine.begin() as conn:
        for r in rows:
            await conn.execute(
                text("""
                    INSERT INTO audit_log (timestamp, client_ip, method, endpoint,
                        status_code, provider, validation_status, cache_hit)
                    VALUES (:ts, :ip, :method, :ep, :status, :provider, :vs, :cache)
                """),
                r,
            )


@pytest.mark.asyncio
async def test_get_dashboard_stats(db: AsyncEngine) -> None:
    await _seed_rows(db)
    stats = await get_dashboard_stats(db)
    assert stats["requests_today"] == 4
    assert stats["requests_all"] == 4
    assert stats["cache_hit_rate"] == 50.0


@pytest.mark.asyncio
async def test_get_audit_rows_with_filter(db: AsyncEngine) -> None:
    await _seed_rows(db)
    rows, total = await get_audit_rows(db, endpoint="parse")
    assert total == 2
    assert all(r["endpoint"] == "/api/v1/parse" for r in rows)


@pytest.mark.asyncio
async def test_get_audit_rows_by_ip(db: AsyncEngine) -> None:
    await _seed_rows(db)
    _rows, total = await get_audit_rows(db, client_ip="1.2.3.4")
    assert total == 2


@pytest.mark.asyncio
async def test_get_endpoint_stats(db: AsyncEngine) -> None:
    await _seed_rows(db)
    stats = await get_endpoint_stats(db, "parse")
    assert stats["total"] == 2
    assert 400 in stats["status_codes"]


@pytest.mark.asyncio
async def test_get_provider_stats(db: AsyncEngine) -> None:
    await _seed_rows(db)
    stats = await get_provider_stats(db, "usps")
    assert stats["total"] == 2
    assert "confirmed" in stats["validation_statuses"]
