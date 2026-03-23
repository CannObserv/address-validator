# Cache Core Table Definitions (#58)

## Summary

Add SQLAlchemy Core `Table` definitions for `validated_addresses` and `query_patterns`
to `db/tables.py`, migrate `cache_provider.py` from raw `text()` SQL to Core expressions,
and clean up schema warts in a single Alembic migration.

## Scope

- Core Table definitions for both cache tables in `db/tables.py`
- Single Alembic migration (006) with three changes:
  - `provider`: convert empty strings to NULL
  - `components_json`, `warnings_json`: Text to JSONB
  - `status`: CHECK constraint (`confirmed`, `partially_confirmed`, `not_confirmed`, `unavailable`)
- Rewrite `cache_provider.py` queries to use Core Table expressions
- Fix deserialization in `_row_to_response` for JSONB columns

## Table definitions

### validated_addresses

| Column | Type | Nullable | Notes |
|---|---|---|---|
| id | BigInteger (Identity) | PK | |
| canonical_key | Text | NOT NULL | Unique |
| provider | Text | NULL | Was empty-string convention; migrated to NULL |
| status | Text | NOT NULL | CHECK constraint |
| dpv_match_code | Text | NULL | |
| address_line_1 | Text | NULL | |
| address_line_2 | Text | NULL | |
| city | Text | NULL | |
| region | Text | NULL | |
| postal_code | Text | NULL | |
| country | Text | NOT NULL | |
| validated | Text | NULL | Single-line canonical address string |
| components_json | JSONB | NULL | Was Text |
| latitude | Double | NULL | |
| longitude | Double | NULL | |
| warnings_json | JSONB | NOT NULL | Default `[]`; was Text |
| created_at | DateTime(tz) | NOT NULL | |
| last_seen_at | DateTime(tz) | NOT NULL | |
| validated_at | DateTime(tz) | NOT NULL | |

### query_patterns

| Column | Type | Nullable | Notes |
|---|---|---|---|
| id | BigInteger (Identity) | PK | |
| pattern_key | Text | NOT NULL | Unique |
| canonical_key | Text | NOT NULL | FK to validated_addresses.canonical_key |
| created_at | DateTime(tz) | NOT NULL | |

## Migration 006

```sql
-- provider: empty string to NULL
UPDATE validated_addresses SET provider = NULL WHERE provider = '';

-- JSONB conversion
ALTER TABLE validated_addresses
  ALTER COLUMN components_json TYPE JSONB USING components_json::JSONB;
ALTER TABLE validated_addresses
  ALTER COLUMN warnings_json TYPE JSONB USING warnings_json::JSONB;

-- CHECK constraint on status
ALTER TABLE validated_addresses ADD CONSTRAINT ck_validated_addresses_status
  CHECK (status IN ('confirmed', 'partially_confirmed', 'not_confirmed', 'unavailable'));
```

## cache_provider.py changes

- Import `validated_addresses`, `query_patterns` from `db.tables`
- Replace all `text()` queries with Core expressions (`select`, `insert`, `update`, `delete`)
- `provider` write: `result.validation.provider` (NULL, not empty string)
- JSONB serialization: `model_dump()` / raw list instead of `model_dump_json()` / `json.dumps()`
- JSONB deserialization: `model_validate()` / direct list access instead of `model_validate_json()` / `json.loads()`

## What doesn't change

- Key hashing functions (`_make_pattern_key`, `_make_canonical_key`)
- Fail-open `except Exception` blocks
- `CachingProvider` class interface
- Audit tables in `tables.py`
- Alembic autogenerate (not wired up)
