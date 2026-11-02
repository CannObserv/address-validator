# Host memory

Single-VM dev+prod means an interactive agent session shares 7.2 GiB **and no
swap** with the production service. Two facts make that sharper than it sounds:

- exe.dev session processes inherit `oom_score_adj` **-1000** from exe.dev's
  `sshd`. The adj survives reparenting: the VS Code server that hosts Claude
  Code outlives its ssh connection and ends up under PID 1 with no `sshd` above
  it, still at -1000, so read the adj up the chain rather than look for `sshd`
  (#236). `exe-init` is the kernel's `init=` and execs systemd, so no `exe-init`
  process is left running (#238). No OOM killer can pick a session —
  neither the kernel's nor earlyoom — so under real exhaustion something on
  the host goes instead, `address-validator.service` included.
- With no swap and a small `vm.min_free_kbytes`, the host does not reach the
  OOM killer at all in the common case. A burst allocation drains the free pool
  faster than reclaim refills it and `GFP_ATOMIC` callers that cannot sleep
  (softirq, network) simply fail: page-allocation errors in `tailscaled` and
  `ksoftirqd`, a dead network, and **nothing killed**. That is how the
  2026-09-16 outage on the sibling `broker` VM presented — the bus was down
  57m 48s (gregoryfoster/skills#295).

**`tailscaled` runs here too** (since 2026-09-29, #232), at about 51 MB RSS.
It carries the notifier hop ([TAILNET.md](TAILNET.md)), and it owns `/etc/resolv.conf` through MagicDNS,
so the page-allocation failure above now also takes out DNS for the USPS and
Google providers, not just the tailnet.

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
| `libpostal.service` (~1.9 GB) | `libpostal: unavailable`, status stays ok | **none, deliberately** — largest thing on the host, `Restart=always`, and the only one whose loss the service survives. The right thing to lose first. Restarts are capped at 5 in 600s (#248): repeated kills under sustained pressure leave it **down** and alert once — each restart reloads 1.9 GB into the same pressure; recover with `sudo systemctl reset-failed libpostal && sudo systemctl start libpostal`. **Never `docker exec` the server binary into the live container:** gopostal loads the model in Go `init()`, before flags are parsed, so even `-h` loads a second 1.9 GB copy and the `--memory=2.5g` cgroup OOM-kills the sidecar (2026-10-19, #328). Inspect it in a throwaway container instead |
| `audit-archive.service` (nightly 03:00 UTC oneshot) | — | none; **capped** at `MemoryHigh=768M` / `MemoryMax=1G` so an overrun kills the job inside its own cgroup, not a reserved service (#228). Measured (2026-09-25, 298k-row synthetic day ≈ the 2026-03-12 peak): streamed export peaks at **153 MB** RSS (+45 MB over its 108 MB import baseline); the pre-#228 whole-day `list[dict]` export peaked at 648 MB. Re-measured on pyarrow 26.0.0 (2026-10-17, #321): **155 MB** (+51 MB over 104 MB), against 148 MB for 19.0.1 in the same run |
| `archive-age-check.service` (daily 06:00 UTC oneshot, #249) | — | none; **capped** at `MemoryHigh=192M` / `MemoryMax=256M`. Measured (2026-10-15): **58 MB** peak RSS — one `min(timestamp)` query, no app import beyond `db/tables.py` |
| `libpostal-probe.service` (every-2-min oneshot, #256) | — | none; **capped** at `MemoryHigh=24M` / `MemoryMax=32M`. bash + one `curl`. Measured (2026-10-17, four transient `systemd-run --wait` runs): **0.3–1.6 MB** cgroup memory peak; curl's own RSS is 11 MB, mostly shared library pages charged elsewhere. Headroom is deliberate: an OOM kill fails the run, and a failing run alerts on every tick |
| `address-validator-liveness.service` (every-5-min oneshot, #255) | — | none; **capped** at `MemoryHigh=24M` / `MemoryMax=32M`. bash + `curl` + `jq`, as `libpostal-probe`. Measured (2026-10-20, four transient `systemd-run --wait` runs of the healthy path, the unit's user, credential and caps): **500–508 KB** cgroup memory peak, ~0.2 s, ~40 ms CPU; the journal keeps no per-unit `memory peak` line on this host. Pass the fallback as `-p 'SetCredential=status-checkin.key:\n'`, as the unit does: the empty value is invalid syntax on systemd 255 — `systemd-run -p` exits 1 before exec, and a unit file ignores the line (#354). Headroom is deliberate: an OOM kill fails the run, and a failing run alerts on every tick |
| `address-validator-backup.service` (nightly 02:30 UTC oneshot, #240) | — | none; **capped** at `MemoryHigh=384M` / `MemoryMax=512M`: python + `google-cloud-storage`, `pg_dump`, two `pg_restore` reads and a ~7 MB upload. `pg_dump`'s server backend is charged to postgres, not here. **Measured 2026-10-26:** the first run's cgroup `memory.peak` was 79 MiB (83,759,104 bytes) for a 6.9 MB dump in about 2.5 s ([RECOVERY.md](RECOVERY.md#install-and-first-run)) |
| `cannobserv-disk-gc.service` (daily 05:15 UTC + ≤30 min oneshot, Provisioner's, #326) | — | none; **capped** at `MemoryHigh=384M` / `MemoryMax=512M` by drop-in `infra/cannobserv-disk-gc-memory.conf` (install line in the file; never edit the installed unit). python + `uv cache prune --force` + the docker CLI. Measured 2026-10-30: report-only run **2.2 MB** cgroup peak, 0.7 s; the first pruning run is unmeasured |
| `cannobserv-disk-headroom.service` (every-10-min oneshot, Provisioner's, #326) | — | none; **capped** at `MemoryHigh=96M` / `MemoryMax=128M` by drop-in `infra/cannobserv-disk-headroom-memory.conf`. python + one check-in. Headroom is deliberate: an OOM kill misses the check-in and co-status pages *has stopped reporting* |
| everything else in `system.slice` | — | the 128M left undistributed after the two claims below |
| `postgresql@16-main` (~281 MB) | `database: error` → **HTTP 503** | `MemoryLow=384M` |
| `address-validator` (~150 MB) | the service itself | `MemoryLow=512M`, `OOMScoreAdjust=-500` |

Protecting postgres is not optional generosity: an outage there fails the
health check outright, so leaving it at `MemoryLow=0` would have protected the
app while letting the thing it returns 503 without be reclaimed out from under
it. Only `address-validator` gets `OOMScoreAdjust` — giving postgres the same
value would restore the tie between them rather than ordering them.

**The journal no longer lives in RAM** (#241). While the image's mask on
`systemd-journal-flush` kept it volatile, journald wrote to `/run/log/journal`,
a tmpfs: unswappable shmem charged to memory, up to its 147.6M runtime cap, and
a 51-day uptime reached 101.5 MB of it. Persistent now, it writes
`/var/log/journal` (512M cap, disk). After the flush `/run/log/journal` is empty
and its pages are free; the disk files are page cache, which reclaim can drop.
The flush runs again at every boot. If it ever stops (a re-mask), the RAM cost
comes back with the data loss: read `df -h /run` and `ls -A /run/log/journal/*/`.

## earlyoom: kept, for libpostal

GH #225. earlyoom cannot close the session gap: 1.7 skips a -1000 process
exactly as the kernel does (`kill.c:242-253`), `--prefer` or not, so a
session, VS Code Server and anything they launch are never its victim. What it
does is act before the kernel stalls, on the host's own processes, so what
decides whether it earns its place is which one it takes.

A dry run on 2026-09-24 answered that: stock earlyoom takes
`wof-libpostal-s` (1.9 G), the first row of the table above, so it stays. But
the stock order rests on RSS alone. After libpostal came the user's session
bus (`dbus-daemon`), the user manager (`systemd`, `(sd-pam)`) and then
postgres, fifth: a 503. `infra/earlyoom.default` sets the order instead. It
`--prefer`s libpostal, which is adj 0 and comes back on its own, up to its
start limit (see the table above). SocratiCode's `qdrant`, `ollama` and
`llama-server` were preferred too, until the local store retired (#381; dry
run 2026-11-02 still takes `wof-libpostal-s` first). It `--avoid`s the service,
postgres and the user manager, which puts postgres behind every small daemon,
and `tailscaled`: without those three, it went third, and its
`Restart=on-failure` treats a SIGTERM as clean, leaving DNS and the notifier hop
down. `--avoid` is -300, a
shift rather than an exemption. The session bus is left out on purpose:
`dbus.socket` restarts it on next use, so it goes second. The file's header
gives the reason for each flag. Where sessions sit at -1000, `--prefer`
reaches nothing of theirs, so it names none of them.

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
  --prefer '^(wof-libpostal)' \
  --avoid '^(uvicorn|postgres|systemd|[(]sd-pam[)]|tailscaled|sshd)$' > dry.txt 2>&1
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
it. The disk GC never prunes it: its `npx` tier keeps `sha512(spec)[:16]` for
both declared specs, recomputed each run, and an unreadable source keeps every
tree (#322, #326; `npx_spec_files` in [TIMERS.md](TIMERS.md) → Disk GC). Install capped, with `choom`: sessions here sit at
`oom_score_adj` -1000, where a cap stalls rather than kills.

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

## Host tools and their trust stores (#345)

After the eMudhra compromise (2026-10-23). Each tool below carries or loads its
own TLS roots, so fixing `ca-certificates` doesn't reach it.

| Tool | Version | Updated by | Trust |
|---|---|---|---|
| uv (`~/.local/bin`, `/usr/local/bin` symlinks) | 0.13.5 | `uv self update` | bundled roots; distrusts eMudhra from 0.13.4 |
| Node (`nodejs`, NodeSource) | 24.22.1 | apt, knob `follow` | bundled roots |
| Claude Code shell CLI | 2.1.308 | `claude update` (stable) | bundled roots. The VS Code extension's copy updates separately, and a session runs the build it started with |
| Tailscale | 1.104.2 (go1.27.3) | knob lane `origin:pkgs.tailscale.com` | Go distrusts the eight eMudhra intermediates by name |
| codex | removed | — | image binary, unused; an image refresh may restore `/usr/local/bin/codex` |

**A process loads its trust store once, at start.** After a store change,
restart each long-lived client that makes outbound TLS calls. On 2026-10-23
ollama still trusted the compromised chain until it was restarted. dockerd
already rejected it without a restart. To probe a client, connect it to
`https://dv-g3b-noct-emudhra.we-have-a-problem.com/`. A certificate error means
it's fixed; any HTTP-level answer means it still trusts the chain.

## SocratiCode's store: co-index (GH #381)

Since 2026-11-02 SocratiCode's vector store and embedder are remote: Qdrant
and Ollama on `index`, the cohort's shared host (CannObserv/notifier#57). This
VM runs neither. The local `socraticode-qdrant` container, its 2.9 GB volume
and its image, `ollama.service` (~310 MiB resident), its 262 MB model store,
its binaries and the `ollama` user were removed then. That freed about 3.5 GB of
disk. Docker stays for libpostal.

- **Client config is committed.** `.socraticode.json` (`projectId`) and the
  `env` block in `.claude/settings.json`, pinned by
  `tests/unit/test_socraticode_config.py`. The one global `QDRANT_API_KEY`
  sits only in the **main checkout's** git-ignored
  `.claude/settings.local.json` (`tests/unit/test_settings_local_ignored.py`).
  A worktree session carries the block but no key, so its `codebase_*` calls
  fail. Search from a main-checkout session, or `grep`.
- **Nothing on a production path depends on it.** If `index` is down, search
  falls back to `grep`. A missing ACL grant shows up as DNS failure, not a
  denial ([TAILNET.md](TAILNET.md)).
- **Engine upgrades are co-index's.** A model or engine change there
  (CannObserv/index#11: ollama 0.40 case-folds input, 0.21 didn't) means a full
  re-index of every client. The watcher and `codebase_index` re-embed only
  changed files, so neither one fixes mixed vectors.

**A full re-index** has two steps:
1. **Run `codebase_remove` on its own.** It drops this project's code, graph and
   context collections, and only those: the store holds every cohort repo.
   Confirm with `codebase_list_projects` that `address-validator` is gone. Sent
   in the same batch as `codebase_index`, it failed (`Unable to remove watcher:
   Invalid argument`) and removed nothing, and the index pass then embedded 0
   chunks in 2.3 s (2026-10-16).
2. **Run `codebase_index`.** It rebuilds the code index, then the graph, then
   every context artifact. No separate `codebase_context_index` is needed.

Measured 2026-11-02, first index into `index` (2 shared vCPUs): 2,583 code
chunks and 1,202 context chunks took **63 min** (code 41, context 22; on the
local CPU embedder it was 53). The cost here is time, not memory: the server
only. It can queue behind another repo's index.

**The way back**, if co-index is gone for good. Bring the local store up **on
loopback, before any session does**. SocratiCode's own `docker run` publishes
an unauthenticated Qdrant on `0.0.0.0` with telemetry on
(gregoryfoster/skills#494). The script creates it on `127.0.0.1` with
`QDRANT__TELEMETRY_DISABLED=true`:

```bash
bash skills-vendor/gregoryfoster-skills/skills/init-socraticode/scripts/loopback-containers.sh qdrant ollama
```

Then drop the store keys (`QDRANT_*`, `OLLAMA_*`, `EMBEDDING_*`) from
`.claude/settings.json` along with their test, put `qdrant` and `ollama` back in
earlyoom's `--prefer`, restart Claude Code and run a full index. Keep
`projectId`. The daily health hook names any non-loopback listener on
16333/16334/11435, so a managed fallback that did go wide gets reported.
