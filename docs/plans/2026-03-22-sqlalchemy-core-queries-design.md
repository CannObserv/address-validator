# SQLAlchemy Core Rewrite — queries.py, audit.py, archive_audit.py

**Date:** 2026-03-22
**Issue:** #51

## Problem

`queries.py` (445 lines) uses hand-rolled SQL with f-string WHERE injection. The
`_API_ENDPOINT_FILTER` and `_ARCHIVED_DATE_GUARD` fragments are currently safe
(hardcoded literals), but the pattern is fragile if extended carelessly. Five query
functions repeat the same archived+live UNION pattern with minor variations.

`services/audit.py` and `scripts/archive_audit.py` also use raw SQL against the
same tables.

## Approach

Full SQLAlchemy Core rewrite — eliminate all raw SQL f-strings in production code.

## New module: `src/address_validator/db/tables.py`

- Shared `MetaData` instance
- `audit_log` and `audit_daily_stats` as `sqlalchemy.Table` objects
- Column definitions match migrations 004 and 005 exactly
- No ORM, no DeclarativeBase

## queries.py changes

### Composable helpers

- `_from_live(columns, *where)` — returns `select()` from `audit_log` with
  optional WHERE clauses
- `_from_archived(columns, *where)` — returns `select()` from `audit_daily_stats`
  with the date guard baked in as a Core scalar subquery
- Callers compose `union_all()` themselves when both live and archived data are
  needed; functions that only need live data (sparklines, audit browsing) call
  `_from_live()` alone

### Shared expressions

- `_API_ENDPOINT_FILTER` → `audit_log.c.endpoint.in_(['/api/v1/parse', '/api/v1/standardize', '/api/v1/validate'])`
- `_ARCHIVED_DATE_GUARD` → `audit_daily_stats.c.date < select(func.coalesce(func.min(audit_log.c.timestamp.cast(Date)), func.current_date())).scalar_subquery()`

### Public API

All 5 functions keep identical signatures and return shapes:

- `get_dashboard_stats(engine) -> dict`
- `get_audit_rows(engine, *, page, per_page, endpoint, provider, client_ip, status_min) -> tuple[list[dict], int]`
- `get_endpoint_stats(engine, endpoint_name) -> dict`
- `get_provider_stats(engine, provider_name) -> dict`
- `get_sparkline_data(engine) -> dict[str, list[float]]`

## services/audit.py changes

- `_INSERT_SQL = text(...)` → `audit_log.insert()`
- Single-line change; `write_audit_row` passes same param dict

## scripts/archive_audit.py changes

- `_build_aggregate_sql()` f-string → `audit_daily_stats.insert().from_select()` with `on_conflict_do_nothing()`
- `fetch_expired_dates` → `select(func.date_trunc(...)).distinct().where(...)`
- `fetch_rows_for_date` → `select()` with column list + timestamp filters
- `delete_expired_rows` → `delete().where(audit_log.c.id.in_(subquery))`
- `vacuum_audit_log` stays as `text("VACUUM ANALYZE audit_log")` — no Core equivalent

## Out of scope

- Moving `cache_db.py` into `db/` (#62)
- ORM models / DeclarativeBase
- Test seed helpers (raw SQL for test setup — independent of production code)
