# Admin Dashboard Fixes — Design

Date: 2026-04-13

## Context

Two admin-dashboard bugs observed in production:

1. **429 rate-limited responses are counted as errors.** A 4-request / 24h window consisting entirely of 429s shows a 100% error rate. Rate limiting is traffic control, not failure.
2. **USPS/Google quota display does not reflect actual daily request volume.** The rendered `{{ q.remaining }} / {{ q.limit }}` reads as "requests today" but is actually the token-bucket state of `QuotaGuard` — for USPS it drifts back up as the rolling 86,400 s window refills, and for Google it only matches reality when Cloud Monitoring is reachable at boot.

A third reported bug (OpenAPI description / admin-route visibility) was already fixed in commits `7e2230c` and `9ab2b8c`; the running systemd service was stale and has since been restarted.

## Scope

- Bug 1: reclassify 429 out of the error bucket in every admin query and template.
- Bug 2: replace the daily-quota number with an audit-log-derived "requests today" count per provider. Keep the existing provider tile layout.

Out of scope: schema migration to add a `rate_limited_count` column on `audit_daily_stats`; burst-headroom indicators beyond the existing quota bar.

## Bug 1 — 429 classification

### Approach

Introduce a shared predicate in `db/tables.py`:

```python
RATE_LIMITED_STATUS = 429
# error = response >= 400 and not a rate-limit throttle
```

Add a small helper in `routers/admin/queries/_shared.py` that returns the `is_error` and `is_rate_limited` SQL expressions for both the `audit_log` live path and the `audit_daily_stats` archived path. Every call site uses the helper so the definition stays in one place.

### Query changes

- `queries/dashboard.py` — `get_dashboard_stats` (error rate), `get_sparkline_data` (error series): exclude 429 from error count; add a parallel `rate_limited_count` aggregate.
- `queries/endpoint.py` — `get_endpoint_stats`: same treatment.
- `queries/provider.py` — if it surfaces error counts, same.

For `audit_daily_stats`, the rollup already groups by `status_code`, so `SUM(CASE WHEN status_code = 429 THEN request_count ELSE 0 END)` gives the rate-limited bucket without a migration. The existing `error_count` column will slightly over-count historical errors by the number of 429s; this is acceptable (small absolute numbers, no backfill required).

### Template changes

Surface a distinct "rate-limited" bucket alongside errors in:

- `templates/admin/dashboard.html` — dashboard summary card.
- `templates/admin/endpoints/*.html` and `providers/*.html` — wherever error counts are shown.

Styling: amber / neutral, not red. One new table column or inline stat — no structural reshuffle.

## Bug 2 — audit-based "requests today"

### Approach

Add a query helper in `routers/admin/queries/provider.py`:

```python
def get_provider_daily_usage(conn, tz="UTC") -> dict[str, int]:
    """Return {provider_name: count} of audit_log rows for the current calendar day."""
```

- Source: `audit_log` filtered to `timestamp >= today_start` (UTC to match existing `_shared.py` time boundaries).
- Group by `provider`, count rows.
- Return `{}` on DB error (fail-open, consistent with other admin queries).

Wire it through `routers/admin/dashboard.py` and `providers.py`; extend the `get_quota_info()` payload (or pass a parallel dict) so templates can render:

```
<provider>: 142 requests today   |  daily limit 10,000
```

The existing token-bucket `remaining / limit` progress bar is removed from the daily-quota tile — it was the source of the confusion. Burst-rate headroom is not a user-facing concern on this dashboard today and can be reintroduced later with a clearer label if operators want it.

### Why not option (c) (both audit and bucket)

We considered showing audit-derived "requests today" alongside the bucket indicator. Rejected: two near-identical numerators invite the same confusion this fix is trying to resolve. Operators who need bucket state can read `/api/v1/health` or journal logs.

## Error handling

- `get_provider_daily_usage` mirrors existing admin query fail-open semantics — returns `{}` on `Exception` so the dashboard renders even when the DB is unreachable.
- Rate-limited bucket defaults to `0` when absent, never `None`.

## Test strategy

- Unit: new helpers in `_shared.py` and `provider.py` covered by existing admin-query test patterns; fixtures seed `audit_log` with a mix of 200/429/500 rows and assert bucket counts.
- Integration: one end-to-end test hitting `/admin/` with seeded data verifies the template shows rate-limited rows separately and provider usage reflects inserted audit rows.
- No new migration → no Alembic test.

## Commit plan

Single logical change per concern:

1. `#<n> fix: exclude 429 from admin error-rate aggregation, surface rate-limited bucket`
2. `#<n> fix: base provider daily-usage tile on audit_log instead of token-bucket state`

Ship under one GitHub issue.
