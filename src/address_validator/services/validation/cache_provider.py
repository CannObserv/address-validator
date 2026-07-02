"""CachingProvider — ValidationProvider wrapper backed by the PostgreSQL validation cache.

Lookup algorithm
----------------
1. Hash the standardised input components → ``pattern_key``
2. SELECT from ``query_patterns`` WHERE ``pattern_key = $1``
   a. Row missing → miss
   b. Row found but no matching ``validated_addresses`` row → orphaned pointer
      (external DB modification); delete and treat as miss
3. HIT  → fetch the linked ``validated_addresses`` row
   a. Pipeline-version check (#145): if ``pipeline_version`` differs from the
      current ``core.pipeline_version.get_pipeline_version()`` (NULL always
      differs) → treat as miss; the row is lazily re-validated and either
      rescued by the upsert (same canonical output → fresh stamp) or left for
      the #144 TTL sweeper to reap (``validated_at`` stops advancing)
   b. TTL check: if ``ttl_days > 0`` and ``validated_at`` older than threshold → treat as miss
   c. Update ``last_seen_at``; return deserialised row
4. MISS → delegate to ``inner.validate(std)``

Cache-miss path (before inner provider call)
--------------------------------------------
Set ``pattern_key`` and ``raw_input`` in the audit ContextVar so the audit row carries
them even if the provider raises (e.g. rate-limited 429). ``raw_input`` is denormalized
onto ``audit_log`` (#147), so no ``query_patterns`` row is needed for rate-limited
requests to surface raw input in the admin audit view.

Store algorithm (after successful inner provider call)
------------------------------------------------------
1. Skip entirely when ``result.validation.status == "unavailable"``
2. Hash the provider-returned address fields → ``canonical_key``
3. INSERT/upsert into ``validated_addresses`` (ON CONFLICT: update last_seen_at,
   validated_at, and pipeline_version — the stamp refresh rescues stale rows)
4. INSERT/upsert into ``query_patterns`` ON CONFLICT: repoint ``canonical_key`` to the
   freshly validated address (latest-wins) and back-fill ``raw_input`` when NULL.
   A ``query_patterns`` row is only ever written here, on a successful validation, and
   is always born with a non-NULL ``canonical_key`` (enforced NOT NULL, migration 018).

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

from address_validator.core.pipeline_version import get_pipeline_version
from address_validator.db.tables import query_patterns, validated_addresses
from address_validator.models import (
    ComponentSet,
    StandardizedAddress,
    ValidateResponseV2,
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


def _make_canonical_key(result: ValidateResponseV2) -> str:
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


def _row_to_response(row: RowMapping) -> ValidateResponseV2:
    components: ComponentSet | None = None
    if row["components_json"]:
        components = ComponentSet.model_validate(row["components_json"])

    warnings: list[str] = row["warnings_json"]

    return ValidateResponseV2(
        address_line_1=row["address_line_1"] or "",
        address_line_2=row["address_line_2"] or "",
        city=row["city"] or "",
        region=row["region"] or "",
        postal_code=row["postal_code"] or "",
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
) -> ValidateResponseV2 | None:
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

        canonical_key: str = qp_row["canonical_key"]

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

        # Pipeline-version check (#145) before the TTL check and before the
        # last_seen_at touch: a stale-stamped row is a miss, and must not look
        # recently-used to the sweeper. NULL never matches — pre-#145 rows that
        # were not backfilled re-validate lazily.
        current_version = get_pipeline_version()
        if va_row["pipeline_version"] != current_version:
            # INFO, not DEBUG: prod runs at INFO and this is the only signal that
            # distinguishes an expected post-bump invalidation wave from a cache
            # regression. Keys are hashes and versions are constants — no PII.
            logger.info(
                "cache_lookup: version_mismatch pattern_key=%s canonical_key=%s "
                "row_version=%s current_version=%s; treating as miss",
                pattern_key,
                canonical_key,
                va_row["pipeline_version"],
                current_version,
            )
            return None

        # Non-positive ttl_days disables expiry entirely (matches sweep_cache.py,
        # which skips sweeping when ttl_days <= 0). A negative value must not be
        # read as a future cutoff that expires every row.
        if ttl_days > 0:
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
    result: ValidateResponseV2,
    *,
    raw_input: str | None,
) -> None:
    now = _now_utc()
    pipeline_version = get_pipeline_version()
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
                pipeline_version=pipeline_version,
            )
            .on_conflict_do_update(
                index_elements=[validated_addresses.c.canonical_key],
                # pipeline_version refreshed on conflict too: a re-validation that
                # reproduces the same canonical output rescues a stale-stamped row.
                set_={
                    "last_seen_at": now,
                    "validated_at": now,
                    "pipeline_version": pipeline_version,
                },
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
                    # Re-validation of an existing pattern: repoint to the freshly
                    # validated canonical address (latest-wins). Its validated_addresses
                    # row was just upserted with a fresh validated_at, so the pattern
                    # serves current data instead of a stale first-seen canonical.
                    # raw_input is back-filled only when NULL — preserve the first
                    # observed raw input for audit provenance.
                    "canonical_key": qp_insert.excluded.canonical_key,
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
    ) -> ValidateResponseV2:
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
                raw_input=raw_input,
            )
            logger.info(
                "validate: provider=%s status=%s cache_hit=true",
                cached.validation.provider,
                cached.validation.status,
            )
            return cached

        # Set pattern_key + raw_input before calling the inner provider so the
        # audit row carries them even when the provider raises (e.g. rate-limited
        # 429). raw_input is denormalized onto audit_log so it survives the full
        # audit retention window independent of cache TTL / sweeps (#147). No
        # query_patterns row is written here — one is created only by _store() on
        # a successful validation (#150).
        set_audit_context(pattern_key=pattern_key, raw_input=raw_input)

        result: ValidateResponseV2 = await self._inner.validate(std, raw_input=raw_input)

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
