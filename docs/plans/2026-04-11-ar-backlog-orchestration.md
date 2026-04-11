# Architectural Review Backlog — Orchestration Plan

**Date:** 2026-04-11
**Issues:** #93, #94, #95, #96, #97, #98, #99
**Tracking issue:** (added after commit)

---

## Goal

Clear the seven architectural findings from the 2026-04-11 AR in a parallel-safe sequence. All issues are refactors or a single correctness fix; no new features. Pre-production context — optimize for building it right over speed.

---

## Approved approach

Hybrid parallelism with git worktrees. Four batches: two parallel agents in Batch A (isolated file sets), one foundational agent in Batch B (high-blast import migration), two parallel agents in Batch C (disjoint DI and type-rename work), one pipeline extraction agent in Batch D. Regular merge commits to main preserve per-agent commit history.

---

## Prioritization rubrics

**Score = (Foundation × 2) + (Correctness × 2) + Scope** — max 15.

| Dimension | 1 | 2 | 3 |
|---|---|---|---|
| Foundation Leverage | Standalone improvement | 1–2 others benefit | Multiple issues depend on or are simplified by this |
| Correctness Risk | Cosmetic / organizational | Runtime failure risk | Data loss / silent failures |
| Scope Clarity | Needs design discovery | Clear direction, minor decisions | Mechanical |

Blast radius drives *sequencing*, not score.

---

## Scored backlog

| # | Title | F | C | S | Score | Blast |
|---|---|---|---|---|---|---|
| #93 | Move shared infra to `core/` | 3 | 1 | 2 | **10** | High |
| #96 | Inject registry/libpostal via `Depends` | 2 | 2 | 2 | **10** | Med |
| #99 | Fix `_API_ENDPOINTS` missing v2 paths | 1 | 2 | 3 | **9** | Low |
| #97 | Alias `StandardizeResponseV1` to version-neutral name | 2 | 1 | 2 | **8** | Med |
| #98 | Extract v2/validate pipeline to service layer | 2 | 1 | 2 | **8** | Med |
| #95 | Deduplicate `_COMPONENT_PROFILE_DESCRIPTION` | 1 | 1 | 3 | **7** | Low |
| #94 | Split `queries.py` into per-area modules | 1 | 1 | 2 | **6** | Low |

No issues appear closed by recent commits. No deferrals requested.

---

## Conflict zones

### Contested files

| File | Issues | Risk |
|---|---|---|
| `routers/v2/validate.py` | #93, #95, #96, #97, #98 | High — 5 issues; #95 (~line 73) and #93 (~line 81) adjacent |
| `routers/v2/parse.py` | #93, #95, #96 | Low — different sections (import, constant, signature) |
| `routers/v2/standardize.py` | #93, #95, #96 | Low — same as parse.py |
| `routers/v1/validate.py` | #93, #96, #98 | Med — must sequence |
| `routers/admin/queries.py` | #94, #99 | High — #94 restructures entire file; must follow #99 |

### Required merge order

`routers/v2/validate.py` drives the critical path:

1. **#95** — removes `_COMPONENT_PROFILE_DESCRIPTION` constant (~line 73)
2. **#93** — changes import line (~line 57) + removes `_build_non_us_std` (~line 81); adjacent to #95's removal; must not run parallel
3. **#96** — adds `Depends(...)` to function signature; inherits clean imports from #93
4. **#97** — updates internal type annotation; disjoint files from #96 (models/protocol/providers)
5. **#98** — rewrites handler body; inherits correct DI wiring (#96) and type name (#97)

`routers/admin/queries.py`:
- #99 first (add 3 strings to `_API_ENDPOINTS` tuple)
- #94 second (split entire file into per-area modules, update admin view imports)

---

## Dependency graph

```
A1(#95) ──────────────┐
                       │ [Batch A merged]
A2(#99 → #94) ────────┴──→ B1(#93) ──┬──→ C1(#96) ──┐
                                       │               ├──→ D1(#98)
                                       └──→ C2(#97) ──┘
```

---

## Batch execution plan

### Batch A — 2 parallel agents — start immediately

| Agent | Issues | Files touched |
|---|---|---|
| A1 | #95 | `services/component_profiles.py` (add constant), `routers/v2/parse.py`, `routers/v2/standardize.py`, `routers/v2/validate.py` (remove constant + import) |
| A2 | #99 → #94 (sequential commits) | `routers/admin/queries.py` (fix filter, then split), `routers/admin/dashboard.py`, `routers/admin/audit_views.py`, `routers/admin/endpoints.py`, `routers/admin/providers.py` (import updates) |

A1 and A2 have **zero file overlap**.

### Batch B — 1 agent — after A merged

| Agent | Issue | Files touched |
|---|---|---|
| B1 | #93 | `core/errors.py`[new], `core/countries.py`[new] (or similar), `routers/v1/core.py` (move symbols), `main.py`, all v1 routers (import updates), all v2 routers (import updates), `routers/v1/validate.py` + `routers/v2/validate.py` (remove `_build_non_us_std`, extract to shared location) |

### Batch C — 2 parallel agents — after B merged

| Agent | Issues | Files touched |
|---|---|---|
| C1 | #96 | `routers/deps.py`[new], `routers/v1/validate.py`, `routers/v2/validate.py`, `routers/v2/parse.py`, `routers/v2/standardize.py` (Depends injection) |
| C2 | #97 | `models.py` (add type alias), `services/validation/protocol.py`, `services/validation/usps_provider.py`, `services/validation/google_provider.py`, `services/validation/null_provider.py`, `services/validation/chain_provider.py`, `services/validation/cache_provider.py` |

C1 and C2 have **zero file overlap**.

### Batch D — 1 agent — after C merged

| Agent | Issue | Files touched |
|---|---|---|
| D1 | #98 | `services/pipeline.py`[new] (or `services/validation/pipeline.py`), `routers/v2/validate.py` (extract handler body), `routers/v1/validate.py` (share pipeline where logic overlaps) |

---

## Key decisions

**#95 in Batch A, not parallel with #93 in Batch B.**
Both remove adjacent blocks from `routers/v2/validate.py` (~lines 73 and ~81 respectively). Proximity conflict risk on the batch branch outweighs the one-batch speedup.

**#99 leads #94 within A2 (same agent, sequential commits).**
#94 restructures `queries.py` into submodules. Applying the v2 endpoint filter fix (#99) on a pre-split file then splitting is cleanest. Doing #94 first then patching across new module files would be unnecessarily fiddly.

**#97 parallel with #96, not after.**
Models, protocol, and providers are genuinely disjoint from the router DI files. No reason to serialize; parallel saves a batch.

**#93 in its own Batch B.**
Foundational import migration across all v1 and v2 routers. Isolating it makes the batch branch diff reviewable and ensures all subsequent issues (B, C, D) start from a clean import graph.

**Regular merge commits to main.**
Preserves per-agent commit history; each issue's changes remain attributable.

---

## Deferred items

None. All seven issues are in scope.

---

## Out of scope

- Observation #10 (VS_META / display ordering leaking into queries) is partially addressed by #94's split, but moving VS_META ordering into the template layer is not in scope for this batch.
- Observation #11 (lifespan growing responsibilities) — noted but not actioned; no issue opened.
