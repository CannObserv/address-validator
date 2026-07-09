# Disk Hygiene — Scheduled Cleanup Design

**Date:** 2026-07-09
**Status:** Approved
**Approach:** One-off prune + weekly scheduled hygiene job (approach A of A/B/C considered)

## Problem

Recurring disk pressure on the single-VM host (30G root FS). Audit on 2026-07-09 found 13G used (45%) with ~3.9G immediately reclaimable and several unbounded growth vectors:

| Growth vector | Rate |
|---|---|
| VS Code remote server builds (`~/.vscode-server/cli/servers`) | ~540M per VS Code update, never evicted (5 copies found) |
| Claude Code extension versions + VSIX cache | ~255M per update + 181M cache |
| npm cache (`_npx` + `_cacache`) | grows per `npx` invocation (709M found) |
| uv cache | grows per resolve (349M found) |
| Leaked harness worktrees (`.claude/worktrees/*` with orphaned `.venv`) | ~300M per leak (1 found: issue-114) |

Fixed/required: `/usr` 3.9G, Docker 2.7G (libpostal image 2.49G required for CA parsing; qdrant image+volume = SocratiCode index), repo `.venv`/`node_modules`, Postgres 340M, `claude` CLI 213M, journald (capped 8M).

## Design

### `scripts/ops/disk-hygiene.sh`

Idempotent, `--dry-run` flag (prints deletion list, deletes nothing). Logs before/after `df -h /` line each run.

| Target | Rule |
|---|---|
| `~/.vscode-server/cli/servers` | Keep 2 most-recent per `lru.json` and any version with a running process; delete rest |
| `~/.vscode-server/extensions` | Per extension, keep newest version dir only |
| `~/.vscode-server/data/CachedExtensionVSIXs` | Delete files older than 14 days |
| `~/.npm` | `npm cache clean --force`; delete `_npx` entries older than 30 days |
| `~/.cache/uv` | `uv cache prune` |
| `~/.cache/pre-commit` | `pre-commit gc` |
| Worktrees | `git worktree prune`; delete `.claude/worktrees/*` and `.worktrees/*` dirs not in `git worktree list` |
| Threshold | Root FS ≥ 75% → log `WARNING` (surfaces in `journalctl -u disk-hygiene`) |

### systemd units

`infra/disk-hygiene.service` (oneshot, `User=exedev`) + `infra/disk-hygiene.timer` (`OnCalendar=weekly`, `Persistent=true`). Install: `sudo cp infra/disk-hygiene.{service,timer} /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now disk-hygiene.timer`.

## Out of scope

- Docker pruning — both images active, no dangling layers; skipping keeps the job non-root
- Postgres retention (audit / training-candidate rows) — DB only 227M; revisit if a future audit shows growth
- journald — already capped
- Registered worktrees' `.venv`/`node_modules` — never touched

## Safety

- Deletes only under `~/.vscode-server`, `~/.npm`, `~/.cache`, and orphaned worktree dirs
- VS Code server prune cross-checks running processes — cannot kill an active remote session
- Dry-run reviewed before first real run

## Testing

- Shell script; outside pytest coverage scope
- `shellcheck` clean
- Verification: `--dry-run` review → manual run → confirm `df` delta, VS Code remote still connects, dev workflow intact

## Rollout

1. Implement in worktree, PR, merge
2. Install units; `sudo systemctl start disk-hygiene`; report reclaimed space
3. Document in `docs/DEPLOYMENT.md` ops section + AGENTS.md deployment table one-liner
