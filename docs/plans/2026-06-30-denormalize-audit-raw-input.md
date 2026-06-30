# Plan — Persist audit `raw_input` independent of cache TTL (#147)

**Goal:** Store `raw_input` directly on `audit_log` at write time so it lives/dies on the
90-day audit clock, independent of cache TTL / #144 sweeps. Also fixes a latent bug where the
admin audit view shows the *first-seen* `raw_input` for a `pattern_key` rather than each
request's actual submitted text.

## Approach (Option 1 — denormalize)

Mirror the existing `pattern_key` ContextVar mechanism end-to-end.

1. **ContextVar** — add `_audit_raw_input` to `services/audit.py`: getter, `raw_input=` param on
   `set_audit_context`, and add to `reset_audit_context()` (load-bearing — prevents PII leak
   across requests on the same task).
2. **Set it** in `cache_provider.validate()` alongside `pattern_key` — both the cache-hit branch
   and the miss/eager-register branch (parity with where `query_patterns.raw_input` is set today).
3. **Thread through** `write_audit_row` and the middleware call site in `middleware/audit.py`.
4. **Schema** — migration 017 adds nullable `Text raw_input` to `audit_log` (mirror
   009_audit_parse_type.py); add column to `db/tables.py`.
5. **Admin query** — `routers/admin/queries/audit.py`: select `audit_log.c.raw_input`, switch the
   ilike filter to `audit_log.c.raw_input`, **drop the `query_patterns` outerjoin** (no fallback —
   it re-couples to TTL and reintroduces the first-seen bug).
6. **Backfill** — one-off batched script under `scripts/db/` populating existing rows from
   `query_patterns` where still joinable; swept rows stay blank (per acceptance).

## Decisions

- **Cold storage:** `infra/archive_audit.py` per-row Parquet export lists columns explicitly and
  does NOT include `raw_input` — left unchanged. `raw_input` dies at the 90-day delete, not
  archived to GCS (PII minimization; satisfies "survives the full audit retention window").
- **Set site:** cache_provider (parity), not the route handler — avoids capturing `raw_input` for
  `VALIDATION_PROVIDER=none`/no-cache deployments that store nothing today.
- **No PII in logs:** column only; no logger call emits `raw_input`.

## Acceptance

- `raw_input` in audit view survives full audit retention regardless of cache TTL / sweeps.
- No PII in logs.
- Existing rows backfilled where pattern still present.
