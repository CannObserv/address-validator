#!/usr/bin/env python3
"""Archive audit_log rows older than the retention window.

Steps: aggregate → export Parquet → upload GCS → delete old rows → VACUUM.

Usage:
    uv run python infra/archive_audit.py               # archive expired rows
    uv run python infra/archive_audit.py --backfill     # aggregate ALL rows first

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

import pyarrow as pa
import pyarrow.parquet as pq
import sqlalchemy as sa
from google.cloud import storage as gcs
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import create_async_engine

from address_validator.db.tables import ERROR_STATUS_MIN, audit_daily_stats, audit_log

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)


def _build_aggregate_select(*, cutoff: bool) -> sa.Select:
    """Build the SELECT portion of the aggregation query."""
    stmt = select(
        sa.cast(func.date_trunc("day", audit_log.c.timestamp), sa.Date).label("date"),
        audit_log.c.endpoint,
        audit_log.c.provider,
        audit_log.c.status_code,
        audit_log.c.cache_hit,
        func.count().label("request_count"),
        func.count().filter(audit_log.c.status_code >= ERROR_STATUS_MIN).label("error_count"),
        sa.cast(func.avg(audit_log.c.latency_ms), sa.Integer).label("avg_latency_ms"),
        sa.cast(
            func.percentile_cont(0.95).within_group(audit_log.c.latency_ms),
            sa.Integer,
        ).label("p95_latency_ms"),
    ).group_by(
        sa.literal_column("date"),
        audit_log.c.endpoint,
        audit_log.c.provider,
        audit_log.c.status_code,
        audit_log.c.cache_hit,
    )
    if cutoff:
        stmt = stmt.where(audit_log.c.timestamp < sa.bindparam("cutoff"))
    return stmt


_AGG_COLUMNS = [
    "date",
    "endpoint",
    "provider",
    "status_code",
    "cache_hit",
    "request_count",
    "error_count",
    "avg_latency_ms",
    "p95_latency_ms",
]


class ArchiveError(Exception):
    """Raised when an archive step fails and the script should abort."""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Archive old audit_log rows.")
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Aggregate ALL existing rows (not just expired ones).",
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
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
    select_stmt = _build_aggregate_select(cutoff=cutoff is not None)
    insert_stmt = (
        pg_insert(audit_daily_stats).from_select(_AGG_COLUMNS, select_stmt).on_conflict_do_nothing()
    )
    params: dict = {} if cutoff is None else {"cutoff": cutoff}

    async with engine.begin() as conn:
        result = await conn.execute(insert_stmt, params)
        return result.rowcount


async def fetch_expired_dates(engine: AsyncEngine, cutoff: datetime) -> list[datetime]:
    """Fetch distinct dates that have expired rows, ordered ascending."""
    day_col = func.date_trunc("day", audit_log.c.timestamp).label("day")
    stmt = (
        select(day_col)
        .distinct()
        .where(audit_log.c.timestamp < cutoff)
        .order_by(sa.literal_column("day"))
    )
    async with engine.connect() as conn:
        result = await conn.execute(stmt)
        return [r.day for r in result]


async def fetch_rows_for_date(
    engine: AsyncEngine,
    day: datetime,
    cutoff: datetime,
) -> list[dict]:
    """Fetch audit_log rows for a single day, bounded by cutoff."""
    day_start = day
    day_end = day + timedelta(days=1)
    # Clamp to cutoff so we don't overshoot on the boundary day.
    end = min(day_end, cutoff)
    stmt = (
        select(
            audit_log.c.id,
            audit_log.c.timestamp,
            audit_log.c.request_id,
            audit_log.c.client_ip,
            audit_log.c.method,
            audit_log.c.endpoint,
            audit_log.c.status_code,
            audit_log.c.latency_ms,
            audit_log.c.provider,
            audit_log.c.validation_status,
            audit_log.c.cache_hit,
            audit_log.c.error_detail,
        )
        .where(audit_log.c.timestamp >= day_start, audit_log.c.timestamp < end)
        .order_by(audit_log.c.timestamp)
    )
    async with engine.connect() as conn:
        result = await conn.execute(stmt)
        return [dict(r._mapping) for r in result]  # noqa: SLF001


def export_parquet(rows: list[dict], dest: Path) -> int:
    """Write rows to a Parquet file. Returns row count written."""
    if not rows:
        return 0

    table = pa.Table.from_pylist(rows)
    pq.write_table(table, dest, compression="snappy")
    return len(rows)


def upload_to_gcs(
    client: gcs.Client,
    local_path: Path,
    bucket_name: str,
    blob_name: str,
) -> None:
    """Upload a local file to GCS."""
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(str(local_path))
    logger.info("Uploaded gs://%s/%s", bucket_name, blob_name)


def verify_gcs_upload(client: gcs.Client, bucket_name: str, blob_name: str) -> bool:
    """Verify a GCS object exists and has non-zero size."""
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.reload()
    return not (blob.size is None or blob.size == 0)


async def delete_expired_rows(
    engine: AsyncEngine,
    cutoff: datetime,
    *,
    batch_size: int = 10_000,
) -> int:
    """Delete audit_log rows older than cutoff in batches. Returns total deleted."""
    total_deleted = 0
    while True:
        subq = select(audit_log.c.id).where(audit_log.c.timestamp < cutoff).limit(batch_size)
        stmt = audit_log.delete().where(audit_log.c.id.in_(subq))
        async with engine.begin() as conn:
            result = await conn.execute(stmt)
            deleted = result.rowcount
            total_deleted += deleted
            if deleted < batch_size:
                break
            logger.info("Deleted %d rows so far...", total_deleted)
    return total_deleted


async def vacuum_audit_log(engine: AsyncEngine) -> None:
    """Run VACUUM ANALYZE on audit_log.

    Requires execution outside a transaction; uses the engine's pool
    with AUTOCOMMIT isolation.
    """
    async with engine.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
        await conn.execute(text("VACUUM ANALYZE audit_log"))


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

        # Step 2: Export expired rows to Parquet — one day at a time
        expired_dates = await fetch_expired_dates(engine, cutoff)
        if not expired_dates:
            logger.info("No expired rows to archive. Done.")
            return

        total_exported = 0
        gcs_client = gcs.Client() if bucket and not args.skip_upload else None

        with tempfile.TemporaryDirectory() as tmpdir:
            for day in expired_dates:
                day_key = day.strftime("%Y-%m-%d")
                year, month, _day = day_key.split("-")
                filename = f"audit-{day_key}.parquet"
                local_path = Path(tmpdir) / filename
                blob_name = f"{prefix}year={year}/month={month}/{filename}"

                rows = await fetch_rows_for_date(engine, day, cutoff)
                exported = export_parquet(rows, local_path)
                total_exported += exported
                logger.info("Exported %d rows for %s.", exported, day_key)

                # Step 3: Upload to GCS
                if gcs_client and bucket:
                    upload_to_gcs(gcs_client, local_path, bucket, blob_name)

                    # Step 4: Verify
                    if not verify_gcs_upload(gcs_client, bucket, blob_name):
                        msg = f"Verification failed for {blob_name}"
                        raise ArchiveError(msg)

                # Remove local file after upload to limit disk usage.
                local_path.unlink(missing_ok=True)

        if gcs_client:
            logger.info("All %d Parquet files verified in GCS.", len(expired_dates))
        elif not bucket:
            logger.warning("AUDIT_ARCHIVE_BUCKET not set — skipping upload.")
        else:
            logger.info("Skipping upload (--skip-upload).")

        logger.info("Exported %d total rows across %d days.", total_exported, len(expired_dates))

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
