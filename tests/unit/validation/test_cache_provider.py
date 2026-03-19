"""Unit tests for CachingProvider."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from address_validator.models import (
    ComponentSet,
    StandardizeResponseV1,
    ValidateResponseV1,
    ValidationResult,
)
from address_validator.services.validation.cache_provider import (
    CachingProvider,
    _make_canonical_key,
    _make_pattern_key,
)
from address_validator.usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_std(
    address_number: str = "123",
    street_name: str = "MAIN",
    street_type: str = "ST",
    city: str = "SPRINGFIELD",
    region: str = "IL",
    postal_code: str = "62701",
    country: str = "US",
) -> StandardizeResponseV1:
    values = {
        "address_number": address_number,
        "street_name": street_name,
        "street_name_post_type": street_type,
        "city": city,
        "state": region,
        "zip_code": postal_code,
    }
    return StandardizeResponseV1(
        address_line_1=f"{address_number} {street_name} {street_type}",
        address_line_2="",
        city=city,
        region=region,
        postal_code=postal_code,
        country=country,
        standardized=(
            f"{address_number} {street_name} {street_type}  {city}, {region} {postal_code}"
        ),
        components=ComponentSet(
            spec=USPS_PUB28_SPEC,
            spec_version=USPS_PUB28_SPEC_VERSION,
            values=values,
        ),
        warnings=[],
    )


def _make_confirmed_response(country: str = "US") -> ValidateResponseV1:
    return ValidateResponseV1(
        address_line_1="123 MAIN ST",
        address_line_2=None,
        city="SPRINGFIELD",
        region="IL",
        postal_code="62701-1234",
        country=country,
        validated="123 MAIN ST  SPRINGFIELD, IL 62701-1234",
        components=ComponentSet(
            spec=USPS_PUB28_SPEC,
            spec_version=USPS_PUB28_SPEC_VERSION,
            values={"address_number": "123", "street_name": "MAIN"},
        ),
        validation=ValidationResult(
            status="confirmed",
            dpv_match_code="Y",
            provider="usps",
        ),
        latitude=None,
        longitude=None,
        warnings=["provider-warning"],
    )


def _make_unavailable_response(country: str = "US") -> ValidateResponseV1:
    return ValidateResponseV1(
        country=country,
        validation=ValidationResult(status="unavailable"),
    )


def _make_not_confirmed_response(country: str = "US") -> ValidateResponseV1:
    return ValidateResponseV1(
        country=country,
        validation=ValidationResult(status="not_confirmed", dpv_match_code="N", provider="usps"),
    )


def _make_provider(response: ValidateResponseV1) -> AsyncMock:
    inner = AsyncMock()
    inner.validate = AsyncMock(return_value=response)
    return inner


async def _backdate_validated_at(engine: AsyncEngine, days_ago: int) -> None:
    """Set validated_at on all validated_addresses rows to `days_ago` days in the past."""
    ts = datetime.now(UTC) - timedelta(days=days_ago)
    async with engine.begin() as conn:
        await conn.execute(
            text("UPDATE validated_addresses SET validated_at = :ts"),
            {"ts": ts},
        )


_TABLES = {"validated_addresses", "query_patterns"}


async def _count_rows(engine: AsyncEngine, table: str) -> int:
    if table not in _TABLES:
        raise ValueError(f"unknown table: {table!r}")
    async with engine.connect() as conn:
        return (await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar()


async def _fetch_one(engine: AsyncEngine, query: str, params: dict | None = None):  # type: ignore[return]
    async with engine.connect() as conn:
        result = await conn.execute(text(query), params or {})
        return result.mappings().fetchone()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCacheMiss:
    async def test_cache_miss_calls_inner(self, db: AsyncEngine) -> None:
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=AsyncMock(return_value=db))
        std = _make_std()

        result = await provider.validate(std)

        inner.validate.assert_awaited_once_with(std)
        assert result.validation.status == "confirmed"

    async def test_miss_stores_pattern(self, db: AsyncEngine) -> None:
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=AsyncMock(return_value=db))
        std = _make_std()

        await provider.validate(std)

        pattern_key = _make_pattern_key(std)
        row = await _fetch_one(
            db,
            "SELECT * FROM query_patterns WHERE pattern_key = :pk",
            {"pk": pattern_key},
        )
        assert row is not None

    async def test_miss_stores_canonical(self, db: AsyncEngine) -> None:
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=AsyncMock(return_value=db))

        await provider.validate(_make_std())

        canonical_key = _make_canonical_key(response)
        row = await _fetch_one(
            db,
            "SELECT * FROM validated_addresses WHERE canonical_key = :ck",
            {"ck": canonical_key},
        )
        assert row is not None
        assert row["status"] == "confirmed"
        assert row["provider"] == "usps"


class TestCacheHit:
    async def test_hit_skips_inner(self, db: AsyncEngine) -> None:
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=AsyncMock(return_value=db))
        std = _make_std()

        await provider.validate(std)  # miss — stores
        inner.validate.reset_mock()
        result = await provider.validate(std)  # hit

        inner.validate.assert_not_awaited()
        assert result.validation.status == "confirmed"

    async def test_response_roundtrip(self, db: AsyncEngine) -> None:
        """All response fields survive the serialize → deserialize round-trip."""
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=AsyncMock(return_value=db))
        std = _make_std()

        await provider.validate(std)
        result = await provider.validate(std)

        assert result.address_line_1 == "123 MAIN ST"
        assert result.city == "SPRINGFIELD"
        assert result.region == "IL"
        assert result.postal_code == "62701-1234"
        assert result.validated == "123 MAIN ST  SPRINGFIELD, IL 62701-1234"
        assert result.validation.status == "confirmed"
        assert result.validation.dpv_match_code == "Y"
        assert result.validation.provider == "usps"
        assert result.components is not None
        assert result.components.spec == USPS_PUB28_SPEC
        assert result.warnings == ["provider-warning"]

    async def test_different_pattern_same_canonical(self, db: AsyncEngine) -> None:
        """Two patterns that produce the same provider result share one canonical record."""
        response = _make_confirmed_response()

        std1 = _make_std(street_name="MAIN")

        # std2 has extra zip_code component — different pattern_key, same canonical result
        std2 = StandardizeResponseV1(
            address_line_1="123 MAIN ST",
            address_line_2="",
            city="SPRINGFIELD",
            region="IL",
            postal_code="62701",
            country="US",
            standardized="123 MAIN ST  SPRINGFIELD, IL 62701",
            components=ComponentSet(
                spec=USPS_PUB28_SPEC,
                spec_version=USPS_PUB28_SPEC_VERSION,
                values={"address_number": "123", "street_name": "MAIN", "zip_code": "62701"},
            ),
            warnings=[],
        )

        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=AsyncMock(return_value=db))

        await provider.validate(std1)  # miss
        inner.validate.reset_mock()
        await provider.validate(std2)  # miss (different pattern) → store same canonical

        assert await _count_rows(db, "query_patterns") == 2
        assert await _count_rows(db, "validated_addresses") == 1


class TestUnavailableNotCached:
    async def test_unavailable_not_stored(self, db: AsyncEngine) -> None:
        response = _make_unavailable_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=AsyncMock(return_value=db))

        await provider.validate(_make_std())

        assert await _count_rows(db, "validated_addresses") == 0

    async def test_unavailable_calls_inner_every_time(self, db: AsyncEngine) -> None:
        response = _make_unavailable_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=AsyncMock(return_value=db))
        std = _make_std()

        await provider.validate(std)
        await provider.validate(std)

        assert inner.validate.await_count == 2


class TestNotConfirmedCached:
    async def test_not_confirmed_is_stored_and_retrieved(self, db: AsyncEngine) -> None:
        response = _make_not_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=AsyncMock(return_value=db))
        std = _make_std()

        await provider.validate(std)
        inner.validate.reset_mock()
        result = await provider.validate(std)

        inner.validate.assert_not_awaited()
        assert result.validation.status == "not_confirmed"


class TestWarnings:
    async def test_provider_warnings_stored_std_warnings_not(self, db: AsyncEngine) -> None:
        """Only provider-level warnings are persisted; std.warnings are not stored."""
        response = ValidateResponseV1(
            address_line_1="123 MAIN ST",
            city="SPRINGFIELD",
            region="IL",
            postal_code="62701",
            country="US",
            validation=ValidationResult(status="confirmed", dpv_match_code="Y", provider="usps"),
            warnings=["provider: inferred city"],
        )
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=AsyncMock(return_value=db))

        std = _make_std()
        std_with_warnings = StandardizeResponseV1(
            **{**std.model_dump(), "warnings": ["standardize: truncated"]}
        )

        await provider.validate(std_with_warnings)
        result = await provider.validate(std_with_warnings)

        assert result.warnings == ["provider: inferred city"]


class TestLatLng:
    async def test_lat_lng_roundtrip(self, db: AsyncEngine) -> None:
        response = ValidateResponseV1(
            address_line_1="123 MAIN ST",
            city="SPRINGFIELD",
            region="IL",
            postal_code="62701",
            country="US",
            validation=ValidationResult(status="confirmed", dpv_match_code="Y", provider="google"),
            latitude=39.7817,
            longitude=-89.6501,
            warnings=[],
        )
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=AsyncMock(return_value=db))
        std = _make_std()

        await provider.validate(std)
        result = await provider.validate(std)

        assert result.latitude == pytest.approx(39.7817)
        assert result.longitude == pytest.approx(-89.6501)


class TestFailOpen:
    async def test_lookup_db_error_calls_inner(self) -> None:
        """A DB error on lookup fails open — inner provider is called instead of raising."""
        get_engine_mock = AsyncMock(side_effect=RuntimeError("db unavailable"))
        inner = _make_provider(_make_confirmed_response())
        provider = CachingProvider(inner=inner, get_engine=get_engine_mock)

        result = await provider.validate(_make_std())

        inner.validate.assert_awaited_once()
        assert result.validation.status == "confirmed"

    async def test_lookup_db_error_does_not_raise(self) -> None:
        """A DB error on lookup is swallowed; the response is returned normally."""
        get_engine_mock = AsyncMock(side_effect=OSError("disk full"))
        inner = _make_provider(_make_confirmed_response())
        provider = CachingProvider(inner=inner, get_engine=get_engine_mock)

        result = await provider.validate(_make_std())
        assert result is not None

    async def test_store_error_returns_result(self, db: AsyncEngine) -> None:
        """A storage error during _store is swallowed; the validated result is returned."""
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=AsyncMock(return_value=db))

        with patch(
            "address_validator.services.validation.cache_provider._store",
            side_effect=RuntimeError("disk full"),
        ):
            result = await provider.validate(_make_std())

        assert result.validation.status == "confirmed"

    async def test_store_error_does_not_cache(self, db: AsyncEngine) -> None:
        """When _store raises, nothing is written to the DB."""
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=AsyncMock(return_value=db))

        with patch(
            "address_validator.services.validation.cache_provider._store",
            side_effect=RuntimeError("disk full"),
        ):
            await provider.validate(_make_std())

        assert await _count_rows(db, "validated_addresses") == 0

    async def test_lookup_internal_error_fails_open(self, db: AsyncEngine) -> None:
        """A _lookup exception (e.g. corrupt row) fails open — inner provider is called."""
        inner = _make_provider(_make_confirmed_response())
        provider = CachingProvider(inner=inner, get_engine=AsyncMock(return_value=db))

        with patch(
            "address_validator.services.validation.cache_provider._lookup",
            side_effect=RuntimeError("corrupt row"),
        ):
            result = await provider.validate(_make_std())

        inner.validate.assert_awaited_once()
        assert result.validation.status == "confirmed"


class TestTTLExpiry:
    async def test_fresh_entry_within_ttl_is_a_hit(self, db: AsyncEngine) -> None:
        """An entry stored moments ago is not expired; inner is not called on second validate."""
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=AsyncMock(return_value=db), ttl_days=30)
        std = _make_std()

        await provider.validate(std)  # miss — stores
        inner.validate.reset_mock()
        result = await provider.validate(std)  # hit — fresh

        inner.validate.assert_not_awaited()
        assert result.validation.status == "confirmed"

    async def test_expired_entry_treated_as_miss(self, db: AsyncEngine) -> None:
        """An entry older than ttl_days is re-validated via the inner provider."""
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=AsyncMock(return_value=db), ttl_days=30)
        std = _make_std()

        await provider.validate(std)  # miss — stores
        await _backdate_validated_at(db, days_ago=31)
        inner.validate.reset_mock()
        await provider.validate(std)  # expired — should call inner

        inner.validate.assert_awaited_once()

    async def test_expired_entry_refreshes_cache(self, db: AsyncEngine) -> None:
        """After expiry, re-validation refreshes validated_at; the next call is a hit."""
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=AsyncMock(return_value=db), ttl_days=30)
        std = _make_std()

        await provider.validate(std)  # miss — stores, validated_at = now
        await _backdate_validated_at(db, days_ago=31)

        result2 = await provider.validate(std)  # expired → inner called → refreshes validated_at

        inner.validate.reset_mock()
        result3 = await provider.validate(std)  # fresh validated_at → hit

        assert result2.validation.status == "confirmed"
        assert result3.validation.status == "confirmed"
        inner.validate.assert_not_awaited()

    async def test_ttl_zero_disables_expiry(self, db: AsyncEngine) -> None:
        """ttl_days=0 means no expiry regardless of entry age."""
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=AsyncMock(return_value=db), ttl_days=0)
        std = _make_std()

        await provider.validate(std)  # miss — stores
        await _backdate_validated_at(db, days_ago=365)
        inner.validate.reset_mock()
        await provider.validate(std)  # should still be a hit

        inner.validate.assert_not_awaited()

    async def test_one_day_short_of_ttl_is_a_hit(self, db: AsyncEngine) -> None:
        """An entry 29 days old is not expired when ttl_days=30."""
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=AsyncMock(return_value=db), ttl_days=30)
        std = _make_std()

        await provider.validate(std)  # miss — stores
        await _backdate_validated_at(db, days_ago=29)
        inner.validate.reset_mock()
        await provider.validate(std)  # should be a hit

        inner.validate.assert_not_awaited()

    async def test_last_seen_at_still_updated_on_hit(self, db: AsyncEngine) -> None:
        """Cache hits update last_seen_at; validated_at is unchanged."""
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=AsyncMock(return_value=db), ttl_days=30)
        std = _make_std()

        await provider.validate(std)  # miss

        row_before = await _fetch_one(db, "SELECT validated_at FROM validated_addresses")
        validated_at_before = row_before["validated_at"]

        await provider.validate(std)  # hit — should bump last_seen_at, not validated_at

        row_after = await _fetch_one(
            db, "SELECT last_seen_at, validated_at FROM validated_addresses"
        )
        assert row_after["validated_at"] == validated_at_before
        assert row_after["last_seen_at"] >= validated_at_before


class TestKeyHelpers:
    def test_pattern_key_is_deterministic(self) -> None:
        std = _make_std()
        assert _make_pattern_key(std) == _make_pattern_key(std)

    def test_canonical_key_is_deterministic(self) -> None:
        resp = _make_confirmed_response()
        assert _make_canonical_key(resp) == _make_canonical_key(resp)

    def test_different_components_different_pattern_key(self) -> None:
        std1 = _make_std(street_name="MAIN")
        std2 = _make_std(street_name="ELM")
        assert _make_pattern_key(std1) != _make_pattern_key(std2)

    def test_different_address_fields_different_canonical_key(self) -> None:
        resp1 = _make_confirmed_response()
        resp2 = ValidateResponseV1(
            address_line_1="456 ELM ST",
            city="SPRINGFIELD",
            region="IL",
            postal_code="62701",
            country="US",
            validation=ValidationResult(status="confirmed", dpv_match_code="Y", provider="usps"),
        )
        assert _make_canonical_key(resp1) != _make_canonical_key(resp2)
