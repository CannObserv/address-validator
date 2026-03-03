# Playbooks

Shorthand commands that map to well-defined, repeatable workflows. When the user references a playbook by name or trigger phrase, execute the matching procedure.

Playbooks are parameterized — the user may append context (e.g., `code review entities.py`, `CR #14`). When no scope is specified, infer from conversation context.

---

## `review` — Code & Documentation Review

**Triggers:** "CR", "code review", "perform a review"

**Purpose:** Systematic review of code and documentation changes, structured for efficient async feedback.

### Scope Detection

Determine what to review, in priority order:
1. **Explicit scope** — user specifies files, a branch, a commit range, or an issue number
2. **Conversation context** — if this conversation implemented changes, review those changes
3. **Uncommitted work** — `git diff` and `git diff --staged`
4. **Ask** — if scope is ambiguous, ask before proceeding

### Procedure

#### Phase 1: Gather Context
- Read the diff (branch comparison, commit range, or working tree)
- Read AGENTS.md conventions relevant to the changed files
- Identify all files touched and their roles in the architecture
- Check the live app if UI changes are involved (browser screenshots)
- Run the app/imports to catch syntax errors

#### Phase 2: Analyze
Evaluate against these dimensions:
- **Correctness** — bugs, logic errors, edge cases, off-by-ones
- **Data integrity** — schema constraints, migration safety, FTS sync
- **Convention compliance** — AGENTS.md patterns (logging, naming, SQL style, template conventions)
- **Documentation** — do AGENTS.md, README.md, and code comments reflect the changes?
- **Robustness** — error handling, graceful degradation, idempotency
- **UX consistency** — if templates changed, do they follow the style guide?

#### Phase 3: Present Findings

Format the review as a structured report:

1. **Title** — `## Code & Documentation Review — [scope description]`

2. **What's solid** — brief list of things done well (reinforces good patterns; not filler)

3. **Numbered findings** — every actionable item gets a single, globally unique number
   - **Numbering is sequential across all severity groups.** Do NOT restart at 1 for each group. If bugs are items 1–3, the first "issue to fix" is 4, the first "minor" continues from there, etc.
   - **Top-level items:** `1.`, `2.`, `3.` (ever-incrementing, never reset within a review round)
   - **Sub-items:** `2a.`, `2b.` (for related points under one finding)
   - Each item includes:
     - **What:** precise description of the issue with file/line references
     - **Why it matters:** impact (bug? style? future maintenance?)
     - **Suggested fix:** concrete, not vague (code snippets when helpful)
   - Group by severity (but numbering flows continuously through all groups):
     - 🔴 **Bugs** — incorrect behavior, crashes, data corruption risk
     - 🟡 **Issues to fix** — not broken but should be addressed before shipping
     - 💭 **Minor / observations** — style, optional improvements, things noted but not blocking

4. **Summary** — 1–2 sentences on overall assessment and which items are highest priority

#### Phase 4: Wait for Feedback

**Stop and wait.** Do not make any changes until the user responds.

The user will respond with terse directives referencing item numbers:
- `1: fix` — implement the suggested fix
- `3: stet` — leave as-is (acknowledged, no action)
- `5: fix, but use X approach instead` — fix with user's preferred approach
- `2: document as TODO` — don't fix now, add a code comment or AGENTS.md note
- `7: investigate further` — gather more information before deciding
- `10: GH` - create or update a corresponding GitHub issue

After receiving directives, implement all requested changes, commit, and present a summary table of what was done.

### If a Second Review Round is Requested

Continue numbering from where the previous round left off (e.g., if the first round ended at item 18, the second starts at 19). This maintains unambiguous references across the full review conversation.

### Documentation Sweep

If the reviewed changes affect:
- Database schema → update AGENTS.md schema section
- New files or public APIs → update AGENTS.md Key Files table and relevant sections
- User-facing behavior → update README.md
- Deployment or CLI → update AGENTS.md Common Tasks

The review should flag missing documentation updates as numbered items.

---

## `ship` — Commit, Push, and Close GitHub Issues

**Triggers:** "ship it", "push GH", "close GH", "wrap up"

**Purpose:** Finalize work by ensuring everything is committed, pushed, and reflected on GitHub.

### Scope Detection

Determine which GitHub issue(s) to close, in priority order:
1. **Explicit scope** — user specifies issue number(s) (e.g., `wrap up #19 #20`)
2. **Conversation context** — infer from issues referenced in recent commit messages or discussion
3. **Ask** — if ambiguous, confirm before closing anything

### Procedure

#### Step 1: Ensure Clean Working Tree
- Check `git status` for uncommitted changes
- If changes exist, commit them with the `#<number>: ` message prefix convention
- If multiple issues are in scope, prefix with all (e.g., `#19, #20: ...`)

#### Step 2: Ensure on `main`
- If on a feature branch, merge to `main` first
- If already on `main`, continue

#### Step 3: Push to Origin
- `git push origin main`
- Confirm push succeeded

#### Step 4: Comment on GitHub Issues
- For each issue in scope, post a summary comment via `gh issue comment`
- Comment should include:
  - What was implemented (brief, 2–4 bullets)
  - Commit range or key commit SHAs
  - Any follow-up items or known limitations noted during implementation

