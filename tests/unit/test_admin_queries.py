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
    get_sparkline_data,
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
        {
            "ts": now,
            "ip": "5.6.7.8",
            "method": "POST",
            "ep": "/api/v1/standardize",
            "status": 200,
            "provider": None,
            "vs": None,
            "cache": None,
        },
        {
            "ts": now,
            "ip": "9.9.9.9",
            "method": "GET",
            "ep": "/favicon.ico",
            "status": 404,
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
    assert stats["requests_today"] == 6
    assert stats["requests_24h"] == 6
    assert stats["requests_all"] == 6
    assert stats["cache_hit_rate"] == 50.0
    # Error rate: 1 error (parse 400) out of 5 API requests in last 24h = 20%
    assert stats["error_rate"] == pytest.approx(20.0)

    # Per-endpoint breakdown
    bd = stats["endpoint_breakdown"]
    assert bd["all"]["/validate"] == 2
    assert bd["all"]["/parse"] == 2
    assert bd["all"]["/standardize"] == 1
    assert bd["all"]["other"] == 1
    assert bd["24h"]["/validate"] == 2
    assert bd["24h"]["/standardize"] == 1
    assert bd["24h"]["other"] == 1


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


@pytest.mark.asyncio
async def test_get_sparkline_data_with_rows(db: AsyncEngine) -> None:
    """Sparkline data returns point lists keyed by card name."""
    await _seed_rows(db)
    data = await get_sparkline_data(db)
    assert set(data.keys()) == {
        "requests_all",
        "requests_week",
        "requests_24h",
        "cache_hit_rate",
        "error_rate",
    }
    # Each value is a list of floats.
    for key in data:
        assert isinstance(data[key], list)
        assert all(isinstance(v, (int, float)) for v in data[key])
    # requests_24h has hourly buckets — seed rows are all "now" so at least one non-zero.
    assert any(v > 0 for v in data["requests_24h"])


@pytest.mark.asyncio
async def test_get_sparkline_data_empty_db(db: AsyncEngine) -> None:
    """Sparkline data returns zero-filled lists on empty audit_log."""
    data = await get_sparkline_data(db)
    assert len(data["requests_all"]) == 30
    assert len(data["requests_week"]) == 7
    assert len(data["requests_24h"]) == 24
    assert len(data["cache_hit_rate"]) == 7
    assert len(data["error_rate"]) == 7
    for key in data:
        assert all(v == 0 for v in data[key])
