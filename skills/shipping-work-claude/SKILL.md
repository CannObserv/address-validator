---
name: shipping-work-claude
description: "Finalizes work by ensuring everything is committed, pushed to the remote, and reflected on GitHub: closes issues, posts summary comments, and presents a completion table. Use when the user says 'ship it', 'push GH', 'close GH', or 'wrap up'."
compatibility: Designed for Claude. Requires git and gh CLI. Python project using uv, ruff, pytest, systemd.
metadata:
  author: gregoryfoster
  version: "1.0"
  triggers: ship it, push GH, close GH, wrap up
  overrides: shipping-work-claude
  override-reason: "Concrete test/lint commands (uv run pytest, uv run ruff check); project commit convention (#n [type]: desc)"
---

# Shipping Work — address-validator

Finalizes work: lint, tests, clean commit, push, GitHub issue comments and closure.

## Scope detection

Determine which GitHub issue(s) to close (priority order):
1. **Explicit scope** — user specifies issue number(s) (e.g., `wrap up #19 #20`)
2. **Conversation context** — issues referenced in recent commit messages or discussion
3. **Ask** — if ambiguous, confirm before closing anything

## Procedure

### Step 1 — Lint and test

```bash
bash skills/shipping-work-claude/scripts/pre-ship.sh
```

Do not proceed if lint or tests fail.

### Step 2 — Ensure a clean working tree

```bash
bash skills/shipping-work-claude/scripts/check-status.sh
```

If uncommitted changes exist, commit them using this project's convention:
```
#<number> [type]: <description>       # with GH issue
[type]: <description>                 # without GH issue
```
Multiple issues: `#19, #20 [type]: <description>`  
Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`

### Step 3 — Ensure on main

If on a feature branch, merge to `main` first. Then continue.

### Step 4 — Push

```bash
bash skills/shipping-work-claude/scripts/push.sh
```

### Step 5 — Comment on GitHub issues

For each issue in scope:

```bash
bash skills/shipping-work-claude/scripts/comment-issue.sh <number> "<summary>"
```

Comment must include:
- What was implemented (2–4 bullets)
- Key commit SHAs or commit range
- Any follow-up items or known limitations

### Step 6 — Close GitHub issues

```bash
bash skills/shipping-work-claude/scripts/close-issue.sh <number>
```

Never close an issue that wasn't fully implemented — ask first if uncertain.

### Step 7 — Report

Present a summary table:

| Issue | Title | Status | Comment |
|---|---|---|---|
| #19 | ... | ✅ Closed | Summary posted |
