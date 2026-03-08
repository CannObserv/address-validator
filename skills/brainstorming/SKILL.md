---
name: brainstorming
description: "Explores user intent, requirements, and design before any implementation. Use when the user says 'brainstorm', 'design this', or 'let's design'. Agent should also proactively suggest brainstorming when a new feature is requested without prior design discussion."
compatibility: Designed for Claude. Requires git and gh CLI. Python project using FastAPI, Pydantic, uv.
metadata:
  author: gregoryfoster
  version: "1.0"
  triggers: brainstorm, design this, let's design
  overrides: brainstorming
  override-reason: "Hard-block variant (no code until design explicitly approved); uses docs/plans/ path convention; Conventional Commits for design doc commit; writing-plans is optional not mandatory terminal state"
---

# Brainstorming Ideas Into Designs — address-validator

Help turn ideas into fully formed designs through collaborative dialogue before any implementation begins.

<HARD-GATE>
Do NOT write any code, create any files (other than the design doc), run any migrations, or take any implementation action until you have presented a complete design AND the user has explicitly approved it with "approved", "proceed", "looks good", or equivalent.

This applies regardless of perceived simplicity. A "quick change" with unexamined assumptions wastes more time than a short design session.
</HARD-GATE>

## Proactive suggestion

If the user describes a new feature, significant refactor, or new endpoint **without** first asking to brainstorm or design, respond with:

> Before diving in — this sounds like a good candidate for a quick design pass. Want me to run brainstorming first, or do you already have a design in mind?

## Process

### Step 1 — Explore project context

- Read AGENTS.md, README.md, and recent commits (`git log --oneline -10`)
- Survey relevant source files for the area being changed
- Note existing patterns to preserve or extend

### Step 2 — Ask clarifying questions

- One question per message — do not ask multiple questions at once
- Prefer multiple-choice when possible; open-ended when necessary
- Cover: purpose, constraints, success criteria, scope boundaries
- Apply YAGNI ruthlessly — challenge scope that isn't clearly needed

### Step 3 — Propose 2–3 approaches

- State trade-offs clearly for each
- Lead with your recommended option and explain why
- Consider: implementation complexity, test surface, AGENTS.md convention alignment

### Step 4 — Present design

- Scale each section to its complexity (a few sentences for simple; up to 300 words for nuanced)
- Cover relevant dimensions: API contract changes, service layer impact, data models, error handling, test strategy
- Ask after each section: "Does this look right so far?"
- Revise until the user explicitly approves the full design

<HARD-GATE>
Do not proceed past Step 4 until the user says "approved", "proceed", "looks good", or clearly equivalent. "sounds fine" or "okay" without affirmative intent does not count.
</HARD-GATE>

### Step 5 — Write design doc

Save the validated design to:
```
docs/plans/YYYY-MM-DD-<topic>-design.md
```

Commit with:
```
docs: add <topic> design doc
```

(Use `#<n> docs:` prefix if associated with a GitHub issue.)

### Step 6 — Transition to implementation

Present a summary of what was decided. Offer to:
- Invoke `writing-plans` to create a detailed implementation plan (optional — appropriate for larger features)
- Proceed directly to implementation for smaller tasks

Do NOT invoke any implementation action without user direction.

## Key principles

- **One question at a time** — never overwhelm
- **YAGNI** — remove unnecessary scope from every design
- **Explicit approval required** — ambiguity does not count as approval
- **docs/plans/ is our convention** — not `docs/` root or project root
- **writing-plans is optional** — useful for large features; not mandatory for small ones
