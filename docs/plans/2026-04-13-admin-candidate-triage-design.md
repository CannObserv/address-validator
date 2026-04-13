# Admin Candidate Triage — Design

**Date:** 2026-04-13
**Status:** Design — pending approval to implement

## Context

The custom usaddress model deployed on 2026-03-28 (`training/sessions/2026_03_28-multi_unit/`) is parsing cleanly at 98.6% over 3,095 requests. The remaining 1.3% (41 Ambiguous parses) flow into `model_training_candidates` via `services/training_candidates.py`.

The schema already supports a triage lifecycle — `status` CHECK constraint: `new | reviewed | labeled | rejected`, plus a free-text `notes` column — but nothing writes those columns today. Candidates accumulate with no workflow hook, so real-world parse failures have no path to becoming the next training session's candidate set.

Inspection of the current 47 rows shows the dominant unhandled pattern is **chained/comma-separated unit designators** (e.g. `STE 110, SMP - 2`, `UNIT D160, BLDG 1, LOWER LEVEL`, `FRNT C`, `SPC E-1`) concentrated in WA/OR/CA cannabis licensee addresses. Many addresses repeat — 47 rows represent roughly 25 distinct raw strings.

## Goal

Add a coarse-triage admin surface that lets an admin:

- Browse training-candidate submissions, grouped by `raw_address`
- Mark groups as `reviewed` (queue for next training session) or `rejected` (noise / out of scope)
- Attach free-text notes
- Drill into a group to inspect individual submissions including `parsed_tokens` and `recovered_components`

**Explicit non-goals** (out of scope):

- Bulk actions (multi-select)
- Pattern buckets / named taxonomy groupings
- In-browser token labeling (remains in `/train-model` conversation)
- Per-user attribution (`reviewed_by` / `reviewed_at`)
- Automated hand-off into `scripts/model/identify.py` — follow-up issue

## Design

### URL surface

```
GET   /admin/candidates/                      list view (grouped by raw_address)
GET   /admin/candidates/{raw_hash}            detail view
POST  /admin/candidates/{raw_hash}/status     HTMX — body: status=reviewed|rejected|new
POST  /admin/candidates/{raw_hash}/notes      HTMX — body: notes=<text>
```

`raw_hash` is the hex SHA-256 of the raw address, reusing the convention established by `cache_provider._make_pattern_key` (note: that helper hashes standardized components, not raw strings — we reuse the *algorithm*, not the same key).

Resolution strategy: add a generated column `model_training_candidates.raw_address_hash TEXT GENERATED ALWAYS AS (encode(sha256(raw_address::bytea), 'hex')) STORED` via a new Alembic migration, with an index. Requires the `pgcrypto` extension — add an `IF NOT EXISTS` CREATE EXTENSION to the migration.

### Group semantics

- Rows with `status = 'labeled'` are **excluded from the triage view entirely** — once a submission has been included in training data, it's done and should not resurface in triage.
- A group's displayed status:
  - If all non-`labeled` rows share one status, display that status
  - Otherwise display `mixed`
- A status action runs `UPDATE model_training_candidates SET status = ? WHERE raw_address = ? AND status != 'labeled'` — applies to every non-`labeled` row, including future submissions of the same raw string until re-triaged.
- Notes are group-scoped: the same UPDATE writes identical `notes` to all matching non-`labeled` rows.
- A new submission arriving after triage inserts with `status='new'`, so the group flips to `mixed` and resurfaces in default filters — desirable (the pattern is still showing up in traffic).

### List view

Default sort: `last_seen DESC`. Default filter: `status IN ('new', 'mixed')` from the last 30 days.

Columns:

| Column | Source |
|---|---|
| Raw address | `raw_address` (truncated, full in detail) |
| Status | rollup; badge styled from `CANDIDATE_STATUS_META` |
| Failure type(s) | `array_agg(DISTINCT failure_type)` |
| Count | `COUNT(*)` (excluding `labeled`) |
| First seen | `MIN(created_at)` |
| Last seen | `MAX(created_at)` |
| Notes | truncated; inline-editable via HTMX |
| Actions | Review / Reject / Reset buttons |

Filter bar (querystring, matching `audit_views.py` convention):

- `status` — `new` (default includes `mixed`), `reviewed`, `rejected`, `all`
- `failure_type` — `any` (default), `repeated_label_error`, `post_parse_recovery`
- `since` — `7d`, `30d` (default), `90d`, or ISO date
- Pagination — `limit=50`, `offset` — match existing admin paging

### Detail view

`GET /admin/candidates/{raw_hash}`:

- Summary card: raw address, rollup status, failure types, count, first/last seen
- Notes textarea (HTMX inline-save)
- Submissions table: every individual row with `id`, `created_at`, `failure_type`, per-row `status`
- Each submission expands to show its `parsed_tokens` (JSONB) as a token table and `recovered_components` (JSONB) as a component list — helps a triager understand *why* the parse failed
- Action buttons (Review / Reject / Reset) — apply to the group

