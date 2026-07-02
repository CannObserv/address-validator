# Deployment

## Common operations

```bash
# Restart after code change
sudo systemctl restart address-validator

# Tail logs
journalctl -u address-validator -f

# Re-install systemd unit after infra/address-validator.service changes
sudo cp infra/address-validator.service /etc/systemd/system/ && sudo systemctl daemon-reload

# Install/enable libpostal sidecar
sudo cp infra/libpostal.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now libpostal

# Install pre-commit hooks (ruff + Tailwind CSS build)
uv run pre-commit install
```

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
```

Docker prune does **not** use `-a` (active images are safe). Logs a journal warning if disk ≥ 85% after prune:

```bash
journalctl -t docker-prune -p warning
```

The cache sweep deletes `validated_addresses` rows (and their `query_patterns` pointers) older than `VALIDATION_CACHE_TTL_DAYS`. Dry-run any time with `PYTHONPATH=src uv run python infra/sweep_cache.py --dry-run` (`PYTHONPATH=src` is required — the package is not installed editable). Logs swept counts to the journal:

```bash
journalctl -u cache-sweep -p info
```

## Database maintenance scripts

**NEVER** source `/etc/address-validator/.env` before `uv run pytest`. That file sets `VALIDATION_CACHE_DSN` to the production database; the audit middleware writes real rows on every `TestClient` request. For one-off scripts only:

```bash
source /etc/address-validator/.env
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
