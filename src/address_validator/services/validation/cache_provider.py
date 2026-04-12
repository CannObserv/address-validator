"""CachingProvider — ValidationProvider wrapper backed by the PostgreSQL validation cache.

Lookup algorithm
----------------
1. Hash the standardised input components → ``pattern_key``
2. SELECT from ``query_patterns`` WHERE ``pattern_key = $1``
   a. Row missing → miss
   b. Row found, ``canonical_key`` IS NULL → partial registration (rate-limited request
      registered raw_input before the provider was called); treat as miss without deleting
   c. Row found, ``canonical_key`` IS NOT NULL but no matching ``validated_addresses`` row
      → orphaned pointer (external DB modification); delete and treat as miss
3. HIT  → fetch the linked ``validated_addresses`` row
   a. TTL check: if ``ttl_days > 0`` and ``validated_at`` older than threshold → treat as miss
   b. Update ``last_seen_at``; return deserialised row
4. MISS → delegate to ``inner.validate(std)``

Cache-miss path (before inner provider call)
--------------------------------------------
1. Set ``pattern_key`` in audit ContextVar so the audit row carries it even if the
   provider raises (e.g. rate-limited 429)
2. INSERT into ``query_patterns`` (``canonical_key`` NULL, ``raw_input`` set) so that
   rate-limited audit rows can join to find raw input — ``_register_query_pattern()``

Store algorithm (after successful inner provider call)
------------------------------------------------------
1. Skip entirely when ``result.validation.status == "unavailable"``
2. Hash the provider-returned address fields → ``canonical_key``
3. INSERT/upsert into ``validated_addresses`` (ON CONFLICT: update last_seen_at and validated_at)
4. INSERT/upsert into ``query_patterns`` ON CONFLICT: back-fill ``canonical_key`` when NULL
   (set by the prior eager registration) and back-fill ``raw_input`` when NULL

The parse → standardise pipeline already normalises casing, abbreviations, and
whitespace before this module is called, so ``pattern_key`` naturally collapses
equivalent inputs to the same hash.
"""

import hashlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import RowMapping, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from address_validator.db.tables import query_patterns, validated_addresses
from address_validator.models import (
    ComponentSet,
    StandardizedAddress,
    ValidateResponseV1,
    ValidationResult,
)
from address_validator.services.audit import set_audit_context
from address_validator.services.validation.protocol import ValidationProvider

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------


