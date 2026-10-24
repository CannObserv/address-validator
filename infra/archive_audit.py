#!/usr/bin/env python3
"""Archive audit_log rows older than the retention window.

Steps: aggregate → export Parquet → upload GCS → delete old rows → VACUUM.

The cutoff is a UTC midnight (``compute_cutoff``), so every archived day is
whole: a mid-day cutoff split a day across two runs, whose rollup then hit
``ON CONFLICT DO NOTHING`` and whose Parquet blob overwrote the first slice
(#228). Days are bucketed in UTC regardless of the DB session timezone.

Archived columns are ``ARCHIVE_SCHEMA``. No address-derived column is
archived: ``raw_input`` (address content, PII) lives only for the
audit-retention window (#147) and is dropped for good when the row is
deleted, and ``pattern_key`` is an unsalted SHA-256 of the standardized
address — archiving it would let anyone holding the archive confirm or
enumerate queried addresses.

No raw IP is archived (#233): ``client_ip`` becomes ``client_ip_hmac``, a hex
HMAC-SHA256 of the canonical IP under ``AUDIT_IP_HMAC_KEY``, plus
``client_ip_key_id``. Without a valid key the script exits 1 before touching
the DB, in every mode (``--skip-upload`` included), and never falls back to
the raw IP. Never swap in a plain SHA-256: the IPv4 space is small enough to
brute-force. Never log a raw IP, the key, or an HMAC (the journal is
persistent, #241). Scheme, key rotation, crypto-shredding and how to look up
a known IP: ``audit_ip_hmac.py``. Files uploaded before #233 held the raw IP
and were rewritten in place by ``rehash_archive_ips.py``.

Usage:
    uv run python infra/archive_audit.py               # archive expired rows
    uv run python infra/archive_audit.py --backfill     # aggregate all complete days first
    uv run python infra/archive_audit.py --skip-upload  # aggregate + delete, no GCS

Env vars:
    VALIDATION_CACHE_DSN    PostgreSQL DSN (required)
    AUDIT_RETENTION_DAYS    Hot window in days (default: 90)
    AUDIT_ARCHIVE_BUCKET    GCS bucket name (required for upload)
    AUDIT_ARCHIVE_PREFIX    GCS key prefix (default: "audit/")
    AUDIT_IP_HMAC_KEY       hex HMAC key, >= 32 bytes (required)
    AUDIT_IP_HMAC_KEY_ID    label stamped on every row (required)
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
from audit_ip_hmac import IpHasher, KeyConfigError, load_hasher
from audit_retention import compute_cutoff, parse_retention_days
from google.cloud import storage as gcs
from journal_logging import configure_logging
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import create_async_engine

from address_validator.db.tables import ERROR_STATUS_MIN, audit_daily_stats, audit_log

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncEngine

    Uploader = Callable[[Path, str], None]

logger = logging.getLogger(__name__)


# UTC day of each row, independent of the session TimeZone.
_UTC_DAY_START = func.date_trunc("day", audit_log.c.timestamp, "UTC")
_UTC_DATE = sa.cast(func.timezone("UTC", audit_log.c.timestamp), sa.Date)

# Columns exported to Parquet, in order. Types match what the earlier
# pylist-inferred export produced, so old and new files read as one dataset.
ARCHIVE_SCHEMA = pa.schema(
    [
        ("id", pa.int64()),
        ("timestamp", pa.timestamp("us", tz="UTC")),
        ("request_id", pa.string()),
        ("client_ip_hmac", pa.string()),
        ("client_ip_key_id", pa.string()),
        ("method", pa.string()),
        ("endpoint", pa.string()),
        ("status_code", pa.int64()),
        ("latency_ms", pa.int64()),
        ("provider", pa.string()),
        ("validation_status", pa.string()),
        ("cache_hit", pa.bool_()),
        ("error_detail", pa.string()),
        ("parse_type", pa.string()),
    ]
)

# Read from audit_log: the archived columns, with the raw client_ip in place
# of the two hashed ones. client_ip never reaches a file (#233).
_HASHED_COLUMNS = ("client_ip_hmac", "client_ip_key_id")
_SOURCE_COLUMNS = [
    "client_ip" if name == "client_ip_hmac" else name
    for name in ARCHIVE_SCHEMA.names
    if name != "client_ip_key_id"
]


def _archive_writer(dest: Path) -> pq.ParquetWriter:
    """The one Parquet writer for archive files; settings are the #321 contract.

    Shared with ``rehash_archive_ips.py`` so a rewritten file and a nightly
    file can't drift apart.
    """
    return pq.ParquetWriter(dest, ARCHIVE_SCHEMA, compression="snappy")


def _hash_row(row: dict, hasher: IpHasher) -> dict:
    """Replace the raw ``client_ip`` with its keyed hash and the key ID."""
    out = dict(row)
    ip = out.pop("client_ip")
    out["client_ip_hmac"] = hasher.hash(ip)
    out["client_ip_key_id"] = hasher.key_id
    return out


def _build_aggregate_select() -> sa.Select:
    """Build the SELECT portion of the aggregation query."""
    return (
        select(
            _UTC_DATE.label("date"),
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
        )
        .where(audit_log.c.timestamp < sa.bindparam("cutoff"))
        .group_by(
            sa.literal_column("date"),
            audit_log.c.endpoint,
            audit_log.c.provider,
            audit_log.c.status_code,
            audit_log.c.cache_hit,
        )
    )


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
        help="Aggregate every complete UTC day (up to today's midnight) first.",
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="Skip GCS upload (aggregate + delete only). Required when no bucket is set.",
    )
    return parser.parse_args()


def _get_config() -> tuple[str, int, str | None, str]:
    """Read and validate env vars. Returns (dsn, retention_days, bucket, prefix)."""
    dsn = os.environ.get("VALIDATION_CACHE_DSN", "").strip()
    if not dsn:
        logger.error("VALIDATION_CACHE_DSN not set")
        sys.exit(1)
    try:
        retention = parse_retention_days(os.environ)
    except ValueError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    bucket = os.environ.get("AUDIT_ARCHIVE_BUCKET", "").strip() or None
    prefix = os.environ.get("AUDIT_ARCHIVE_PREFIX", "audit/").strip()
    return dsn, retention, bucket, prefix


def _load_hasher_or_exit() -> IpHasher:
    """The archive's IP hasher, or exit 1: never archive raw IPs (#233).

    The error names the variable, never its value.
    """
    try:
        hasher = load_hasher(os.environ)
    except KeyConfigError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    return hasher


def _check_upload_config(bucket: str | None, *, skip_upload: bool) -> None:
    """Exit unless there is somewhere to upload or skipping was asked for.

    Without this guard a missing AUDIT_ARCHIVE_BUCKET logged a warning and
    then deleted the rows anyway (#228).
    """
    if bucket is None and not skip_upload:
        logger.error("AUDIT_ARCHIVE_BUCKET not set; pass --skip-upload to delete without archiving")
        sys.exit(1)


async def aggregate(engine: AsyncEngine, *, cutoff: datetime) -> int:
    """Aggregate audit_log rows before ``cutoff`` into audit_daily_stats.

    ``cutoff`` must be a UTC midnight: ON CONFLICT DO NOTHING never tops up a
    day that was rolled up while incomplete. Returns number of rows inserted.
    """
    insert_stmt = (
        pg_insert(audit_daily_stats)
        .from_select(_AGG_COLUMNS, _build_aggregate_select())
        .on_conflict_do_nothing()
    )
    async with engine.begin() as conn:
        result = await conn.execute(insert_stmt, {"cutoff": cutoff})
        return result.rowcount


async def fetch_expired_dates(engine: AsyncEngine, cutoff: datetime) -> list[datetime]:
    """Fetch distinct UTC day starts that have expired rows, ordered ascending."""
    day_col = _UTC_DAY_START.label("day")
    stmt = (
        select(day_col)
        .distinct()
        .where(audit_log.c.timestamp < cutoff)
        .order_by(sa.literal_column("day"))
    )
    async with engine.connect() as conn:
        result = await conn.execute(stmt)
        return [r.day for r in result]


async def export_day(
    engine: AsyncEngine,
    day: datetime,
    cutoff: datetime,
    dest: Path,
    *,
    hasher: IpHasher,
    batch_size: int = 10_000,
) -> int:
    """Stream one UTC day's rows (bounded by cutoff) to Parquet. Returns rows written.

    Rows go through a server-side cursor in ``batch_size`` batches, so peak
    memory is one batch, not the whole day. Writes no file for an empty day.
    Each row's ``client_ip`` is replaced by ``hasher``'s HMAC and key ID.
    """
    end = min(day + timedelta(days=1), cutoff)
    stmt = (
        select(*(audit_log.c[name] for name in _SOURCE_COLUMNS))
        .where(audit_log.c.timestamp >= day, audit_log.c.timestamp < end)
        .order_by(audit_log.c.timestamp)
    )
    written = 0
    writer: pq.ParquetWriter | None = None
    try:
        async with engine.connect() as conn:
            result = await conn.stream(stmt)
            async for partition in result.partitions(batch_size):
                batch = pa.RecordBatch.from_pylist(
                    [_hash_row(dict(r._mapping), hasher) for r in partition],  # noqa: SLF001
                    schema=ARCHIVE_SCHEMA,
                )
                if writer is None:
                    writer = _archive_writer(dest)
                writer.write_batch(batch)
                written += batch.num_rows
    finally:
        if writer is not None:
            writer.close()
    return written


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


def _gcs_uploader(bucket: str) -> Uploader:
    """Return an uploader that uploads then verifies, raising ArchiveError on failure."""
    client = gcs.Client()

    def upload(local_path: Path, blob_name: str) -> None:
        upload_to_gcs(client, local_path, bucket, blob_name)
        if not verify_gcs_upload(client, bucket, blob_name):
            msg = f"Verification failed for {blob_name}"
            raise ArchiveError(msg)

    return upload


async def run_archive(
    engine: AsyncEngine,
    cutoff: datetime,
    *,
    prefix: str,
    hasher: IpHasher,
    upload: Uploader | None,
) -> None:
    """Aggregate, export, upload, delete and VACUUM rows before ``cutoff``.

    ``upload=None`` skips the upload (explicit --skip-upload). Any upload
    failure raises before the delete step, so no row is lost.
    """
    logger.info("Hashing archived client IPs with key id %s.", hasher.key_id)
    logger.info("Aggregating rows older than %s...", cutoff.date())
    inserted = await aggregate(engine, cutoff=cutoff)
    logger.info("Aggregated %d rollup rows.", inserted)

    expired_dates = await fetch_expired_dates(engine, cutoff)
    if not expired_dates:
        logger.info("No expired rows to archive. Done.")
        return

    total_exported = 0
    uploaded = 0
    with tempfile.TemporaryDirectory() as tmpdir:
        for day in expired_dates:
            day_key = day.strftime("%Y-%m-%d")
            year, month, _day = day_key.split("-")
            filename = f"audit-{day_key}.parquet"
            local_path = Path(tmpdir) / filename
            blob_name = f"{prefix}year={year}/month={month}/{filename}"

            exported = await export_day(engine, day, cutoff, local_path, hasher=hasher)
            total_exported += exported
            logger.info("Exported %d rows for %s.", exported, day_key)

            if upload is not None and exported:
                upload(local_path, blob_name)
                uploaded += 1

            # Remove local file after upload to limit disk usage.
            local_path.unlink(missing_ok=True)

    if upload is None:
        logger.info("Skipping upload (--skip-upload).")
    else:
        logger.info("Uploaded and verified %d Parquet file(s).", uploaded)
    logger.info("Exported %d total rows across %d days.", total_exported, len(expired_dates))

    deleted = await delete_expired_rows(engine, cutoff)
    logger.info("Deleted %d expired rows from audit_log.", deleted)

    await vacuum_audit_log(engine)
    logger.info("VACUUM ANALYZE complete.")


async def main() -> None:
    configure_logging()

    args = _parse_args()
    dsn, retention_days, bucket, prefix = _get_config()
    _check_upload_config(bucket, skip_upload=args.skip_upload)
    hasher = _load_hasher_or_exit()

    engine = create_async_engine(dsn)
    now = datetime.now(UTC)

    try:
        if args.backfill:
            # Stop at today's midnight: a rollup of the still-running day would
            # never be topped up (ON CONFLICT DO NOTHING).
            logger.info("Backfill mode: aggregating all complete days into audit_daily_stats...")
            inserted = await aggregate(engine, cutoff=compute_cutoff(now, 0))
            logger.info("Backfilled %d rollup rows.", inserted)

        upload = _gcs_uploader(bucket) if bucket and not args.skip_upload else None
        await run_archive(
            engine,
            compute_cutoff(now, retention_days),
            prefix=prefix,
            hasher=hasher,
            upload=upload,
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
