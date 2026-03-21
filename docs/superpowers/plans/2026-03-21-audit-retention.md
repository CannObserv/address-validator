# Audit Log Retention Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement automated audit log lifecycle: 90-day hot window in PostgreSQL, daily pre-aggregated rollups, and Parquet-based cold storage in GCS.

**Architecture:** New `audit_daily_stats` table stores pre-aggregated counts. A standalone `scripts/archive_audit.py` script (run daily via systemd timer) aggregates rows older than 90 days into rollups, exports them as Parquet files, uploads to GCS, then deletes from the hot table. Dashboard "all-time" queries UNION hot + rollup tables.

**Tech Stack:** Python, SQLAlchemy (raw SQL), Alembic, pyarrow, google-cloud-storage, systemd timers, pytest

---

## File Structure

| Action | File | Responsibility |
|--------|------|----------------|
| Create | `alembic/versions/005_audit_daily_stats.py` | Migration: `audit_daily_stats` table |
| Create | `scripts/archive_audit.py` | Archive script: aggregate → export → upload → delete |
| Create | `tests/unit/test_archive_audit.py` | Tests for archive script logic |
| Create | `audit-archive.service` | Systemd service unit for archive job |
| Create | `audit-archive.timer` | Systemd timer unit (daily 03:00 UTC) |
| Modify | `src/address_validator/routers/admin/queries.py` | UNION `audit_daily_stats` into all-time queries |
| Modify | `tests/unit/test_admin_queries.py` | Tests for UNION'd all-time queries |
| Modify | `tests/unit/validation/conftest.py` | Add `audit_daily_stats` to TRUNCATE list |
| Modify | `pyproject.toml` | Add `pyarrow`, `google-cloud-storage` deps |

---

### Task 1: Add Dependencies

**Files:**
- Modify: `pyproject.toml:6-21`

- [ ] **Step 1: Add pyarrow and google-cloud-storage to dependencies**

In `pyproject.toml`, add to the `dependencies` list:

```toml
    "pyarrow>=19.0,<20",
    "google-cloud-storage>=3.1,<4",
```

- [ ] **Step 2: Install dependencies**

Run: `uv sync`
Expected: Resolves and installs both packages with no conflicts.

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "#49 chore: add pyarrow and google-cloud-storage deps"
```

---

### Task 2: Alembic Migration — `audit_daily_stats` Table

**Files:**
- Create: `alembic/versions/005_audit_daily_stats.py`

- [ ] **Step 1: Write the migration**

Create `alembic/versions/005_audit_daily_stats.py`:

```python
"""Add audit_daily_stats table for pre-aggregated audit rollups.

Revision ID: 005
Revises: 004
Create Date: 2026-03-21
"""

revision: str = "005"
down_revision: str = "004"
branch_labels = None
depends_on = None

