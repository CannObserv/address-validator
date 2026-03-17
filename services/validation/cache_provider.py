"""CachingProvider — ValidationProvider wrapper backed by the SQLite validation cache.

Lookup algorithm
----------------
1. Hash the standardised input components → ``pattern_key``
2. SELECT from ``query_patterns`` WHERE ``pattern_key = ?``
3. HIT  → deserialise the linked ``validated_addresses`` row; return immediately
4. MISS → delegate to ``inner.validate(std)``

Store algorithm
---------------
1. Skip entirely when ``result.validation.status == "unavailable"``
2. Hash the provider-returned address fields → ``canonical_key``
3. INSERT/upsert into ``validated_addresses`` (ON CONFLICT: update last_seen_at)
4. INSERT OR IGNORE into ``query_patterns``

The parse → standardise pipeline already normalises casing, abbreviations, and
whitespace before this module is called, so ``pattern_key`` naturally collapses
equivalent inputs to the same hash.
"""

import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import aiosqlite

from models import ComponentSet, StandardizeResponseV1, ValidateResponseV1, ValidationResult
from services.validation.protocol import ValidationProvider

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


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Row deserialisation
# ---------------------------------------------------------------------------


def _row_to_response(row: aiosqlite.Row) -> ValidateResponseV1:
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
    db: aiosqlite.Connection,
    pattern_key: str,
) -> ValidateResponseV1 | None:
    async with db.execute(
        "SELECT canonical_key FROM query_patterns WHERE pattern_key = ?",
        (pattern_key,),
    ) as cur:
        qp_row = await cur.fetchone()

    if qp_row is None:
        logger.debug("cache_lookup: miss pattern_key=%s", pattern_key)
        return None

    canonical_key: str = qp_row["canonical_key"]

    async with db.execute(
        "SELECT * FROM validated_addresses WHERE canonical_key = ?",
        (canonical_key,),
    ) as cur:
        va_row = await cur.fetchone()

    if va_row is None:
        # Orphaned pattern — treat as miss (FK enforcement may catch this, but
        # be defensive in case the DB was modified externally).
        logger.debug(
            "cache_lookup: orphaned pattern_key=%s canonical_key=%s; treating as miss",
            pattern_key,
            canonical_key,
        )
        await db.execute(
            "DELETE FROM query_patterns WHERE pattern_key = ?", (pattern_key,)
        )
        await db.commit()
        return None

    await db.execute(
        "UPDATE validated_addresses SET last_seen_at = ? WHERE canonical_key = ?",
        (_now_iso(), canonical_key),
    )
    await db.commit()

    logger.debug(
        "cache_lookup: hit pattern_key=%s canonical_key=%s",
        pattern_key,
        canonical_key,
    )
    return _row_to_response(va_row)


async def _store(
    db: aiosqlite.Connection,
    pattern_key: str,
    canonical_key: str,
    result: ValidateResponseV1,
) -> None:
    now = _now_iso()
    components_json: str | None = (
        result.components.model_dump_json() if result.components else None
    )
    warnings_json = json.dumps(result.warnings)

    await db.execute(
        """
        INSERT INTO validated_addresses
            (canonical_key, provider, status, dpv_match_code,
             address_line_1, address_line_2, city, region, postal_code, country,
             validated, components_json, latitude, longitude,
             warnings_json, created_at, last_seen_at)
        VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(canonical_key) DO UPDATE SET last_seen_at = excluded.last_seen_at
        """,
        (
            canonical_key,
            result.validation.provider or "",
            result.validation.status,
            result.validation.dpv_match_code,
            result.address_line_1,
            result.address_line_2,
            result.city,
            result.region,
            result.postal_code,
            result.country,
            result.validated,
            components_json,
            result.latitude,
            result.longitude,
            warnings_json,
            now,
            now,
        ),
    )

    await db.execute(
        """
        INSERT OR IGNORE INTO query_patterns (pattern_key, canonical_key, created_at)
        VALUES (?, ?, ?)
        """,
        (pattern_key, canonical_key, now),
    )

    await db.commit()

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

    Intercepts calls to ``validate()``, checks the local SQLite cache, and
    falls through to ``inner`` only on a miss.  Results are stored after every
    successful provider call (``status != "unavailable"``).

    The ``get_db`` callable is injected rather than imported directly so that
    tests can supply an in-memory connection without touching the module global.
    """

    def __init__(
        self,
        inner: ValidationProvider,
        get_db: Callable[[], Awaitable[aiosqlite.Connection]],
    ) -> None:
        self._inner = inner
        self._get_db = get_db

    async def validate(self, std: StandardizeResponseV1) -> ValidateResponseV1:
        """Check the cache; delegate to inner provider on miss; store the result."""
        db = await self._get_db()
        pattern_key = _make_pattern_key(std)

        cached = await _lookup(db, pattern_key)
        if cached is not None:
            return cached

        result: ValidateResponseV1 = await self._inner.validate(std)

        if result.validation.status == "unavailable":
            logger.debug(
                "cache_store: skip provider=%s status=unavailable",
                result.validation.provider,
            )
            return result

        canonical_key = _make_canonical_key(result)
        await _store(db, pattern_key, canonical_key, result)
        return result