### Code layout

```
src/address_validator/routers/admin/
    candidates.py              # list, detail, status, notes handlers
    queries/
        candidates.py          # SQLAlchemy Core query helpers
src/address_validator/templates/admin/candidates/
    index.html
    detail.html
    _rows.html                 # HTMX partial for grouped rows
    _status.html               # HTMX partial for a single row's status cell + buttons
    _notes.html                # HTMX partial for inline notes edit
```

Register the router in `routers/admin/router.py` alongside the existing sub-routers.

### Query helpers (`queries/candidates.py`)

- `get_candidate_groups(conn, *, status, failure_type, since, until, limit, offset)` — grouped list with rollup status, failure-type set, count, first/last seen, latest-notes
- `get_candidate_group(conn, raw_hash)` — group summary for detail view
- `get_candidate_submissions(conn, raw_hash)` — per-row submissions for detail view
- `update_candidate_status(conn, raw_hash, status)` — excludes `status='labeled'` rows
- `update_candidate_notes(conn, raw_hash, notes)` — excludes `status='labeled'` rows

Rollup status SQL:

```sql
CASE
    WHEN COUNT(DISTINCT status) = 1 THEN MIN(status)
    ELSE 'mixed'
END AS rollup_status
```

All queries filter `WHERE status != 'labeled'` except where explicitly including them for display.

### Status metadata (`routers/admin/_config.py`)

Add `CANDIDATE_STATUS_META` alongside existing `VS_META`:

```python
CANDIDATE_STATUS_META = {
    "new":      {"label": "New",      "symbol": "●", "color": "blue"},
    "reviewed": {"label": "Reviewed", "symbol": "✓", "color": "green"},
    "rejected": {"label": "Rejected", "symbol": "✗", "color": "gray"},
    "mixed":    {"label": "Mixed",    "symbol": "~", "color": "amber"},
}
```

Exact Tailwind color tokens to match existing admin palette (verify against `docs/STYLE.md`).

### Navigation

Add "Candidates" link to the admin nav between "Audit" and "Endpoints". Link label includes a count badge of `new`-status groups over the last 30 days — serves as a triage-queue indicator.

### Authorization and CSRF

- Auth: existing `AdminContext` via exe.dev proxy headers; any authenticated user is admin. No change.
- CSRF: follow existing admin surface stance (none — exe.dev SSO). If CSRF becomes a requirement, add it for the whole admin surface in a separate PR, not scoped to this feature.

### Test strategy

- **Query tests** (`tests/test_admin_candidate_queries.py`): seed fixtures with mixed statuses, duplicates, and a `labeled` row; assert:
  - rollup semantics (single status vs mixed)
  - filter behavior (status, failure_type, date range)
  - `labeled` rows are excluded from groups
  - UPDATE helpers do not mutate `labeled` rows
  - Notes round-trip correctly across a group
- **Router tests** (`tests/test_admin_candidate_views.py`):
  - Auth required (unauth → 302/401 per existing admin pattern)
  - List renders with filters applied
  - Detail 404 on unknown hash
  - HTMX action endpoints return the correct fragment and mutate DB
  - Notes round-trip
- **Template smoke**: render each template with representative context, assert no Jinja errors.
- **No JS changes** — `npm test` scope unchanged.
- Coverage must not drop below 80% line + branch (current baseline ~93%).

### Migration

New Alembic revision under `alembic/versions/`:

1. `CREATE EXTENSION IF NOT EXISTS pgcrypto;`
2. Add generated column `model_training_candidates.raw_address_hash TEXT GENERATED ALWAYS AS (encode(sha256(raw_address::bytea), 'hex')) STORED`
3. Add index `ix_model_training_candidates_raw_address_hash` on that column
4. Downgrade: drop index + column (leave pgcrypto installed — harmless)

No change to the existing `status` CHECK constraint or any other column.

## Deployment notes

- Tailwind rebuild required (new template classes) — handled by existing `pre-commit` hook.
- After merge: restart systemd service to pick up router + migration (`sudo systemctl restart address-validator`).
- Migration runs automatically during lifespan startup (see `db/engine.py`).

## Follow-ups (explicitly deferred, for a future issue)

1. Bulk multi-select triage actions — when queue > 50 groups
2. Pattern buckets / named taxonomy — once triage reveals whether explicit grouping pays off
3. In-browser token labeling — replaces `scripts/model/label.py` for agents without API access
4. `reviewed_by` / `reviewed_at` attribution — when multiple admins triage
5. `scripts/model/identify.py --from-reviewed` integration to pull `status='reviewed'` candidates into a new training session
