# Nightly Postgres backup — Design (GH #240)

**Date:** 2026-10-26
**Issue:** #240 · **Epic:** #272 · **Gates:** #350 · **Refs:** #235, #233, #241, #255, #352, #354
**Checked against:** `origin/main` `f50e7f1`

## Problem

The cluster has no backup. `audit-archive` exports expired audit rows and
deletes them: archival, not a backup. #350 (the move onto co-image-base) needs a
regime live first: its transfer *is* the restore path.

**Measured 2026-10-26:** `address_validator` is 152 MB on disk (indexes
included), alembic head `022`, about 92k live rows (82.6k in `audit_log`). The
#235 dump was 12.7 MB in 1.5 s. PostgreSQL 16.15, `pg_dump` 16.15. No large
objects, no table under row security. `pg_hba.conf` has `local all all peer`.

## Approach

**Port the cohort pattern** (provisioner `docs/BACKUP.md`) from its closest
sibling, wslcb-licensing-tracker (`backup.py`, `restore.py`,
`wslcb-backup.service`, `setup-backup-role.sql`, the rehearsal test), less its
file-archive phase and monthly tier. That is the only approach considered
seriously:

- A bash job like the liveness probe is out: the host has no `gcloud`, and
  shell can't easily make a create-only GCS upload with object metadata.
- Reusing `audit-archive`'s identity is out (coordinator): that's ADC as
  `exedev`, with `delete` on the audit bucket.

## Decisions to approve

| Question | Proposal |
|---|---|
| **Bucket** | `gs://co-address-validator-backup`, project `co-address-validator`, the audit bucket's location (`US-WEST2`), `STANDARD`. Uniform access, public-access prevention, no versioning. Lifecycle: delete at age 30. Soft-delete on (7-day default) |
| **Writer** | Service account `address-validator-backup@co-address-validator.iam.gserviceaccount.com`, bucket-level `objectCreator` + `objectViewer` only. Key `/etc/address-validator/backup-gcs.json`, root:root 0600 |
| **Object** | `<hostname>/<YYYYMMDDTHHMMSSZ>.dump` (`address-validator/…`). Metadata: `dumped_at`, `sha256`, `size_bytes`, `alembic_head`, `server_version`, `pg_dump_version`, `toc_entries`, `source_host`. `if_generation_match=0` |
| **Time** | **02:30 UTC** daily, `Persistent=true`, no jitter. `TimeoutStartSec=15min`, so even a slow run finishes before the 03:00 archive deletes rows. Knob: `quiet 03:00-06:15` → `quiet 02:30-06:15` |
| **Unit / role** | `address-validator-backup.service`, `DynamicUser=yes`, `User=address_validator_backup`. Postgres role `address_validator_backup`: `LOGIN INHERIT PASSWORD NULL`, every other attribute off (`NOSUPERUSER … NOBYPASSRLS`), member of `pg_read_all_data` only, reached by `local all all peer` (already covers it) |
| **Check-in** | co-status monitor `co-address-validator-backup`, operator-created. **My call:** in the liveness tenant, so the unit reuses `/etc/address-validator/status-checkin.key` (it exists, so no fallback is needed). If you'd rather a separate tenant, it gets its own `backup-checkin.key`. Fields: `interval_seconds` 86400, `grace_seconds` 3600 (≥ timeout + slack), `renotify_seconds` 86400, `enabled` true |
| **PII** | Below |

## PII stance

The dump holds the live `audit_log` (`raw_input` and **raw client IPs**; #233
hashes IPs only in the Parquet archive), `model_training_candidates` (raw
addresses) and the validation cache. So:

- **Readers:** the backup service account and the project owner. Nobody else.
  The provisioning commands list the project's IAM policy and must show no
  project-level storage role (on `address-validator@`, the API's account, or
  anyone else) that reaches the bucket.
- **Retention:** 30 days, plus 7 days of soft-delete. A row the archive deletes
  at 90 days survives in backups up to ~37 days more. That is the stated cost
  of a 24 h RPO; `docs/RECOVERY.md` says it.
- **No client-side encryption.** The key would sit on the host beside the GCS
  key, so it buys nothing against host compromise; the cohort doesn't either.
  GCS encrypts at rest.
