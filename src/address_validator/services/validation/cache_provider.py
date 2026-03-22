"""CachingProvider — ValidationProvider wrapper backed by the PostgreSQL validation cache.

Lookup algorithm
----------------
1. Hash the standardised input components → ``pattern_key``
2. SELECT from ``query_patterns`` WHERE ``pattern_key = $1``
3. HIT  → fetch the linked ``validated_addresses`` row
   a. TTL check: if ``ttl_days > 0`` and ``validated_at`` older than threshold → treat as miss
   b. Update ``last_seen_at``; return deserialised row
4. MISS → delegate to ``inner.validate(std)``

Store algorithm
---------------
1. Skip entirely when ``result.validation.status == "unavailable"``
2. Hash the provider-returned address fields → ``canonical_key``
3. INSERT/upsert into ``validated_addresses`` (ON CONFLICT: update last_seen_at and validated_at)
4. INSERT INTO query_patterns … ON CONFLICT DO NOTHING

The parse → standardise pipeline already normalises casing, abbreviations, and
whitespace before this module is called, so ``pattern_key`` naturally collapses
equivalent inputs to the same hash.
"""

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncEngine

from address_validator.models import (
    ComponentSet,
    StandardizeResponseV1,
    ValidateResponseV1,
    ValidationResult,
)
from address_validator.services.audit import set_audit_context
from address_validator.services.validation.protocol import ValidationProvider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


