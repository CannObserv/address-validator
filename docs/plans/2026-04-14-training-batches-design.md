# Training Batches & Candidate Assignment — Design

**Date:** 2026-04-14
**Builds on:** #102 (admin candidate triage surface)
**Issue:** TBD (opened after this doc is committed)

## Summary

Reshape the triage workflow so that every accepted candidate is **assigned to a
training batch** (existing or planned) rather than vaguely "reviewed". Introduce
a formal lifecycle for training work and surface more submission context on the
triage detail screen.

Three interlocking pieces:

1. **Rename** "training session" → "training **batch**" across code, docs,
   skill, templates, and filesystem (`training/sessions/` →
   `training/batches/`).
2. **Lifecycle** — coarse `status` (planned / active / deployed / observing /
   closed) plus fine-grained `current_step` (identifying / labeling / training
   / testing / deployed / observing / contributed). Multiple batches run in
   parallel. `closed` absorbs the previous "contributed" terminal state.
3. **Assignment** — triage status becomes `new / assigned / rejected /
   labeled`. A candidate group can be assigned to **multiple** batches (M:N
   via join table). Unassignment is per-batch.

## 1. Nomenclature & directory layout

- `training/sessions/` → `training/batches/` (one `git mv`).
- Skill rename: `/train-model` copy updated to say "batch" throughout; CLI
  flags unchanged.
- `manifest.json` stays on disk as the artifact ledger but is no longer the
  source of truth for `status` / `current_step` / assignment — those live in
  the DB.

## 2. Data model

### New table `training_batches` (Alembic migration 013)

| column | type | notes |
|---|---|---|
| `id` | `text` PK | ULID (26-char Crockford base32); generated in app code, consistent with `request_id` convention |
| `slug` | `text UNIQUE NOT NULL` | e.g. `2026_03_28-multi_unit` |
| `description` | `text NOT NULL` | human summary |
| `targeted_failure_pattern` | `text` | optional — e.g. `repeated_label_error`, `bldg-numeric-id` |
| `status` | `text NOT NULL` CHECK IN (`planned`, `active`, `deployed`, `observing`, `closed`) | |
| `current_step` | `text` CHECK IN (`identifying`, `labeling`, `training`, `testing`, `deployed`, `observing`, `contributed`) | nullable for `planned` |
| `manifest_path` | `text` | relative, e.g. `training/batches/2026_03_28-multi_unit` |
| `upstream_pr` | `text` | URL, set when contributed |
| `created_at` | `timestamptz NOT NULL DEFAULT now()` | |
| `activated_at` / `deployed_at` / `closed_at` | `timestamptz` | status-transition timestamps |

### New table `candidate_batch_assignments` (M:N join)

| column | type | notes |
|---|---|---|
| `raw_address_hash` | `text NOT NULL` | references `model_training_candidates.raw_address_hash` |
| `batch_id` | `text NOT NULL REFERENCES training_batches(id) ON DELETE CASCADE` | ULID |
| `assigned_at` | `timestamptz NOT NULL DEFAULT now()` | |
| `assigned_by` | `text` | exe.dev email from admin auth context |
| PRIMARY KEY | `(raw_address_hash, batch_id)` | natural idempotency |

### Changes to `model_training_candidates` (same migration)

- **Add columns** — denormalized from audit at write time (survives 90-day
  audit retention):
  - `endpoint text`
  - `provider text`
  - `api_version text`
  - `failure_reason text` — short free-form written by the parser at recovery
    time (e.g. `"RepeatedLabelError on token 'ROOM'"`).
- **Relax status CHECK** — drop `reviewed`, add `assigned`. Migration updates
  any existing `reviewed` rows → `new`.

### Derived group status (no stored `assigned` column)

Admin query computes group rollup as before; the mapping is:

- `labeled` if any row has `status='labeled'` (these rows stay excluded from
  the triage view entirely).
- `rejected` if all non-labeled rows are `rejected`.
- `assigned` if ≥1 row has `status='new'` **and** ≥1 active assignment exists
  in `candidate_batch_assignments`.
