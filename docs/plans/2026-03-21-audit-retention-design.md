# Audit Log Retention Policy — Design

## Problem

`audit_log` grows indefinitely (~30K rows/day, ~650 MB/year). No retention,
archival, or pre-aggregation exists. Goals in priority order:

1. **Disk/cost discipline** — bounded hot table, unbounded cold archive
2. **Query performance** — keep hot table small, indexes lean
3. **Data durability** — full-fidelity archive survives VM loss

## Design

### Pre-aggregation table: `audit_daily_stats`

New Alembic migration. One row per unique (date, endpoint, provider,
status_code, cache_hit) combination.

| Column | Type |
|---|---|
| `id` | `BIGSERIAL PRIMARY KEY` |
| `date` | `DATE NOT NULL` |
| `endpoint` | `TEXT NOT NULL` |
| `provider` | `TEXT` |
| `status_code` | `SMALLINT NOT NULL` |
| `cache_hit` | `BOOLEAN` |
| `request_count` | `INTEGER NOT NULL` |
| `error_count` | `INTEGER NOT NULL` |
| `avg_latency_ms` | `INTEGER` |
| `p95_latency_ms` | `INTEGER` |

- Unique constraint: `(date, endpoint, provider, status_code, cache_hit)`
- Index: `(date DESC)`
- ~365 rows/year at current cardinality; negligible storage

### Dashboard query updates

`queries.py` "all-time" aggregations UNION live `audit_log` (last 90 days)
with `audit_daily_stats` (older). Time-windowed queries (24h, 7d, 30d)
remain unchanged — they only hit `audit_log`.

### Archive script: `scripts/archive_audit.py`

Daily systemd timer. Steps in order:

1. **Aggregate** — INSERT into `audit_daily_stats` from `audit_log` WHERE
   `timestamp < now() - AUDIT_RETENTION_DAYS`, grouped by dimensions.
   `ON CONFLICT DO NOTHING` for idempotency.
2. **Export** — SELECT archived rows, convert to Parquet via `pyarrow`,
   write to local temp file.
3. **Upload** — Push to
   `gs://<bucket>/<prefix>year=YYYY/month=MM/audit-YYYY-MM-DD.parquet`
   using `google-cloud-storage` SDK (ADC credentials already on VM).
4. **Verify** — Confirm GCS object exists and row count matches.
5. **Delete** — DELETE from `audit_log` WHERE
   `timestamp < now() - AUDIT_RETENTION_DAYS` in batches (10K rows per
   transaction to avoid long locks).
6. **VACUUM** — Run `VACUUM ANALYZE audit_log` after deletion.

Failure at any step logs error and exits — no partial deletes. Re-runnable
safely: idempotent aggregation, date-partitioned Parquet overwrites.

### Backfill

On first run (or via `--backfill` flag), aggregate ALL existing `audit_log`
rows into `audit_daily_stats`, not just those past the retention window.
This populates historical rollups for dashboard "all-time" stats.

### Systemd timer

`/etc/systemd/system/audit-archive.timer` + `.service` — daily at 03:00 UTC.
Logs to journalctl.

### New dependencies

- `pyarrow` — Parquet writing
- `google-cloud-storage` — GCS upload (already have `google-cloud-*` deps)

### New env vars

| Variable | Default | Purpose |
|---|---|---|
| `AUDIT_RETENTION_DAYS` | `90` | Hot window before archival |
| `AUDIT_ARCHIVE_BUCKET` | — (required) | GCS bucket name |
| `AUDIT_ARCHIVE_PREFIX` | `audit/` | Key prefix in bucket |

### Parquet partitioning

```
gs://<bucket>/<prefix>year=YYYY/month=MM/audit-YYYY-MM-DD.parquet
```

Hive-style partitioning. Queryable via BigQuery external tables, DuckDB,
pandas, or polars. Schema embedded in file (self-describing).

### Test strategy

- Unit: aggregation SQL correctness + idempotency
- Unit: Parquet export schema, row count, partitioning
- Integration: full cycle (aggregate → export → upload mock → delete → verify)
- GCS upload mocked in CI; real bucket in manual smoke test

### Estimated lifecycle

| Location | Rows | Size | Retention |
|---|---|---|---|
| `audit_log` (hot) | ~2.7M | ~160 MB | Rolling 90 days |
| `audit_daily_stats` | ~365/year | < 1 MB | Indefinite |
| GCS Parquet (cold) | ~10.9M/year | ~40–80 MB/year | Indefinite |

### Out of scope

- Querying cold storage from dashboard (use DuckDB/BigQuery ad-hoc)
- Hourly rollups (re-aggregate from Parquet if needed later)
