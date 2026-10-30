# Scheduled timers

Split out of [DEPLOYMENT.md](DEPLOYMENT.md), which keeps the service unit, ports, worktrees and env. Every `infra/*.timer`: install, schedule, alerting, journal queries.

Installed units are copies: they do not follow the repo. `infra/install-units.sh`
is the single install path — it copies units, runs `daemon-reload`, and clears
stale `failed` state. Run it whenever a unit **or a path a unit references**
changes; #109 moved scripts out of `scripts/` without re-installing, and two
units failed nightly for months unnoticed (#228).

```bash
sudo infra/install-units.sh                          # all infra/*.service + *.timer
sudo infra/install-units.sh audit-archive.service    # just one
infra/install-units.sh --check                       # exit 1 on drift; sudo to include address-validator.service
```

It installs mode 0644, so the root-only `0600` that `address-validator.service`
carries today goes away on its next install (its text is public in the repo);
until then `--check` needs sudo to compare it. Scope is `*.service` + `*.timer`
only: the `infra/*.conf` memory drop-ins are not installed or checked here —
see [HOST-MEMORY.md](HOST-MEMORY.md#reservations). It does not enable units.
First-time timer installs still need `enable --now`:

```bash
# Audit log archive timer (daily GCS archival + row deletion)
sudo infra/install-units.sh audit-archive.service audit-archive.timer \
  && sudo systemctl enable --now audit-archive.timer

# Validation-cache TTL sweep timer (daily 04:00 UTC)
sudo infra/install-units.sh cache-sweep.service cache-sweep.timer \
  && sudo systemctl enable --now cache-sweep.timer

# Archive-age check (daily 06:00 UTC): alerts if audit-archive stops running (#249)
sudo infra/install-units.sh archive-age-check.service archive-age-check.timer \
  && sudo systemctl enable --now archive-age-check.timer

# libpostal liveness probe (every 2 min): alerts if the sidecar stops answering (#256)
sudo infra/install-units.sh libpostal-probe.service libpostal-probe.timer \
  && sudo systemctl enable --now libpostal-probe.timer

# API liveness check-in to co-status (every 5 min): hung, degraded, VM down (#255)
sudo infra/install-units.sh address-validator-liveness.service address-validator-liveness.timer \
  && sudo systemctl enable --now address-validator-liveness.timer

# Nightly Postgres backup to GCS (02:30 UTC, #240). Role and key first, and one
# hand-started run before enabling: docs/RECOVERY.md "Install and first run"
sudo infra/install-units.sh address-validator-backup.service address-validator-backup.timer \
  && sudo systemctl start address-validator-backup.service \
  && sudo systemctl enable --now address-validator-backup.timer
```

Rule, exit codes, limits: `infra/archive_age_check.py` docstring.

**Postgres backup** (#240): daily 02:30 UTC, a verified `pg_dump` to
`gs://co-address-validator-backup/<hostname>/<ts>.dump`, create-only, as its own
`DynamicUser=` with no database credential. A missing key fails the start
(`243/CREDENTIALS`, no `SetCredential=` fallback); a failed run alerts through
`unit-failure@` and checks in `alert` to co-status's `co-address-validator-backup`,
whose deadline catches a run that never happens. Provisioning, restore, the
go/no-go and the drill: [RECOVERY.md](RECOVERY.md).

```bash
journalctl -u address-validator-backup -o cat           # object, size, sha256, alembic head
systemctl show -p Result,ExecMainStatus address-validator-backup.service   # LogTarget=console hides Finished lines
```

**libpostal liveness probe** (#256): every 2 min, curls the knob's synthetic
`/parse` URL. The 3rd consecutive miss exits 1 (one `OnFailure=` alert per
outage, its `<4>` miss line in the tail); later misses and recovery log at info.
Stands down while `libpostal.service` isn't `active`; a restart costs one miss at most.
No `OnCalendar=`: a declared frequent probe (`FREQUENT_PROBES`,
`tests/unit/test_patching_knob.py`). Rule, sizing, exit codes: the
`infra/libpostal-probe.sh` header.

```bash
journalctl -t libpostal-probe -p info     # misses, alert, recovery, stand-downs
cat /run/libpostal-probe/misses           # consecutive misses; a reboot clears it
```

**API liveness check-in** (#255): the off-VM uptime check. Every 5 min
`address-validator-liveness.timer` probes `http://localhost:8000/api/v2/health`
(the knob's health line) and checks in to co-status's dead-man monitor
`co-address-validator-live` (tenant of the same name, on CannObserv/status's own
VM; healthchecks.io watches co-status). Rule, sizing and exit codes: the
`infra/address-validator-liveness.sh` header.

| Probe result (retried every 2 s for 20 s) | Check-in | What co-status sends |
|---|---|---|
| 200 | `ok` | nothing; the `ok` that ends a fault sends *has cleared* |
| 503 all window | `alert`, `degraded` (the failing component) | report when the fault opens, then hourly (`renotify_seconds` 3600) |
| refused, timed out, other status | `alert`, `unreachable` (the cause) | as above; a change of fault reports at once (`metadata.fault`) |
| no check-in (VM down, timer stopped) | — | *has stopped reporting* 300 + 600 s after the last one, *has recovered* on the next |

A fault reports on the first failing run: 5 min + 20 s after onset at most. A
deploy restart (~4 s) or the boot's ~6 s 503 falls inside the retry and never
reports. A crashloop alerts twice by design: `unit-failure@` when the start
limit trips, and co-status's `unreachable`.

- **Key:** `/etc/address-validator/status-checkin.key`, root, mode 0600, read by
  systemd through `LoadCredential=`. Never in `.env` or argv. Missing or empty:
  the probe runs, logs `liveness check-in unconfigured`, exits 0.
- **Monitor id:** `Environment=LIVENESS_MONITOR_ID=` in the unit (not a secret).
- **Check-in failures** (co-status down, a 4xx/5xx, the tailnet hop) are a
  `<4>` warning and exit 0: co-status's deadline is the backstop.
  `OnFailure=` fires only for a broken run (bug, OOM kill, timeout).

```bash
journalctl -t address-validator-liveness -p info    # faults, unconfigured, check-in failures
systemctl list-timers address-validator-liveness.timer
curl -s http://status:9000/health | jq .environment  # "production"; DNS failure = ACL (TAILNET.md)
```

Runbook:

- **Report (`unreachable`/`degraded`):** `systemctl status address-validator`,
  `curl -s localhost:8000/api/v2/health`, `journalctl -u address-validator -n 50`.
  `degraded` = Postgres unreachable from the app.
- **Has stopped reporting:** is the VM up (exe.dev console)? Then
  `systemctl list-timers` and this unit's journal: a run that can't reach
  co-status warns every tick.
- **Planned stop of the API, any length:** a stopped API reports `unreachable`
  at the next tick. `sudo systemctl stop address-validator-liveness.timer`
  first, `start` it after. If the timer stays stopped past about 15 min
  (interval + grace), also pause the monitor, or it reports *has stopped
  reporting*. Pausing alone isn't enough: a disabled monitor still reports
  `alert` check-ins (status monitors.md § Faults). Pause and resume with the
  tenant's own key:

  ```bash
  ID=$(systemctl show -p Environment address-validator-liveness.service | /bin/grep -o 'LIVENESS_MONITOR_ID=[^ ]*' | cut -d= -f2)
  sudo cat /etc/address-validator/status-checkin.key | sed 's/^/X-API-Key: /' \
    | curl -sX PATCH "http://status:9000/api/v1/monitors/$ID" -H @- \
        -H 'Content-Type: application/json' -d '{"enabled": false}'   # true to resume
  ```
- **Known limit:** the probe runs on this VM against localhost. The exe.dev
  proxy path and the tailnet hop to consumers (power-map) are not covered.

**Failures are reported to the journal and pushed through notifier.** Every
timer-driven service carries `OnFailure=unit-failure@%n.service`. A failed
oneshot otherwise only sets `failed` state, which is how #228 went unnoticed.
A new timer's service must carry the hook — `tests/unit/test_unit_failure_hooks.py`
enforces it.

The restarting services carry it too (#248), with `RestartMode=direct` and an
explicit, reachable start limit; the test's docstring says why each is needed. A
crashloop alerts **once**, when the limit trips, then **stays down** until
`sudo systemctl reset-failed <unit> && sudo systemctl start <unit>`. Manual
restarts count toward the burst.

| Unit | Limit | Trips after |
|---|---|---|
| `address-validator` | 10 starts / 600s | 10 × (3s + startup) |
| `libpostal` | 5 starts / 600s | 5 × (5s + container start and model load) |

A hung-but-running process, or the VM itself being down, never fails the unit;
the API liveness check-in below covers both (#255), and the libpostal probe
covers a hung sidecar (#256). Both units'
alerts carry a journal tail (#252). The API's JSON records carry their real
priority, and the tail sends each one as `LEVEL logger: message [ExceptionClass]`,
never the traceback; read that on the host with `journalctl -u address-validator
-n 50`. libpostal's stderr, the only output it keeps, is filed at `err`.

The `infra/unit-failure@.service` template runs two steps:

1. **Journal line (the fallback).** One `crit` line tagged `unit-failure`
   naming the failed unit. It runs first and needs no network, venv or config.
2. **Notifier dispatch (#232).** `infra/notify_unit_failure.py` sends the unit,
   its result, the host, and the failed run's last WARNING-or-higher journal
   lines to the sibling [notifier](https://github.com/CannObserv/notifier)
   service, which delivers to Slack and Mailgun. It is **fail-open**: the
   `ExecStart=-` prefix makes systemd ignore its exit status, and a failure
   (notifier unreachable, key rejected, config incomplete) logs a WARNING and
   leaves the journal line as the only signal. With `NOTIFIER_URL` unset, only
   the journal line is written.

```bash
journalctl -t unit-failure                        # every reported failure
journalctl -t unit-failure --since today
journalctl -u 'unit-failure@*' -p warning         # dispatches that did not deliver
```

Only WARNING+ lines leave the host, and the API's JSON records only as
allowlisted fields. Those two filters are the PII guard; see `docs/LOGGING.md`. The tail is scoped to the run's invocation ID, which
`logger` / `systemd-cat` lines lack; shell scripts warn via `infra/journal-warn.sh` (#246).

**Notifier config** — in `/etc/address-validator/.env`:

| Variable | Value |
|---|---|
| `NOTIFIER_URL` | `http://notifier:9000` (tailnet; unset = journal only) |
| `NOTIFIER_API_KEY` | `address-validator` tenant key, `nk_…` — pasted by the operator, never through an agent |
| `NOTIFIER_UNIT_FAILURE_TEMPLATE_ID` | `01M3R970XBXWBKD31Y4EGPMAQE` (printed by `publish_notifier_template.py`) |
| `NOTIFIER_UNIT_FAILURE_CHANNEL_IDS` | `01M3QRP1BCJW3KFB04FX4V8EQ8,01M3QRP1BDAXK27G60EJQEVEZD` (`address-validator-slack`, `-mailgun`; tenant `01M3QQMHYVVQWAS2SZ7NE66D2N`, CannObserv/notifier#95) |
| `NOTIFIER_ARCHIVE_AGE_TEMPLATE_ID` | `01M513CW6JRYH8X0JPPYNF6EP6` (printed by `publish_notifier_template.py --template archive-age`, #249) |

**Network.** Notifier is reachable only over the `cannobserv.org.github`
tailnet (`http://notifier:9000`). Node, ACL, DNS takeover, and the join/rejoin
recipe: [TAILNET.md](TAILNET.md). The handler refuses to dispatch unless `/health` reports
`"environment": "production"`.

**Templates.** The wording lives in `infra/notifier/<name>.json`.
Notifier stores a copy under this tenant, and template lookup is tenant-scoped.
The publisher reads only the `NOTIFIER_*` keys from the env file, so the
production DSN never enters its environment. It previews the sample through
notifier, then creates the template (or PATCHes it when
`NOTIFIER_UNIT_FAILURE_TEMPLATE_ID` is set):

```bash
.venv/bin/python infra/publish_notifier_template.py --dry-run   # preview only
.venv/bin/python infra/publish_notifier_template.py             # create / update
.venv/bin/python infra/publish_notifier_template.py --template archive-age
```

**Smoke tests.** The end-to-end check is a throwaway unit that fails. It takes
the same path a real failure does: `MONITOR_*` variables, the
`{unit}:{MONITOR_INVOCATION_ID}` key, and the invocation-scoped journal tail.
`systemd-run` does not expand `%n`, so spell out the handler name:

```bash
sudo systemd-run --unit=av-smoke -p OnFailure=unit-failure@av-smoke.service.service /bin/false
journalctl -u unit-failure@av-smoke.service.service -n 5   # "notifier dispatch … succeeded"
sudo systemctl reset-failed av-smoke.service
```

For a restarting unit, add its settings to the same command
(`-p Restart=on-failure -p RestartSec=3 -p RestartMode=direct -p StartLimitIntervalSec=600 -p StartLimitBurst=10`):
expect `NRestarts=10`, then `failed`, and **one** `unit-failure` line, about 35s in.

The quick check, `sudo systemctl start unit-failure@smoke-test.service`, carries
no `MONITOR_*` variables. It reports result `manual` and uses a fresh
`{unit}:manual:{uuid}` key. Never use a fixed key: notifier returns a replayed
key's earlier dispatch and delivers nothing.

One failed run yields one dispatch. **Deploying a handler change:** run `uv sync` in the main
checkout first. Without `notifier-client` in its `.venv`, the import fails
before any fail-open code runs, and the `-` prefix hides it: failures go
journal-only, silently. Then `sudo infra/install-units.sh unit-failure@.service`.

**Disk GC and headroom sensor (#326).** Root-disk garbage collection is the cohort's canonical disk GC (CannObserv/provisioner#61), not this repo's: it replaced `disk-hygiene` and `docker-prune` (#326). Installed from the pinned `co-image-base` release by the vendored `/maintaining-host-disk` skill, whose `scripts/_pin.sh` is the only place the release is named; a skills-vendor bump that raises the pin's `VERSION` is how an update arrives. Files live under `/usr/local/lib/` (units in `/usr/local/lib/systemd/system/`), so `infra/install-units.sh` neither installs nor checks them. **Never edit the installed copy**: raise a change on Provisioner through the operator.

| Unit | Schedule | Does |
|---|---|---|
| `cannobserv-disk-gc.timer` | daily 05:15 UTC + up to 30 min, no catch-up | prunes tooling garbage per the config's tiers, as root, `TimeoutStartSec=1800` |
| `cannobserv-disk-headroom.timer` | every 10 min | reads `/` and the GC's last run; checks in to co-status (`co-address-validator-disk-headroom`, CannObserv/status#77) |

Host config, root-owned, never in the repo:

- `/etc/cannobserv/disk-gc/disk-gc.conf` (0644). Tiers: VS Code server builds and CLIs, old Claude Code extensions, the VSIX cache, the plugin cache, `_npx` (keeps every `SOCRATICODE_SPEC` from `.claude/settings.json` and the VS Code machine setting, [HOST-MEMORY.md](HOST-MEMORY.md)), the npm cache, pytest garbage, orphaned `.worktrees/` and `.claude/worktrees/` dirs (unregistered, untouched 60 min), `uv cache prune --force`, and `docker` (dangling images, build cache older than 168 h; never a container, a volume or a tagged image). Lines: alert at 80% used, clear at 75%. patching-hosts reads `[sensor] alert_at_percent` as a floor.
- `/etc/cannobserv/disk-gc/checkin.key` (0600): a copy of `/etc/address-validator/status-checkin.key`, the `co-address-validator-live` tenant key. **A key rotation updates both files.**

A failed GC run has no `OnFailure=`: it reaches the operator as the sensor's `gc-failed` on co-status, and a GC that stops running as `gc-stale` after 72 h. The GC's lines carry priorities; read them as root (this host's manager has `LogTarget=console`, so use `systemctl show` for results):

```bash
sudo journalctl -u cannobserv-disk-gc -p warning     # warnings and errors only
sudo journalctl -u cannobserv-disk-headroom -n 5     # expect "checked in ok (ok): HTTP 202"
systemctl show cannobserv-disk-gc.service -p Result,ExecMainStatus
sudo /usr/bin/python3 -I /usr/local/lib/cannobserv/disk-gc/disk-gc gc   # report only; removes nothing
```

The cache sweep deletes `validated_addresses` rows (and their `query_patterns` pointers) older than `VALIDATION_CACHE_TTL_DAYS`. Dry-run any time with `PYTHONPATH=src uv run python infra/sweep_cache.py --dry-run` (`PYTHONPATH=src` is required — the package is not installed editable). Logs swept counts to the journal:

```bash
journalctl -u cache-sweep -p info
```
