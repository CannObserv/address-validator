# Sparkline Graphs for Admin Dashboard Stat Boxes

Tracks: #47

## Summary

Add inline SVG sparklines to all five stat boxes on the admin dashboard landing page.
Server-side rendered, zero JS dependency, WCAG 2.1 AA compliant.

## Cards & Data

| Card | Bucket | Period | Data source |
|------|--------|--------|-------------|
| All Requests | daily | last 30 days | `COUNT(*)` grouped by `date_trunc('day')` |
| Requests This Week | daily | last 7 days | same, filtered ≥ 7 days ago |
| Requests Today | hourly | last 24 hours | `date_trunc('hour')`, filtered ≥ 24h ago |
| Cache Hit Rate | daily | last 7 days | hits/total per day for `/api/v1/validate` |
| Error Rate (Today) | daily | last 7 days | errors/api_requests per day |

Missing buckets are zero-filled in Python so sparklines have no gaps.

## Color Palette

| Card | Color | Hex |
|------|-------|-----|
| All Requests | co-purple | `#6d4488` |
| Requests This Week | teal | `#2d9f9f` |
| Requests Today | blue | `#4a7fbf` |
| Cache Hit Rate | orange | `#d4882a` |
| Error Rate | magenta | `#c44e8a` |

All have ≥ 3:1 contrast against `bg-white` and `bg-gray-800` (dark mode).
Color-blind safe per STYLE.md guidance (no red/green reliance).

## SVG Builder — `routers/admin/_sparkline.py`

`build_sparkline_svg(points, color, width=120, height=32, label="") -> str`

- Returns inline `<svg>` string with `role="img"` + `aria-label`
- Polyline stroke (2px, round caps), no fill
- Viewbox-based scaling — responsive within card
- All-zero / empty data: flat line at midpoint + muted "No data" text
- No animations (static render; respects `prefers-reduced-motion` by default)

## Data Layer — `queries.py`

New function: `get_sparkline_data(engine) -> dict[str, list[dict]]`

Returns `{"t": datetime, "v": float}` lists keyed by card name.
Runs alongside existing `get_dashboard_stats()` in the same handler.

## Template — `dashboard.html`

Each card receives `{{ sparkline_svgs.<key> | safe }}` below the big number,
above the endpoint breakdown. SVG dimensions: ~120×32px.

## Handler — `dashboard.py`

- Calls `get_sparkline_data(engine)` alongside `get_dashboard_stats(engine)`
- Passes point data through `build_sparkline_svg()` per card
- Adds `sparkline_svgs` dict to template context
- Falls back to empty dict on error (same fail-open pattern)

## Accessibility

- `role="img"` + descriptive `aria-label` on each SVG (e.g., "All requests over 30 days, trending up")
- Numeric values already present in card — sparkline is supplementary (WCAG 1.4.1)
- Stroke contrast ≥ 3:1 against card background (WCAG 1.4.11)
- No motion / animation

## Empty State

Flat line at zero with muted "No data" label centered in the SVG.

## Not in Scope

- Tooltips or hover interactions on sparkline points
- Click-to-drill-down from sparklines
- Real-time / HTMX polling refresh
- Quota cards (separate section, already have progress bars)

## Test Strategy

- `_sparkline.py`: unit tests — normal data, all-zero, empty, single point
- `queries.py`: test `get_sparkline_data` with existing test DB fixtures
- Template: verify SVG string present in rendered HTML
