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
    assert 400 in stats["status_codes_all"]


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
    assert stats["status_codes_all"][200] == 52


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


async def _seed_cache_row(
    engine: AsyncEngine,
    *,
    pattern_key: str,
    raw_input: str,
    audit_pattern_key: str | None = None,
) -> None:
    """Insert a query_patterns row and optionally link an audit_log row via pattern_key."""
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        # Insert a minimal validated_addresses row first (FK requirement)
        await conn.execute(
            text("""
                INSERT INTO validated_addresses
                    (canonical_key, provider, status, country,
                     created_at, last_seen_at, validated_at)
                VALUES (:ck, 'usps', 'confirmed', 'US', :now, :now, :now)
                ON CONFLICT DO NOTHING
            """),
            {"ck": f"canonical_{pattern_key}", "now": now},
        )
        await conn.execute(
            text("""
                INSERT INTO query_patterns (pattern_key, canonical_key, created_at, raw_input)
                VALUES (:pk, :ck, :now, :raw)
            """),
            {"pk": pattern_key, "ck": f"canonical_{pattern_key}", "now": now, "raw": raw_input},
        )
        if audit_pattern_key is not None:
            await conn.execute(
                text("""
                    UPDATE audit_log SET pattern_key = :pk
                    WHERE id = (
                        SELECT id FROM audit_log
                        WHERE pattern_key IS NULL
                        LIMIT 1
                    )
                """),
                {"pk": audit_pattern_key},
            )


@pytest.mark.asyncio
async def test_get_audit_rows_by_raw_input(db: AsyncEngine) -> None:
    """raw_input filter returns only rows joined to a matching query_patterns entry."""
    await _seed_rows(db)

    # Assign pattern_key to the first validate row, seed matching query_patterns entry
    pk = "aaaa1111"
    async with db.begin() as conn:
        await conn.execute(
            text("""
                UPDATE audit_log SET pattern_key = :pk
                WHERE id = (
                    SELECT id FROM audit_log
                    WHERE endpoint = '/api/v1/validate'
                      AND pattern_key IS NULL
                    ORDER BY id
                    LIMIT 1
                )
            """),
            {"pk": pk},
        )
    await _seed_cache_row(db, pattern_key=pk, raw_input="123 Main St, Springfield IL")

    rows, total = await get_audit_rows(db, raw_input="Springfield")
    assert total == 1
    assert rows[0]["raw_input"] == "123 Main St, Springfield IL"


@pytest.mark.asyncio
async def test_get_audit_rows_raw_input_not_set_excluded(db: AsyncEngine) -> None:
    """Rows without a linked query_patterns entry are excluded when filtering by raw_input."""
    await _seed_rows(db)

    _rows, total = await get_audit_rows(db, raw_input="anything")
    assert total == 0


@pytest.mark.asyncio
async def test_get_audit_rows_includes_raw_input_column(db: AsyncEngine) -> None:
    """Each returned row dict contains a 'raw_input' key (NULL when no cache link)."""
    await _seed_rows(db)
    rows, _ = await get_audit_rows(db)
    assert all("raw_input" in r for r in rows)


@pytest.mark.asyncio
async def test_get_audit_rows_by_status_codes_single(db: AsyncEngine) -> None:
    await _seed_rows(db)
    rows, total = await get_audit_rows(db, status_codes=[400])
    # seed has: parse/400, favicon/404 — testing exact 400 only
    assert total == 1
    assert rows[0]["status_code"] == 400


@pytest.mark.asyncio
async def test_get_audit_rows_by_status_codes_multiple(db: AsyncEngine) -> None:
    """Multiple status_codes = OR: returns rows matching any of the given codes."""
    await _seed_rows(db)
    rows, total = await get_audit_rows(db, status_codes=[400, 404])
    assert total == 2
    assert {r["status_code"] for r in rows} == {400, 404}


