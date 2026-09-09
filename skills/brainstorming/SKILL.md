---
name: brainstorming
description: "Explores user intent, requirements, and design before any implementation. Use when the user says 'brainstorm', 'design this', or 'let's design'. Agent should also proactively suggest brainstorming when a new feature is requested without prior design discussion."
compatibility: Designed for Claude. Requires git and gh CLI. Python project using FastAPI, Pydantic, uv.
metadata:
  author: gregoryfoster
  version: "6.3.0"
  synced-from: "obra-superpowers v6.3.0 (b36e0829c6d0140e93cfef2ca599b1b07d4a7797)"
  triggers: brainstorm, design this, let's design
  overrides: obra-superpowers/brainstorming
  override-reason: "Hard-block variant (explicit approval words, no implied consent); docs/plans/ path convention; project commit convention for the design doc; GitHub issue opened per design; writing-plans offered, never mandatory; visual companion omitted (its files are not vendored into this override)"
---

# Brainstorming Ideas Into Designs — address-validator

Help turn ideas into fully formed designs through collaborative dialogue before any implementation begins.

Classify how much process the request needs, then work your path: understand the context, refine the idea, present a design, get explicit approval.

<HARD-GATE>
Do NOT write any code, create any files (other than the design doc), run any migrations, or take any implementation action until you have presented a design AND the user has explicitly approved it with "approved", "proceed", "looks good", or clearly equivalent. "sounds fine" or "okay" without affirmative intent does not count.

This applies to EVERY path below. The ceremony scales with the task; the approval gate never does.
</HARD-GATE>

## Three paths

Before your first question, classify the request and say the classification out loud — "this looks bounded, so I'll present a short design here rather than write a design doc" — so the user can override it:

- **Spike** — a feasibility question ("can we…", "is it possible…", "quick and dirty is fine") whose output is an answer, not code you keep. Present the question and what you'll try in 2–3 sentences, get approval, then find out as cheaply as correctness allows. No design doc, no issue. Report findings as a recommendation; anything you built stays labeled throwaway.
- **Bounded** — a well-scoped change to code that already exists in this repo: a new flag, a new warning string, a one-file fix. Understanding the kind of app is not enough — bounded means the flow you are changing is already here to read. If there is no existing flow to change, the task is not bounded. Ask the clarifying questions that matter, present a short design IN CHAT (a few sentences to a few short paragraphs), and STOP. No design doc, no plan document.
- **Architectural** — new subsystems, new endpoints, schema changes, changes that restructure how components fit together or alter API contracts others depend on. Follow the full process: questions, approaches, sectioned design, written design doc, GitHub issue.

When in doubt between two paths, take the heavier one. The ratchet is one-way: hidden complexity discovered mid-task upgrades the path — stop, say so, and step up. Nothing downgrades mid-task.

**This repo's tells.** A change touching `models.py`, `alembic/versions/`, `core/warnings.py`, `core/validation_status.py`, or `PIPELINE_CODE_VERSION` is architectural by default — those are documented single sources of truth with drift tests and migration requirements behind them (see AGENTS.md → Key conventions).

## Anti-pattern: "too simple to need approval"

Every path ends with the user approving your intent before implementation. A one-line fix, a config change, a new constant — the design may be two sentences in chat, but you MUST present it and get approval. "Simple" tasks are where unexamined assumptions cause the most wasted work. What scales with simplicity is the artifact, never the approval.

## Red flags

| Thought | Reality |
|---|---|
| "This is too simple to need a design" | Simple means a short design, not no design. Two sentences in chat, then approval. |
| "I'll call it bounded and skip the design doc" | Reaching for a label to skip work IS the doubt — take the heavier path. |
| "It's bounded and the design is obvious — I'll start while they read it" | The gate is the approval, not the design's length. Present, then stop until you hear yes. |
| "I understand this kind of service, so it's bounded" | Bounded measures the repo, not your familiarity. No existing flow to change means architectural. |
| "The spike works, so I'll keep the code" | A spike's output is an answer. Keeping the code is a new request — classify it. |
| "It grew, but I'm almost done — no need to re-classify" | Hidden complexity upgrades the path mid-task. Stop and say so. |
| "They approved the spike, so the follow-up is approved too" | Each task gets its own classification and its own approval. |

## Proactive suggestion

If the user describes a new feature, significant refactor, or new endpoint **without** first asking to brainstorm or design, respond with:

> Before diving in — this sounds like a good candidate for a quick design pass. Want me to run brainstorming first, or do you already have a design in mind?

## Checklists

Classify first, announce the path, then work the items in order.

**Spike:**
1. **Explore project context** — enough to frame the probe
2. **Present question + probe plan** — 2–3 sentences
3. **Get approval**
4. **Investigate** — as cheaply as correctness allows
5. **Report findings** — a recommendation; label anything built as throwaway

**Bounded:**
1. **Explore project context** — AGENTS.md, the files in the area, recent commits
2. **Ask clarifying questions** — one at a time, the ones that matter
3. **Present short design in chat** — approach, files touched, test strategy
4. **Get approval** — STOP and wait for an explicit yes; presenting the design and starting in the same breath skips the gate
5. **Implement** — normal workflow (`test-driven-development` applies); no design doc, no plan document

**Architectural:**
1. **Explore project context** — AGENTS.md, README.md, `git log --oneline -10`, relevant source
2. **Ask clarifying questions** — one at a time; purpose, constraints, success criteria, scope boundaries
3. **Propose 2–3 approaches** — trade-offs, lead with your recommendation
4. **Present design** — in sections scaled to their complexity, approval after each section
5. **Write design doc** — `docs/plans/YYYY-MM-DD-<topic>-design.md`, then commit
6. **Design self-review** — inline check for placeholders, contradictions, ambiguity, scope
7. **User reviews the written doc** — ask before proceeding
8. **Open a GitHub issue** — track the work
9. **Transition to implementation** — offer `writing-plans`; do not invoke it unbidden

