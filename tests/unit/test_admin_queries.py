"""Tests for admin dashboard SQL query helpers."""

from datetime import UTC, date, datetime

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
    assert stats["requests_24h"] == 6
    assert stats["requests_7d"] == 6
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
    assert bd["7d"]["/validate"] == 2
    assert bd["7d"]["/parse"] == 2
    assert bd["7d"]["/standardize"] == 1
    assert bd["7d"]["other"] == 1
    assert bd["24h"]["/validate"] == 2
    assert bd["24h"]["/standardize"] == 1
    assert bd["24h"]["other"] == 1


@pytest.mark.asyncio
async def test_get_audit_rows_unfiltered(db: AsyncEngine) -> None:
    await _seed_rows(db)
    rows, total = await get_audit_rows(db)
    assert total == 6
    assert len(rows) == 6


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
    assert stats["last_24h"] == 2
    assert 400 in stats["status_codes"]


@pytest.mark.asyncio
async def test_get_provider_stats(db: AsyncEngine) -> None:
    await _seed_rows(db)
    stats = await get_provider_stats(db, "usps")
    assert stats["total"] == 2
    assert stats["last_24h"] == 2
    assert "confirmed" in stats["validation_statuses"]


@pytest.mark.asyncio
async def test_get_sparkline_data_with_rows(db: AsyncEngine) -> None:
    """Sparkline data returns point lists keyed by card name."""
    await _seed_rows(db)
    data = await get_sparkline_data(db)
    assert set(data.keys()) == {
        "requests_all",
        "requests_7d",
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


async def _seed_stats_rows(engine: AsyncEngine) -> None:
    """Insert audit_daily_stats rows simulating archived data."""
    rows = [
        # Archived: 120 days ago — validate/200/usps/cached
        {
            "d": date(2025, 11, 21),
            "ep": "/api/v1/validate",
            "provider": "usps",
            "status": 200,
            "cache": True,
            "req_count": 50,
            "err_count": 0,
            "avg_lat": 45,
            "p95_lat": 90,
        },
        # Archived: 120 days ago — parse/400/null/null
        {
            "d": date(2025, 11, 21),
            "ep": "/api/v1/parse",
            "provider": None,
            "status": 400,
            "cache": None,
            "req_count": 10,
            "err_count": 10,
            "avg_lat": 5,
            "p95_lat": 8,
        },
    ]
    async with engine.begin() as conn:
        for r in rows:
            await conn.execute(
                text("""
                    INSERT INTO audit_daily_stats
                        (date, endpoint, provider, status_code, cache_hit,
                         request_count, error_count, avg_latency_ms, p95_latency_ms)
                    VALUES (:d, :ep, :provider, :status, :cache,
                            :req_count, :err_count, :avg_lat, :p95_lat)
                """),
                r,
            )


@pytest.mark.asyncio
async def test_dashboard_stats_includes_archived(db: AsyncEngine) -> None:
    """All-time total includes both live audit_log and archived audit_daily_stats."""
    await _seed_rows(db)  # 6 live rows
    await _seed_stats_rows(db)  # 60 archived requests

    stats = await get_dashboard_stats(db)
    # All-time: 6 live + 60 archived = 66
    assert stats["requests_all"] == 66
    # 24h and 7d should only count live rows
    assert stats["requests_24h"] == 6
    assert stats["requests_7d"] == 6

    # Endpoint breakdown all-time should include archived
    bd = stats["endpoint_breakdown"]
    assert bd["all"]["/validate"] == 52  # 2 live + 50 archived
    assert bd["all"]["/parse"] == 12  # 2 live + 10 archived


@pytest.mark.asyncio
async def test_endpoint_stats_includes_archived(db: AsyncEngine) -> None:
    """Per-endpoint all-time stats include archived data."""
    await _seed_rows(db)
    await _seed_stats_rows(db)

    stats = await get_endpoint_stats(db, "validate")
    assert stats["total"] == 52  # 2 live + 50 archived
    assert stats["last_24h"] == 2  # Only live

    # Status codes should include archived
    assert stats["status_codes"][200] == 52


@pytest.mark.asyncio
async def test_provider_stats_includes_archived(db: AsyncEngine) -> None:
    """Per-provider all-time stats include archived data."""
    await _seed_rows(db)
    await _seed_stats_rows(db)

    stats = await get_provider_stats(db, "usps")
    assert stats["total"] == 52  # 2 live + 50 archived
    assert stats["last_24h"] == 2  # Only live


@pytest.mark.asyncio
async def test_get_sparkline_data_empty_db(db: AsyncEngine) -> None:
    """Sparkline data returns zero-filled lists on empty audit_log."""
    data = await get_sparkline_data(db)
    assert len(data["requests_all"]) == 30
    assert len(data["requests_7d"]) == 7
    assert len(data["requests_24h"]) == 24
    assert len(data["cache_hit_rate"]) == 7
    assert len(data["error_rate"]) == 7
    for key in data:
        assert all(v == 0 for v in data[key])
