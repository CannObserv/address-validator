# Archive `client_ip` as a keyed hash — Design (GH #233)

**Date:** 2026-10-24
**Issue:** #233 · **Epic:** #272 · **Refs:** #228, #230, #321, #241
**Checked against:** `origin/main` `3431e24`

## Problem

`infra/archive_audit.py` writes `audit_log.client_ip` raw to GCS, with no
expiry. Every `/api/*` caller shares one API key, so the IP is the only thing
that tells callers apart after the 90-day hot window. The caller query (#233,
2026-10-23) found a residential `/api` caller and 53 crawler IPs, so the IPs
are personal data and the HMAC goes ahead.

**What is in the bucket now** (measured 2026-10-24, aggregates only):
141 files, `audit-2026-03-05` → `audit-2026-07-25`, 8.3 MB, 512,857 rows,
one schema. They hold 43 distinct `client_ip` values: 41 public IPv4
addresses, loopback, and one non-IP string. 1,339 rows are non-`/api`.

**Bucket** `co-address-validator-audit` (`US-WEST2`, `NEARLINE`): no
versioning, no retention policy, no lifecycle rule, 7-day soft-delete. The
archive's service account holds `objects.{create,delete,get,list}`, but not
`buckets.get` or `buckets.update`.

## Decisions (operator, 2026-10-24)

| Question | Decision |
|---|---|
| Files already uploaded | **Rewrite all of them in place** with hashes. No versioning, so no raw version survives the overwrite. Soft-delete keeps each raw original restorable for 7 days, by someone with restore permission only |
| GCS expiry rule | **A separate issue.** The service account can't set one, and the retention period is a policy call |
| Crawler (non-`/api`) rows | **Keep them, hashed.** The archive stays row-for-row equal to `audit_daily_stats` |
| `X-Forwarded-For` | **Take the rightmost entry, in this change** (its own commit). exe.dev's proxy docs say it *appends* the IP it saw to any value the caller sent, so the leftmost entry can be faked |

## Design

### 1. Hashing module: `infra/audit_ip_hmac.py` (new, stdlib-only)

```python
KEY_ENV = "AUDIT_IP_HMAC_KEY"        # hex, ≥ 32 bytes (openssl rand -hex 32)
KEY_ID_ENV = "AUDIT_IP_HMAC_KEY_ID"  # ^[A-Za-z0-9._-]{1,32}$, e.g. "2026-10"

class IpHasher:                      # __repr__ never shows the key
    key_id: str
    def hash(self, ip: str) -> str:  # hex HMAC-SHA256(key, canonical_ip(ip))

def canonical_ip(ip: str) -> str     # ipaddress form when it parses; else stripped
def load_hasher(environ) -> IpHasher # raises KeyConfigError (no value in message)
```

