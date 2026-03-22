"""Tests for the audit service ContextVars and write helper."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from address_validator.models import (
    ComponentSet,
    StandardizeResponseV1,
    ValidateResponseV1,
    ValidationResult,
)
from address_validator.services.audit import (
    get_audit_cache_hit,
    get_audit_provider,
    get_audit_validation_status,
    reset_audit_context,
    set_audit_context,
    write_audit_row,
)
from address_validator.services.validation.cache_provider import CachingProvider


def test_context_vars_default_to_none() -> None:
    assert get_audit_provider() is None
    assert get_audit_validation_status() is None
    assert get_audit_cache_hit() is None


def test_set_audit_context_sets_values() -> None:
    set_audit_context(provider="usps", validation_status="confirmed", cache_hit=False)
    assert get_audit_provider() == "usps"
    assert get_audit_validation_status() == "confirmed"
    assert get_audit_cache_hit() is False
    # Clean up
    reset_audit_context()


def test_reset_audit_context_clears_values() -> None:
    set_audit_context(provider="google", validation_status="not_confirmed", cache_hit=True)
    reset_audit_context()
    assert get_audit_provider() is None
    assert get_audit_validation_status() is None
    assert get_audit_cache_hit() is None


@pytest.mark.asyncio
async def test_write_audit_row(db: AsyncEngine) -> None:
    """Verify write_audit_row inserts a row into audit_log."""
    await write_audit_row(
        db,
        timestamp=datetime.now(UTC),
        request_id="01TESTULID",
        client_ip="127.0.0.1",
        method="POST",
        endpoint="/api/v1/validate",
        status_code=200,
        latency_ms=42,
        provider="usps",
        validation_status="confirmed",
        cache_hit=False,
        error_detail=None,
    )
    async with db.connect() as conn:
        result = await conn.execute(text("SELECT * FROM audit_log"))
        rows = result.fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row.client_ip == "127.0.0.1"
    assert row.endpoint == "/api/v1/validate"
    assert row.status_code == 200
    assert row.provider == "usps"


@pytest.mark.asyncio
async def test_write_audit_row_fail_open(db: AsyncEngine) -> None:
    """Verify write_audit_row swallows errors."""
    await db.dispose()  # break the engine
    # Should not raise
    await write_audit_row(
        db,
        timestamp=datetime.now(UTC),
        request_id=None,
        client_ip="1.2.3.4",
        method="GET",
        endpoint="/api/v1/health",
        status_code=200,
        latency_ms=1,
        provider=None,
        validation_status=None,
        cache_hit=None,
        error_detail=None,
    )


@pytest.mark.asyncio
async def test_cache_provider_sets_audit_context_on_miss(db: AsyncEngine) -> None:
    """CachingProvider sets audit ContextVars on cache miss."""
    reset_audit_context()

    inner = AsyncMock()
    provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db), ttl_days=30)

    result = ValidateResponseV1(
        country="US",
        validation=ValidationResult(status="confirmed", provider="usps"),
    )
    inner.validate.return_value = result

    std = StandardizeResponseV1(
        address_line_1="123 MAIN ST",
        address_line_2="",
        city="ANYTOWN",
        region="WA",
        postal_code="98101",
        country="US",
        standardized="123 MAIN ST  ANYTOWN WA 98101",
        components=ComponentSet(
            spec="usps-pub28",
            spec_version="unknown",
            values={"PlaceName": "ANYTOWN", "StateName": "WA", "ZipCode": "98101"},
        ),
        warnings=[],
    )

    await provider.validate(std)
    assert get_audit_provider() == "usps"
    assert get_audit_validation_status() == "confirmed"
    assert get_audit_cache_hit() is False
    reset_audit_context()