def _make_pattern_key(std: StandardizeResponseV1) -> str:
    """SHA-256 of the sorted standardised component values + country.

    Sorting the dict eliminates key-insertion-order non-determinism.
    Country is included to guard against cross-country collisions.
    """
    payload = json.dumps(
        {
            "country": std.country,
            "components": dict(sorted(std.components.values.items())),
        },
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _make_canonical_key(result: ValidateResponseV1) -> str:
    """SHA-256 of the provider-returned address fields.

    For ``not_confirmed`` results all address fields are empty, so all
    unconfirmed results for a given country collapse to one canonical record —
    the provider returned no corrected address, which is the correct degenerate.
    """
    payload = json.dumps(
        {
            "address_line_1": result.address_line_1 or "",
            "address_line_2": result.address_line_2 or "",
            "city": result.city or "",
            "region": result.region or "",
            "postal_code": result.postal_code or "",
            "country": result.country,
        },
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _now_utc() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Row deserialisation
# ---------------------------------------------------------------------------


def _row_to_response(row: RowMapping) -> ValidateResponseV1:
    components: ComponentSet | None = None
    if row["components_json"]:
        components = ComponentSet.model_validate_json(row["components_json"])

    warnings: list[str] = json.loads(row["warnings_json"])

    return ValidateResponseV1(
        address_line_1=row["address_line_1"],
        address_line_2=row["address_line_2"],
        city=row["city"],
        region=row["region"],
        postal_code=row["postal_code"],
        country=row["country"],
        validated=row["validated"],
        components=components,
        validation=ValidationResult(
            status=row["status"],
            dpv_match_code=row["dpv_match_code"],
            provider=row["provider"] or None,
        ),
        latitude=row["latitude"],
        longitude=row["longitude"],
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# DB operations
# ---------------------------------------------------------------------------


async def _lookup(
    engine: AsyncEngine,
    pattern_key: str,
    ttl_days: int,
) -> ValidateResponseV1 | None:
    async with engine.connect() as conn:
        qp_row = (
            (
                await conn.execute(
                    text("SELECT canonical_key FROM query_patterns WHERE pattern_key = :pk"),
                    {"pk": pattern_key},
                )
            )
            .mappings()
            .fetchone()
        )

        if qp_row is None:
            logger.debug("cache_lookup: miss pattern_key=%s", pattern_key)
            return None

        canonical_key: str = qp_row["canonical_key"]

        va_row = (
            (
                await conn.execute(
                    text("SELECT * FROM validated_addresses WHERE canonical_key = :ck"),
                    {"ck": canonical_key},
                )
            )
            .mappings()
            .fetchone()
        )

        if va_row is None:
            # Orphaned pattern — treat as miss.
            logger.debug(
                "cache_lookup: orphaned pattern_key=%s canonical_key=%s; treating as miss",
                pattern_key,
                canonical_key,
            )
            async with engine.begin() as wconn:
                await wconn.execute(
                    text("DELETE FROM query_patterns WHERE pattern_key = :pk"),
                    {"pk": pattern_key},
                )
            return None

        if ttl_days:
            cutoff = datetime.now(UTC) - timedelta(days=ttl_days)
            validated_at = va_row["validated_at"] or va_row["created_at"]
            if validated_at < cutoff:
                logger.debug(
                    "cache_lookup: expired pattern_key=%s canonical_key=%s validated_at=%s",
                    pattern_key,
                    canonical_key,
                    validated_at,
                )
                return None

    async with engine.begin() as wconn:
        await wconn.execute(
            text("UPDATE validated_addresses SET last_seen_at = :ts WHERE canonical_key = :ck"),
            {"ts": _now_utc(), "ck": canonical_key},
        )

    logger.debug(
        "cache_lookup: hit pattern_key=%s canonical_key=%s",
        pattern_key,
        canonical_key,
    )
    return _row_to_response(va_row)


async def _store(
    engine: AsyncEngine,
    pattern_key: str,
    canonical_key: str,
    result: ValidateResponseV1,
) -> None:
    now = _now_utc()
    components_json: str | None = result.components.model_dump_json() if result.components else None
    warnings_json = json.dumps(result.warnings)

    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO validated_addresses
                    (canonical_key, provider, status, dpv_match_code,
                     address_line_1, address_line_2, city, region, postal_code, country,
                     validated, components_json, latitude, longitude,
                     warnings_json, created_at, last_seen_at, validated_at)
                VALUES
                    (:canonical_key, :provider, :status, :dpv_match_code,
                     :address_line_1, :address_line_2, :city, :region, :postal_code, :country,
                     :validated, :components_json, :latitude, :longitude,
                     :warnings_json, :created_at, :last_seen_at, :validated_at)
                ON CONFLICT (canonical_key) DO UPDATE SET
                    last_seen_at = EXCLUDED.last_seen_at,
                    validated_at = EXCLUDED.validated_at
                """
            ),
            {
                "canonical_key": canonical_key,
                "provider": result.validation.provider or "",
                "status": result.validation.status,
                "dpv_match_code": result.validation.dpv_match_code,
                "address_line_1": result.address_line_1,
                "address_line_2": result.address_line_2,
                "city": result.city,
                "region": result.region,
                "postal_code": result.postal_code,
                "country": result.country,
                "validated": result.validated,
                "components_json": components_json,
                "latitude": result.latitude,
                "longitude": result.longitude,
                "warnings_json": warnings_json,
                "created_at": now,
                "last_seen_at": now,
                "validated_at": now,
            },
        )

        await conn.execute(
            text(
                """
                INSERT INTO query_patterns (pattern_key, canonical_key, created_at)
                VALUES (:pattern_key, :canonical_key, :created_at)
                ON CONFLICT (pattern_key) DO NOTHING
                """
            ),
            {"pattern_key": pattern_key, "canonical_key": canonical_key, "created_at": now},
        )

    logger.debug(
        "cache_store: pattern_key=%s canonical_key=%s status=%s",
        pattern_key,
        canonical_key,
        result.validation.status,
    )


# ---------------------------------------------------------------------------
# CachingProvider
# ---------------------------------------------------------------------------


class CachingProvider:
    """Caching wrapper that implements the :class:`ValidationProvider` protocol.

    Intercepts calls to ``validate()``, checks the PostgreSQL validation cache,
    and falls through to ``inner`` only on a miss.  Results are stored after
    every successful provider call (``status != "unavailable"``).

    Cache errors (connection failures, query errors) are handled with a
    fail-open policy: on a lookup error the request is forwarded to the inner
    provider; on a store error the validated result is still returned to the
    caller.  The cache is advisory — its unavailability must never surface as
    a request failure.

    The ``get_engine`` callable is injected rather than imported directly so
    that tests can supply an isolated engine without touching the module global.
    """

    def __init__(
        self,
        inner: ValidationProvider,
        get_engine: Callable[[], AsyncEngine],
        ttl_days: int = 30,
    ) -> None:
        self._inner = inner
        self._get_engine = get_engine
        self._ttl_days = ttl_days

    async def validate(self, std: StandardizeResponseV1) -> ValidateResponseV1:
        """Check the cache; delegate to inner provider on miss; store the result.

        Fail-open: any database error during lookup or store is logged as a
        warning and the request continues without the cache.
        """
        pattern_key = _make_pattern_key(std)
        engine: AsyncEngine | None = None

        try:
            engine = self._get_engine()
            cached = await _lookup(engine, pattern_key, self._ttl_days)
        except Exception:
            logger.warning("cache_lookup: storage error — failing open", exc_info=True)
            cached = None

        if cached is not None:
            set_audit_context(
                provider=cached.validation.provider,
                validation_status=cached.validation.status,
                cache_hit=True,
            )
            return cached

        result: ValidateResponseV1 = await self._inner.validate(std)

        set_audit_context(
            provider=result.validation.provider,
            validation_status=result.validation.status,
            cache_hit=False,
        )

        if result.validation.status == "unavailable":
            logger.debug(
                "cache_store: skip provider=%s status=unavailable",
                result.validation.provider,
            )
            return result

        if engine is not None:
            try:
                canonical_key = _make_canonical_key(result)
                await _store(engine, pattern_key, canonical_key, result)
            except Exception:
                logger.warning("cache_store: storage error — result not cached", exc_info=True)

        return result
