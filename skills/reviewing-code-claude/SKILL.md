---
name: reviewing-code-claude
description: Performs a structured code and documentation review using a severity-tiered findings format. Use when the user says "CR", "code review", or "perform a review". Produces a numbered findings report, waits for terse directives (fix/stet/GH), then implements and commits approved changes.
compatibility: Designed for Claude. Requires git and gh CLI. Python project using FastAPI, Pydantic, uv, ruff, pytest.
metadata:
  author: gregoryfoster
  version: "1.0"
  triggers: CR, code review, perform a review
  overrides: reviewing-code-claude
  override-reason: Adds ruff lint check to gather-context; replaces generic review dimensions with FastAPI/Pydantic-specific ones; uses uv run pytest/ruff
---

# Code & Documentation Review — address-validator

A systematic review workflow for this FastAPI/Pydantic/uv project. Produces a numbered findings report, waits for directives, then implements approved changes.

## Scope detection

Determine what to review (priority order):
1. **Explicit scope** — files, branch, commit range, or issue number specified by the user
2. **Conversation context** — changes implemented in this conversation
3. **Uncommitted work** — `git diff` and `git diff --staged`
4. **Ask** — if scope is ambiguous, ask before proceeding

## Procedure

### Phase 1 — Gather context

```bash
bash skills/reviewing-code-claude/scripts/gather-context.sh
```

Also:
- Read AGENTS.md conventions relevant to the changed files
- Identify all files touched and their roles in the architecture
- Check the live app if router/model changes are involved (browser screenshot of `/docs`)

### Phase 2 — Analyze

Evaluate against these dimensions:

- **Correctness** — bugs, logic errors, edge cases, off-by-ones
- **API contract** — Pydantic model changes; are field names geography-neutral? Does `models.py` remain the single source of truth?
- **Convention compliance** — AGENTS.md patterns: logging levels (no PII at INFO+), `getLogger(__name__)`, Conventional Commits format, router/service separation
- **Standardizer/parser sensitivity** — changes to `_get()`, `_lookup()`, post-parse recovery, or `usps_data/` tables have wide blast radius; flag explicitly
- **Auth** — any change near `/api/*` routes, `auth.py`, or the env file
- **Documentation** — do AGENTS.md, README.md, and comments reflect the changes?
- **Robustness** — error handling, idempotency, Pydantic `Field` constraints
- **Test coverage** — does coverage stay above 80%? New logic needs tests.

### Phase 3 — Present findings

Title: `## Code & Documentation Review — [scope]`

1. **What's solid** — genuine positives, not filler
2. **Numbered findings** — sequential across ALL severity groups, never reset between them
   - Top-level: `1.`, `2.`, `3.` — Sub-items: `2a.`, `2b.`
   - Each finding: **What** (file:line) · **Why it matters** · **Suggested fix** (code snippet when useful)
   - Groups: 🔴 Bugs → 🟡 Issues to fix → 💭 Minor/observations
3. **Summary** — 1–2 sentences on overall assessment and top priorities

### Phase 4 — Wait for feedback

**Stop. Do not make changes until the user responds.**

Accepted directives (reference by item number):

| Directive | Meaning |
|---|---|
| `1: fix` | Implement the suggested fix |
| `3: stet` | Leave as-is |
| `5: fix, but use X approach` | Fix with user's preferred approach |
| `2: document as TODO` | Add a code comment or AGENTS.md note |
| `7: investigate further` | Gather more information first |
| `10: GH` | Create or update a GitHub issue |

After directives, implement all requested changes, commit, and present a summary table:

| Item | Action | Result |
|---|---|---|
| 1 | Fixed | `services/parser.py:42 — added bounds check` |
| 3 | Stet | — |

## Second review rounds

Continue numbering from where the previous round ended. Never reset.

## Documentation sweep

If changes affect schema, new APIs, user-facing behaviour, or deployment — flag missing documentation updates as numbered findings.
