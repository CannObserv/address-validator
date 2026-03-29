---
name: using-git-worktrees
description: Use when starting feature work that needs isolation — creates git worktrees with address-validator-specific setup (uv sync, .env copy, dev server on port 8001)
---

# Using Git Worktrees — Address Validator

## Overview

Git worktrees create isolated workspaces sharing the same repository. This project requires all development on worktrees — never modify the main worktree directly.

**Announce at start:** "I'm using the using-git-worktrees skill to set up an isolated workspace."

## Port Convention

- **Port 8000** — systemd production service (main worktree). NEVER start uvicorn manually on this port.
- **Port 8001** — dev server (active worktree, `--reload`). Accessible via exe.dev proxy at `https://address-validator.exe.xyz:8001/`.

## Directory Selection

Worktrees go in `.worktrees/` (project-local, hidden, gitignored).

## Creation Steps

### 1. Verify .worktrees is gitignored

```bash
git check-ignore -q .worktrees 2>/dev/null || echo "NOT IGNORED"
```

If not ignored: add `.worktrees` to `.gitignore` and commit before proceeding.

### 2. Create worktree

```bash
mkdir -p .worktrees
git worktree add .worktrees/$BRANCH_NAME -b $BRANCH_NAME
cd .worktrees/$BRANCH_NAME
```

### 3. Project setup

```bash
uv sync
```

### 4. Copy .env from main worktree

```bash
cp /home/exedev/address-validator/.env .env
```

### 5. Kill existing dev server and start fresh

```bash
# Kill any existing process on port 8001
lsof -ti:8001 | xargs kill 2>/dev/null || true

# Start dev server with --reload pointing to this worktree
uv run uvicorn address_validator.main:app --reload --port 8001 &
```

Wait for startup, then verify:
```bash
curl -s http://localhost:8001/api/v1/health | python -m json.tool
```

### 6. Run baseline tests

```bash
uv run pytest --no-cov -x
```

**If tests fail:** Report failures, ask whether to proceed or investigate.

### 7. Report ready

```
Worktree ready at <full-path>
Dev server running on port 8001 (https://address-validator.exe.xyz:8001/)
Tests passing (<N> tests, 0 failures)
Ready to implement <feature-name>
```

## Cleanup

When work is complete (merged or abandoned):

```bash
# Kill dev server
lsof -ti:8001 | xargs kill 2>/dev/null || true

# Remove worktree
cd /home/exedev/address-validator
git worktree remove .worktrees/$BRANCH_NAME
git branch -d $BRANCH_NAME  # only if merged
```

## Quick Reference

| Situation | Action |
|-----------|--------|
| `.worktrees/` not ignored | Add to `.gitignore`, commit, then proceed |
| Dev server already on 8001 | Kill it first (`lsof -ti:8001 \| xargs kill`) |
| Tests fail during baseline | Report failures + ask |
| Switching worktrees | Kill 8001 server, start from new worktree |
| Production issue | Use systemd (`sudo systemctl restart address-validator`), never touch port 8000 manually |

## Integration

**Called by:**
- **brainstorming** (after design approved) — REQUIRED before implementation
- **subagent-driven-development** — REQUIRED before executing tasks
- **executing-plans** — REQUIRED before executing tasks

**Pairs with:**
- **shipping-work-claude** — for PR creation after implementation
