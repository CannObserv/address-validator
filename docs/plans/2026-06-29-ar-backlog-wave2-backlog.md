# AR Backlog Wave 2 — Orchestration Plan (#133–141)

**Date:** 2026-06-29
**Skill:** `/orchestrating-issue-backlog`
**Precedent:** [2026-04-11-ar-backlog-orchestration.md](2026-04-11-ar-backlog-orchestration.md) (wave 1)

## Goal

Clear the second wave of `/reviewing-architecture` findings (#133–141) — nine maintainability/cohesion
refactors plus one perf item — as a prioritized, merge-safe, parallel execution plan. All nine are
behavior-preserving refactors filed from a single AR pass; the objective is to eliminate the duplicated
guards, multi-source-of-truth drift, and oversized modules the review surfaced, without regressing the
~93% coverage baseline or the API contract in `models.py`.

## Approved approach

- **Five agents**, formed by bundling issues that rewrite the same file (cohesive define-then-use / sequential commits).
- **Two batches.** Batch A = 4 fully file-disjoint agents in parallel. Batch B = 1 agent (PARSER), gated on A.
- **No host-project worktree ceiling** — `worktree-create.sh` is plain `git worktree add` into `.worktrees/`;
  TDD agents run `uv run pytest` only (no per-worktree port pool / Nginx vhost). Per-batch parallelism capped
  at file-disjoint count (4). No chunking needed.
- **Merge strategy:** intra-batch worker→`batch/a` is fast-forward/regular-merge (fixed); `batch/a`→`main` is a
  **regular merge commit** (preserves per-agent history). Batch B is single-agent — its feature branch is the batch branch.
- **Deployment context:** early production — keep each batch green and reversible.

## Prioritization rubrics

Quality priority (Q1) = **maintainability first**, so Foundation Leverage is weighted highest.

**Score = (Foundation × 3) + (Correctness × 2) + Scope**, max 18.

| Dimension | 1 | 2 | 3 |
|---|---|---|---|
| **Foundation Leverage** | Standalone improvement | 1–2 other things benefit | Multiple modules/issues depend on or are simplified by this |
| **Correctness Risk** | Cosmetic / organizational | Edge-case incorrect behavior, runtime failure risk | Data loss, race conditions, silent failures |
| **Scope Clarity** | Requires design discovery | Clear direction, minor decisions | Mechanical — obvious from the issue |

Blast radius (file contention with other issues in this set) drives **sequencing**, not score.

## Scored backlog

| # | Issue | Found. | Corr. | Scope | **Score** | Blast |
|---|---|:--:|:--:|:--:|:--:|:--:|
| #136 | SSOT + drift guard for validation-status vocabulary | 3 | 3 | 2 | **17** | Med |
| #138 | Remove hidden request-scoped side effects from `parse_address` | 2 | 3 | 2 | **14** | High |
| #133 | Dedupe `component_profile` validation across v2 routers | 2 | 2 | 3 | **13** | High |
| #135 | Centralize `LibpostalUnavailableError`→503 translation | 2 | 2 | 3 | **13** | High |
| #134 | Factor shared response-assembly tail in parse/standardize | 2 | 2 | 2 | **12** | High |
| #139 | Split US/CA standardization, share line-assembly | 2 | 2 | 2 | **12** | Low |
| #137 | Split recovery heuristics out of `services/parser.py` | 2 | 1 | 2 | **10** | Med |
| #140 | Parallelize independent dashboard queries | 1 | 2 | 2 | **9** | Med |
| #141 | SSOT for v1/v2 endpoint list in admin queries | 1 | 1 | 3 | **8** | Med |

Scoring notes:
- **#136 (17)** — AR's own "highest priority." Foundation 3 (4 modules derive from the canonical tuple),
  Correctness 3 (already drifted: `VS_META` missing `not_found`/`unavailable`/`error`; silent desync of API contract + DB constraint + dashboard).
- **#138 (14)** — Correctness 3: rewires the audit/training ContextVar flow, a documented sensitive area
  (writes invisible to audit middleware silently break training-candidate collection).
- **#141 (8)** — pure DRY, cosmetic, but mechanical (Scope 3).

## Conflict zones (contested files + required merge order)

| Contested file | Issues | Required order |
|---|---|---|
| `routers/v2/parse.py` | #133, #134, #135, #138 | V2 bundle (A) → #138 (B) |
| `routers/v2/standardize.py` | #133, #134, #135, #138 | V2 bundle (A) → #138 (B) |
| `services/validation/pipeline.py` | #135, #138 | #135 (A) → #138 (B) |
| `services/component_profiles.py` | #133, #134 | within V2 bundle (#133 → #134) |
| `services/parser.py` | #137, #138 | within PARSER bundle (#138 → #137) |
| `models.py` | #136, #138 | different classes (`ValidationResult.status` vs `ParseResponseV2`); separated into different batches |
| `routers/admin/queries/dashboard.py` | #140, #141 | within ADMIN bundle (#141 → #140) |

Confirmed collision: `parse_address` is called from `routers/v2/parse.py:77`, `routers/v2/standardize.py:93`,
and `services/validation/pipeline.py:97,178`. #138 moves `set_audit_context`/`set_candidate_data` out of
`parser.py` into those callers — all rewritten by the V2 bundle — so #138 (PARSER) **must** follow Batch A.

## Dependency graph

```
Batch A — 4 parallel agents (file-disjoint):

   ┌─ STATUS (#136) ──── models.py[status], db/tables, _helpers, admin/_config, +core/validation_status.py, tests
   ├─ V2     (#133→134→135) ── v2 routers, component_profiles, pipeline[503], core/errors
   ├─ STD    (#139) ──── standardizer.py → package        (isolated)
   └─ ADMIN  (#141→140) ─ admin/queries/dashboard, _shared (isolated)

            │ GATE: Batch A merged to main, full suite green
            ▼
Batch B — 1 agent:

   PARSER (#138→#137)  needs merged v2 routers + pipeline (V2) AND merged models.py (STATUS)
```

## Batch execution plan

### Batch A — `batch/a`, 4 parallel agents

| Agent | Issues (commit order) | Files | Notes |
|---|---|---|---|
| **V2** | #133 → #134 → #135 | parse.py, standardize.py, validate.py, component_profiles.py (exists), pipeline.py, core/errors.py | #133: `valid_component_profile` FastAPI dependency. #134: `build_output_component_set` helper. #135: `raise_parsing_unavailable()` in core/errors. |
| **STATUS** | #136 | +`core/validation_status.py`, models.py, db/tables.py, services/validation/_helpers.py, routers/admin/_config.py, +drift test | Mirror warnings catalogue (#131/#132): canonical `VALIDATION_STATUSES` tuple → derive Literal / constraint doc / `VS_META`; add `tests/unit/test_validation_status_catalogue.py`. Fixes existing VS_META drift. |
| **STD** | #139 | standardizer.py → `standardizer/` package | Split `us.py` / `ca.py` / shared `_lines.py`; `standardize()` stays as dispatch entry. Fully isolated. |
| **ADMIN** | #141 → #140 | admin/queries/dashboard.py, admin/queries/_shared.py | #141: single ordered `{path: label}` map in `_shared.py`. #140: `asyncio.gather` over the deduped independent queries. |

Gate: start immediately after `git checkout main && git pull --ff-only && git checkout -b batch/a`.

### Batch B — single agent (feature branch = batch branch)

| Agent | Issues (commit order) | Files | Notes |
|---|---|---|---|
| **PARSER** | #138 → #137 | parser.py, models.py, pipeline.py, routers/v2/parse.py, routers/v2/standardize.py | #138: return `parse_type`/candidate metadata on `ParseResponseV2`; move `set_audit_context`/`set_candidate_data` writes to caller layer. #137: extract recovery heuristics → `services/parse_recovery.py` (`recover_components(...)`); `parser.py` keeps orchestration + `TAG_NAMES`. |

Gate: after Batch A reviewed, merged to `main` (regular merge commit), full suite green, local `main` re-synced.

## Key decisions

- **PARSER #138 leads #137** (correctness fix leads refactor). #138 rewires audit/training ContextVar flow —
  a documented sensitive area — and lands against the known-good structure first; the mechanical 250-line
  extraction (#137) rebases on top. Putting the surgical change first keeps its diff legible.
- **ADMIN does #141 before #140** despite #140's higher score — #141 establishes the single `{path: label}`
  mapping that #140's parallelized queries then build on (define-then-use).
- **STATUS + V2 run parallel despite both high-blast** — disjoint file sets; the only `models.py` contention
  is #138, isolated in Batch B.
- **PARSER alone in Batch B** — #138 conflicts with V2 (three `parse_address` callers) *and* STATUS (`models.py`),
  i.e. two of the four Batch-A agents; it rebases onto post-A `main` and inherits both.
- **No worktree ceiling** — 4 parallel agents = file-disjoint count; no sub-wave chunking.

## Deferred items

None. All nine issues (#133–141) are in scope (Q3).

## Out of scope

- Other open issues not in 133–141 (#123 research, #122 USPS-fields epic, #76 FGDC, #37 batch endpoint, #35 CORS).
- #138's downstream enabler value (reusing `parse_address` for the #37 batch endpoint / training tooling) is
  noted as motivation but not built here.