- **HMAC-SHA256, hex.** Never a plain SHA-256: the IPv4 space can be
  brute-forced, the same flaw that kept `pattern_key` out (#228).
- **Canonical input.** `ipaddress.ip_address(s).compressed` when it parses,
  so IPv6 spellings of one address hash alike. Anything else (`unknown`) is
  hashed as written. The lookup helper uses the same function.
- **Key validation.** Missing, non-hex, short (< 32 bytes) key, or a
  missing or malformed key ID → `KeyConfigError`, whose message names the
  variable and never contains a value. The `fromhex` exception text is
  never passed on.
- **Lookup CLI.** `python infra/audit_ip_hmac.py` prompts for the IP with
  `getpass` (off-screen, out of shell history and `ps`), or reads it from
  stdin, and prints `<key_id> <hmac>`. `--check` validates the key and
  prints only the key ID and key length.

### 2. `ARCHIVE_SCHEMA` and export (`infra/archive_audit.py`)

- `client_ip` is replaced, in the same position, by
  `client_ip_hmac: string` and `client_ip_key_id: string`. Every row
  carries the key ID, since a per-row column survives concatenation across
  files. Parquet dictionary-encodes the column, so it costs almost nothing.
- The DB select now reads `_SOURCE_COLUMNS`, which is the archive columns
  with `client_ip` in place of the two new ones. Each row is mapped through
  `hasher` before `RecordBatch.from_pylist`.
- `export_day(..., hasher)` and `run_archive(..., hasher)` take the hasher.
  Writer settings move to one `_archive_writer(dest)` helper, shared with
  the rewrite script, so the two writers can't drift (the #321 contract).
- **Refuses to run without the key.** `main()` calls `load_hasher` next to
  `_get_config`, before the engine is created, and exits 1 on
  `KeyConfigError`. This applies to every mode, `--skip-upload` included,
  so there is no path that falls back to raw.
- Logging: logs the key ID once at INFO. Never logs the key, an IP, or an
  HMAC.

### 3. Rewrite script: `infra/rehash_archive_ips.py` (new)

- Dry run by default; `--apply` writes. It uses the same env: bucket,
  prefix and key.
- For each blob under the prefix: download it to a temp file and sort its
  schema into one of three cases.
  - **legacy**, equal to `LEGACY_ARCHIVE_SCHEMA` (pinned in the script):
    rewrite it.
  - **current**, equal to `ARCHIVE_SCHEMA`: skip it, so a rerun is
    idempotent.
  - **anything else**: abort, before any further write.
- **Rewrite** = `rehash_table(table, hasher)`, a pure function that swaps
  `client_ip` for the two columns and leaves every other column untouched.
  It is written with `_archive_writer`, then verified locally:
  - the schema equals `ARCHIVE_SCHEMA`
  - the row count and the `id` column are unchanged
  - there is no `client_ip` column

  It uploads with `if_generation_match=<original generation>`, then
  downloads the new blob again and re-checks its schema and row count.
- Logs only the blob name, row count and outcome; ends with a summary of
  counts.
- Memory: files average 59 KB, and the script handles one file at a time.
- It is one-shot (raw → hashed). Rotating a key cannot re-hash old files,
  because the raw IP is gone; see §5.

### 4. `X-Forwarded-For` (`middleware/audit.py`)

`_get_client_ip` takes the **rightmost** entry of the last
`X-Forwarded-For` header, which is the address exe.dev saw. An empty entry
falls back to `scope["client"]`, and then to `unknown`. If the proxy
replaced the header instead, the rightmost entry is still the right one.
The function had no tests until now. This changes the live table's values
from the deploy onwards. Rows stored before it keep the leftmost value,
and so do their hashes; the cut-over date goes in the docs. Not a
parse/standardize change, so no `PIPELINE_CODE_VERSION` bump.

### 5. Keys: rotation, shredding, lookup

These procedures go in the `audit_ip_hmac.py` docstring. The other docs
point to it.

- **Backup.** The operator keeps a copy of each key, under its key ID,
  outside the VM (a password manager). If the key is lost, so is every
  lookup, which amounts to accidental shredding.
- **Rotate.** Put a new key and a new ID in `.env`; the next nightly file
  uses them. A caller hashes differently under each key, so linking rows
  across the boundary needs both keys.
- **Shred.** Destroy every copy of a key ID. Its files can no longer be
  linked to an IP. They remain pseudonymous activity records (timestamps,
  endpoints, `request_id`).
- **Look up a known IP.** Run `audit_ip_hmac.py` to get the HMAC for each
  key ID still held. Then `gcloud storage cp -r gs://…/audit ./audit`, and
  filter `client_ip_hmac` in DuckDB or pyarrow.

## Tests

- `client_ip ∉ ARCHIVE_SCHEMA`; the full `ARCHIVE_SCHEMA.names` list is
  pinned.
- Export: the HMAC matches an independently computed
  `hmac.new(...).hexdigest()`, the key ID is on every row, and equal IPs
  give equal hashes.
- Missing, short or non-hex key, or a missing or bad key ID:
  `load_hasher` raises, `main()` exits 1 before any DB or GCS work, and
  the key value appears in no message.
- **No raw IP or key in the logs.** Run `run_archive` with caplog at DEBUG
  and assert that no seeded IP or key text appears.
- **Mixed generation (new; #321's was a manual check, not a test).**
  - A legacy-schema file, rewritten, passes the same file-level contract
    as `test_export_day_parquet_contract`: format 2.6, `ARROW:schema`, UTC
    µs timestamps, SNAPPY.
  - It reads together with a freshly exported file as one dataset.
  - An unknown schema aborts.
  - The existing `test_export_day_parquet_contract` keeps passing,
    unchanged.
- `_get_client_ip`:
  - no header → the client address
  - one entry → that entry
  - `fake, real` → `real`
  - empty → the client address
  - several header lines → the last line's last entry
  - no client → `unknown`

## Docs

- `archive_audit.py` docstring: scheme and env vars.
- `SENSITIVE-AREAS-INFRA.md`: the `archive_audit.py` row, plus rows for
  the two new scripts.
- `SENSITIVE-AREAS.md`: XFF in the `middleware/audit.py` row.
- `VALIDATION-PROVIDERS.md` env table: the two key variables.
- `DEPLOYMENT.md` maintenance block: the rewrite and lookup commands.
- `CHANGELOG.md`.

## Deploy (outside the quiet ranges 03:00–06:15, 07:25–08:15, 13:25–14:15)

1. **The operator adds the key first.** Put `AUDIT_IP_HMAC_KEY` (from
   `openssl rand -hex 32`) and `AUDIT_IP_HMAC_KEY_ID` (e.g. `2026-10`) in
   `/etc/address-validator/.env`, and back the key up off the VM. I check
   the names only (`grep -c '^AUDIT_IP_HMAC_KEY='`) and run
   `audit_ip_hmac.py --check`. If the code is merged without the key, the
   03:00 run exits 1: the unit-failure alert fires, and no rows are lost.
2. Operator approval → merge → `git pull` in the main checkout. No
   dependency change. `sudo systemctl restart address-validator` for the
   XFF change. `/api/v2/health` returns 200, and `install-units.sh --check`
   is clean (no unit changes).
3. Rewrite the bucket: a dry run first, which should show 141 legacy files,
   then `--apply`, then a second dry run, which should show 141 current.
   Run it under `systemd-run` with the unit's memory caps, before 03:00.
4. **Verify on the 03:00 run:** `Result=success` and "Uploaded and
   verified". The new `audit-2026-07-26` blob has the two new columns and
   no `client_ip`. Then 06:00 `archive-age-check`: no breach.
5. Record when the soft-deleted raw originals become unrecoverable (the
   rewrite date + 7 days).

## Alternatives considered

- **Leave the old files raw and record the cut-over date.** Rejected: 43
  IPs would stay raw with no expiry, and a key destroyed later would
  shred only part of the archive.
- **Key ID in Parquet file metadata instead of a column.** Rejected: it is
  lost when files are concatenated, and the issue asks for a column.
- **Rewrite as an `archive_audit.py --rehash` flag.** Rejected: the nightly
  script would gain a bucket-wide overwrite path it never needs.
- **Drop crawler rows.** Rejected by the operator: hashing already covers
  them, and dropping them would make the archive disagree with the rollup.

## Out of scope

- The GCS expiry rule (new issue). The live `audit_log` keeps raw IPs for
  90 days (the dashboard needs them). The hosting-egress caller's identity.