#### Step 5: Close GitHub Issues
- `gh issue close <number>` for each issue in scope
- Confirm closure succeeded

#### Step 6: Report
- Present a summary table:

| Issue | Title | Status | Comment |
|---|---|---|---|
| #19 | ... | ✅ Closed | Summary posted |

### Notes
- If `gh` CLI hits errors (e.g., Projects Classic deprecation), use `--json` flag workarounds as needed
- Never close an issue that wasn't fully implemented — ask first if uncertain
- If tests haven't been run this session, run them before pushing


---

## `architecture-review` — Architectural Review

**Triggers:** "AR", "architecture review", "architectural review"

**Purpose:** High-level review of application architecture, evaluating structural health, adherence to design principles, and long-term maintainability — distinct from line-level code review.

### Scope Detection

Determine what to review, in priority order:
1. **Explicit scope** — user specifies apps, modules, layers, or areas of concern
2. **Conversation context** — if recent work touched a subsystem, review that subsystem
3. **Full project** — if no scope is given or implied, review the entire project
4. **Ask** — if scope is ambiguous, ask before proceeding

### Procedure

#### Phase 1: Gather Context
- Read AGENTS.md, README.md, and project layout documentation
- Survey the full directory tree and identify all modules, apps, and layers
- Read key files: settings, URL routing, models, entry points, service configs
- Note dependency graph between modules (imports, shared state, coupling)
- Check file sizes (`wc -l`) across all source files to flag oversized modules
- Review `pyproject.toml` for dependency health (unused deps, pinning strategy)

#### Phase 2: Analyze

Evaluate against these architectural dimensions:

- **DRY (Don't Repeat Yourself)** — duplicated logic, copy-pasted code, constants defined in multiple places, parallel structures that should be unified
- **Module size & cohesion** — files that are too large or mix unrelated concerns; any source file over ~300 lines deserves scrutiny, over ~500 lines is a strong signal to split
- **Separation of concerns** — clear boundaries between layers (models, views, services, serialization); business logic leaking into request handlers or templates
- **Coupling & dependency direction** — circular imports, tight coupling between modules that should be independent, violations of layered architecture (e.g., lower layers importing from higher ones)
- **Efficiency & performance** — N+1 query patterns, missing database indexes on filtered/sorted columns, unnecessary eager loading, unoptimized loops over large collections, missing caching opportunities
- **Configuration & environment** — secrets management, environment-specific settings, hardcoded values that should be configurable
- **Error handling patterns** — inconsistent error handling strategies across modules, bare excepts, swallowed errors, missing retry/backoff on external calls
- **Naming & discoverability** — module and package names that obscure purpose, inconsistent naming conventions across apps, files whose role is unclear from name alone
- **Schema & data model health** — missing constraints, denormalization without justification, orphaned tables/columns, migration history cleanliness
- **Scalability concerns** — patterns that will break at 10× or 100× current scale, synchronous work that should be async, missing pagination
- **Test architecture** — test isolation, fixture reuse, test speed bottlenecks, gaps in coverage by layer

#### Phase 3: Present Findings

Format the review following the same structure as a code review:

1. **Title** — `## Architectural Review — [scope description]`

2. **Architecture summary** — brief description of the current architecture (2–4 sentences), serving as shared context for the findings

3. **What's solid** — architectural strengths worth preserving (not filler — genuine positives that inform future decisions)

4. **Numbered findings** — every actionable item gets a single, globally unique number
   - **Numbering is sequential across all severity groups.** Do NOT restart at 1 for each group.
   - **Top-level items:** `1.`, `2.`, `3.` (ever-incrementing, never reset within a review round)
   - **Sub-items:** `2a.`, `2b.` (for related points under one finding)
   - Each item includes:
     - **What:** precise description with file/module references
     - **Why it matters:** architectural impact (maintainability? performance? correctness?)
     - **Suggested approach:** concrete refactoring direction (not vague — name new modules, describe the split, sketch the pattern)
   - Group by severity (numbering flows continuously through all groups):
     - 🔴 **Structural problems** — architectural issues causing bugs, data integrity risk, or blocking feature development
     - 🟡 **Design improvements** — not broken, but the architecture would meaningfully benefit from change
     - 💭 **Observations & opportunities** — minor structural notes, forward-looking suggestions, patterns to adopt over time

5. **Summary** — 1–2 sentences on overall architectural health and highest-priority items

#### Phase 4: Wait for Feedback

**Stop and wait.** Do not make any changes until the user responds.

The user will respond with terse directives referencing item numbers:
- `1: fix` — implement the suggested refactoring
- `3: stet` — leave as-is (acknowledged, no action)
- `5: fix, but use X approach instead` — refactor with user's preferred approach
- `2: document as TODO` — don't fix now, add a code comment or AGENTS.md note
- `7: investigate further` — gather more information before deciding
- `10: GH` — create or update a corresponding GitHub issue

After receiving directives, implement all requested changes, commit, and present a summary table of what was done.

### If a Second Review Round is Requested

Continue numbering from where the previous round left off (e.g., if the first round ended at item 12, the second starts at 13). This maintains unambiguous references across the full review conversation.

### Documentation Sweep

If the review leads to structural changes, update:
- AGENTS.md project layout and architecture sections
- README.md if module boundaries or service topology changed
- Any module-level docstrings affected by refactoring