import sqlalchemy as sa  # noqa: E402

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.create_table(
        "audit_daily_stats",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("status_code", sa.SmallInteger(), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=True),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("avg_latency_ms", sa.Integer(), nullable=True),
        sa.Column("p95_latency_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # Unique index with COALESCE handles NULLs — PostgreSQL unique constraints
    # treat NULLs as distinct, so ON CONFLICT wouldn't catch duplicates without this.
    op.create_index(
        "uq_daily_stats_dimensions",
        "audit_daily_stats",
        [
            "date", "endpoint",
            sa.text("COALESCE(provider, '')"),
            "status_code",
            sa.text("COALESCE(cache_hit, false)"),
        ],
        unique=True,
    )
    op.create_index(
        "idx_daily_stats_date", "audit_daily_stats", [sa.text("date DESC")]
    )


def downgrade() -> None:
    op.drop_index("idx_daily_stats_date", table_name="audit_daily_stats")
    op.drop_index("uq_daily_stats_dimensions", table_name="audit_daily_stats")
    op.drop_table("audit_daily_stats")
```

- [ ] **Step 2: Update test conftest to truncate new table**

In `tests/unit/validation/conftest.py:34-35`, change the TRUNCATE statement:

Old:
```python
            text("TRUNCATE validated_addresses, query_patterns, audit_log RESTART IDENTITY CASCADE")
```

New:
```python
            text("TRUNCATE validated_addresses, query_patterns, audit_log, audit_daily_stats RESTART IDENTITY CASCADE")
```

- [ ] **Step 3: Run migrations against test DB to verify**

Run: `uv run pytest tests/unit/test_admin_queries.py -x --no-cov -v`
Expected: All existing tests pass (migration applied automatically via session fixture).

- [ ] **Step 4: Commit**

```bash
git add alembic/versions/005_audit_daily_stats.py tests/unit/validation/conftest.py
git commit -m "#49 feat: add audit_daily_stats migration"
```

---

### Task 3: Archive Script — Core Logic

**Files:**
- Create: `scripts/archive_audit.py`
- Create: `tests/unit/test_archive_audit.py`

The script has 4 logical phases: aggregate, export, upload, delete. We build and test each phase. The script uses its own async engine (same pattern as `scripts/backfill_audit_log.py`).

- [ ] **Step 1: Write failing tests for aggregation**

Create `tests/unit/test_archive_audit.py`:

```python
"""Tests for audit log archive script."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def _seed_old_and_new_rows(engine: AsyncEngine) -> None:
    """Insert audit rows: 3 old (100 days ago) and 2 recent (today)."""
    old = datetime.now(UTC) - timedelta(days=100)
    now = datetime.now(UTC)
    rows = [
        # Old rows — should be aggregated and archived
        {"ts": old, "ip": "1.1.1.1", "method": "POST", "ep": "/api/v1/validate",
         "status": 200, "provider": "usps", "vs": "confirmed", "cache": True, "latency": 42},
        {"ts": old, "ip": "1.1.1.1", "method": "POST", "ep": "/api/v1/validate",
         "status": 200, "provider": "usps", "vs": "confirmed", "cache": False, "latency": 88},
        {"ts": old, "ip": "2.2.2.2", "method": "POST", "ep": "/api/v1/parse",
         "status": 400, "provider": None, "vs": None, "cache": None, "latency": 5},
        # Recent rows — should remain untouched
        {"ts": now, "ip": "3.3.3.3", "method": "POST", "ep": "/api/v1/validate",
         "status": 200, "provider": "usps", "vs": "confirmed", "cache": True, "latency": 30},
        {"ts": now, "ip": "3.3.3.3", "method": "POST", "ep": "/api/v1/parse",
         "status": 200, "provider": None, "vs": None, "cache": None, "latency": 10},
    ]
    async with engine.begin() as conn:
        for r in rows:
            await conn.execute(
                text("""
                    INSERT INTO audit_log (timestamp, client_ip, method, endpoint,
                        status_code, provider, validation_status, cache_hit, latency_ms)
                    VALUES (:ts, :ip, :method, :ep, :status, :provider, :vs, :cache, :latency)
                """),
                r,
            )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_archive_audit.py -x --no-cov -v`
Expected: File imports but no test functions collected (yet). No errors from seed helper.

- [ ] **Step 3: Write the aggregation function**

Create `scripts/archive_audit.py` with the aggregation logic:

```python
#!/usr/bin/env python3
"""Archive audit_log rows older than the retention window.

Steps: aggregate → export Parquet → upload GCS → delete old rows → VACUUM.

Usage:
    uv run python scripts/archive_audit.py               # archive expired rows
    uv run python scripts/archive_audit.py --backfill     # aggregate ALL rows first

Env vars:
    VALIDATION_CACHE_DSN    PostgreSQL DSN (required)
    AUDIT_RETENTION_DAYS    Hot window in days (default: 90)
    AUDIT_ARCHIVE_BUCKET    GCS bucket name (required for upload)
    AUDIT_ARCHIVE_PREFIX    GCS key prefix (default: "audit/")
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

_AGGREGATE_SQL = text("""
    INSERT INTO audit_daily_stats
        (date, endpoint, provider, status_code, cache_hit,
         request_count, error_count, avg_latency_ms, p95_latency_ms)
    SELECT
        date_trunc('day', timestamp)::date AS date,
        endpoint,
        provider,
        status_code,
        cache_hit,
        COUNT(*) AS request_count,
        COUNT(*) FILTER (WHERE status_code >= 400) AS error_count,
        AVG(latency_ms)::integer AS avg_latency_ms,
        PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)::integer AS p95_latency_ms
    FROM audit_log
    WHERE timestamp < :cutoff
    GROUP BY date, endpoint, provider, status_code, cache_hit
    ON CONFLICT (date, endpoint, COALESCE(provider, ''), status_code, COALESCE(cache_hit, false))
    DO NOTHING
""")

_AGGREGATE_ALL_SQL = text("""
    INSERT INTO audit_daily_stats
        (date, endpoint, provider, status_code, cache_hit,
         request_count, error_count, avg_latency_ms, p95_latency_ms)
    SELECT
        date_trunc('day', timestamp)::date AS date,
        endpoint,
        provider,
        status_code,
        cache_hit,
        COUNT(*) AS request_count,
        COUNT(*) FILTER (WHERE status_code >= 400) AS error_count,
        AVG(latency_ms)::integer AS avg_latency_ms,
        PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY latency_ms)::integer AS p95_latency_ms
    FROM audit_log
    GROUP BY date, endpoint, provider, status_code, cache_hit
    ON CONFLICT (date, endpoint, COALESCE(provider, ''), status_code, COALESCE(cache_hit, false))
    DO NOTHING
""")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive old audit_log rows.")
    parser.add_argument(
        "--backfill", action="store_true",
        help="Aggregate ALL existing rows (not just expired ones).",
    )
    parser.add_argument(
        "--skip-upload", action="store_true",
        help="Skip GCS upload (aggregate + delete only).",
    )
    return parser.parse_args()


def _get_config() -> tuple[str, int, str | None, str]:
    """Read and validate env vars. Returns (dsn, retention_days, bucket, prefix)."""
    dsn = os.environ.get("VALIDATION_CACHE_DSN", "").strip()
    if not dsn:
        logger.error("VALIDATION_CACHE_DSN not set")
        sys.exit(1)
    retention_days = int(os.environ.get("AUDIT_RETENTION_DAYS", "90"))
    bucket = os.environ.get("AUDIT_ARCHIVE_BUCKET", "").strip() or None
    prefix = os.environ.get("AUDIT_ARCHIVE_PREFIX", "audit/").strip()
    return dsn, retention_days, bucket, prefix


async def aggregate(engine: AsyncEngine, *, cutoff: datetime | None = None) -> int:
    """Aggregate audit_log rows into audit_daily_stats.

    If cutoff is None, aggregates ALL rows (backfill mode).
    Returns number of rows inserted.
    """
    if cutoff is None:
        sql = _AGGREGATE_ALL_SQL
        params: dict = {}
    else:
        sql = _AGGREGATE_SQL
        params = {"cutoff": cutoff}

    async with engine.begin() as conn:
        result = await conn.execute(sql, params)
        return result.rowcount


async def fetch_expired_rows(engine: AsyncEngine, cutoff: datetime) -> list[dict]:
    """Fetch all audit_log rows older than cutoff as dicts."""
    async with engine.connect() as conn:
        result = await conn.execute(
            text("""
                SELECT id, timestamp, request_id, client_ip, method, endpoint,
                       status_code, latency_ms, provider, validation_status,
                       cache_hit, error_detail
                FROM audit_log
                WHERE timestamp < :cutoff
                ORDER BY timestamp
            """),
            {"cutoff": cutoff},
        )
        return [dict(r._mapping) for r in result]


def export_parquet(rows: list[dict], dest: Path) -> int:
    """Write rows to a Parquet file. Returns row count written."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    if not rows:
        return 0

    table = pa.Table.from_pylist(rows)
    pq.write_table(table, dest, compression="snappy")
    return len(rows)


def group_rows_by_date(rows: list[dict]) -> dict[str, list[dict]]:
    """Group rows by date string (YYYY-MM-DD) for per-day Parquet files."""
    groups: dict[str, list[dict]] = {}
    for row in rows:
        ts = row["timestamp"]
        day_key = ts.strftime("%Y-%m-%d")
        groups.setdefault(day_key, []).append(row)
    return groups


def upload_to_gcs(local_path: Path, bucket_name: str, blob_name: str) -> None:
    """Upload a local file to GCS."""
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(str(local_path))
    logger.info("Uploaded gs://%s/%s", bucket_name, blob_name)


def verify_gcs_upload(bucket_name: str, blob_name: str) -> bool:
    """Verify a GCS object exists and has non-zero size."""
    from google.cloud import storage

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.reload()
    if blob.size is None or blob.size == 0:
        return False
    return True


async def delete_expired_rows(
    engine: AsyncEngine, cutoff: datetime, *, batch_size: int = 10_000,
) -> int:
    """Delete audit_log rows older than cutoff in batches. Returns total deleted."""
    total_deleted = 0
    while True:
        async with engine.begin() as conn:
            result = await conn.execute(
                text("""
                    DELETE FROM audit_log
                    WHERE id IN (
                        SELECT id FROM audit_log
                        WHERE timestamp < :cutoff
                        LIMIT :batch_size
                    )
                """),
                {"cutoff": cutoff, "batch_size": batch_size},
            )
            deleted = result.rowcount
            total_deleted += deleted
            if deleted < batch_size:
                break
            logger.info("Deleted %d rows so far...", total_deleted)
    return total_deleted


async def vacuum_audit_log(engine: AsyncEngine) -> None:
    """Run VACUUM ANALYZE on audit_log (requires autocommit)."""
    raw_conn = await engine.raw_connection()
    try:
        await raw_conn.driver_connection.execute("VACUUM ANALYZE audit_log")
    finally:
        await raw_conn.close()


async def main() -> None:
    args = _parse_args()
    dsn, retention_days, bucket, prefix = _get_config()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    engine = create_async_engine(dsn)
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)

    try:
        # Step 1: Aggregate
        if args.backfill:
            logger.info("Backfill mode: aggregating ALL rows into audit_daily_stats...")
            inserted = await aggregate(engine)
        else:
            logger.info("Aggregating rows older than %s...", cutoff.date())
            inserted = await aggregate(engine, cutoff=cutoff)
        logger.info("Aggregated %d rollup rows.", inserted)

        # Step 2: Export expired rows to Parquet
        rows = await fetch_expired_rows(engine, cutoff)
        if not rows:
            logger.info("No expired rows to archive. Done.")
            return

        logger.info("Exporting %d expired rows to Parquet...", len(rows))
        day_groups = group_rows_by_date(rows)

        with tempfile.TemporaryDirectory() as tmpdir:
            parquet_files: list[tuple[Path, str]] = []
            for day_key, day_rows in sorted(day_groups.items()):
                year, month, _day = day_key.split("-")
                filename = f"audit-{day_key}.parquet"
                local_path = Path(tmpdir) / filename
                export_parquet(day_rows, local_path)
                blob_name = f"{prefix}year={year}/month={month}/{filename}"
                parquet_files.append((local_path, blob_name))

            # Step 3: Upload to GCS
            if bucket and not args.skip_upload:
                for local_path, blob_name in parquet_files:
                    upload_to_gcs(local_path, bucket, blob_name)

                # Step 4: Verify
                for _local_path, blob_name in parquet_files:
                    if not verify_gcs_upload(bucket, blob_name):
                        logger.error("Verification failed for %s. Aborting.", blob_name)
                        sys.exit(1)
                logger.info("All %d Parquet files verified in GCS.", len(parquet_files))
            elif not bucket:
                logger.warning("AUDIT_ARCHIVE_BUCKET not set — skipping upload.")
            else:
                logger.info("Skipping upload (--skip-upload).")

        # Step 5: Delete expired rows
        deleted = await delete_expired_rows(engine, cutoff)
        logger.info("Deleted %d expired rows from audit_log.", deleted)

        # Step 6: VACUUM
        await vacuum_audit_log(engine)
        logger.info("VACUUM ANALYZE complete.")

    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Write tests for aggregation**

Add tests to `tests/unit/test_archive_audit.py`:

```python
# Add these imports at the top of the file (after existing imports):
import sys
from pathlib import Path

# Add scripts/ to path so we can import the archive module
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from archive_audit import aggregate, delete_expired_rows, export_parquet, fetch_expired_rows


@pytest.mark.asyncio
async def test_aggregate_with_cutoff(db: AsyncEngine) -> None:
    """Aggregation rolls up only rows older than cutoff."""
    await _seed_old_and_new_rows(db)
    cutoff = datetime.now(UTC) - timedelta(days=90)

    inserted = await aggregate(db, cutoff=cutoff)
    assert inserted > 0

    # Verify rollup rows exist
    async with db.connect() as conn:
        stats = (
            await conn.execute(text("SELECT * FROM audit_daily_stats ORDER BY date"))
        ).fetchall()

    # Old rows: 2 validate/200/usps (cache=True and cache=False), 1 parse/400/null
    assert len(stats) == 3

    # Check validate cache=True rollup
    validate_cached = [s for s in stats if s.cache_hit is True]
    assert len(validate_cached) == 1
    assert validate_cached[0].request_count == 1
    assert validate_cached[0].endpoint == "/api/v1/validate"

    # Check parse 400 rollup
    parse_error = [s for s in stats if s.status_code == 400]
    assert len(parse_error) == 1
    assert parse_error[0].error_count == 1


@pytest.mark.asyncio
async def test_aggregate_idempotent(db: AsyncEngine) -> None:
    """Running aggregation twice inserts zero new rows the second time."""
    await _seed_old_and_new_rows(db)
    cutoff = datetime.now(UTC) - timedelta(days=90)

    first = await aggregate(db, cutoff=cutoff)
    assert first > 0

    second = await aggregate(db, cutoff=cutoff)
    assert second == 0


@pytest.mark.asyncio
async def test_aggregate_backfill(db: AsyncEngine) -> None:
    """Backfill mode (no cutoff) aggregates ALL rows."""
    await _seed_old_and_new_rows(db)

    inserted = await aggregate(db)
    # Should aggregate old + new rows across all dimension combos
    assert inserted > 3  # More than just the old rows


@pytest.mark.asyncio
async def test_fetch_expired_rows(db: AsyncEngine) -> None:
    """fetch_expired_rows returns only rows older than cutoff."""
    await _seed_old_and_new_rows(db)
    cutoff = datetime.now(UTC) - timedelta(days=90)

    rows = await fetch_expired_rows(db, cutoff)
    assert len(rows) == 3  # Only the 3 old rows
    assert all(r["timestamp"] < cutoff for r in rows)
```

- [ ] **Step 5: Run tests to verify aggregation works**

Run: `uv run pytest tests/unit/test_archive_audit.py -x --no-cov -v`
Expected: All 4 tests pass.

- [ ] **Step 6: Write tests for Parquet export**

Add to `tests/unit/test_archive_audit.py`:

```python
def test_export_parquet_writes_file(tmp_path) -> None:
    """export_parquet creates a valid Parquet file with correct row count."""
    import pyarrow.parquet as pq

    rows = [
        {"id": 1, "timestamp": datetime.now(UTC), "endpoint": "/api/v1/validate",
         "status_code": 200, "client_ip": "1.1.1.1"},
        {"id": 2, "timestamp": datetime.now(UTC), "endpoint": "/api/v1/parse",
         "status_code": 400, "client_ip": "2.2.2.2"},
    ]
    dest = tmp_path / "test.parquet"
    count = export_parquet(rows, dest)

    assert count == 2
    assert dest.exists()

    # Verify Parquet is readable and has correct schema
    table = pq.read_table(dest)
    assert table.num_rows == 2
    assert "endpoint" in table.column_names
    assert "status_code" in table.column_names


def test_export_parquet_empty_returns_zero(tmp_path) -> None:
    """Empty row list writes nothing and returns 0."""
    dest = tmp_path / "empty.parquet"
    count = export_parquet([], dest)
    assert count == 0
    assert not dest.exists()
```

- [ ] **Step 7: Run Parquet export tests**

Run: `uv run pytest tests/unit/test_archive_audit.py::test_export_parquet_writes_file tests/unit/test_archive_audit.py::test_export_parquet_empty_returns_zero -x --no-cov -v`
Expected: Both tests pass.

- [ ] **Step 8: Write tests for batched deletion**

Add to `tests/unit/test_archive_audit.py`:

```python
@pytest.mark.asyncio
async def test_delete_expired_rows(db: AsyncEngine) -> None:
    """delete_expired_rows removes only rows older than cutoff."""
    await _seed_old_and_new_rows(db)
    cutoff = datetime.now(UTC) - timedelta(days=90)

    deleted = await delete_expired_rows(db, cutoff, batch_size=2)
    assert deleted == 3  # 3 old rows

    # Verify only recent rows remain
    async with db.connect() as conn:
        remaining = (await conn.execute(text("SELECT COUNT(*) FROM audit_log"))).scalar()
    assert remaining == 2


@pytest.mark.asyncio
async def test_delete_no_expired_rows(db: AsyncEngine) -> None:
    """delete_expired_rows returns 0 when nothing to delete."""
    await _seed_old_and_new_rows(db)
    # Cutoff in the past — no rows older than 200 days
    cutoff = datetime.now(UTC) - timedelta(days=200)

    deleted = await delete_expired_rows(db, cutoff)
    assert deleted == 0
```

- [ ] **Step 9: Run deletion tests**

Run: `uv run pytest tests/unit/test_archive_audit.py::test_delete_expired_rows tests/unit/test_archive_audit.py::test_delete_no_expired_rows -x --no-cov -v`
Expected: Both pass.

- [ ] **Step 10: Run full test suite to check for regressions**

Run: `uv run pytest --no-cov -x`
Expected: All tests pass.

- [ ] **Step 11: Commit**

```bash
git add scripts/archive_audit.py tests/unit/test_archive_audit.py
git commit -m "#49 feat: add audit archive script with aggregate, export, and delete"
```

---

### Task 4: Dashboard Query Updates — UNION All-Time Stats

**Files:**
- Modify: `src/address_validator/routers/admin/queries.py:26-114,176-219,222-263`
- Modify: `tests/unit/test_admin_queries.py`

Three functions need UNION logic for their all-time metrics: `get_dashboard_stats`, `get_endpoint_stats`, `get_provider_stats`. Time-windowed queries (24h, 7d, 30d) and sparklines remain unchanged.

- [ ] **Step 1: Write failing test for dashboard stats with archived data**

Add to `tests/unit/test_admin_queries.py`:

```python
async def _seed_stats_rows(engine: AsyncEngine) -> None:
    """Insert audit_daily_stats rows simulating archived data."""
    from datetime import date

    rows = [
        # Archived: 120 days ago — validate/200/usps/cached
        {"d": date(2025, 11, 21), "ep": "/api/v1/validate", "provider": "usps",
         "status": 200, "cache": True, "req_count": 50, "err_count": 0,
         "avg_lat": 45, "p95_lat": 90},
        # Archived: 120 days ago — parse/400/null/null
        {"d": date(2025, 11, 21), "ep": "/api/v1/parse", "provider": None,
         "status": 400, "cache": None, "req_count": 10, "err_count": 10,
         "avg_lat": 5, "p95_lat": 8},
    ]
    async with engine.begin() as conn:
        for r in rows:
            await conn.execute(
                text("""
                    INSERT INTO audit_daily_stats
                        (date, endpoint, provider, status_code, cache_hit,
                         request_count, error_count, avg_latency_ms, p95_latency_ms)
                    VALUES (:d, :ep, :provider, :status, :cache,
                            :req_count, :err_count, :avg_lat, :p95_lat)
                """),
                r,
            )


@pytest.mark.asyncio
async def test_dashboard_stats_includes_archived(db: AsyncEngine) -> None:
    """All-time total includes both live audit_log and archived audit_daily_stats."""
    await _seed_rows(db)          # 6 live rows
    await _seed_stats_rows(db)    # 60 archived requests

    stats = await get_dashboard_stats(db)
    # All-time: 6 live + 60 archived = 66
    assert stats["requests_all"] == 66
    # 24h and week should only count live rows
    assert stats["requests_24h"] == 6

    # Endpoint breakdown all-time should include archived
    bd = stats["endpoint_breakdown"]
    assert bd["all"]["/validate"] == 52  # 2 live + 50 archived
    assert bd["all"]["/parse"] == 12     # 2 live + 10 archived


@pytest.mark.asyncio
async def test_endpoint_stats_includes_archived(db: AsyncEngine) -> None:
    """Per-endpoint all-time stats include archived data."""
    await _seed_rows(db)
    await _seed_stats_rows(db)

    stats = await get_endpoint_stats(db, "validate")
    assert stats["total"] == 52   # 2 live + 50 archived
    assert stats["last_24h"] == 2  # Only live

    # Status codes should include archived
    assert stats["status_codes"][200] == 52


@pytest.mark.asyncio
async def test_provider_stats_includes_archived(db: AsyncEngine) -> None:
    """Per-provider all-time stats include archived data."""
    await _seed_rows(db)
    await _seed_stats_rows(db)

    stats = await get_provider_stats(db, "usps")
    assert stats["total"] == 52   # 2 live + 50 archived
    assert stats["last_24h"] == 2  # Only live
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_admin_queries.py::test_dashboard_stats_includes_archived -x --no-cov -v`
Expected: FAIL — `requests_all` is 6 (doesn't include archived yet).

- [ ] **Step 3: Update `get_dashboard_stats` with UNION logic**

In `src/address_validator/routers/admin/queries.py`, replace the main stats query (lines 38–57) and endpoint breakdown query (lines 73–86):

Replace the entire function body with a two-query approach (live + archived, merged in Python):

```python
async def get_dashboard_stats(engine: AsyncEngine) -> dict:
    """Fetch aggregate stats for the dashboard landing page."""
    tb = _time_boundaries()
    week_start = tb["week"]
    last_24h = tb["last_24h"]

    # SAFETY: endpoint literals are hardcoded, not user-supplied.
    _API_ENDPOINT_FILTER = (
        "endpoint IN ('/api/v1/parse', '/api/v1/standardize', '/api/v1/validate')"
    )

    async with engine.connect() as conn:
        # Live audit_log stats
        row = (
            await conn.execute(
                text(f"""
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE timestamp >= :last_24h) AS last_24h,
                        COUNT(*) FILTER (WHERE timestamp >= :week) AS week,
                        COUNT(*) FILTER (
                            WHERE status_code >= 400 AND timestamp >= :last_24h
                            AND {_API_ENDPOINT_FILTER}
                        ) AS errors_24h,
                        COUNT(*) FILTER (
                            WHERE timestamp >= :last_24h
                            AND {_API_ENDPOINT_FILTER}
                        ) AS api_24h
                    FROM audit_log
                """),  # noqa: S608
                {"last_24h": last_24h, "week": week_start},
            )
        ).one()

        # Archived totals — only count dates before the earliest live row
        # to avoid double-counting when --backfill has run.
        archived_total = (
            await conn.execute(
                text("""
                    SELECT COALESCE(SUM(request_count), 0) FROM audit_daily_stats
                    WHERE date < (SELECT COALESCE(MIN(timestamp)::date, CURRENT_DATE)
                                  FROM audit_log)
                """)
            )
        ).scalar()

        cache_row = (
            await conn.execute(
                text("""
                    SELECT
                        COUNT(*) FILTER (WHERE cache_hit = true) AS hits,
                        COUNT(*) FILTER (WHERE cache_hit IS NOT NULL) AS total
                    FROM audit_log
                    WHERE endpoint = '/api/v1/validate'
                        AND timestamp >= :week
                """),
                {"week": week_start},
            )
        ).one()

        # Live endpoint breakdown
        ep_rows = (
            await conn.execute(
                text("""
                    SELECT
                        endpoint,
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE timestamp >= :last_24h) AS last_24h,
                        COUNT(*) FILTER (WHERE timestamp >= :week) AS week
                    FROM audit_log
                    GROUP BY endpoint
                """),
                {"last_24h": last_24h, "week": week_start},
            )
        ).fetchall()

        # Archived endpoint breakdown — only dates before earliest live row
        archived_ep_rows = (
            await conn.execute(
                text("""
                    SELECT endpoint, SUM(request_count) AS total
                    FROM audit_daily_stats
                    WHERE date < (SELECT COALESCE(MIN(timestamp)::date, CURRENT_DATE)
                                  FROM audit_log)
                    GROUP BY endpoint
                """)
            )
        ).fetchall()

    error_rate = (row.errors_24h / row.api_24h * 100) if row.api_24h > 0 else None
    cache_hit_rate = (cache_row.hits / cache_row.total * 100) if cache_row.total > 0 else None

    known = {
        "/api/v1/parse": "/parse",
        "/api/v1/standardize": "/standardize",
        "/api/v1/validate": "/validate",
    }
    breakdown: dict[str, dict[str, int]] = {
        "all": {},
        "week": {},
        "24h": {},
    }

    # Live breakdown
    for ep_row in ep_rows:
        label = known.get(ep_row.endpoint, "other")
        periods = (("all", "total"), ("week", "week"), ("24h", "last_24h"))
        for period, col in periods:
            breakdown[period][label] = breakdown[period].get(label, 0) + ep_row._mapping[col]  # noqa: SLF001

    # Add archived totals to "all" period
    for ar_row in archived_ep_rows:
        label = known.get(ar_row.endpoint, "other")
        breakdown["all"][label] = breakdown["all"].get(label, 0) + ar_row.total

    return {
        "requests_24h": row.last_24h,
        "requests_week": row.week,
        "requests_all": row.total + archived_total,
        "error_rate": error_rate,
        "cache_hit_rate": cache_hit_rate,
        "endpoint_breakdown": breakdown,
    }
```

- [ ] **Step 4: Run dashboard stats tests**

Run: `uv run pytest tests/unit/test_admin_queries.py::test_dashboard_stats_includes_archived tests/unit/test_admin_queries.py::test_get_dashboard_stats -x --no-cov -v`
Expected: Both pass.

- [ ] **Step 5: Update `get_endpoint_stats` with archived data**

Replace `get_endpoint_stats` in `queries.py`:

```python
async def get_endpoint_stats(engine: AsyncEngine, endpoint_name: str) -> dict:
    """Fetch stats for a specific endpoint."""
    endpoint_path = f"/api/v1/{endpoint_name}"
    tb = _time_boundaries()

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("""
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE timestamp >= :last_24h) AS last_24h,
                        COUNT(*) FILTER (WHERE timestamp >= :week) AS week,
                        COUNT(*) FILTER (WHERE status_code >= 400) AS errors,
                        AVG(latency_ms) FILTER (WHERE latency_ms IS NOT NULL) AS avg_latency
                    FROM audit_log
                    WHERE endpoint = :endpoint
                """),
                {"last_24h": tb["last_24h"], "week": tb["week"], "endpoint": endpoint_path},
            )
        ).one()

        # Archived totals for this endpoint — only dates before earliest live row
        archived = (
            await conn.execute(
                text("""
                    SELECT
                        COALESCE(SUM(request_count), 0) AS total,
                        COALESCE(SUM(error_count), 0) AS errors
                    FROM audit_daily_stats
                    WHERE endpoint = :endpoint
                        AND date < (SELECT COALESCE(MIN(timestamp)::date, CURRENT_DATE)
                                    FROM audit_log)
                """),
                {"endpoint": endpoint_path},
            )
        ).one()

        # Live + archived status code distribution
        status_rows = (
            await conn.execute(
                text("""
                    SELECT status_code, SUM(cnt) AS count FROM (
                        SELECT status_code, COUNT(*) AS cnt
                        FROM audit_log
                        WHERE endpoint = :endpoint
                        GROUP BY status_code
                        UNION ALL
                        SELECT status_code, SUM(request_count) AS cnt
                        FROM audit_daily_stats
                        WHERE endpoint = :endpoint
                            AND date < (SELECT COALESCE(MIN(timestamp)::date, CURRENT_DATE)
                                        FROM audit_log)
                        GROUP BY status_code
                    ) AS combined
                    GROUP BY status_code
                    ORDER BY status_code
                """),
                {"endpoint": endpoint_path},
            )
        ).fetchall()

    total = row.total + archived.total
    errors = row.errors + archived.errors
    error_rate = (errors / total * 100) if total > 0 else None
    return {
        "total": total,
        "last_24h": row.last_24h,
        "week": row.week,
        "error_rate": error_rate,
        "avg_latency_ms": round(row.avg_latency) if row.avg_latency else None,
        "status_codes": {r.status_code: r.count for r in status_rows},
    }
```

- [ ] **Step 6: Run endpoint stats tests**

Run: `uv run pytest tests/unit/test_admin_queries.py::test_endpoint_stats_includes_archived tests/unit/test_admin_queries.py::test_get_endpoint_stats -x --no-cov -v`
Expected: Both pass.

- [ ] **Step 7: Update `get_provider_stats` with archived data**

Replace `get_provider_stats` in `queries.py`:

```python
async def get_provider_stats(engine: AsyncEngine, provider_name: str) -> dict:
    """Fetch stats for a specific validation provider."""
    tb = _time_boundaries()

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("""
                    SELECT
                        COUNT(*) AS total,
                        COUNT(*) FILTER (WHERE timestamp >= :last_24h) AS last_24h,
                        COUNT(*) FILTER (WHERE cache_hit = true
                            AND timestamp >= :week) AS cache_hits,
                        COUNT(*) FILTER (WHERE cache_hit IS NOT NULL
                            AND timestamp >= :week) AS cache_total
                    FROM audit_log
                    WHERE provider = :provider
                """),
                {"last_24h": tb["last_24h"], "week": tb["week"], "provider": provider_name},
            )
        ).one()

        # Archived total for this provider — only dates before earliest live row
        archived_total = (
            await conn.execute(
                text("""
                    SELECT COALESCE(SUM(request_count), 0)
                    FROM audit_daily_stats
                    WHERE provider = :provider
                        AND date < (SELECT COALESCE(MIN(timestamp)::date, CURRENT_DATE)
                                    FROM audit_log)
                """),
                {"provider": provider_name},
            )
        ).scalar()

        # Live + archived validation status distribution
        status_rows = (
            await conn.execute(
                text("""
                    SELECT validation_status, COUNT(*) AS count
                    FROM audit_log
                    WHERE provider = :provider AND validation_status IS NOT NULL
                    GROUP BY validation_status
                    ORDER BY count DESC
                """),
                {"provider": provider_name},
            )
        ).fetchall()

    cache_hit_rate = (row.cache_hits / row.cache_total * 100) if row.cache_total > 0 else None
    return {
        "total": row.total + archived_total,
        "last_24h": row.last_24h,
        "cache_hit_rate": cache_hit_rate,
        "validation_statuses": {r.validation_status: r.count for r in status_rows},
    }
```

**Note:** `validation_status` is not stored in `audit_daily_stats` (it's a per-row field, not a dimension we aggregate by). The live `audit_log` gives us recent status distribution; archived distribution is lost. This is an acceptable trade-off per the design — if finer historical breakdowns are needed, query the Parquet files.

- [ ] **Step 8: Run provider stats tests**

Run: `uv run pytest tests/unit/test_admin_queries.py::test_provider_stats_includes_archived tests/unit/test_admin_queries.py::test_get_provider_stats -x --no-cov -v`
Expected: Both pass.

- [ ] **Step 9: Run full test suite**

Run: `uv run pytest --no-cov -x`
Expected: All tests pass.

- [ ] **Step 10: Lint check**

Run: `uv run ruff check .`
Expected: Clean.

- [ ] **Step 11: Commit**

```bash
git add src/address_validator/routers/admin/queries.py tests/unit/test_admin_queries.py
git commit -m "#49 feat: UNION audit_daily_stats into all-time dashboard queries"
```

---

### Task 5: Systemd Timer and Service Units

**Files:**
- Create: `audit-archive.service`
- Create: `audit-archive.timer`

- [ ] **Step 1: Create the service unit**

Create `audit-archive.service`:

```ini
[Unit]
Description=Archive expired audit_log rows to GCS
After=network.target postgresql.service

[Service]
Type=oneshot
User=exedev
WorkingDirectory=/home/exedev/address-validator
EnvironmentFile=/etc/address-validator/env
Environment=PYTHONPATH=/home/exedev/address-validator/src
ExecStart=/home/exedev/address-validator/.venv/bin/python scripts/archive_audit.py

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create the timer unit**

Create `audit-archive.timer`:

```ini
[Unit]
Description=Daily audit log archival (03:00 UTC)

[Timer]
OnCalendar=*-*-* 03:00:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Commit**

```bash
git add audit-archive.service audit-archive.timer
git commit -m "#49 feat: add systemd timer for daily audit archival"
```

---

### Task 6: Documentation Updates

**Files:**
- Modify: `AGENTS.md` (env var table, deployment section)

- [ ] **Step 1: Add new env vars to AGENTS.md**

In the env var table in `AGENTS.md`, add after `VALIDATION_CACHE_TTL_DAYS`:

```markdown
| `AUDIT_RETENTION_DAYS` | non-negative int | `90` |
| `AUDIT_ARCHIVE_BUCKET` | GCS bucket name | — (required for archival) |
| `AUDIT_ARCHIVE_PREFIX` | string | `audit/` |
```

- [ ] **Step 2: Add archival commands to deployment section**

In the deployment section of `AGENTS.md`, add:

```markdown
- Archive audit log: `source /etc/address-validator/env && uv run python scripts/archive_audit.py`
- Backfill rollups: `source /etc/address-validator/env && uv run python scripts/archive_audit.py --backfill`
- Install timer: `sudo cp audit-archive.service audit-archive.timer /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now audit-archive.timer`
```

- [ ] **Step 3: Add sensitive area entry for audit_daily_stats**

In the sensitive areas table in `AGENTS.md`, add:

```markdown
| `scripts/archive_audit.py` | Deletes audit_log rows after archival — verify GCS upload succeeded before deletion; `ON CONFLICT DO NOTHING` in aggregation is load-bearing for idempotency |
```

- [ ] **Step 4: Run lint**

Run: `uv run ruff check .`
Expected: Clean.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md
git commit -m "#49 docs: add audit retention env vars and deployment commands"
```

---

### Task 7: End-to-End Integration Test

**Files:**
- Modify: `tests/unit/test_archive_audit.py`

- [ ] **Step 1: Write integration test for full archive cycle**

Add to `tests/unit/test_archive_audit.py`:

```python
from archive_audit import group_rows_by_date


@pytest.mark.asyncio
async def test_full_archive_cycle(db: AsyncEngine, tmp_path) -> None:
    """End-to-end: aggregate → export → delete preserves data integrity."""
    await _seed_old_and_new_rows(db)
    cutoff = datetime.now(UTC) - timedelta(days=90)

    # Step 1: Aggregate
    inserted = await aggregate(db, cutoff=cutoff)
    assert inserted > 0

    # Step 2: Fetch + Export
    rows = await fetch_expired_rows(db, cutoff)
    assert len(rows) == 3

    day_groups = group_rows_by_date(rows)
    total_exported = 0
    for day_key, day_rows in day_groups.items():
        dest = tmp_path / f"audit-{day_key}.parquet"
        total_exported += export_parquet(day_rows, dest)
        assert dest.exists()
    assert total_exported == 3

    # Step 3: Delete (skip GCS upload — tested separately)
    deleted = await delete_expired_rows(db, cutoff)
    assert deleted == 3

    # Verify: live table has only recent rows
    async with db.connect() as conn:
        live_count = (await conn.execute(text("SELECT COUNT(*) FROM audit_log"))).scalar()
        stats_count = (await conn.execute(text("SELECT COUNT(*) FROM audit_daily_stats"))).scalar()
    assert live_count == 2   # Only recent rows
    assert stats_count > 0   # Rollups exist
```

- [ ] **Step 2: Run integration test**

Run: `uv run pytest tests/unit/test_archive_audit.py::test_full_archive_cycle -x --no-cov -v`
Expected: PASS.

- [ ] **Step 3: Run full test suite with coverage**

Run: `uv run pytest`
Expected: All tests pass, coverage >= 80%.

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_archive_audit.py
git commit -m "#49 test: add end-to-end archive cycle integration test"
```
