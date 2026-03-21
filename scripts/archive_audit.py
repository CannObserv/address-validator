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

import pyarrow as pa
import pyarrow.parquet as pq
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
        return [dict(r._mapping) for r in result]  # noqa: SLF001


def export_parquet(rows: list[dict], dest: Path) -> int:
    """Write rows to a Parquet file. Returns row count written."""
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
    from google.cloud import storage  # noqa: PLC0415

    client = storage.Client()
    bucket = client.bucket(bucket_name)
    blob = bucket.blob(blob_name)
    blob.upload_from_filename(str(local_path))
    logger.info("Uploaded gs://%s/%s", bucket_name, blob_name)


def verify_gcs_upload(bucket_name: str, blob_name: str) -> bool:
    """Verify a GCS object exists and has non-zero size."""
    from google.cloud import storage  # noqa: PLC0415

    client = storage.Client()
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