- `new` otherwise.
- `mixed` rollup semantics retained for genuinely divergent groups.

### Write semantics — per-batch unassign

Removing the last assignment while rows are `status='assigned'` reverts those
rows to `status='new'`. Implemented in `queries/candidates.py:unassign_from_batch()`
as a single transactional statement.

## 3. Admin UX

### New page `/admin/batches/`

List all batches. Columns: slug · status · current_step · description ·
assigned count · last activity. Filter by status. "Plan new batch" button →
modal (slug, description, targeted_failure_pattern).

### `/admin/batches/{slug}/`

Batch detail: metadata, status-transition history with timestamps, list of
assigned candidate groups, `manifest.json` preview, links to `rationale.md` /
`performance.md` when present.

### Triage list (`/admin/candidates/`) changes

- Status filter chips: `new | assigned | rejected | all` (drop `reviewed`).
- New column **Batches** — pill list of assigned batch slugs.
- Row action on `new` groups: "Assign to batch" dropdown (populated with
  `planned` + `active` batches) + "Create new batch" option (inline modal).

### Triage detail (`/admin/candidates/{raw_hash}/`) additions

- **Submission context** section on each submission row: `endpoint`,
  `provider`, `api_version`, `failure_reason` (tooltip-revealed line under
  the failure_type badge).
- **Batches** panel: list of assigned batches with per-batch unassign button;
  "Assign to batch" action.

Nav badge unchanged (counts groups whose rollup is `new`).

## 4. API / service impact

No v1/v2 API contract changes. All surface area is admin + training pipeline.

- `services/training_candidates.py:write_training_candidate()` — extended
  signature: `endpoint`, `provider`, `api_version`, `failure_reason`. Call
  sites:
  - `parser.py` populates `failure_reason` at each recovery point.
  - Audit middleware fills endpoint/provider/api_version from request scope +
    ContextVars before the write fires.
- **New** `services/training_batches.py` — CRUD helpers (`create_batch`,
  `transition_status`, `advance_step`, `assign_candidates`,
  `unassign_candidates`). Consumed by admin routes and `scripts/model/*.py`.
- `scripts/model/identify.py` — add `--batch <slug>` to auto-assign exported
  candidates and `--create-batch <slug>` to bootstrap a `planned` batch.
- `scripts/model/train.py`, `deploy.py`, `performance.py`, `contribute.py` —
  each advances the batch's `current_step` and, at step boundaries, its
  `status` via `transition_status()`.

### State machine

Single source of truth: `_ALLOWED_TRANSITIONS` dict in
`services/training_batches.py`.

```
planned  → active
active   → deployed | closed
deployed → observing | closed
observing → closed
(any)    → closed (explicit abandon path)
```

Invalid transitions raise `ValueError`, surfaced as HTTP 400 by admin routes.

## 5. Error handling, testing, rollout

- **Assignment idempotency** — PK on `(raw_address_hash, batch_id)` +
  `ON CONFLICT DO NOTHING`.
- **ULID generation** — reuse the same lib already used for `request_id`;
  IDs generated in app code (not DB default) for testability.
- **Tests:**
  - Query helpers for batches — parallel to `queries/candidates.py`.
  - Write-helper tests for the M:N join — parallel to #102's
    `test_candidates_write.py`.
  - State-machine transition tests (allowed + denied paths).
  - Migration test verifying `reviewed → new` data move.
  - Admin route tests for assign / unassign / plan-new-batch flows.
  - Skill dry-run integration smoke for `--batch` / `--create-batch`.
- **Rollout:**
  - One Alembic migration (013): tables + column adds + status-CHECK relaxation + `reviewed → new` update + seed INSERT for the existing
    `multi_unit` batch (`status='deployed'`, `current_step='deployed'`,
    pointing at its relocated `manifest_path`).
  - One filesystem `git mv` (`training/sessions/` → `training/batches/`).
- **Out of scope (YAGNI):** batch-level metrics dashboards, cross-batch
  candidate dedup, per-batch notifications, multi-tenant batch ownership.
