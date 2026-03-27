# Raw Input Audit Traceability — Design

**Issue:** #73
**Date:** 2026-03-27

## Problem

`query_patterns` stores only a SHA-256 `pattern_key` derived from standardised components. The original raw address string is discarded, making it impossible to:

- Trace a cached result back to the exact input that produced it
- Determine whether the parse/standardise pipeline mangled an input before it reached the provider (as seen in #72 — 244 likely-corrupted entries, no way to identify original inputs)
- Distinguish provider corrections from our own pipeline errors on `confirmed_bad_secondary` results

## Decision

**Path 1: add `pattern_key` to `audit_log` + `raw_input` to `query_patterns`.**

- `query_patterns.raw_input` — stores the original caller input once, at first cache entry time
- `audit_log.pattern_key` — stores a soft FK per validate request, enabling a definitive JOIN from the audit view to the cache entry

Rejected alternatives:
- *Path 2 (raw_input ContextVar only):* raw_input would live in both tables independently with no FK link between them; the audit ↔ cache association would be implicit.
- *Protocol ContextVar for inbound data:* ContextVar pattern is appropriate for data flowing outward (cache → audit middleware), not inward (router → cache layer); an explicit kwarg keeps inbound data flow readable.

## Schema changes

Two nullable columns added by migration 007:

```sql
ALTER TABLE query_patterns ADD COLUMN raw_input TEXT;
ALTER TABLE audit_log      ADD COLUMN pattern_key TEXT;
```

`audit_log.pattern_key` is a soft FK (no DB-level constraint) so `query_patterns` rows can be pruned by TTL without breaking audit history.

`raw_input` format:
- Caller submitted `address` string → store verbatim
- Caller submitted `components` dict → store `json.dumps(components.model_dump())`

Existing rows will have `NULL` in both columns. Backfill evaluation tracked in #74.

## Code changes

### 1. `ValidationProvider` protocol

Add keyword-only arg with default `None`:

```python
async def validate(self, std: StandardizeResponseV1, *, raw_input: str | None = None) -> ValidateResponseV1:
```

### 2. Provider touch-points

| Provider | Change |
|---|---|
| `null_provider` | Accept `raw_input=None`, ignore |
| `usps_provider` | Accept `raw_input=None`, ignore |
| `google_provider` | Accept `raw_input=None`, ignore |
| `chain_provider` | Accept + thread to each sub-provider call |
| `cache_provider` | Accept + pass to `_store()`; `_store()` inserts into `query_patterns` |

`_store()` signature:
```python
async def _store(self, std, response, *, raw_input: str | None) -> None:
```

### 3. Router (`routers/v1/validate.py`)

Extract `raw_input` before calling `provider.validate()`:

```python
if req.address is not None:
    raw_input: str | None = req.address
else:
    raw_input = json.dumps(req.components.model_dump())

result = await provider.validate(std, raw_input=raw_input)
```

No request/response model changes.

### 4. Audit ContextVar — `pattern_key`

`services/audit.py`: add `_audit_pattern_key: ContextVar[str | None]`. Extend `set_audit_context()`, `reset_audit_context()`, and `write_audit_row()` with a `pattern_key` keyword.

`cache_provider` sets it in two places:
- `_lookup()` — cache **hit**: set to matched row's `pattern_key`
- `_store()` — cache **miss** (new entry): set to newly inserted `pattern_key`

The audit middleware already reads ContextVars after `self.app()` returns; `pattern_key` is added to that write path without structural change.

### 5. Admin UI

**`queries.get_audit_rows()`**
- LEFT JOIN `query_patterns` on `audit_log.pattern_key = query_patterns.pattern_key`
- Add `query_patterns.c.raw_input` to SELECT
- New `raw_input: str | None` filter → `query_patterns.c.raw_input.ilike(f"%{value}%")`

**`audit_views.py`**: accept `?raw_input=` query param, pass to `get_audit_rows()`.

**Template**: add `raw_input` column (truncated, full value in tooltip); show `—` when NULL. Add filter input alongside existing ones.

## Migration

**007** — two `ALTER TABLE`, no data migration required.

## Test strategy

- `cache_provider` unit: `_store()` writes `raw_input`; `_lookup()` sets `pattern_key` ContextVar on hit
- Router unit: correct `raw_input` extracted for address-string vs. component input
- Audit unit: `pattern_key` present in context after validate call
- Integration: `query_patterns.raw_input` populated after a validate round-trip; `audit_log.pattern_key` matches
- Admin: `?raw_input=` filter returns only matching rows; NULL rows not returned