def _make_pattern_key(std: StandardizedAddress) -> str:
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
        components = ComponentSet.model_validate(row["components_json"])

    warnings: list[str] = row["warnings_json"]

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
            provider=row["provider"],
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
                    select(query_patterns.c.canonical_key).where(
                        query_patterns.c.pattern_key == pattern_key
                    ),
                )
            )
            .mappings()
            .fetchone()
        )

        if qp_row is None:
            logger.debug("cache_lookup: miss pattern_key=%s", pattern_key)
            return None

        canonical_key: str | None = qp_row["canonical_key"]

        if canonical_key is None:
            # Partial registration: _register_query_pattern created this row
            # before the inner provider was called (e.g. request was rate-limited).
            # canonical_key not yet set — treat as a miss without deleting the row.
            logger.debug("cache_lookup: miss pattern_key=%s canonical_key=NULL", pattern_key)
            return None

        va_row = (
            (
                await conn.execute(
                    select(validated_addresses).where(
                        validated_addresses.c.canonical_key == canonical_key
                    ),
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
                    delete(query_patterns).where(query_patterns.c.pattern_key == pattern_key),
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
            update(validated_addresses)
            .where(validated_addresses.c.canonical_key == canonical_key)
            .values(last_seen_at=_now_utc()),
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
    *,
    raw_input: str | None,
) -> None:
    now = _now_utc()
    components_json = result.components.model_dump(mode="python") if result.components else None
    warnings_json = result.warnings

    async with engine.begin() as conn:
        await conn.execute(
            pg_insert(validated_addresses)
            .values(
                canonical_key=canonical_key,
                provider=result.validation.provider,
                status=result.validation.status,
                dpv_match_code=result.validation.dpv_match_code,
                address_line_1=result.address_line_1,
                address_line_2=result.address_line_2,
                city=result.city,
                region=result.region,
                postal_code=result.postal_code,
                country=result.country,
                validated=result.validated,
                components_json=components_json,
                latitude=result.latitude,
                longitude=result.longitude,
                warnings_json=warnings_json,
                created_at=now,
                last_seen_at=now,
                validated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[validated_addresses.c.canonical_key],
                set_={"last_seen_at": now, "validated_at": now},
            ),
        )

        qp_insert = pg_insert(query_patterns).values(
            pattern_key=pattern_key,
            canonical_key=canonical_key,
            created_at=now,
            raw_input=raw_input,
        )
        await conn.execute(
            qp_insert.on_conflict_do_update(
                index_elements=[query_patterns.c.pattern_key],
                set_={
                    # Back-fill canonical_key when a prior rate-limited request
                    # registered the pattern with canonical_key=NULL.
                    "canonical_key": func.coalesce(
                        query_patterns.c.canonical_key,
                        qp_insert.excluded.canonical_key,
                    ),
                    "raw_input": func.coalesce(
                        query_patterns.c.raw_input,
                        qp_insert.excluded.raw_input,
                    ),
                },
            ),
        )

    logger.debug(
        "cache_store: pattern_key=%s canonical_key=%s status=%s",
        pattern_key,
        canonical_key,
        result.validation.status,
    )


async def _register_query_pattern(
    engine: AsyncEngine,
    pattern_key: str,
    raw_input: str | None,
) -> None:
    """Eagerly register a query pattern before the inner provider is called.

    Inserts a query_patterns row with canonical_key=NULL so that rate-limited
    requests still produce a joinable row in the audit view.  _store() will
    back-fill canonical_key via ON CONFLICT when validation later succeeds.
    """
    now = _now_utc()
    qp_insert = pg_insert(query_patterns).values(
        pattern_key=pattern_key,
        canonical_key=None,
        created_at=now,
        raw_input=raw_input,
    )
    async with engine.begin() as conn:
        await conn.execute(
            qp_insert.on_conflict_do_update(
                index_elements=[query_patterns.c.pattern_key],
                set_={
                    # canonical_key intentionally absent: if this conflicts against a
                    # row that already has canonical_key set (i.e. a prior successful
                    # validation), we must not overwrite it with NULL.
                    "raw_input": func.coalesce(
                        query_patterns.c.raw_input,
                        qp_insert.excluded.raw_input,
                    ),
                },
            ),
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

    @property
    def supports_non_us(self) -> bool:
        """Delegate to the inner provider."""
        return self._inner.supports_non_us

    async def validate(
        self, std: StandardizedAddress, *, raw_input: str | None = None
    ) -> ValidateResponseV1:
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
                pattern_key=pattern_key,
            )
            logger.info(
                "validate: provider=%s status=%s cache_hit=true",
                cached.validation.provider,
                cached.validation.status,
            )
            return cached

        # Set pattern_key before calling the inner provider so the audit row
        # carries it even when the provider raises (e.g. rate-limited 429).
        set_audit_context(pattern_key=pattern_key)

        # Register the query pattern eagerly so rate-limited requests still
        # produce a joinable row (with raw_input) in the admin audit view.
        # canonical_key is NULL until _store() fills it in on success.
        if engine is not None:
            try:
                await _register_query_pattern(engine, pattern_key, raw_input)
            except Exception:
                logger.warning("cache_register: storage error — continuing", exc_info=True)

        result: ValidateResponseV1 = await self._inner.validate(std, raw_input=raw_input)

        set_audit_context(
            provider=result.validation.provider,
            validation_status=result.validation.status,
            cache_hit=False,
        )

        logger.info(
            "validate: provider=%s status=%s cache_hit=false",
            result.validation.provider,
            result.validation.status,
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
                await _store(engine, pattern_key, canonical_key, result, raw_input=raw_input)
            except Exception:
                logger.warning("cache_store: storage error — result not cached", exc_info=True)

        return result
