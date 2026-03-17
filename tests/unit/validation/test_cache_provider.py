"""Unit tests for CachingProvider."""

from unittest.mock import AsyncMock

import aiosqlite
import pytest

from models import ComponentSet, StandardizeResponseV1, ValidateResponseV1, ValidationResult
from services.validation.cache_db import _init_schema
from services.validation.cache_provider import (
    CachingProvider,
    _make_canonical_key,
    _make_pattern_key,
)
from usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def db() -> aiosqlite.Connection:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON")
    await _init_schema(conn)
    yield conn
    await conn.close()


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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestCacheMiss:
    async def test_cache_miss_calls_inner(self, db: aiosqlite.Connection) -> None:
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_db=AsyncMock(return_value=db))
        std = _make_std()

        result = await provider.validate(std)

        inner.validate.assert_awaited_once_with(std)
        assert result.validation.status == "confirmed"

    async def test_miss_stores_pattern(self, db: aiosqlite.Connection) -> None:
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_db=AsyncMock(return_value=db))
        std = _make_std()

        await provider.validate(std)

        pattern_key = _make_pattern_key(std)
        async with db.execute(
            "SELECT * FROM query_patterns WHERE pattern_key = ?", (pattern_key,)
        ) as cur:
            row = await cur.fetchone()
        assert row is not None

    async def test_miss_stores_canonical(self, db: aiosqlite.Connection) -> None:
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_db=AsyncMock(return_value=db))

        await provider.validate(_make_std())

        canonical_key = _make_canonical_key(response)
        async with db.execute(
            "SELECT * FROM validated_addresses WHERE canonical_key = ?", (canonical_key,)
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row["status"] == "confirmed"
        assert row["provider"] == "usps"


class TestCacheHit:
    async def test_hit_skips_inner(self, db: aiosqlite.Connection) -> None:
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_db=AsyncMock(return_value=db))
        std = _make_std()

        await provider.validate(std)         # miss — stores
        inner.validate.reset_mock()
        result = await provider.validate(std)  # hit

        inner.validate.assert_not_awaited()
        assert result.validation.status == "confirmed"

    async def test_response_roundtrip(self, db: aiosqlite.Connection) -> None:
        """All response fields survive the serialize → deserialize round-trip."""
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_db=AsyncMock(return_value=db))
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

    async def test_different_pattern_same_canonical(self, db: aiosqlite.Connection) -> None:
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
        provider = CachingProvider(inner=inner, get_db=AsyncMock(return_value=db))

        await provider.validate(std1)  # miss
        inner.validate.reset_mock()
        await provider.validate(std2)  # miss (different pattern) → store same canonical

        # Both patterns exist
        async with db.execute("SELECT COUNT(*) FROM query_patterns") as cur:
            count = (await cur.fetchone())[0]
        assert count == 2

        # But only one canonical record
        async with db.execute("SELECT COUNT(*) FROM validated_addresses") as cur:
            count = (await cur.fetchone())[0]
        assert count == 1


class TestUnavailableNotCached:
    async def test_unavailable_not_stored(self, db: aiosqlite.Connection) -> None:
        response = _make_unavailable_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_db=AsyncMock(return_value=db))

        await provider.validate(_make_std())

        async with db.execute("SELECT COUNT(*) FROM validated_addresses") as cur:
            count = (await cur.fetchone())[0]
        assert count == 0

    async def test_unavailable_calls_inner_every_time(self, db: aiosqlite.Connection) -> None:
        response = _make_unavailable_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_db=AsyncMock(return_value=db))
        std = _make_std()

        await provider.validate(std)
        await provider.validate(std)

        assert inner.validate.await_count == 2


class TestNotConfirmedCached:
    async def test_not_confirmed_is_stored_and_retrieved(self, db: aiosqlite.Connection) -> None:
        response = _make_not_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_db=AsyncMock(return_value=db))
        std = _make_std()

        await provider.validate(std)
        inner.validate.reset_mock()
        result = await provider.validate(std)

        inner.validate.assert_not_awaited()
        assert result.validation.status == "not_confirmed"


class TestWarnings:
    async def test_provider_warnings_stored_std_warnings_not(
        self, db: aiosqlite.Connection
    ) -> None:
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
        provider = CachingProvider(inner=inner, get_db=AsyncMock(return_value=db))

        std = _make_std()
        std_with_warnings = StandardizeResponseV1(
            **{**std.model_dump(), "warnings": ["standardize: truncated"]}
        )

        await provider.validate(std_with_warnings)
        result = await provider.validate(std_with_warnings)

        # Cached result carries provider warnings only
        assert result.warnings == ["provider: inferred city"]


class TestLatLng:
    async def test_lat_lng_roundtrip(self, db: aiosqlite.Connection) -> None:
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
        provider = CachingProvider(inner=inner, get_db=AsyncMock(return_value=db))
        std = _make_std()

        await provider.validate(std)
        result = await provider.validate(std)

        assert result.latitude == pytest.approx(39.7817)
        assert result.longitude == pytest.approx(-89.6501)


class TestDbErrorPropagates:
    async def test_lookup_db_error_propagates(self) -> None:
        """An exception from the DB on lookup is not swallowed."""
        get_db_mock = AsyncMock(side_effect=RuntimeError("db unavailable"))
        inner = _make_provider(_make_confirmed_response())
        provider = CachingProvider(inner=inner, get_db=get_db_mock)

        with pytest.raises(RuntimeError, match="db unavailable"):
            await provider.validate(_make_std())


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