- **Logs:** object name, size, sha256, alembic head, counts and error text
  only. Never row values, dump bytes, or key material (the journal persists,
  #241). `pg_dump`/`pg_restore` stderr is passed on only as a 3-line tail,
  which for a schema-only error names objects, never data.
- **On the host:** the dump lives only in the unit's `PrivateTmp=` and is gone
  when the run ends. A restore downloads to a new 0700 directory, file 0600,
  deleted after.
- **Bonus:** the knob gains `backup address-validator-backup.service
  postgresql@16-main`, so a patch run's recovery point starts this unit
  instead of writing its own PII dump under `$HOME`.

## Shape

**Code** (`infra/`, run by `.venv/bin/python`, as the other jobs):

- `infra/backup_db.py`: preflight list → `psql` alembic head → `pg_dump
  --format=custom --file=` (wslcb's finding: not stdout) → `pg_restore --list`
  must find data sections for `alembic_version` and every table in
  `db/tables.py` (already drift-tested against the schema) → `pg_restore
  --file=/dev/null` → sha256 → create-only upload → `ok`/`alert` check-in.
  Refuses a `GOOGLE_APPLICATION_CREDENTIALS` outside `$CREDENTIALS_DIRECTORY`.
- `infra/restore_db.py`: `--list`; `--latest --prefix HOST` (never defaults
  to this host); `--into DB --run-as postgres` (sha256 checked, then
  `--exit-on-error --single-transaction` on stdin); `--download-only DIR`.
- `infra/status_checkin.py`: a small Python check-in (`httpx`, already a
  dependency). The liveness probe stays bash.
- `scripts/db/setup-backup-role.sql`: wslcb's script, renamed. Idempotent, with
  a read-only report.

**Unit:** wslcb's sandbox: `ProtectSystem=strict`, `ProtectHome=tmpfs` +
`BindReadOnlyPaths=/home/exedev/address-validator` (the venv's python is
`/usr/bin/python3.12`, so it stays visible), `InaccessiblePaths=` for
`/etc/address-validator` and the repo `.env`, `PrivateTmp=`, no capabilities,
`Nice=10`. Bucket and monitor id go in `Environment=` (not secrets), with no
`EnvironmentFile=`. `LoadCredential=gcs:` and `LoadCredential=checkin-key:`,
**no `SetCredential=` fallback**: a missing key is 243/CREDENTIALS →
`OnFailure=unit-failure@%n.service`, and the check-in is missed. Memory:
`MemoryHigh=384M` / `MemoryMax=512M` to start; the first run is measured and the
figure goes in HOST-MEMORY.

**Tests:**

- unit tests: the TOC parse, the key, metadata, the 412 rule, refusals;
- a unit-file test, as wslcb's `test_infra_backup_units.py`: no
  `SetCredential=`, grace ≥ timeout, the timer inside a quiet range (the
  existing knob test covers that too);
- **the rehearsal:** seed a DB → real `pg_dump` → in-memory bucket fake → real
  `pg_restore` into a standing scratch DB → row counts and head match. Runs
  against an isolated `address_validator_test_240` during this branch. The
  restore target is `address_validator_test_rehearsal`. I create both as
  `postgres`, the way #253 did; neither is a prod database or role.

**Docs:** `docs/RECOVERY.md` in wslcb's shape: what's backed up, step 0,
provisioning (gcloud), the co-status monitor, install and first run, proving
the grant is create-only, restore, go/no-go, drill. Also a TIMERS.md install
entry linking it, a DEPLOYMENT.md link, a SENSITIVE-AREAS-INFRA row, and a
HOST-MEMORY row. **Nothing in AGENTS.md.**

## Operator steps (written, not run by me)

1. gcloud: bucket, lifecycle, SA, bindings, key; IAM policy check.
2. co-status: the monitor; the id goes into the unit by a follow-up commit.
3. The role: `sudo -u postgres psql -d address_validator <
   scripts/db/setup-backup-role.sql`. **Needs your approval first.**

## Gates (your explicit approval each)

Role creation · merge · unit install + enable (first a manual `start`, then
`enable --now` the timer) · the restore drill into `address_validator_drill`,
checking row counts, alembic head and roles, then `dropdb`. The result is
recorded on #240.

## Out of scope / moot

- The `/var/backups/*20260929T033416Z*` files are gone. The PR says so.
- `~/backups/audit_log-2026-09-26.dump` (#228) isn't touched. Once this
  regime has run clean, its case for existing is weaker; that's a separate
  call.
- Roles and cluster state aren't in the dump. RECOVERY.md lists them in step 0.
