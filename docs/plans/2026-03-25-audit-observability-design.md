# Audit Observability — Detect Silent Data Loss

**Date:** 2026-03-25
**Issue:** #70 (items 1 + 2 only)
**Status:** Approved

## Background

Issue #69 revealed 117,520 audit rows with NULL `provider`, `validation_status`,
and `cache_hit` due to a ContextVar propagation bug in `BaseHTTPMiddleware`. The
data is unrecoverable. These two changes ensure any future regression is detected
immediately.

## Item 1: Invariant check in audit middleware

**File:** `src/address_validator/middleware/audit.py`

### Behavior

- New helper: `_check_validate_invariants(endpoint, status_code, provider, validation_status, cache_hit) -> bool`
- Triggers on: `/api/v1/validate` + any 2xx status code
- Checks: `provider`, `validation_status`, `cache_hit` must all be non-None
- On violation:
  - Log WARNING naming the specific NULL fields
  - Override `error_detail` to `"audit_invariant_violated"`
- Row is always written (preserves request count even with incomplete data)

### Tests (4 cases)

- Validate + 200 + all fields set → no warning, no error_detail override
- Validate + 200 + NULL provider → WARNING logged, `error_detail == "audit_invariant_violated"`
- Validate + 422 → invariant check skipped
- Non-validate endpoint + 200 → invariant check skipped

## Item 2: Structured INFO log in CachingProvider

**File:** `src/address_validator/services/validation/cache_provider.py`

### Behavior

- Logfmt-style INFO log line before each `return` in `CachingProvider.validate()`
- Format: `provider=usps status=confirmed cache_hit=true`
- Local `cache_hit` bool tracks which code path was taken
- No PII (no address content) — compliant with `docs/LOGGING.md`
- No `latency_ms` — already captured in the audit row by the middleware layer

### Tests (3 cases)

- Cache hit → INFO log with `cache_hit=true`
- Cache miss → INFO log with `cache_hit=false`
- Provider unavailable → INFO log with `status=unavailable cache_hit=false`

## Out of scope

- Items 3–5 from #70 (health endpoint sampling, archive script warning, dashboard indicator)
- JSON structured logging (project-wide decision, not piecemeal)
- Latency tracking in CachingProvider
