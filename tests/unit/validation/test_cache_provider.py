"""Unit tests for CachingProvider."""

import logging
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from address_validator.db.tables import query_patterns, validated_addresses
from address_validator.models import (
    ComponentSet,
    StandardizeResponseV1,
    ValidateResponseV1,
    ValidationResult,
)
from address_validator.services.audit import get_audit_pattern_key, reset_audit_context
from address_validator.services.validation.cache_provider import (
    CachingProvider,
    _lookup,
    _make_canonical_key,
    _make_pattern_key,
    _register_query_pattern,
    _store,
)
from address_validator.services.validation.errors import ProviderRateLimitedError
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
        "premise_number": address_number,
        "thoroughfare_name": street_name,
        "thoroughfare_trailing_type": street_type,
        "locality": city,
        "administrative_area": region,
        "postcode": postal_code,
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
            values={"premise_number": "123", "thoroughfare_name": "MAIN"},
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
        await conn.execute(update(validated_addresses).values(validated_at=ts))


_TABLE_MAP = {
    "validated_addresses": validated_addresses,
    "query_patterns": query_patterns,
}


async def _count_rows(engine: AsyncEngine, table: str) -> int:
    t = _TABLE_MAP.get(table)
    if t is None:
        raise ValueError(f"unknown table: {table!r}")
    async with engine.connect() as conn:
        return (await conn.execute(select(func.count()).select_from(t))).scalar()


async def _fetch_one(engine: AsyncEngine, table, *where):
    """Fetch one row from a Core Table with optional WHERE clauses."""
    async with engine.connect() as conn:
        stmt = select(table).where(*where) if where else select(table)
        return (await conn.execute(stmt)).mappings().fetchone()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSupportsNonUs:
    def test_delegates_to_inner_true(self) -> None:
        inner = AsyncMock()
        inner.supports_non_us = True
        provider = CachingProvider(inner=inner, get_engine=MagicMock())
        assert provider.supports_non_us is True

    def test_delegates_to_inner_false(self) -> None:
        inner = AsyncMock()
        inner.supports_non_us = False
        provider = CachingProvider(inner=inner, get_engine=MagicMock())
        assert provider.supports_non_us is False


class TestCacheMiss:
    async def test_cache_miss_calls_inner(self, db: AsyncEngine) -> None:
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))
        std = _make_std()

        result = await provider.validate(std)

        inner.validate.assert_awaited_once_with(std, raw_input=None)
        assert result.validation.status == "confirmed"

    async def test_miss_stores_pattern(self, db: AsyncEngine) -> None:
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))
        std = _make_std()

        await provider.validate(std)

        pattern_key = _make_pattern_key(std)
        row = await _fetch_one(db, query_patterns, query_patterns.c.pattern_key == pattern_key)
        assert row is not None

    async def test_miss_stores_canonical(self, db: AsyncEngine) -> None:
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))

        await provider.validate(_make_std())

        canonical_key = _make_canonical_key(response)
        row = await _fetch_one(
            db, validated_addresses, validated_addresses.c.canonical_key == canonical_key
        )
        assert row is not None
        assert row["status"] == "confirmed"
        assert row["provider"] == "usps"


class TestCacheHit:
    async def test_hit_skips_inner(self, db: AsyncEngine) -> None:
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))
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
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))
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

        # std2 has extra postcode component — different pattern_key, same canonical result
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
                values={"premise_number": "123", "thoroughfare_name": "MAIN", "postcode": "62701"},
            ),
            warnings=[],
        )

        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))

        await provider.validate(std1)  # miss
        inner.validate.reset_mock()
        await provider.validate(std2)  # miss (different pattern) → store same canonical

        assert await _count_rows(db, "query_patterns") == 2
        assert await _count_rows(db, "validated_addresses") == 1


class TestUnavailableNotCached:
    async def test_unavailable_not_stored(self, db: AsyncEngine) -> None:
        response = _make_unavailable_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))

        await provider.validate(_make_std())

        assert await _count_rows(db, "validated_addresses") == 0

    async def test_unavailable_calls_inner_every_time(self, db: AsyncEngine) -> None:
        response = _make_unavailable_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))
        std = _make_std()

        await provider.validate(std)
        await provider.validate(std)

        assert inner.validate.await_count == 2