@pytest.mark.asyncio
async def test_get_audit_rows_by_validation_statuses_single(db: AsyncEngine) -> None:
    await _seed_rows(db)
    rows, total = await get_audit_rows(db, validation_statuses=["confirmed"])
    assert total == 2
    assert all(r["validation_status"] == "confirmed" for r in rows)


@pytest.mark.asyncio
async def test_get_audit_rows_by_validation_statuses_multiple(db: AsyncEngine) -> None:
    """Multiple validation_statuses = OR behavior."""
    # seed_rows only has 'confirmed'; add a not_confirmed row
    await _seed_rows(db)
    async with db.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO audit_log (timestamp, client_ip, method, endpoint,
                    status_code, provider, validation_status, cache_hit)
                VALUES (:ts, '1.2.3.4', 'POST', '/api/v1/validate',
                    200, 'usps', 'not_confirmed', false)
            """),
            {"ts": datetime.now(UTC)},
        )
    rows, total = await get_audit_rows(db, validation_statuses=["confirmed", "not_confirmed"])
    assert total == 3  # 2 confirmed from seed + 1 not_confirmed
    statuses = {r["validation_status"] for r in rows}
    assert statuses == {"confirmed", "not_confirmed"}


@pytest.mark.asyncio
async def test_get_audit_rows_status_codes_and_validation_statuses_combined(
    db: AsyncEngine,
) -> None:
    """status_codes AND validation_statuses filters combine as AND across categories."""
    await _seed_rows(db)
    # seed has 2 validate/200/usps/confirmed rows — filter to both confirmed AND 200
    rows, total = await get_audit_rows(db, status_codes=[200], validation_statuses=["confirmed"])
    assert total == 2
    assert all(r["status_code"] == 200 and r["validation_status"] == "confirmed" for r in rows)


@pytest.mark.asyncio
async def test_get_audit_rows_empty_status_codes_returns_all(db: AsyncEngine) -> None:
    """Empty status_codes list applies no filter — returns all rows."""
    await _seed_rows(db)
    _rows, total = await get_audit_rows(db, status_codes=[])
    assert total == 6  # all seed rows


@pytest.mark.asyncio
async def test_get_audit_rows_empty_validation_statuses_returns_all(db: AsyncEngine) -> None:
    """Empty validation_statuses list applies no filter — returns all rows."""
    await _seed_rows(db)
    _rows, total = await get_audit_rows(db, validation_statuses=[])
    assert total == 6  # all seed rows


@pytest.mark.asyncio
async def test_get_endpoint_stats_has_per_window_status_codes(db: AsyncEngine) -> None:
    """get_endpoint_stats returns status_codes_24h, status_codes_7d, status_codes_all."""
    await _seed_rows(db)
    stats = await get_endpoint_stats(db, "parse")
    # parse has: 1x 200, 1x 400
    assert "status_codes_all" in stats
    assert "status_codes_24h" in stats
    assert "status_codes_7d" in stats
    assert stats["status_codes_all"][400] == 1
    assert stats["status_codes_all"][200] == 1
    assert stats["status_codes_24h"][400] == 1
    assert stats["status_codes_7d"][400] == 1


@pytest.mark.asyncio
async def test_get_endpoint_stats_status_codes_key_removed(db: AsyncEngine) -> None:
    """Old 'status_codes' key is gone — callers must use status_codes_all."""
    await _seed_rows(db)
    stats = await get_endpoint_stats(db, "parse")
    assert "status_codes" not in stats


@pytest.mark.asyncio
async def test_get_endpoint_stats_all_time_includes_archived(db: AsyncEngine) -> None:
    """status_codes_all merges live + archived; 24h/7d are live only."""
    await _seed_rows(db)
    await _seed_stats_rows(db)  # adds archived parse/400 x10
    stats = await get_endpoint_stats(db, "parse")
    assert stats["status_codes_all"][400] == 11  # 1 live + 10 archived
    assert stats["status_codes_24h"][400] == 1  # live only
    assert stats["status_codes_7d"][400] == 1  # live only
