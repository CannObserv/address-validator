# Operational Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure env files, document dev workflow, and update skills to prevent stale-process incidents (adapted from CannObserv/watcher#49).

**Architecture:** Pure operational/docs changes — no `src/` code modified. Env file renamed, systemd service updated, AGENTS.md gains Infrastructure/Server Lifecycle/Environment sections, local worktree skill override created, skill scripts load `.env` before pytest.

**Tech Stack:** Bash, systemd, git worktrees, shell scripts

**Issue:** #77

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Rename | `env` → `.env` | Dev/agent secrets (GH_TOKEN) |
| Modify | `.gitignore` | Remove `env` line, add `.worktrees` |
| Modify | `address-validator.service` | Point to `/etc/address-validator/.env`, add optional repo `.env` |
| Modify | `AGENTS.md` | All env path refs + new Infrastructure/Server Lifecycle/Environment sections |
| Modify | `README.md` | Env path reference |
| Modify | `scripts/model/deploy.py` | Env path in print message |
| Modify | `skills/train-model/SKILL.md` | `source` commands |
| Modify | `skills/reviewing-code-claude/scripts/gather-context.sh` | Load `.env` before pytest |
| Modify | `skills/shipping-work-claude/scripts/pre-ship.sh` | Load `.env` before pytest |
| Create | `skills/using-git-worktrees/SKILL.md` | Local worktree skill override |

---

### Task 1: Rename env file and update .gitignore

**Files:**
- Rename: `env` → `.env`
- Modify: `.gitignore`

- [ ] **Step 1: Rename `env` → `.env` and update contents**

```bash
cd /home/exedev/address-validator
git mv env .env
```

- [ ] **Step 2: Replace `GITHUB_TOKEN` with `GH_TOKEN` inside `.env`**

Edit `.env` — change:
```
GITHUB_TOKEN=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
```
to:
```
GH_TOKEN=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
```

- [ ] **Step 3: Update `.gitignore` — remove `env` line, add `.worktrees`**

Edit `.gitignore` — change:
```
.env
env
```
to:
```
.env
.worktrees
```

- [ ] **Step 4: Verify**

```bash
git status
# Should show: renamed env -> .env, modified .gitignore
git check-ignore .env .worktrees
# Should print both paths
```

- [ ] **Step 5: Commit**

```bash
git add .env .gitignore
git commit -m "#77 chore: rename env to .env, GITHUB_TOKEN to GH_TOKEN"
```

---

### Task 2: Update systemd service

**Files:**
- Modify: `address-validator.service`

- [ ] **Step 1: Update EnvironmentFile directives**

Edit `address-validator.service` — change:
```
EnvironmentFile=/etc/address-validator/env
```
to:
```
EnvironmentFile=/etc/address-validator/.env
EnvironmentFile=-/home/exedev/address-validator/.env
```

The `-` prefix makes the second file optional (no error if missing).

- [ ] **Step 2: Commit**

```bash
git add address-validator.service
git commit -m "#77 chore: update systemd service for new env file paths"
```

---

### Task 3: Update AGENTS.md — env path references

**Files:**
- Modify: `AGENTS.md`

All `/etc/address-validator/env` references become `/etc/address-validator/.env`. The `GITHUB_TOKEN` reference becomes `GH_TOKEN`.

- [ ] **Step 1: Update Authentication section (line 78)**

Change:
```markdown
- Key at `/etc/address-validator/env` (mode 640); loaded via `EnvironmentFile=` in systemd unit
```
to:
```markdown
- Key at `/etc/address-validator/.env` (mode 640); loaded via `EnvironmentFile=` in systemd unit
```

- [ ] **Step 2: Update Validation provider section (line 91)**

Change:
```markdown
Env vars in `/etc/address-validator/env`:
```
to:
```markdown
Env vars in `/etc/address-validator/.env`:
```

- [ ] **Step 3: Update Deployment section (lines 117, 122-124)**

Change:
```markdown
- Env file: `/etc/address-validator/env`
```
to:
```markdown
- Env file: `/etc/address-validator/.env`
```

Change:
```markdown
- Backfill audit log: `source /etc/address-validator/env && uv run python scripts/backfill_audit_log.py`
- Archive audit log: `source /etc/address-validator/env && uv run python scripts/archive_audit.py`
- Backfill rollups: `source /etc/address-validator/env && uv run python scripts/archive_audit.py --backfill`
```
to:
```markdown
- Backfill audit log: `source /etc/address-validator/.env && uv run python scripts/backfill_audit_log.py`
- Archive audit log: `source /etc/address-validator/.env && uv run python scripts/archive_audit.py`
- Backfill rollups: `source /etc/address-validator/.env && uv run python scripts/archive_audit.py --backfill`
```

- [ ] **Step 4: Update GitHub CLI section (lines 152-155)**