class TestNotConfirmedCached:
    async def test_not_confirmed_is_stored_and_retrieved(self, db: AsyncEngine) -> None:
        response = _make_not_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))
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
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))

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
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))
        std = _make_std()

        await provider.validate(std)
        result = await provider.validate(std)

        assert result.latitude == pytest.approx(39.7817)
        assert result.longitude == pytest.approx(-89.6501)


class TestFailOpen:
    async def test_lookup_db_error_calls_inner(self) -> None:
        """A DB error on lookup fails open — inner provider is called instead of raising."""
        get_engine_mock = MagicMock(side_effect=RuntimeError("db unavailable"))
        inner = _make_provider(_make_confirmed_response())
        provider = CachingProvider(inner=inner, get_engine=get_engine_mock)

        result = await provider.validate(_make_std())

        inner.validate.assert_awaited_once()
        assert result.validation.status == "confirmed"

    async def test_lookup_db_error_does_not_raise(self) -> None:
        """A DB error on lookup is swallowed; the response is returned normally."""
        get_engine_mock = MagicMock(side_effect=OSError("disk full"))
        inner = _make_provider(_make_confirmed_response())
        provider = CachingProvider(inner=inner, get_engine=get_engine_mock)

        result = await provider.validate(_make_std())
        assert result is not None

    async def test_store_error_returns_result(self, db: AsyncEngine) -> None:
        """A storage error during _store is swallowed; the validated result is returned."""
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))

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
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))

        with patch(
            "address_validator.services.validation.cache_provider._store",
            side_effect=RuntimeError("disk full"),
        ):
            await provider.validate(_make_std())

        assert await _count_rows(db, "validated_addresses") == 0

    async def test_lookup_null_canonical_key_is_a_miss(self, db: AsyncEngine) -> None:
        """_lookup treats a NULL canonical_key row as a cache miss, not an orphan.

        _register_query_pattern creates rows with canonical_key=NULL before the
        inner provider is called.  _lookup must not misidentify these as orphaned
        rows and must return None (miss) without deleting the row.
        """
        std = _make_std()
        pattern_key = _make_pattern_key(std)
        await _register_query_pattern(db, pattern_key, raw_input="123 Main St")

        result = await _lookup(db, pattern_key, ttl_days=30)

        assert result is None

    async def test_lookup_null_canonical_key_does_not_delete_row(self, db: AsyncEngine) -> None:
        """A NULL canonical_key row is preserved across a _lookup call.

        The row must survive so that _store() can back-fill canonical_key when
        the provider later succeeds, and so the admin audit view keeps raw_input.
        """
        std = _make_std()
        pattern_key = _make_pattern_key(std)
        await _register_query_pattern(db, pattern_key, raw_input="123 Main St")

        await _lookup(db, pattern_key, ttl_days=30)

        row = await _fetch_one(db, query_patterns, query_patterns.c.pattern_key == pattern_key)
        assert row is not None
        assert row["raw_input"] == "123 Main St"

    async def test_lookup_internal_error_fails_open(self, db: AsyncEngine) -> None:
        """A _lookup exception (e.g. corrupt row) fails open — inner provider is called."""
        inner = _make_provider(_make_confirmed_response())
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))

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
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db), ttl_days=30)
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
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db), ttl_days=30)
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
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db), ttl_days=30)
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
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db), ttl_days=0)
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
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db), ttl_days=30)
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
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db), ttl_days=30)
        std = _make_std()

        await provider.validate(std)  # miss

        row_before = await _fetch_one(db, validated_addresses)
        validated_at_before = row_before["validated_at"]

        await provider.validate(std)  # hit — should bump last_seen_at, not validated_at

        row_after = await _fetch_one(db, validated_addresses)
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


_CACHE_LOGGER = "address_validator.services.validation.cache_provider"


