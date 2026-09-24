# Host memory

Single-VM dev+prod means an interactive agent session shares 7.2 GiB **and no
swap** with the production service. Two facts make that sharper than it sounds:

- exe.dev session processes inherit `oom_score_adj` **-1000** from exe.dev's
  `sshd` (this host has no `exe-init`). No OOM killer can pick a session —
  neither the kernel's nor earlyoom — so under real exhaustion something on
  the host goes instead, `address-validator.service` included.
- With no swap and a small `vm.min_free_kbytes`, the host does not reach the
  OOM killer at all in the common case. A burst allocation drains the free pool
  faster than reclaim refills it and `GFP_ATOMIC` callers that cannot sleep
  (softirq, network) simply fail: page-allocation errors in `tailscaled` and
  `ksoftirqd`, a dead network, and **nothing killed**. That is how the
  2026-09-16 outage on the sibling `broker` VM presented — the bus was down
  57m 48s (gregoryfoster/skills#295).

## Reservations

Six defences, all installed rather than tuned at runtime:

| What | Where | Install |
|---|---|---|
| **Parent allocation** — `MemoryLow=1G` on `system.slice` | `infra/system-slice-memory.conf` | `sudo mkdir -p /etc/systemd/system/system.slice.d && sudo cp infra/system-slice-memory.conf /etc/systemd/system/system.slice.d/10-memory-reservation.conf && sudo systemctl daemon-reload` |
| Service reservation — `MemoryLow=512M`, `OOMScoreAdjust=-500` | `infra/address-validator.service` | `Service unit change` row under [Server lifecycle](DEPLOYMENT.md#server-lifecycle) |
| Database reservation — `MemoryLow=384M`, **both levels** | `infra/system-postgresql-slice-memory.conf` + `infra/postgresql-memory.conf` | `sudo mkdir -p /etc/systemd/system/system-postgresql.slice.d /etc/systemd/system/postgresql@16-main.service.d && sudo cp infra/system-postgresql-slice-memory.conf /etc/systemd/system/system-postgresql.slice.d/10-memory-reservation.conf && sudo cp infra/postgresql-memory.conf /etc/systemd/system/postgresql@16-main.service.d/10-memory-reservation.conf && sudo systemctl daemon-reload` |
| Atomic-allocation headroom — `vm.min_free_kbytes = 131072` | `infra/60-address-validator-memory.conf` | `sudo cp infra/60-address-validator-memory.conf /etc/sysctl.d/ && sudo sysctl --system` |
| A killer that acts before the kernel stalls — libpostal first, never a session ([below](#earlyoom-kept-for-libpostal)) | `infra/earlyoom.default` | `sudo apt-get install -y earlyoom && sudo cp infra/earlyoom.default /etc/default/earlyoom && sudo systemctl restart earlyoom` |

**A templated unit hides an extra level.** `postgresql@16-main.service` is a
template instance, so systemd files it under an auto-created
`system-postgresql.slice` rather than directly in `system.slice` — and that
intermediate slice is created with no resource settings, i.e. `memory.low` 0,
which clamps the unit's own 384M to an effective zero. Both levels need the
allocation. Any templated unit added to this host needs its own
`system-<prefix>.slice` drop-in; a plain unit does not.

**The parent allocation is not optional, and its absence is invisible.**
`systemd.resource-control(5)`: *"For a protection to be effective, it is
generally required to set a corresponding allocation on all ancestors, which is
then distributed between children (with the exception of the root slice)."* A
cgroup's effective `memory.low` is bounded by its ancestors', so `MemoryLow=`
on the service alone is inert while `system.slice` sits at the default 0. This
host does not soften that: `/sys/fs/cgroup` is mounted `rw,relatime` with no
`memory_recursiveprot`. `system.slice`'s own parent is the root slice, which
the man page exempts, so those two levels are the whole chain.

Verify — **read the kernel's view, not the unit property.** `systemctl show`
reports what is configured, which on a host missing the parent allocation is
`MemoryLow=536870912` next to an effective protection of zero:

```bash
# The effective chain. EVERY ancestor must be non-zero or the child's value is
# decoration; the numbers only mean anything read together. Note postgres is a
# TEMPLATED unit, so it sits one level deeper, under an implicit
# system-postgresql.slice -- read the path, not the unit name.
cat /sys/fs/cgroup/system.slice/memory.low                           # want 1073741824
cat /sys/fs/cgroup/system.slice/address-validator.service/memory.low # want 536870912
cat /sys/fs/cgroup/system.slice/system-postgresql.slice/memory.low   # want 402653184
cat /sys/fs/cgroup/system.slice/system-postgresql.slice/postgresql@16-main.service/memory.low  # want 402653184

# Reclaim actually deferred under pressure, cumulative since boot:
grep '^low ' /sys/fs/cgroup/system.slice/address-validator.service/memory.events

systemctl show address-validator -p OOMScoreAdjust -p MemoryCurrent  # these two are honest
sysctl vm.min_free_kbytes
# What earlyoom runs, not whether it is active: want infra/earlyoom.default's args, not -r 3600
tr '\0' ' ' < "/proc/$(systemctl show earlyoom -p MainPID --value)/cmdline"; echo
```

`MemoryLow` is a **soft** floor — the kernel reclaims from the service only
once everything unprotected is exhausted — and `OOMScoreAdjust=-500` cannot
outrank a session at -1000; it does not need to, it needs to outrank the rest
of the host. Steady-state RSS for the service is ~150 MB, so 512 MB is
reservation, not a cap.

**What to lose, in order.** `/api/v2/health` already ranks these, and the
reservations follow it rather than inventing a second opinion:

| Service | Health says | Reservation |
|---|---|---|
| `libpostal.service` (~1.9 GB) | `libpostal: unavailable`, status stays ok | **none, deliberately** — largest thing on the host, `Restart=always`, and the only one whose loss the service survives. The right thing to lose first |
| everything else in `system.slice` | — | the 128M left undistributed after the two claims below |
| `postgresql@16-main` (~281 MB) | `database: error` → **HTTP 503** | `MemoryLow=384M` |
| `address-validator` (~150 MB) | the service itself | `MemoryLow=512M`, `OOMScoreAdjust=-500` |

Protecting postgres is not optional generosity: an outage there fails the
health check outright, so leaving it at `MemoryLow=0` would have protected the
app while letting the thing it returns 503 without be reclaimed out from under
it. Only `address-validator` gets `OOMScoreAdjust` — giving postgres the same
value would restore the tie between them rather than ordering them.

## earlyoom: kept, for libpostal

GH #225. earlyoom cannot close the session gap: 1.7 skips a -1000 process
exactly as the kernel does (`kill.c:242-253`), `--prefer` or not, so a
session, VS Code Server and anything they launch are never its victim. What it
does is act before the kernel stalls, on the host's own processes, so what
decides whether it earns its place is which one it takes.

A dry run on 2026-09-24 answered that: stock earlyoom takes
`wof-libpostal-s` (1.9 G), the first row of the table above, so it stays. But
the stock order rests on RSS alone. After libpostal came the user manager
(`dbus-daemon`, `systemd`, `(sd-pam)`) and then postgres, fifth: a 503.
`infra/earlyoom.default` sets the order instead. It `--prefer`s libpostal, then
SocratiCode's `qdrant` and `ollama` (adj 0, all three restart on their own).
It `--avoid`s the service, postgres and the user manager, which puts postgres
behind every small daemon. `--avoid` is -300, a shift rather than an
exemption. The file's header gives the reason for each flag. Where sessions sit
at -1000, `--prefer` reaches nothing of theirs, so it names none of them.

Two traps, both silent. `apt install` starts the daemon on Debian's
`-r 3600`, and `enable --now` never restarts it, so an `active` unit can run
stock arguments; this host did until #225. And Debian's unit splits
`$EARLYOOM_ARGS` itself, so a space inside a regex makes two arguments and a
backslash is dropped. The file uses neither.

`tests/unit/test_earlyoom_config.py` holds the file to those rules. On this
host only, it also checks that the running daemon carries the file's arguments
and that this session's root still reads -1000. That premise is exe.dev's, not
this repo's; if it flips, sessions become killable and #225 reopens.

Re-measure after anything large starts running here. A dry run kills nothing
and needs no root; read its verdict, never the `-d` badness column, which
prints -1000 processes' scores from before the skip (upstream `init-socraticode`
→ `references/host-memory.md` §4):

```bash
cd "$(mktemp -d)" && apt-get download earlyoom && dpkg-deb -x earlyoom_*.deb x
timeout -s INT 2 ./x/usr/bin/earlyoom --dryrun -d -r 0 -m 99,98 -s 100,100 \
  --prefer '^(wof-libpostal|qdrant|ollama)' \
  --avoid '^(uvicorn|postgres|systemd|[(]sd-pam[)]|sshd)$' > dry.txt 2>&1
grep -m1 '^sending .* to process' dry.txt   # want wof-libpostal-s
```

## Don't install a SocratiCode server at launch

The plugin launches `npx -y --prefer-online ${SOCRATICODE_SPEC:-socraticode@latest}`;
on `@latest`, `--prefer-online` revalidates every launch, so any day the
package moves, a launch installs. Measured on `broker`: install + server + full
index peaked at **1.2 G**, all 126 `MemoryHigh` throttle events in the install;
a pre-installed build, **75 MB**.

Both launches are pinned to **1.14.0**: the driver's (daily health hook,
`index`, `verify`) by a pre-install that `mcp-driver.mjs` resolves ahead of the
plugin's command (GH #214), the session's by `SOCRATICODE_SPEC` in Claude
Code's environment **at startup** (GH #223). `.claude/settings.json`'s `env`
block only declares it: the VS Code extension (2.1.280) expands the plugin's
args before merging that block, so the server gets the variable in its
environment and `@latest` in its argv (gregoryfoster/skills#332, two hosts). The
mechanism is VS Code's machine-scoped setting, in
`~/.vscode-server/data/Machine/settings.json`, live after a full reconnect:

```json
{ "claudeCode.environmentVariables": [{ "name": "SOCRATICODE_SPEC", "value": "socraticode@1.14.0" }] }
```

Verified here 2026-09-24, extension 2.1.281: the session's server launched as
`npm exec socraticode@1.14.0` from a checkout with no settings block, so the
machine setting alone carried it.

An exact spec launches from its own npx tree — built on first launch, so warm
it. Install capped, with `choom`: sessions here sit at `oom_score_adj` -1000,
where a cap stalls rather than kills.

```bash
npm view socraticode version        # pick a literal; never @latest
cap() { systemd-run --user --scope -p MemoryHigh=1200M -p MemoryMax=1536M choom -n 500 -- "$@"; }
cap npm install --prefix ~/.socraticode/pin socraticode@<version>
cap npm exec --yes --prefer-online --package=socraticode@<version> -- true
node skills-vendor/gregoryfoster-skills/skills/init-socraticode/scripts/mcp-driver.mjs resolve
bash skills-vendor/gregoryfoster-skills/skills/init-socraticode/scripts/preflight.sh --check
```

`resolve` launches nothing; it should name the pin. The variable does nothing on
a plugin build that doesn't read it (a 1.14.0 label does not guarantee one);
preflight says so, and warns if the declared pins disagree.

**Neither preflight nor the health hook sees the launch.** Both read
`SOCRATICODE_SPEC` from their own environment, which the settings block always
reaches, so both report the session pinned (preflight's ✓, the hook's absent
pin-drift note) while it runs `@latest` — measured here, health-check said
*fixed at 1.14.0* over a live `@latest` server (gregoryfoster/skills#332).
Verify what launched, not a manifest or a check — two of the plugin's three
manifests hardcode `@latest`. List only the servers the IDE sessions' own
`claude` started; a bare `grep` also matches its own shell and any server a
`claude mcp list` or preflight run launched with the shell's environment:

```bash
for p in $(pgrep -f 'native-binary/claude'); do ps --ppid "$p" -o args= | grep '^npm exec socraticode'; done
```

Expect `npm exec socraticode@1.14.0`. Not `claude mcp list` from a shell: it
reports the calling shell's environment, and PATH's CLI trusts no folder here,
so the settings block never reaches it (measured 2026-09-24, 2.1.267).

**Re-pin it all together** — pre-install, warm-up, machine setting,
`.claude/settings.json` — as a decision, never on a schedule, then run preflight
for the declared values and `ps` for the launch. The health hook's pin-drift
check goes silent whenever the variable is set, so a half re-pin (two builds
writing one store) passes it.
