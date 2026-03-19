# PostgreSQL Migration Design

**Date:** 2026-03-19
**Status:** Approved

## Summary

Migrate the validation cache from SQLite (`aiosqlite`) to PostgreSQL using SQLAlchemy
async Core + `asyncpg` driver + Alembic for schema migrations. Closes #33 (adopt a
migration library) as a by-product.

## Motivation

Performance and query flexibility at scale. The cache sits on every `/validate` call;
SQLite's single-writer model and lack of server-side query planning become bottlenecks
under concurrent load. Additionally, adopting SQLAlchemy + Alembic resolves the fragile
inline `PRAGMA`-based migration pattern flagged in #33, and the dialect abstraction
provides forward flexibility if the database host or engine changes again.

## Scope

- Replace `aiosqlite` with `sqlalchemy[asyncio]` + `asyncpg` + `alembic`
- Rewrite `cache_db.py` (connection management, schema init removed)
- Update `cache_provider.py` (SQL syntax, row access, connection lifecycle)
- Update `factory.py` (inject engine instead of connection)
- Rename env var `VALIDATION_CACHE_DB` → `VALIDATION_CACHE_DSN`
- Add Alembic setup with migration 001 (full current schema)
- Write one-time `scripts/migrate_sqlite_to_postgres.py` data migration
- Update test fixtures (replace `:memory:` with test PostgreSQL DB)
- Update AGENTS.md and docs/VALIDATION-PROVIDERS.md

Out of scope: ORM layer, timestamp column type change (TEXT → TIMESTAMPTZ; deferred to
a follow-on Alembic migration), Docker-ification of PostgreSQL.

## Dependencies

Remove:
- `aiosqlite`

Add:
- `sqlalchemy[asyncio]` — async Core engine + connection pool
- `asyncpg` — PostgreSQL wire-protocol driver (used by SQLAlchemy under the hood)
- `alembic` — migration runner; closes #33

## Alembic Setup

```
alembic/
  env.py              # async migration runner; reads VALIDATION_CACHE_DSN
  versions/
    001_initial_schema.py   # full schema: both tables + indexes, validated_at included
alembic.ini                 # script_location = alembic; sqlalchemy.url = %(VALIDATION_CACHE_DSN)s
```

- Migration `001` encodes the full current schema. `validated_at` is included from day
  one — no column-add migration needed for fresh databases.
- `alembic upgrade head` is called automatically from the FastAPI lifespan startup hook
  (inside `cache_db` init), before the engine is returned to callers.
- Future schema changes = new numbered migration files. The inline `PRAGMA` pattern in
  `_init_schema()` is deleted entirely.

## `cache_db.py` Changes

| Before | After |
|---|---|
| `aiosqlite.Connection` singleton | `AsyncEngine` singleton (connection pool) |
| `get_db()` → returns shared connection | `get_engine()` → returns shared engine |
| `close_db()` → `db.close()` | `close_db()` → `engine.dispose()` |
| `_init_schema()` with PRAGMA DDL | removed — Alembic handles this |
| `PRAGMA journal_mode=WAL` | removed — not applicable to PostgreSQL |
| `PRAGMA foreign_keys=ON` | removed — PostgreSQL enforces FKs by default |
| `VALIDATION_CACHE_DB` env var (file path) | `VALIDATION_CACHE_DSN` env var (Postgres DSN) |

`get_engine()` is lazy: creates the `AsyncEngine` on first call, runs
`alembic upgrade head`, then returns. Subsequent calls return the cached engine.

## `cache_provider.py` Changes

Logic unchanged (hit/miss/store/TTL algorithm). SQL syntax adjustments:

| SQLite | PostgreSQL |
|---|---|
| `?` positional params | `$1, $2, …` via SQLAlchemy `text()` + `bindparams` |
| `INSERT OR IGNORE` | `INSERT … ON CONFLICT DO NOTHING` |
| `aiosqlite.Row` (dict-style) | SQLAlchemy `Row` (dict-style — same access pattern) |
| `async with db.execute(…)` | `async with engine.begin() as conn: await conn.execute(…)` |
| `await db.commit()` | implicit — `engine.begin()` commits on context-manager exit |

`_lookup` and `_store` accept an `AsyncConnection` rather than an `aiosqlite.Connection`.
`CachingProvider.__init__` receives `get_engine: Callable[[], Awaitable[AsyncEngine]]`
instead of `get_db`.

Timestamps remain stored as `TEXT` (ISO-8601 strings) in this migration to minimise
risk. A follow-on Alembic migration can convert columns to `TIMESTAMPTZ`.

## `factory.py` Changes

- `cache_db.get_db` → `cache_db.get_engine`
- `CachingProvider(…, get_db=cache_db.get_db, …)` → `CachingProvider(…, get_engine=cache_db.get_engine, …)`
- `validate_config()`: check that `VALIDATION_CACHE_DSN` is non-empty when provider is
  non-null (mirrors the existing `VALIDATION_CACHE_DB` path check)

## Env Var Changes

| Variable | Before | After |
|---|---|---|
| `VALIDATION_CACHE_DB` | Absolute path to SQLite file; default `/var/lib/address-validator/validation_cache.db` | **Removed** |
| `VALIDATION_CACHE_DSN` | — | PostgreSQL DSN; no default; required when provider is non-null |

Example value:
```
VALIDATION_CACHE_DSN=postgresql+asyncpg://address_validator@localhost/address_validator
```

## Data Migration Script

`scripts/migrate_sqlite_to_postgres.py` — standalone, run once after Alembic migrations
have been applied (tables must exist before insertion).

Steps:
1. Read `VALIDATION_CACHE_DB` (old SQLite path) and `VALIDATION_CACHE_DSN` (new PG DSN)
   from env or CLI arguments.
2. Open SQLite via stdlib `sqlite3` (sync — no async needed for a one-shot script).
3. Connect to PostgreSQL via `psycopg2` (sync) or construct the DSN for `asyncpg`.
4. Copy all rows from `validated_addresses`:
   - Backfill `validated_at` from `created_at` where null (handles pre-migration SQLite rows).
   - `INSERT … ON CONFLICT DO NOTHING` — idempotent; safe to re-run.
5. Copy all rows from `query_patterns` with the same idempotency guarantee.

Run order:
```bash
alembic upgrade head                        # 1. create tables
python scripts/migrate_sqlite_to_postgres.py  # 2. copy data
sudo systemctl restart address-validator    # 3. switch to PG
```

## Test Strategy

Current `VALIDATION_CACHE_DB=:memory:` pattern is removed.

Replacement:
- A dedicated `address_validator_test` PostgreSQL database on the local VM.
- `VALIDATION_CACHE_DSN` in test env points to this database.
- **Session-scoped fixture:** create the engine and run Alembic migrations once per
  test session.
- **Function-scoped fixture:** `TRUNCATE validated_addresses, query_patterns RESTART
  IDENTITY CASCADE` between tests — fast and isolation-preserving.

No new test dependencies required beyond the packages added in the Dependencies section.

## PostgreSQL Provisioning (VM)

```bash
sudo apt install postgresql
sudo -u postgres createuser --no-superuser --no-createdb --no-createrole address_validator
sudo -u postgres createdb --owner=address_validator address_validator
sudo -u postgres createdb --owner=address_validator address_validator_test
```

`pg_hba.conf`: peer auth for the `address_validator` system user is sufficient for
local systemd service operation.
