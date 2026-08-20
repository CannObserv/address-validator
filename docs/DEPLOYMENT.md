# Deployment

## Common operations

```bash
# Restart after code change
sudo systemctl restart address-validator

# Tail logs (structured JSON, one object per line — see docs/LOGGING.md)
journalctl -u address-validator -f

# Tail logs, pretty-printed / filtered by request
journalctl -u address-validator -f -o cat | jq -c '{t:.timestamp, l:.level, rid:.request_id, m:.message}'
journalctl -u address-validator -o cat | jq 'select(.request_id == "<ULID>")'

# Re-install systemd unit after infra/address-validator.service changes
sudo cp infra/address-validator.service /etc/systemd/system/ && sudo systemctl daemon-reload

# Install/enable libpostal sidecar
sudo cp infra/libpostal.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now libpostal

# Install pre-commit hooks (ruff + Tailwind CSS build)
uv run pre-commit install
```

### Unit file and code must move together

`ExecStart` passes `--log-config src/address_validator/core/log_config.json`
(GH #185), so the unit file and the working tree are coupled. A mismatch is a
**boot crash**, not degraded logging — uvicorn dies with
`ValueError: Unable to configure formatter 'json'` before binding the port, and
`Restart=on-failure` turns that into a crashloop.

| Direction | Order |
|---|---|
| Deploy | merge to `main` → `git pull` in the main checkout → **`uv sync`** → `cp` the unit → `daemon-reload` → `restart` |
| Rollback | revert the code **and** the unit together, then `uv sync` → `daemon-reload` → `restart` |

Three ways to break it: `cp`ing the new unit before the merge lands (config
file absent), reverting the code while the installed unit still passes the
flag, or — the one that actually bit on the #185 deploy — **skipping `uv sync`
when the branch added a dependency**.

`uv sync` is not optional on any deploy that touches `pyproject.toml`. The
service runs `/home/exedev/address-validator/.venv/bin/uvicorn` from the main
checkout, so a package missing there surfaces as the same
`Unable to configure formatter 'json'` crashloop — the traceback's real cause
is `ModuleNotFoundError` several frames up, which is easy to miss.

**Which venv a worktree uses depends on how it was created**, and the two cases
have opposite hazards:

- **Created by `worktree-create.sh`** (the documented path) — the script
  symlinks `.venv` to the main checkout's real venv, so the worktree and
  production **share one environment**. This removes the #185 trap: a
  dependency added in the worktree is already present in the main venv. It
  also means **`uv sync` / `uv add` in that worktree writes directly into the
  venv the port-8000 service is running from.** On this single-VM dev+prod box
  that is a production mutation — expect a restart to pick up whatever the
  worktree resolved, and don't run `uv lock --upgrade && uv sync` from a
  worktree unless you intend to upgrade production's dependencies.
- **Created any other way** — notably the Claude Code Agent tool's
  `isolation: "worktree"`, which calls `git worktree add` directly and never
  runs the script — the worktree inherits **no** venv at all. Link one before
  the first test run, rather than resolving a fresh one (a new environment can
  silently collect fewer tests and still report green):

  ```bash
  ln -s /home/exedev/address-validator/.venv .venv
  ```

If the service is crashlooping after a deploy, run
`journalctl -u address-validator -n 50` and read the **whole** traceback, not
just the final `ValueError`.

## Server lifecycle

| After… | Do this |
|---|---|
| Code change (no env/service) | `sudo systemctl restart address-validator` |
| Env var change | Edit `/etc/address-validator/.env`, then restart |
| Service unit change | `sudo cp infra/address-validator.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl restart address-validator` |
| New worktree created | Kill any dev server on 8001 (`pgrep -f "\.worktrees/.*uvicorn" \| xargs -r kill`), then start from new worktree using the dev-server command below (add `--reload`) |
| Dev/test iteration | Dev server on 8001 with `--reload` auto-picks up changes |
| Agent-driven smoke check | Dev-server command below, minus `--reload` — the watcher process leaks if the agent shell exits before cleanup |
| Worktree finished | `bash skills-vendor/gregoryfoster-skills/skills/using-git-worktrees/scripts/worktree-destroy.sh <branch>` — never `git worktree remove` by hand |
| Stale process suspected | `pgrep -af "\.worktrees/.*uvicorn"` lists zombies; kill all PIDs not matching `systemctl show address-validator -p MainPID` |

Dev-server command (from the worktree root). `PYTHONPATH=src` is mandatory —
the project is not installed into `.venv/`, and `--log-config`'s `"()"` factory
is resolved by `dictConfig` before uvicorn imports the app, so a missing
PYTHONPATH fails at boot with `Unable to configure formatter 'json'`. Always
pass `--log-config`, or uvicorn's own lines revert to plain text alongside the
JSON app records:

```bash
PYTHONPATH=src uv run uvicorn address_validator.main:app --host 0.0.0.0 --port 8001 --reload \
  --log-config src/address_validator/core/log_config.json
```

## Worktree conventions

**Worktree path convention — `.worktrees/<branch-slug>/` only.** Always create worktrees via `bash skills-vendor/gregoryfoster-skills/skills/using-git-worktrees/scripts/worktree-create.sh [--new] <branch>` (resolves to `<repo>/.worktrees/`). Never create sibling-directory worktrees (`../address-validator-<n>/`) or hand-roll paths — these are invisible to `worktree-destroy.sh` and the source of leaked dev-server zombies. Always destroy via `worktree-destroy.sh <branch>`; never run `git worktree remove` by hand.

**Destroy flags.** `worktree-destroy.sh` finds the worktree **by branch** via the git registry, so any directory layout resolves — including the harness's `.claude/worktrees/agent-<id>/`, where branch and directory leaf differ. Preview any destroy with `--dry-run`: it prints the resolved path, base ref, merge verdict, lock state and removal command, exits with the code the real run would return, and changes nothing. `--unlock` is for one case only — a destroy that actually *reports* a held lock, meaning the owning agent is still running or died without releasing; check which before overriding, and note `--force` is not the remedy (git wants `-f -f` for a lock). The script also refuses to destroy the worktree it is being run from, so `cd` to the main checkout first.

**Worktrees that have had `.skills/doctor.sh` run in them need `worktree-destroy.sh <branch> --force`.** The doctor heals dangling vendor symlinks by running `git submodule update --init`, and git refuses to remove a worktree containing checked-out submodules (`fatal: working trees containing submodules cannot be moved or removed`). The `/reviewing-code-python-fastapi` preflight invokes the doctor automatically, so any worktree that has been through a code review is in this state. Note `--force` also bypasses git's dirty-tree check — commit or stash first. The Claude Code harness creates its own worktrees at `.claude/worktrees/` when an Agent runs with `isolation: "worktree"` — that is harness-owned state and outside this convention; leave it alone.

## Scheduled timers

```bash
# Audit log archive timer (daily GCS archival + row deletion)
sudo cp infra/audit-archive.service infra/audit-archive.timer /etc/systemd/system/ \
  && sudo systemctl daemon-reload && sudo systemctl enable --now audit-archive.timer

# Docker hygiene timer (weekly prune, Sun 03:30 UTC)
sudo cp infra/docker-prune.service infra/docker-prune.timer /etc/systemd/system/ \
  && sudo systemctl daemon-reload && sudo systemctl enable --now docker-prune.timer

# Validation-cache TTL sweep timer (daily 04:00 UTC)
sudo cp infra/cache-sweep.service infra/cache-sweep.timer /etc/systemd/system/ \
  && sudo systemctl daemon-reload && sudo systemctl enable --now cache-sweep.timer

# Disk hygiene timer (weekly, Sun 05:00 UTC)
sudo cp infra/disk-hygiene.service infra/disk-hygiene.timer /etc/systemd/system/ \
  && sudo systemctl daemon-reload && sudo systemctl enable --now disk-hygiene.timer
```

Docker prune does **not** use `-a` (active images are safe). Logs a journal warning if disk ≥ 85% after prune:

```bash
journalctl -t docker-prune -p warning
```

Disk hygiene prunes stale VS Code server builds (keeps 2 newest + any running), old extension versions, VSIX cache >14d, npm cache + `_npx` >30d, uv/pre-commit caches, and orphaned worktree dirs (`.claude/worktrees/*`, `.worktrees/*` not in `git worktree list`, aged >30min). Never touches Docker, Postgres, or registered worktrees. Dry-run: `infra/disk-hygiene.sh --dry-run`. Sandbox test rig: `bash tests/shell/disk-hygiene-test.sh`. Warnings (≥75% root-FS usage, unremovable paths, unreadable lru.json) go to the journal at real warning priority, matching docker-prune:

```bash
journalctl -u disk-hygiene -p info      # full run log
journalctl -t disk-hygiene -p warning   # warnings only
```

The cache sweep deletes `validated_addresses` rows (and their `query_patterns` pointers) older than `VALIDATION_CACHE_TTL_DAYS`. Dry-run any time with `PYTHONPATH=src uv run python infra/sweep_cache.py --dry-run` (`PYTHONPATH=src` is required — the package is not installed editable). Logs swept counts to the journal:

```bash
journalctl -u cache-sweep -p info
```

## Database maintenance scripts

**NEVER** source `/etc/address-validator/.env` before `uv run pytest`. That file sets `VALIDATION_CACHE_DSN` to the production database; the audit middleware writes real rows on every `TestClient` request. For one-off scripts only:

```bash
# set -a is required: the env file is systemd KEY=VALUE format with no `export`
# statements, so a plain `source` sets shell-local vars the child python never sees.
set -a && source /etc/address-validator/.env && set +a
# PYTHONPATH=src is required for every command below — the package is not
# installed editable, so `address_validator` is only importable from src/.
export PYTHONPATH=src

# Backfill audit_log rows missing structured fields
uv run python scripts/db/backfill_audit_log.py

# Backfill pattern_key column (dry-run by default; add --apply)
uv run python scripts/db/backfill_pattern_key.py

# Backfill audit_log.raw_input from query_patterns (dry-run by default; add --apply)
# One-off after the #147 deploy; rows whose query_patterns parent was already swept stay NULL
uv run python scripts/db/backfill_audit_raw_input.py

# (Removed in #151) The one-off NULL-canonical_key cleanup script has been retired;
# migration 018 makes query_patterns.canonical_key NOT NULL, so the rows can no longer exist.

# Stamp pre-#145 validated_addresses rows with the current pipeline version
# (dry-run by default; add --apply). One-off after the #145 deploy — unstamped
# NULL rows mismatch every lookup and lazily re-validate (hit-rate cliff).
# Must run with the same CUSTOM_MODEL_PATH the service uses.
uv run python scripts/db/backfill_pipeline_version.py --apply

# Archive audit log to GCS + delete archived rows
uv run python infra/archive_audit.py

# Backfill daily rollup aggregates
uv run python infra/archive_audit.py --backfill
```

## Env file locations

| File | Contents | Loaded by |
|---|---|---|
| `/etc/address-validator/.env` | Production secrets — `API_KEY`, DSN, provider creds, `CUSTOM_MODEL_PATH` | systemd `EnvironmentFile=` (required) |
| `/home/exedev/address-validator/.env` | Dev/agent secrets — `GH_TOKEN` | systemd (optional, `-` prefix), manual `export` |

### CORS

`ALLOWED_ORIGINS` (optional, GH #35) — comma-separated browser origins granted
cross-origin access (e.g. `https://app.example.com,https://admin.example.com`),
or `*` for any origin. Unset (the default) emits no
`Access-Control-Allow-Origin` header at all: server-to-server clients (curl,
SDKs) are unaffected, browsers are denied cross-origin access. Set it in
`/etc/address-validator/.env` and restart the service.
