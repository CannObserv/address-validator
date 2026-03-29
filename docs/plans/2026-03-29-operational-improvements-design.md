# Operational Improvements — Env Restructure, Dev Workflow, Skill Updates

**Date:** 2026-03-29
**Motivated by:** [CannObserv/watcher#49](https://github.com/CannObserv/watcher/issues/49) — production 500 from stale uvicorn processes + env management gaps

## Context

Watcher experienced a production outage caused by manually-started uvicorn processes competing for port 8000, with exhausted DB connection pools. Post-incident analysis revealed operational gaps shared by address-validator: no dev server port convention, no env file standard, skills unaware of env vars, no worktree workflow enforcement.

## Changes

### 1. Environment file restructure

- Rename repo `env` → `.env` (standard convention)
- Rename variable `GITHUB_TOKEN` → `GH_TOKEN`
- Rename `/etc/address-validator/env` → `/etc/address-validator/.env`
- Remove `env` line from `.gitignore` (`.env` already covered)
- Update `address-validator.service`:
  - `EnvironmentFile=/etc/address-validator/.env` (required)
  - `EnvironmentFile=-/home/exedev/address-validator/.env` (optional, dev/agent secrets)
- Update all references in AGENTS.md, CLAUDE.md

### 2. Operational documentation in AGENTS.md

New sections after "Deployment":

**Infrastructure:**
- Single-VM dev+prod model (exe.dev)
- Port 8000 = systemd production (main worktree), port 8001 = dev server (active worktree)
- All development on git worktrees — never modify main worktree directly
- Standard workflow: brainstorming → design doc → worktree → implement → PR → merge → cleanup

**Server Lifecycle table:**

| After... | Do this |
|---|---|
| Code change (no env/service) | `sudo systemctl restart address-validator` |
| Env var change | Edit `/etc/address-validator/.env`, then restart |
| Service unit change | `sudo cp address-validator.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart address-validator` |
| New worktree created | Kill dev server on 8001, start from new worktree with `--reload` |
| Dev/test iteration | Dev server on 8001 with `--reload` auto-picks up changes |
| Worktree finished | Kill dev server on 8001, delete worktree |
| Stale process suspected | `ps aux | grep uvicorn` — kill anything not systemd MainPID |

**Environment hierarchy:**

| File | Contents | Loaded by |
|---|---|---|
| `/etc/address-validator/.env` | Production secrets | systemd (required) |
| `/home/exedev/address-validator/.env` | Dev/agent secrets (GH_TOKEN) | systemd (optional), manual export |

### 3. Skill updates

**Local worktree skill** (`skills/using-git-worktrees/SKILL.md`):
- Override vendor symlink with local version
- `uv sync` after worktree creation
- Copy `.env` from main worktree
- Kill existing process on 8001, start with `--reload`
- exe.dev proxy: `https://address-validator.exe.xyz:8001/`
- Cleanup: kill dev server + remove worktree

**gather-context.sh** (reviewing-code-claude):
- Load `.env` before pytest

**pre-ship.sh** (shipping-work-claude):
- Load `.env` before pytest

### 4. Deployment steps (post-merge)

1. `sudo mv /etc/address-validator/env /etc/address-validator/.env`
2. `sudo cp address-validator.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart address-validator`

## Out of scope

- No `src/` code changes
- No test changes (conftest.py uses `os.environ.setdefault`, unaffected)
- No changes to `/etc/address-validator/.env` contents
