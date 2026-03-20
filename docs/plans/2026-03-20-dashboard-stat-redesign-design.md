# Admin Dashboard Stat Card Redesign

**Date:** 2026-03-20

## Summary

Reorganize the admin dashboard landing page stat cards: promote all-time requests to a top-level box, add per-endpoint breakdowns inside request count boxes, and regroup rate metrics on a second row.

## Layout

### Row 1 — Request counts (3-column grid)

| All Requests | Requests This Week | Requests Today |
|---|---|---|

Each box contains:
- Rollup number (`text-2xl font-bold`)
- Per-endpoint breakdown below (`text-xs text-gray-400` labels, `text-xs text-gray-500 font-medium` counts)
- Separated by `mt-2 border-t border-gray-100 pt-2`
- Endpoints: `/parse`, `/standardize`, `/validate`, `other`

### Row 2 — Rate metrics (2-column grid)

| Cache Hit Rate | Error Rate (Today) |
|---|---|

No per-endpoint breakdown. Existing behavior preserved.

### Provider Quota

Heading renamed from "Provider Quota" to "Validation Provider Quota". No other changes.

## Query changes

Extend `get_dashboard_stats()` with a grouped query:

```sql
SELECT
    endpoint,
    COUNT(*) AS total,
    COUNT(*) FILTER (WHERE timestamp >= :today) AS today,
    COUNT(*) FILTER (WHERE timestamp >= :week) AS week
FROM audit_log
GROUP BY endpoint
```

Post-process in Python:
- Bucket `/api/v1/parse`, `/api/v1/standardize`, `/api/v1/validate` by stripping prefix to get `/parse`, `/standardize`, `/validate`
- Sum everything else into `other`
- Return as `endpoint_breakdown` dict keyed by time period

## Template changes

- Row 1: `grid-cols-1 sm:grid-cols-2 lg:grid-cols-3`
- Row 2: `grid-cols-1 sm:grid-cols-2`
- Per-endpoint breakdown rendered as a small list inside each request count card

## Responsive behavior

- Row 1 collapses: 3 → 2 → 1 columns
- Row 2 collapses: 2 → 1 columns
- Endpoint breakdowns stack naturally inside cards

## Sparklines

Deferred to #47, blocked by #46 (accessibility style guide).

## Out of scope

- Per-endpoint breakdown in rate boxes
- Provider quota card changes (beyond heading rename)
- HTMX boost behavior changes
