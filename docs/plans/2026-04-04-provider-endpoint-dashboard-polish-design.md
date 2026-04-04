# Provider & Endpoint Dashboard Polish — Design

**Date:** 2026-04-04

## Summary

Consistency and accuracy improvements to the provider and endpoint admin detail pages:
card layout alignment with the main dashboard, a new 7-day requests card for providers,
validation status color fixes, a compact accessible Result column in the providers audit
table, and minor copy/UX cleanup.

## Scope

1. Add "Requests (Last 7 Days)" card to providers detail page
2. Reorder request cards on both screens to match main dashboard: All Time · 7 Days · 24 Hours
3. Isolate the three request cards on their own first row; move metric cards to row 2
4. Rename table heading "Recent Requests" → "Requests" on both screens
5. Add a compact "Result" (validation_status) column to the providers audit table only
6. Fix `confirmed_missing_secondary` color from green → yellow everywhere on providers page
7. Pin `not_confirmed` to the end of the validation status filter pill list
8. Confirm `/parse` pagination absence is data-driven (no code change required)

## Data layer — `get_provider_stats()`

Add three new keys:

| Key | Description |
|---|---|
| `last_7d` | Request count for the last 7 days |
| `status_codes_7d` | `{code: count}` for the last 7 days |
| `validation_statuses_7d` | `{status: count}` for the last 7 days (live rows only, same as existing windows) |

Additionally, return all `validation_statuses_*` dicts with keys in **canonical order**:
`confirmed → confirmed_missing_secondary → confirmed_bad_secondary → not_confirmed`.
Implemented by sorting after fetch using a fixed-priority key, not by relying on DB order.

No changes to `get_endpoint_stats()` — it already returns `last_7d` and `status_codes_7d`.

## Card layout

### Providers detail (before: single 4-col grid)

**Row 1** — `grid-cols-1 sm:grid-cols-3` — request cards in order:
- Requests (All Time) — `stats.total`, `status_codes_all`, `validation_statuses_all`
- Requests (Last 7 Days) — `stats.last_7d`, `status_codes_7d`, `validation_statuses_7d` *(new)*
- Requests (Last 24 Hours) — `stats.last_24h`, `status_codes_24h`, `validation_statuses_24h`

**Row 2** — `grid-cols-1 sm:grid-cols-2` — metric cards:
- Cache Hit Rate (Last 7 Days)
- Daily Quota (or N/A)

### Endpoints detail (before: single 5-col grid)

**Row 1** — `grid-cols-1 sm:grid-cols-3` — request cards in order:
- Requests (All Time) — `stats.total`, `status_codes_all`
- Requests (Last 7 Days) — `stats.last_7d`, `status_codes_7d`
- Requests (Last 24 Hours) — `stats.last_24h`, `status_codes_24h`

**Row 2** — `grid-cols-1 sm:grid-cols-2` — metric cards:
- Avg Latency
- Error Rate (All Time)

## Audit table — Result column (providers only)

`_thead.html` and `_rows.html` gain a conditional column gated on `show_result` (a boolean
passed from the template context). Providers router passes `show_result=True`; endpoints
router passes `show_result=False`.

**Visual design:** shape + color symbol, matching the existing Status column pattern.
Full status string rendered as `<span class="sr-only">` for screen readers, satisfying
WCAG 1.4.11 (Non-text Contrast) and the STYLE.md rule against icon-as-sole-conveyor.

| `validation_status` | Symbol | Color class |
|---|---|---|
| `confirmed` | ✓ (&#10003;) | `text-green-700 dark:text-green-400` |
| `confirmed_missing_secondary` | △ (&#9650;) | `text-yellow-600 dark:text-yellow-400` |
| `confirmed_bad_secondary` | △ (&#9650;) | `text-yellow-600 dark:text-yellow-400` |
| `not_confirmed` | ✗ (&#10005;) | `text-red-600 dark:text-red-400` |
| null | — | muted |

Empty-state `colspan` is `{% if show_result %}10{% else %}9{% endif %}`.

Column placed between Cache and Raw Input (preserves Raw Input as the last, widest column).

## Color fix — `confirmed_missing_secondary`

Currently treated as green (grouped with `confirmed`) in:
- Card validation status breakdowns (24h card, all-time card, new 7d card)
- Filter pill row

Fix: move to yellow bucket everywhere. Rationale: a deliverable address with a missing
unit number is a data-quality warning, not a success. Consistent with
`confirmed_bad_secondary` (already yellow).

## Filter pill ordering — `not_confirmed`

Currently iterates `stats.validation_statuses_all` in dict key order (unpredictable).
Fix: render pills by iterating a hardcoded canonical list and looking up counts from
the dict:

```
['confirmed', 'confirmed_missing_secondary', 'confirmed_bad_secondary', 'not_confirmed']
```

Unknown statuses (if any future value appears) fall through a final `{% else %}` neutral style.

## `/parse` pagination

Pagination renders only when `total_pages > 1` (i.e., > 50 rows). Absence on `/parse`
is expected when row count is ≤ 50. **No code change.** Verify row count during
implementation; document in PR if confirmed.

## Test strategy

- Extend `test_admin_views.py` provider tests to assert `last_7d` appears in rendered HTML
- Assert `confirmed_missing_secondary` renders with yellow classes, not green
- Assert `not_confirmed` pill appears after `confirmed_bad_secondary` in DOM order
- Assert Result column `<th>` present in provider response, absent in endpoint response
- Assert Result cell sr-only text matches `validation_status` value in provider rows
- Existing card/filter tests should continue to pass; update any that assert old card order

## Files touched

| File | Change |
|---|---|
| `src/address_validator/routers/admin/queries.py` | Add `last_7d`, `status_codes_7d`, `validation_statuses_7d` to `get_provider_stats()`; sort validation_statuses by canonical order |
| `src/address_validator/routers/admin/providers.py` | Pass `show_result=True` to template context |
| `src/address_validator/routers/admin/endpoints.py` | Pass `show_result=False` to template context |
| `src/address_validator/templates/admin/providers/detail.html` | Two-row card layout; new 7d card; color fixes; pill ordering; rename heading |
| `src/address_validator/templates/admin/endpoints/detail.html` | Two-row card layout; reorder cards; rename heading |
| `src/address_validator/templates/admin/audit/_thead.html` | Conditional Result `<th>` |
| `src/address_validator/templates/admin/audit/_rows.html` | Conditional Result `<td>` with symbol + sr-only; updated colspan |
| `tests/unit/test_admin_views.py` | New assertions per test strategy above |