Change:
```markdown
PAT in `env` (project root) as `GITHUB_TOKEN`:

\```bash
export GH_TOKEN=$(grep GITHUB_TOKEN env | cut -d= -f2)
\```
```
to:
```markdown
PAT in `.env` (project root) as `GH_TOKEN`:

\```bash
export GH_TOKEN=$(grep GH_TOKEN .env | cut -d= -f2)
\```
```

- [ ] **Step 5: Verify no remaining old references**

```bash
grep -n '/etc/address-validator/env[^.]' AGENTS.md
grep -n 'GITHUB_TOKEN' AGENTS.md
# Both should return nothing
```

- [ ] **Step 6: Commit**

```bash
git add AGENTS.md
git commit -m "#77 docs: update env file paths and GH_TOKEN references in AGENTS.md"
```

---

### Task 4: Update AGENTS.md — new operational sections

**Files:**
- Modify: `AGENTS.md`

- [ ] **Step 1: Add Infrastructure, Server Lifecycle, and Environment sections after Deployment**

Insert the following after the Deployment section (after line 125, before "## Testing and linting"):

```markdown
## Infrastructure

Single-VM dev+prod model ([exe.dev](https://exe.dev)):
- Port 8000 = systemd production service (main worktree) — **never** start uvicorn manually on this port
- Port 8001 = dev server (active git worktree, `--reload`)
- exe.dev proxy: dev server accessible at `https://address-validator.exe.xyz:8001/`
- All development work happens on git worktrees — never modify the main worktree directly
- Standard workflow: `/brainstorming` → design doc → worktree → implement → PR → merge → clean up worktree

## Server lifecycle

| After… | Do this |
|---|---|
| Code change (no env/service) | `sudo systemctl restart address-validator` |
| Env var change | Edit `/etc/address-validator/.env`, then restart |
| Service unit change | `sudo cp address-validator.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart address-validator` |
| New worktree created | Kill any dev server on 8001 (`lsof -ti:8001 \| xargs kill 2>/dev/null`), then start from new worktree with `--reload` |
| Dev/test iteration | Dev server on 8001 with `--reload` auto-picks up changes |
| Worktree finished | Kill dev server on 8001, delete worktree |
| Stale process suspected | `ps aux \| grep uvicorn` — kill anything not PID from `systemctl show address-validator -p MainPID` |

## Environment

| File | Contents | Loaded by |
|---|---|---|
| `/etc/address-validator/.env` | Production secrets (`API_KEY`, DSN, provider creds, `CUSTOM_MODEL_PATH`) | systemd (required) |
| `/home/exedev/address-validator/.env` | Dev/agent secrets (`GH_TOKEN`) | systemd (optional with `-` prefix), manual `export` |
```

- [ ] **Step 2: Verify sections render correctly**

```bash
head -170 AGENTS.md | tail -40
# Should show the new sections with proper markdown tables
```

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md
git commit -m "#77 docs: add Infrastructure, Server lifecycle, and Environment sections"
```

---

### Task 5: Update remaining files with new env paths

**Files:**
- Modify: `README.md:204`
- Modify: `scripts/model/deploy.py:99`
- Modify: `skills/train-model/SKILL.md:83,272`

- [ ] **Step 1: Update README.md**

Edit `README.md` line 204 — change:
```
`/etc/address-validator/env` and loaded via `EnvironmentFile=`.
```
to:
```
`/etc/address-validator/.env` and loaded via `EnvironmentFile=`.
```

- [ ] **Step 2: Update deploy.py**

Edit `scripts/model/deploy.py` line 99 — change:
```python
    print(f"1. Ensure CUSTOM_MODEL_PATH={DEPLOY_PATH} in /etc/address-validator/env")
```
to:
```python
    print(f"1. Ensure CUSTOM_MODEL_PATH={DEPLOY_PATH} in /etc/address-validator/.env")
```

- [ ] **Step 3: Update train-model skill (line 83)**

Edit `skills/train-model/SKILL.md` line 83 — change:
```
source /etc/address-validator/env 2>/dev/null || true
```
to:
```
source /etc/address-validator/.env 2>/dev/null || true
```

- [ ] **Step 4: Update train-model skill (line 272)**

Edit `skills/train-model/SKILL.md` line 272 — change:
```
source /etc/address-validator/env
```
to:
```
source /etc/address-validator/.env
```

- [ ] **Step 5: Verify no remaining old references in active files**

```bash
grep -rn '/etc/address-validator/env[^.]' --include='*.md' --include='*.py' --include='*.sh' --include='*.service' \
  --exclude-dir=vendor --exclude-dir=docs/plans --exclude-dir=docs/superpowers . | grep -v '.pyc'
# Should return nothing (vendor/ and historical docs/plans/ are excluded — they're not actionable)
```

- [ ] **Step 6: Commit**