class TestValidateInfoLog:
    async def test_cache_hit_logs_info(
        self, db: AsyncEngine, caplog: pytest.LogCaptureFixture
    ) -> None:
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))
        std = _make_std()

        await provider.validate(std)  # miss — stores
        with caplog.at_level(logging.INFO, logger=_CACHE_LOGGER):
            await provider.validate(std)  # hit

        assert any(
            "provider=usps" in r.message and "cache_hit=true" in r.message for r in caplog.records
        )

    async def test_cache_miss_logs_info(
        self, db: AsyncEngine, caplog: pytest.LogCaptureFixture
    ) -> None:
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))

        with caplog.at_level(logging.INFO, logger=_CACHE_LOGGER):
            await provider.validate(_make_std())

        assert any(
            "provider=usps" in r.message and "cache_hit=false" in r.message for r in caplog.records
        )

    async def test_unavailable_logs_info(
        self, db: AsyncEngine, caplog: pytest.LogCaptureFixture
    ) -> None:
        response = _make_unavailable_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))

        with caplog.at_level(logging.INFO, logger=_CACHE_LOGGER):
            await provider.validate(_make_std())

        assert any(
            "status=unavailable" in r.message and "cache_hit=false" in r.message
            for r in caplog.records
        )


class TestRawInput:
    async def test_raw_input_stored_on_cache_miss(self, db: AsyncEngine) -> None:
        """raw_input is written to query_patterns on the first (miss) call."""
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))
        std = _make_std()

        await provider.validate(std, raw_input="123 Main St, Springfield IL 62701")

        pattern_key = _make_pattern_key(std)
        row = await _fetch_one(db, query_patterns, query_patterns.c.pattern_key == pattern_key)
        assert row is not None
        assert row["raw_input"] == "123 Main St, Springfield IL 62701"

    async def test_raw_input_none_stored_when_not_provided(self, db: AsyncEngine) -> None:
        """raw_input is NULL when not supplied (e.g. called without the kwarg)."""
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))

        await provider.validate(_make_std())

        row = await _fetch_one(db, query_patterns)
        assert row["raw_input"] is None

    async def test_raw_input_registered_on_rate_limit(self, db: AsyncEngine) -> None:
        """raw_input is written to query_patterns even when inner raises ProviderRateLimitedError.

        The query_patterns row is created eagerly before the inner provider is
        called, so rate-limited audit rows can join to find the raw input.
        canonical_key is NULL at this stage — _store fills it in on success.
        """
        inner = AsyncMock()
        inner.validate = AsyncMock(side_effect=ProviderRateLimitedError("usps"))
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))
        std = _make_std()

        with pytest.raises(ProviderRateLimitedError):
            await provider.validate(std, raw_input="123 Main St, Springfield IL 62701")

        pattern_key = _make_pattern_key(std)
        row = await _fetch_one(db, query_patterns, query_patterns.c.pattern_key == pattern_key)
        assert row is not None
        assert row["raw_input"] == "123 Main St, Springfield IL 62701"
        assert row["canonical_key"] is None

    async def test_canonical_key_backfilled_on_subsequent_success(self, db: AsyncEngine) -> None:
        """After a rate-limited registration, a successful validate fills in canonical_key.

        The first request is rate-limited — query_patterns row exists with
        canonical_key=NULL.  The second request succeeds; _store must update
        canonical_key via ON CONFLICT so the cache is fully usable afterwards.
        """
        response = _make_confirmed_response()
        std = _make_std()

        # First call: rate-limited — registers pattern with NULL canonical_key
        inner_rl = AsyncMock()
        inner_rl.validate = AsyncMock(side_effect=ProviderRateLimitedError("usps"))
        provider_rl = CachingProvider(inner=inner_rl, get_engine=MagicMock(return_value=db))
        with pytest.raises(ProviderRateLimitedError):
            await provider_rl.validate(std, raw_input="123 Main St")

        # Verify the partial row exists with NULL canonical_key before the success
        pattern_key = _make_pattern_key(std)
        partial = await _fetch_one(db, query_patterns, query_patterns.c.pattern_key == pattern_key)
        assert partial is not None, "rate-limited call should register a query_patterns row"
        assert partial["canonical_key"] is None

        # Second call: success — should back-fill canonical_key
        inner_ok = _make_provider(response)
        provider_ok = CachingProvider(inner=inner_ok, get_engine=MagicMock(return_value=db))
        await provider_ok.validate(std, raw_input="123 Main St")

        pattern_key = _make_pattern_key(std)
        row = await _fetch_one(db, query_patterns, query_patterns.c.pattern_key == pattern_key)
        assert row["canonical_key"] == _make_canonical_key(response)
        assert row["raw_input"] == "123 Main St"

    async def test_raw_input_backfilled_when_initially_null(self, db: AsyncEngine) -> None:
        """If raw_input is NULL on first store, a later call with raw_input fills it in."""
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))
        std = _make_std()

        # First call — no raw_input (NULL stored)
        await provider.validate(std, raw_input=None)

        # Second call — same std, now with raw_input
        # Need a fresh inner since the cache will HIT on second call via CachingProvider
        # Instead, call _store directly with the same pattern_key
        pattern_key = _make_pattern_key(std)
        canonical_key = _make_canonical_key(response)
        await _store(db, pattern_key, canonical_key, response, raw_input="456 Elm Ave")

        row = await _fetch_one(db, query_patterns, query_patterns.c.pattern_key == pattern_key)
        assert row["raw_input"] == "456 Elm Ave"


