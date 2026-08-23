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

**Since GH #201 this step carries more weight, not less.** Under the old `link`
default, a worktree that ran `uv add` wrote the package straight into
production's venv, so a deploy that then skipped `uv sync` still booted — the
shared venv was accidentally masking the #185 trap. Only for that route: a
branch that hand-edited `pyproject.toml` installed nothing either way, which is
why #185 happened at all. With `worktree_venv=none` **no** worktree action
reaches production's venv, so **the deploy-time `uv sync` is now the only thing
that puts a new dependency there** — the masking is gone along with the hazard.
Removing the shared-venv coupling and sharpening this step are the same change;
the deploy checklist above is what covers it.

**Every worktree provisions its own venv.** This box sets
`.skills/worktree_venv=none` (read by `worktree-create.sh` from the primary
checkout), so no worktree gets a `.venv` symlink and none can reach the venv the
port-8000 service runs from. After creating a worktree — by the script or any
other route, including the Claude Code Agent tool's `isolation: "worktree"` —
provision it once:

```bash
uv sync                    # venv — before the first test run
bash .skills/doctor.sh     # vendored skills + hooks — before the first skill call
```

Both, every time. A fresh worktree is missing **two** things, and only the first
one announces itself.

`worktree-create.sh` announces the missing venv on stderr rather than leaving you
to discover it:

```
NOTE: .skills/worktree_venv=none — no .venv linked into <path>; provision one there
```

### Why `bash .skills/doctor.sh` is the other half (GH #203)

A worktree checks out the repo's committed symlinks but **not** its submodules.
Every `skills/`, `.claude/skills/`, and `.claude/hooks/` entry is a symlink into
`skills-vendor/`, so in a fresh worktree all of them dangle — measured on
2026-08-23: **38 broken links** (17 `skills/`, 17 `.claude/skills/`, 4
`.claude/hooks/`). Until the doctor runs, that worktree has **no vendored skills
and no working hooks**: `/reviewing-code-python-fastapi`, `/using-git-worktrees`
and the rest cannot resolve their `scripts/` directory, and there is no
`~/.claude/skills` on this box to fall back to.

`.skills/doctor.sh` is installed as a **real file, never a symlink**, precisely
so it still runs in that state. It initializes the submodules and heals all 38:

```
doctor: dangling symlinks detected — initializing submodules...
```

Cost: **~2 s** and ~6.6 MiB per worktree (two shallow submodule clones).

Two consequences worth knowing:

- **It self-heals on the main paths.** The Phase 1 preflight of every
  `reviewing-*` / `shipping-*` skill invokes the doctor, so a worktree that
  reaches a review or a ship repairs itself. The gap this bootstrap closes is
  only the window *before* that first preflight — where the visible symptom is
  `tests/unit/test_socraticode_prefetch_sync.py` failing on a dangling hook.
  Those two failures are a **canary, not noise**: they mean the vendored skills
  in that worktree are unreachable too.
- **It makes `--force` mandatory on destroy.** Initialized submodules are what
  `git worktree remove` refuses to act on, so use
  `worktree-destroy.sh <branch> --force` afterwards — already the norm for any
  worktree that has been through a review.

### Why `none` and not `link` (GH #201 — settled, do not re-litigate)

`link` shares **one mutable environment** between every worktree and production.
Measured on this VM (uv 0.10.4), what each uv verb does to that shared venv when
run from a worktree:

| Command in a worktree | Effect on the production venv under `link` |
|---|---|
| `uv run pytest` / `uv run --group dev …` | no prune, no mutation |
| `uv add <pkg>` | installs into it — additive, recoverable |
| `uv sync` | **prunes** any package absent from the branch's lockfile |

That last row is the whole hazard, and it is unrecoverable by restart: it
uninstalls packages from the venv a live uvicorn imports from, producing an
immediate `ModuleNotFoundError` crashloop. Under `link` the only thing preventing
it is a reader remembering this page.

The cost of removing that coupling is negligible — measured, not estimated:

| | |
|---|---|
| `uv sync` in a fresh worktree | **0.20 s** (warm shared `~/.cache/uv`) |
| Disk per worktree venv | **~4 MiB real** |

`du` reports ~313 MiB for a worktree venv and is misleading: uv hardlinks out of
`~/.cache/uv`, so the `df` delta across a full sync is ~4.6 MiB. Against the
disk-hygiene budget that is noise.

That figure has a precondition: hardlinks require the cache and the worktree to
sit on **one filesystem**. Both are under `/` here. Move `~/.cache/uv`, or put
`.worktrees/` on a separate mount, and uv silently falls back to copying — at
which point the real cost per worktree is the full ~313 MiB and this tradeoff
is worth re-measuring rather than re-reading.

Two properties of this repo make `link` less dangerous than the
`using-git-worktrees` skill's worst case, and neither changes the verdict:
`infra/address-validator.service` invokes **no `uv`** (its `ExecStart` is
`.venv/bin/uvicorn` directly, and there is no `ExecStartPre=uv sync`), so the
service never rewrites the venv under a worktree; and `pyproject.toml` declares
no `[build-system]`, so `address_validator` is never installed into `.venv` and
`uv run` has nothing to reinstall. These cut the hazard's *frequency*, not its
*severity*.

Secondary benefit: a worktree venv resolves from **that branch's** `uv.lock`, so
a branch that adds a dependency is tested against the dependencies it declares.
The skill's caution that "a freshly resolved environment can silently collect
fewer tests" targets ad-hoc `uv venv` + install, not a deterministic `uv sync`
from a committed lockfile.

**The knob is machine-local.** `.skills/worktree_venv` is gitignored and must
stay uncommitted — it encodes "this checkout is a live service's
`WorkingDirectory=`", a property of this VM, not of the repo. A clone elsewhere
gets the `link` default, which is correct there.

Because it is untracked, nothing in git notices if it disappears — and what it
re-enables is silent until production falls over. `tests/unit/test_worktree_venv_knob.py`
is the notice: it compares the primary checkout against the unit's
`WorkingDirectory=` and asserts the knob reads `none` only on the box where the
hazard exists, skipping on every other clone and in CI.

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