```bash
git add README.md scripts/model/deploy.py skills/train-model/SKILL.md
git commit -m "#77 docs: update env file paths in README, deploy script, and train-model skill"
```

---

### Task 6: Update skill scripts to load .env

**Files:**
- Modify: `skills/reviewing-code-claude/scripts/gather-context.sh`
- Modify: `skills/shipping-work-claude/scripts/pre-ship.sh`

- [ ] **Step 1: Add env loading to gather-context.sh**

Edit `skills/reviewing-code-claude/scripts/gather-context.sh` — after line 18 (`cd "$PROJECT_ROOT"`), add:

```bash

# Load env vars (needed for pytest in worktrees)
if [ -f "$PROJECT_ROOT/.env" ]; then
  export $(grep -v '^#' "$PROJECT_ROOT/.env" | xargs) 2>/dev/null || true
fi
```

The full file after the `cd` line should read:
```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$PROJECT_ROOT"

# Load env vars (needed for pytest in worktrees)
if [ -f "$PROJECT_ROOT/.env" ]; then
  export $(grep -v '^#' "$PROJECT_ROOT/.env" | xargs) 2>/dev/null || true
fi

echo "=== Project root ==="
```

- [ ] **Step 2: Add env loading to pre-ship.sh**

Edit `skills/shipping-work-claude/scripts/pre-ship.sh` — after line 22 (`cd "$PROJECT_ROOT"`), add:

```bash

# Load env vars (needed for pytest in worktrees)
if [ -f "$PROJECT_ROOT/.env" ]; then
  export $(grep -v '^#' "$PROJECT_ROOT/.env" | xargs) 2>/dev/null || true
fi
```

The full file after the `cd` line should read:
```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$PROJECT_ROOT"

# Load env vars (needed for pytest in worktrees)
if [ -f "$PROJECT_ROOT/.env" ]; then
  export $(grep -v '^#' "$PROJECT_ROOT/.env" | xargs) 2>/dev/null || true
fi

echo "=== Lint (ruff) ==="
```

- [ ] **Step 3: Test gather-context.sh runs without error**

```bash
bash skills/reviewing-code-claude/scripts/gather-context.sh 2>&1 | head -5
# Should show "=== Project root ===" and path
```

- [ ] **Step 4: Test pre-ship.sh runs without error**

```bash
bash skills/shipping-work-claude/scripts/pre-ship.sh 2>&1 | head -5
# Should show "=== Lint (ruff) ===" output
```

- [ ] **Step 5: Commit**

```bash
git add skills/reviewing-code-claude/scripts/gather-context.sh skills/shipping-work-claude/scripts/pre-ship.sh
git commit -m "#77 fix: load .env in gather-context.sh and pre-ship.sh for worktree support"
```

---

### Task 7: Create local worktree skill

**Files:**
- Create: `skills/using-git-worktrees/SKILL.md`
- Create symlink: `.claude/skills/using-git-worktrees`

This overrides the vendor skill at `vendor/obra-superpowers/skills/using-git-worktrees/` with a local version tailored to address-validator's single-VM setup.

- [ ] **Step 1: Create the local skill directory and file**

```bash
mkdir -p skills/using-git-worktrees
```

Write `skills/using-git-worktrees/SKILL.md`:

```markdown
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
```

- [ ] **Step 2: Create symlink in .claude/skills/**

```bash
ln -sf ../../skills/using-git-worktrees .claude/skills/using-git-worktrees
```

- [ ] **Step 3: Verify symlink resolves**

```bash
ls -la .claude/skills/using-git-worktrees
# Should point to ../../skills/using-git-worktrees
cat .claude/skills/using-git-worktrees/SKILL.md | head -5
# Should show the frontmatter
```

- [ ] **Step 4: Commit**

```bash
git add skills/using-git-worktrees/SKILL.md .claude/skills/using-git-worktrees
git commit -m "#77 feat: add local worktree skill with address-validator dev workflow"
```

---

### Task 8: Deploy — rename production env file and reload service

**This task modifies live production state. Requires operator confirmation before each step.**

- [ ] **Step 1: Rename production env file**

```bash
sudo mv /etc/address-validator/env /etc/address-validator/.env
```

- [ ] **Step 2: Verify permissions preserved**

```bash
ls -la /etc/address-validator/.env
# Should show: -rw-r----- root exedev (mode 640)
```

- [ ] **Step 3: Install updated systemd unit and restart**

```bash
sudo cp address-validator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart address-validator
```

- [ ] **Step 4: Verify service is healthy**

```bash
sudo systemctl is-active address-validator
# Should print: active

curl -s http://localhost:8000/api/v1/health | python -m json.tool
# Should show: {"status": "ok", ...}
```

- [ ] **Step 5: Verify env loading works**

```bash
export GH_TOKEN=$(grep GH_TOKEN .env | cut -d= -f2)
gh auth status
# Should show authenticated
```
