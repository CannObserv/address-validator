# Backup and recovery (#240)

The nightly backup and the runbook around it. The pattern is the cohort's
(CannObserv/provisioner `docs/BACKUP.md`), ported from
CannObserv/wslcb-licensing-tracker's `docs/RECOVERY.md` and code. This repo
drops wslcb's file-archive phase and its monthly tier: there are no files to
archive, and the cohort keeps a flat 30 days. Drill results go in the
[log](#drill-log) at the end, and on #240 for the first one.

## What is backed up

| | |
|---|---|
| **What** | `pg_dump --format=custom` of `address_validator`: every table, the alembic head, ownership and grants. 12.7 MB in 1.5 s at #235 (2026-09-29) |
| **Not** | roles (cluster state; see [step 0](#step-0--what-no-backup-holds)), the `address_validator_test*` databases, the files in step 0 |
| **Bucket** | `gs://co-address-validator-backup` (the cohort's `co-gcs` project), its own, never the audit archive's bucket |
| **Key** | `<hostname>/<YYYYMMDDTHHMMSSZ>.dump`, the time `pg_dump` started: `address-validator/20261027T023001Z.dump` |
| **Metadata** | `dumped_at`, `sha256`, `size_bytes`, `alembic_head`, `server_version`, `pg_dump_version`, `toc_entries`, `source_host` |
| **Retention** | the bucket's: a lifecycle rule deletes at 30 days; soft-delete (7 days) stays on |
| **Verified** | `pg_restore --list` finds a data section for `alembic_version` and every table `db/tables.py` mirrors; `pg_dump` is the server's major version; `pg_restore --file=/dev/null` reads it through |
| **RPO** | 24 h. Lost since the dump: audit rows and training candidates (gone); validations (re-run at the usual USPS/Google quota cost) |

`infra/address-validator-backup.timer` runs `infra/backup_db.py` daily at
**02:30 UTC** as `address-validator-backup.service`. That is before the 03:00
`audit-archive` deletes expired rows, so each dump still holds them, and the
unit's 15-minute timeout ends any run by 02:45. The patching knob's quiet range
starts at 02:30 to cover it. A failed run fails the unit
(`OnFailure=unit-failure@`) and checks in `alert`; a good one checks in `ok`.

**Why create-only.** The service account holds `objectCreator` + `objectViewer`
on its bucket and nothing else: no overwrite, no delete. A compromised host
can't erase its history; retention belongs to the bucket. `objectViewer` lets
the restore use the same key.

**Why no credential.** The unit runs as its own `DynamicUser=`,
`address_validator_backup`, with no capabilities, in an empty `/home` with only
the checkout bound back read-only. `pg_dump` reaches the role of the same name
by peer auth (`pg_hba.conf`: `local all all peer`). That role holds
`pg_read_all_data` and nothing else. The GCS key arrives by `LoadCredential=`,
and `backup_db.py` refuses a `GOOGLE_APPLICATION_CREDENTIALS` outside the run's
credential directory. There is **no `SetCredential=` fallback**, unlike the
liveness unit (#354). A missing key is `243/CREDENTIALS`, a failed unit and a
missed check-in. Loud is right for a backup.

## PII

The dump holds the live `audit_log`: `raw_input` (address content) and **raw
client IPs**. #233 hashes IPs only in the Parquet archive. It also holds
`model_training_candidates` (raw addresses) and the validation cache. So:

- **Readers:** the backup service account and the project owner, nobody else.
  [Provisioning](#provisioning--needs-the-gcp-project-owner) strips the bucket's
  default `projectEditor`/`projectViewer` bindings and checks the project
  policy for any other storage role.
- **Retention:** 30 days plus 7 of soft-delete. A row the archive deletes at 90
  days survives in backups up to ~37 days longer. That is the stated cost of a
  24 h RPO.
- **No client-side encryption.** Its key would sit on the host beside the GCS
  key, so it would buy nothing against host compromise. GCS encrypts at rest.
- **Logs and check-ins** carry the object name, size, sha256, versions and
  error text only. `pg_dump`/`pg_restore` stderr is cut to a 3-line tail with
  every `DETAIL`/`CONTEXT` line dropped, since libpq quotes row values there.
  The journal persists (#241), and WARNING+ lines leave the host through
  notifier.
- **On disk:** the dump lives only in the unit's `PrivateTmp=`, gone when the
  run ends. A restore fetches into a new 0700 directory (file 0600, `O_EXCL`).
  `--into` removes it afterwards; `--download-only` leaves it to you to
  `shred -u`.
- The patching run's recovery point starts this unit (the knob's `backup` line)
  rather than writing a second PII dump under `$HOME`.

## Step 0 — what no backup holds

A rebuilt host needs these from their owners, not from GCS:

- `/etc/address-validator/.env` (`API_KEY`, the DSN with the
  `address_validator` role's password, `USPS_*`, `NOTIFIER_*`, `AUDIT_*`).
- `/etc/address-validator/gcp-sa-key.json` (the Google provider and the audit
  archive's identity), `audit-ip-hmac.key` (the archive's HMAC key; see
  `infra/audit_ip_hmac.py`), `status-checkin.key` (co-status tenant key), and
  `backup-gcs.json` (this backup's key; a fresh one from the GCP owner).
- The roles: `address_validator` (LOGIN, password matching the DSN) and
  `address_validator_backup` (`scripts/db/setup-backup-role.sql`).
- The repo `.env` (GitHub PATs), and the tailnet node ([TAILNET.md](TAILNET.md)).

Code, units and the custom CRF model (`src/address_validator/custom_model/`)
are in git. libpostal is a stateless image.

## Provisioning — needs the GCP project owner

The node has no `gcloud`. Run this from a workstation with `roles/storage.admin`
and `roles/iam.serviceAccountAdmin` on `co-gcs`. The cohort sets no gcloud
default project, so every command names it:

```bash
PROJECT=co-gcs
BUCKET=co-address-validator-backup
SA=address-validator-backup
MEMBER="serviceAccount:$SA@$PROJECT.iam.gserviceaccount.com"
LOCATION=$(gcloud storage buckets describe gs://co-address-validator-audit --format="value(location)")

gcloud storage buckets create "gs://$BUCKET" --project="$PROJECT" --location="$LOCATION" \
    --default-storage-class=STANDARD --uniform-bucket-level-access --public-access-prevention

cat > /tmp/av-backup-lifecycle.json <<'EOF'
{"rule": [{"action": {"type": "Delete"}, "condition": {"age": 30}}]}
EOF
gcloud storage buckets update "gs://$BUCKET" --lifecycle-file=/tmp/av-backup-lifecycle.json
gcloud storage buckets describe "gs://$BUCKET" \
    --format="yaml(name, location, default_storage_class, lifecycle_config, soft_delete_policy, versioning_enabled, uniform_bucket_level_access, public_access_prevention)"
#   gcloud storage's key names; the API's camelCase spellings print nothing rather than erroring

gcloud iam service-accounts create "$SA" --project="$PROJECT" \
    --display-name="address-validator Postgres backup writer (#240)"
for ROLE in roles/storage.objectCreator roles/storage.objectViewer; do
    gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member="$MEMBER" --role="$ROLE"
done

# PII: a new bucket grants the project's editors and viewers by convenience
# value. Strip them; the project owner keeps its bindings. A pair the policy
# doesn't hold errors harmlessly.
for PAIR in projectEditor:roles/storage.legacyBucketOwner projectEditor:roles/storage.legacyObjectOwner \
            projectViewer:roles/storage.legacyBucketReader projectViewer:roles/storage.legacyObjectReader; do
    gcloud storage buckets remove-iam-policy-binding "gs://$BUCKET" \
        --member="${PAIR%%:*}:$PROJECT" --role="${PAIR#*:}" || true
done
gcloud storage buckets get-iam-policy "gs://$BUCKET" --format=json
gcloud projects get-iam-policy "$PROJECT" --flatten="bindings[].members" \
    --format="table(bindings.role, bindings.members)" \
    --filter="bindings.role~^roles/(storage|editor|viewer|owner)"

gcloud iam service-accounts keys create address-validator-backup.json --iam-account="${MEMBER#serviceAccount:}"
```

**Check before going on:**

- `describe` shows the one 30-day rule, soft-delete on, versioning off, uniform
  access and public-access prevention `enforced`.
- The bucket policy holds only `projectOwner` legacy bindings and the service
  account's two roles.
- The project policy grants no storage role, and no `editor` or `viewer`,
  beyond the owner. A project-level role there reaches this bucket too, and
  `co-gcs` is shared by the cohort: any other host's service account with a
  project-level storage role could read this host's PII. Each must hold its
  grants on its own buckets only.

Copy `address-validator-backup.json` to the node by `scp`, then delete the
workstation copy.

## The co-status monitor

Created on co-status (CannObserv/status#63, 2026-10-26), as #255 did for
`co-address-validator-live`: monitor id `01M5WQXG1GGJBQW3ZKBPV806MY`. It is in the same tenant, so the unit reuses
`/etc/address-validator/status-checkin.key`, which is already there. Monitor
`co-address-validator-backup`, on the tenant's notifier channels:

| Field | Value | Why |
|---|---|---|
| `interval_seconds` | `86400` | one run a day |
| `grace_seconds` | `3600` | the unit's 15-minute timeout plus the timer's accuracy and a margin: a run killed by the timeout cannot check in (`tests/unit/test_backup_unit.py` holds them to this) |
| `renotify_seconds` | `86400` | repeat daily while missing |
| `title_template` / `body_template` | `address-validator backup {{ outcome }} on {{ source_host }}` / `{{ error }}` | rendered for an `alert` only |
| `enabled` | `true`, stated | a disabled monitor still delivers `alert`s but never alarms on silence |

The check-in is `POST /api/v1/monitors/<id>/checkin`, `X-API-Key`, body
`{"status": "ok"|"alert", "variables": {…}, "metadata": {"fault": …}}`. An
`alert` carries `source_host`, `outcome` (`failed`) and `error`, with fault
`misconfigured` or `failed`. An `ok` carries `outcome` (`uploaded` or
`unchanged`), `object`, `dumped_at`, `size_bytes`, `sha256`, `alembic_head` and
`source_host`.

The id is in the unit as `Environment=BACKUP_MONITOR_ID=` (not a secret). An
empty value makes each run log `check-in not configured` and ship anyway. The
monitor was created disabled: co-status enables it after the first `ok`.

**Then see the alarm fire** before relying on it: narrow the monitor to
`interval_seconds` 60 / `grace_seconds` 0 and wait for *"has stopped
reporting"*. Then `sudo systemctl start address-validator-backup.service` for
*"has recovered"*, and restore 86400 / 3600.

## Install and first run

Each step is the owner's to approve. **The role first**, once per cluster
(purely additive; read its header):

```bash
sudo -u postgres psql -d address_validator < scripts/db/setup-backup-role.sql
```

Its report must read `t` for `login`, `inherit`, `no_password`,
`reads_all_data` and `can_connect`, `f` elsewhere, and `0` for `unreadable`,
`under_row_security` and `large_objects`.

**Then the key**, root:root 0600, read only by systemd:

```bash
sudo install -m 0600 -o root -g root address-validator-backup.json /etc/address-validator/backup-gcs.json
shred -u address-validator-backup.json
```

**Then the units**, after the merge, from the main checkout. Start one run by
hand, and enable the timer only once it succeeds:

```bash
sudo infra/install-units.sh address-validator-backup.service address-validator-backup.timer
infra/install-units.sh --check && systemd-analyze verify infra/address-validator-backup.{service,timer}
# start without waiting, and sample the cgroup's memory peak while it runs (no swap, #228)
sudo systemctl start --no-block address-validator-backup.service
while [ "$(systemctl show -p ActiveState --value address-validator-backup.service)" = activating ]; do
    cat /sys/fs/cgroup/system.slice/address-validator-backup.service/memory.peak 2>/dev/null; sleep 0.2
done | sort -n | tail -1
systemctl show -p Result,ExecMainStatus address-validator-backup.service   # success, 0
journalctl -u address-validator-backup -n 20 -o cat --no-pager
sudo systemctl enable --now address-validator-backup.timer
```

This host's manager has `LogTarget=console`, so no `Starting`/`Finished` lines
reach the journal (#354): read `Result`/`ExecMainStatus`. Record the memory
peak in [HOST-MEMORY.md](HOST-MEMORY.md#reservations). Failures:

- `243/CREDENTIALS`: a key file is missing.
- `203/EXEC`: an interpreter the empty `/home` hides.
- `FATAL: role "address_validator_backup" does not exist`: the role script
  hasn't run yet.
- `not found, or not listable`: the bucket name, or a key without
  `objectViewer`.

### Prove the grant is create-only

By observation, on a probe object of its own: create it, then try to overwrite
and to delete it. Both must answer **403**. (An `if_generation_match=0` upload
over an existing name proves nothing: GCS answers 412 whatever the grant.) As
root, the one identity that can read the key, from the main checkout:

```bash
sudo env GOOGLE_APPLICATION_CREDENTIALS=/etc/address-validator/backup-gcs.json .venv/bin/python - <<'PY'
from datetime import UTC, datetime
from google.api_core.exceptions import Forbidden
from google.cloud import storage
blob = storage.Client().bucket("co-address-validator-backup").blob(f"probe/{datetime.now(UTC):%Y%m%dT%H%M%SZ}")
blob.upload_from_string(b"probe", if_generation_match=0)  # the create: must succeed
for attempt, act in (("overwrite", lambda: blob.upload_from_string(b"again")), ("delete", blob.delete)):
    try:
        act()
        print(f"{attempt}: ALLOWED, the grant is too wide")
    except Forbidden:
        print(f"{attempt}: 403, create-only holds")
PY
```

The probe sits under `probe/`, outside every listing the restore reads, and
the lifecycle rule takes it at 30 days.

## Restore

Run as root with the key, from the main checkout:

```bash
cd /home/exedev/address-validator
R() { sudo env PYTHONPATH=src GOOGLE_APPLICATION_CREDENTIALS=/etc/address-validator/backup-gcs.json \
      BACKUP_BUCKET=co-address-validator-backup .venv/bin/python infra/restore_db.py "$@"; }
R --list                                    # every host's dumps; SUSPECT marks a name the bucket's clock contradicts
```

**Name the source host.** `--latest` requires `--prefix` and never defaults to
this host. Once a rebuilt host's own timer has run, its newest dump is a real,
verifiable dump of the wrong database. `--latest` passes over any name later
than the bucket's own creation time for it. A host renamed mid-migration writes
under `<name>-next/` until its cutover rename (provisioner `docs/BACKUP.md`);
the restore names `address-validator`.

On a fresh cluster, the owning role, the backup role and an empty database come
first. The dump carries ownership and grants, not roles:

```bash
sudo -u postgres createuser --login address_validator && sudo -u postgres psql -c "\password address_validator"
sudo -u postgres createdb -O address_validator address_validator
sudo -u postgres psql -d address_validator < scripts/db/setup-backup-role.sql
R --latest --prefix address-validator --into address_validator --run-as postgres
```

`--into` refuses a database that holds any table. It fetches into a private
temporary directory, checks the recorded sha256 and the backup's own
`pg_restore` reads, and loads on stdin with `--exit-on-error
--single-transaction`, so a failure leaves the target empty and `postgres`
never reads a root-written file. To fetch and inspect only: `--download-only
DIR` (a new or private 0700 directory).

**Go/no-go** runs after every `--into` load and prints one line per check, then
`GO` or `NO-GO` (exit 1):

1. each table's `count(*)` equals the dump's own `COPY` row count;
2. `alembic_version` equals the `alembic_head` the backup recorded;
3. the roles `address_validator` and `address_validator_backup` exist.

On `GO`, start `address-validator.service` and check `/api/v2/health`.

## Drill

Quarterly, after any change to `backup_db.py`, `restore_db.py` or the unit, and
as #350's migration proof. Restore into a scratch database, **never
`address_validator`**:

```bash
sudo -u postgres createdb -O address_validator address_validator_drill
R --latest --prefix address-validator --into address_validator_drill --run-as postgres   # GO
sudo -u postgres dropdb address_validator_drill
```

Record the date, object, timings, the go/no-go lines and anything that
surprised you below.

Every test run also rehearses a real `pg_dump` → fake bucket → `pg_restore`
round trip and the go/no-go (`tests/unit/scripts/test_backup_rehearsal.py`). It
restores into the standing `<test db>_rehearsal` database, which the test role
can't create:

```bash
sudo -u postgres createdb -O address_validator address_validator_test_rehearsal
# and for an isolated address_validator_test_<n>: address_validator_test_<n>_rehearsal
```

## Drill log

Newest first.

| Date | By | Object | What it proved | Notes |
|---|---|---|---|---|
