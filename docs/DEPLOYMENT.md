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
```

Docker prune does **not** use `-a` (active images are safe). Logs a journal warning if disk ≥ 85% after prune:

```bash
journalctl -t docker-prune -p warning
```

## Database maintenance scripts

**NEVER** source `/etc/address-validator/.env` before `uv run pytest`. For one-off scripts only:

```bash
source /etc/address-validator/.env

# Backfill audit_log rows missing structured fields
uv run python scripts/db/backfill_audit_log.py

# Backfill pattern_key column (dry-run by default; add --apply)
uv run python scripts/db/backfill_pattern_key.py

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