## The process

The subsections below serve the bounded and architectural paths. A spike stops at "present the probe, get approval". Everything from **Exploring approaches** onward is architectural-path depth.

**Understanding the idea:**

- Read AGENTS.md, README.md, and recent commits (`git log --oneline -10`) first
- Survey the source files for the area being changed; note existing patterns to preserve or extend
- Before asking detailed questions, assess scope: if the request describes multiple independent subsystems, flag it immediately rather than refining details of something that needs decomposing first
- If it is too large for a single design, help decompose it into sub-projects — what are the independent pieces, how do they relate, what order? Each sub-project then gets its own design → issue → implementation cycle
- Ask questions one at a time; never more than one question per message
- Prefer multiple choice when possible; open-ended when necessary
- Focus on purpose, constraints, success criteria, scope boundaries

**Exploring approaches:**

- Propose 2–3 approaches with trade-offs
- Lead with your recommended option and explain why
- Consider implementation complexity, test surface, and AGENTS.md convention alignment
- YAGNI ruthlessly — remove unnecessary scope from every approach

**Presenting the design:**

- Scale each section to its complexity: a few sentences if straightforward, up to 300 words if nuanced
- Cover the dimensions that apply: API contract changes, service layer impact, data models, migrations, error handling, warnings/status vocabulary, test strategy
- Ask after each section: "Does this look right so far?"
- Revise until the user explicitly approves the full design

**Design for isolation and clarity:**

- Break the work into units with one clear purpose, well-defined interfaces, and independent testability
- For each unit: what does it do, how do you use it, what does it depend on?
- Can someone understand a unit without reading its internals? Can you change the internals without breaking consumers? If not, the boundaries need work
- A file growing large is usually a signal it is doing too much

**Working in existing code:**

- Explore the current structure before proposing changes; follow existing patterns
- Where existing code has problems that affect the work, include targeted improvements in the design
- Don't propose unrelated refactoring — stay focused on what serves the current goal

<HARD-GATE>
Do not proceed to the design doc until the user has explicitly approved the design.
</HARD-GATE>

## After the design (architectural path)

**Write the design doc.** Save the validated design to:

```
docs/plans/YYYY-MM-DD-<topic>-design.md
```

Commit it using the project convention (AGENTS.md → Commit convention):

```
[docs]: add <topic> design doc          # issue number not yet known
#<n> [docs]: add <topic> design doc     # issue number known
```

**Design self-review.** After writing, look at it with fresh eyes:

1. **Placeholder scan** — any "TBD", "TODO", incomplete sections, vague requirements? Fix them.
2. **Internal consistency** — do any sections contradict each other? Does the design match the feature description?
3. **Scope check** — focused enough for a single implementation pass, or does it need decomposition?
4. **Ambiguity check** — could any requirement be read two ways? Pick one and make it explicit.

Fix issues inline. No need to re-review — fix and move on.

**User review gate.** Then ask:

> "Design doc written and committed to `<path>`. Please review it and let me know if you want any changes before we go further."

Wait for the response. If they request changes, make them and re-run the self-review. Only proceed once the user approves.

**Open a GitHub issue.** After the doc is approved, create the tracking issue:

```bash
gh issue create \
  --title "<topic — concise imperative phrase>" \
  --body "$(cat <<'EOF'
## Summary
<1–3 sentence summary of what was designed>

## Design doc
`docs/plans/YYYY-MM-DD-<topic>-design.md`

## Scope
<bullet list of the key decisions / in-scope items from the design>
EOF
)"
```

- Title: short imperative phrase matching the design topic (e.g. "Add rate-limit header to validate endpoint")
- Report the issue number to the user (e.g. "Opened #42")
- If the issue number was not known at commit time, note the doc path in the issue body rather than amending the commit

**Transition to implementation.** Present a summary of what was decided, then offer:

- `writing-plans` to create a detailed implementation plan — appropriate for larger features, **optional**
- Direct implementation for smaller work

Do NOT invoke any implementation action without user direction.

## Key principles

- **One question at a time** — never overwhelm
- **YAGNI** — remove unnecessary scope from every design
- **Explicit approval required** — ambiguity does not count as approval, on every path
- **Classify out loud** — the user can only override a path they can see
- **`docs/plans/` is our convention** — not `docs/` root, not project root
- **`writing-plans` is optional** — useful for large features, never a mandatory terminal state

## Deliberate deviations from obra-superpowers v6.3.0

Recorded so the next sync can tell a deviation from a drift:

| Vendor | Here | Why |
|---|---|---|
| Approval may be "a nod" | Explicit approval words required | Hard-block variant — implied consent has burned us |
| `docs/superpowers/specs/` | `docs/plans/` | Project convention (AGENTS.md) |
| "Commit the design document" | `[docs]:` / `#<n> [docs]:` prefix | Project commit convention (AGENTS.md) |
| No issue step | `gh issue create` after the doc | Work here is issue-tracked |
| Architectural MUST end in `writing-plans` | Offered, never mandatory | Small architectural changes don't earn a plan doc |
| Visual companion + `scripts/` | Omitted | Those files are not vendored into this override; the vendor's `skills/brainstorming/visual-companion.md` path would not resolve |
| Process-flow digraph | Omitted | The path checklists carry the same routing |
