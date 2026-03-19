"""One-time migration: copy the SQLite validation cache to PostgreSQL.

Run order
---------
1. Apply Alembic migrations (tables must exist before insertion)::

       VALIDATION_CACHE_DSN=<dsn> alembic upgrade head

2. Run this script::

       VALIDATION_CACHE_DB=/path/to/validation_cache.db \\
       VALIDATION_CACHE_DSN=postgresql+asyncpg://... \\
       python scripts/migrate_sqlite_to_postgres.py

The script is idempotent: rows already present in PostgreSQL are skipped
(ON CONFLICT DO NOTHING) so it is safe to re-run.

Dependencies
------------
Requires ``asyncpg`` — already present in the project venv::

    uv run python scripts/migrate_sqlite_to_postgres.py
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from pathlib import Path

import asyncpg  # type: ignore[import-untyped]

_DEFAULT_SQLITE_PATH = "/var/lib/address-validator/validation_cache.db"


def _get_env() -> tuple[str, str]:
    sqlite_path = os.environ.get("VALIDATION_CACHE_DB", _DEFAULT_SQLITE_PATH).strip()
    pg_dsn = os.environ.get("VALIDATION_CACHE_DSN", "").strip()
    if not pg_dsn:
        print("ERROR: VALIDATION_CACHE_DSN is not set.", file=sys.stderr)
        sys.exit(1)
    return sqlite_path, pg_dsn


def _read_sqlite(sqlite_path: str) -> tuple[list[dict], list[dict]]:
    """Return (validated_addresses rows, query_patterns rows) from SQLite."""
    conn = sqlite3.connect(sqlite_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("SELECT * FROM validated_addresses")
        va_rows = [dict(row) for row in cur.fetchall()]
        cur = conn.execute("SELECT * FROM query_patterns")
        qp_rows = [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()
    # Backfill validated_at from created_at for rows predating the column.
    for row in va_rows:
        if not row.get("validated_at"):
            row["validated_at"] = row["created_at"]
    return va_rows, qp_rows


async def _insert_postgres(pg_dsn: str, va_rows: list[dict], qp_rows: list[dict]) -> None:
    # asyncpg uses postgresql:// not postgresql+asyncpg://
    dsn = pg_dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    try:
        # validated_addresses
        va_inserted = 0
        for row in va_rows:
            result = await conn.execute(
                """
                INSERT INTO validated_addresses
                    (canonical_key, provider, status, dpv_match_code,
                     address_line_1, address_line_2, city, region, postal_code, country,
                     validated, components_json, latitude, longitude,
                     warnings_json, created_at, last_seen_at, validated_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18)
                ON CONFLICT (canonical_key) DO NOTHING
                """,
                row["canonical_key"],
                row["provider"],
                row["status"],
                row.get("dpv_match_code"),
                row.get("address_line_1"),
                row.get("address_line_2"),
                row.get("city"),
                row.get("region"),
                row.get("postal_code"),
                row["country"],
                row.get("validated"),
                row.get("components_json"),
                row.get("latitude"),
                row.get("longitude"),
                row.get("warnings_json", "[]"),
                row["created_at"],
                row["last_seen_at"],
                row["validated_at"],
            )
            if result == "INSERT 0 1":
                va_inserted += 1
        print(f"validated_addresses: {va_inserted}/{len(va_rows)} inserted (rest already present)")

        # query_patterns
        qp_inserted = 0
        for row in qp_rows:
            result = await conn.execute(
                """
                INSERT INTO query_patterns (pattern_key, canonical_key, created_at)
                VALUES ($1, $2, $3)
                ON CONFLICT (pattern_key) DO NOTHING
                """,
                row["pattern_key"],
                row["canonical_key"],
                row["created_at"],
            )
            if result == "INSERT 0 1":
                qp_inserted += 1
        print(f"query_patterns: {qp_inserted}/{len(qp_rows)} inserted (rest already present)")
    finally:
        await conn.close()


async def main() -> None:
    sqlite_path, pg_dsn = _get_env()

    if not Path(sqlite_path).exists():
        print(f"ERROR: SQLite file not found: {sqlite_path}", file=sys.stderr)
        sys.exit(1)

    print(f"Reading from SQLite: {sqlite_path}")
    va_rows, qp_rows = _read_sqlite(sqlite_path)
    print(f"Found {len(va_rows)} validated_addresses, {len(qp_rows)} query_patterns")

    if not va_rows and not qp_rows:
        print("Nothing to migrate.")
        return

    print("Writing to PostgreSQL…")
    await _insert_postgres(pg_dsn, va_rows, qp_rows)
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