class TestPatternKeyContextVar:
    async def test_pattern_key_set_on_cache_miss(self, db: AsyncEngine) -> None:
        """pattern_key ContextVar is set after a successful cache store."""
        reset_audit_context()

        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))
        std = _make_std()

        await provider.validate(std)

        expected = _make_pattern_key(std)
        assert get_audit_pattern_key() == expected
        reset_audit_context()

    async def test_pattern_key_set_on_cache_hit(self, db: AsyncEngine) -> None:
        """pattern_key ContextVar is set on a cache hit."""
        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))
        std = _make_std()

        await provider.validate(std)  # miss — stores
        reset_audit_context()

        await provider.validate(std)  # hit

        expected = _make_pattern_key(std)
        assert get_audit_pattern_key() == expected
        reset_audit_context()

    async def test_pattern_key_set_even_on_store_failure(self, db: AsyncEngine) -> None:
        """pattern_key ContextVar is set even when _store raises (fail-open).

        The pattern_key is a deterministic hash of the standardized input and
        is computed before the store attempt.  The audit row should always
        carry it so the query_patterns join works for cache-miss rows too.
        """
        reset_audit_context()

        response = _make_confirmed_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))

        with patch(
            "address_validator.services.validation.cache_provider._store",
            side_effect=RuntimeError("disk full"),
        ):
            await provider.validate(_make_std())

        assert get_audit_pattern_key() is not None
        reset_audit_context()

    async def test_pattern_key_set_for_unavailable(self, db: AsyncEngine) -> None:
        """pattern_key is set even when status is unavailable (no cache store).

        The pattern_key identifies the input query, not the outcome — the
        audit row should carry it regardless of validation status.
        """
        reset_audit_context()

        response = _make_unavailable_response()
        inner = _make_provider(response)
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))

        await provider.validate(_make_std())

        assert get_audit_pattern_key() is not None
        reset_audit_context()

    async def test_pattern_key_set_on_rate_limit(self, db: AsyncEngine) -> None:
        """pattern_key ContextVar is set even when inner raises ProviderRateLimitedError.

        The exception propagates (callers must handle it) but the audit row
        must carry the pattern_key so the query_patterns join works for 429 rows.
        """
        reset_audit_context()

        inner = AsyncMock()
        inner.validate = AsyncMock(side_effect=ProviderRateLimitedError("usps"))
        provider = CachingProvider(inner=inner, get_engine=MagicMock(return_value=db))
        std = _make_std()

        with pytest.raises(ProviderRateLimitedError):
            await provider.validate(std)

        assert get_audit_pattern_key() == _make_pattern_key(std)
        reset_audit_context()
