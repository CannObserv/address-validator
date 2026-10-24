#!/usr/bin/env python3
"""Rewrite archived audit Parquet files: raw ``client_ip`` -> keyed hash (GH #233).

One-off. Files uploaded before #233 hold the raw IP (``LEGACY_ARCHIVE_SCHEMA``).
This replaces it, in place, with ``client_ip_hmac`` + ``client_ip_key_id``
under the same key the nightly ``archive_audit.py`` uses, so a rewritten day
and a nightly day are one dataset and one IP links across both.

Per blob under the prefix (one at a time; the largest is ~300k rows):

- legacy schema  -> rewrite with ``archive_audit._archive_writer``, verify the
  local file (schema, row count, ``id`` column), upload conditional on the
  generation that was read, then download the new blob and re-check it
- current schema -> skip, so a rerun is idempotent
- anything else  -> abort before any further write
- not ``.parquet`` -> skip

Dry run by default; ``--apply`` writes. Logs blob names and counts only,
never an IP, the key or an HMAC.

The bucket has no versioning, so an overwrite leaves no raw version behind.
Its 7-day soft-delete keeps each raw original restorable for 7 days (to
someone with restore permission); after that it is gone. Key rotation can't
use this script: once rewritten, the raw IP no longer exists.

Usage (main checkout, outside the quiet ranges):
    set -a && source /etc/address-validator/.env && set +a
    PYTHONPATH=src .venv/bin/python infra/rehash_archive_ips.py           # dry run
    PYTHONPATH=src .venv/bin/python infra/rehash_archive_ips.py --apply

Env vars: AUDIT_ARCHIVE_BUCKET (required), AUDIT_ARCHIVE_PREFIX (default
"audit/"), AUDIT_IP_HMAC_KEY + AUDIT_IP_HMAC_KEY_ID (required).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import pyarrow as pa
import pyarrow.parquet as pq
from archive_audit import ARCHIVE_SCHEMA, _archive_writer
from audit_ip_hmac import IpHasher, KeyConfigError, load_hasher
from google.cloud import storage as gcs

if TYPE_CHECKING:
    from collections.abc import Sequence

logger = logging.getLogger(__name__)

# The pre-#233 archive layout. Every blob uploaded before #233 matches it
# exactly, types and (empty) schema metadata included (measured 2026-10-24:
# 141 files, pyarrow 19.0.1 and 26.0.0 writers).
LEGACY_ARCHIVE_SCHEMA = pa.schema(
    [
        ("id", pa.int64()),
        ("timestamp", pa.timestamp("us", tz="UTC")),
        ("request_id", pa.string()),
        ("client_ip", pa.string()),
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

_ROW_GROUP_SIZE = 10_000  # the nightly export's batch size: one row group per batch


def configure_logging() -> None:
    """Plain ``LEVEL: message`` lines, like the other one-off scripts.

    Run by hand, not by a unit, so not ``journal_logging`` (whose test holds
    every user to a unit with ``src`` on PYTHONPATH).
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


class UnknownSchemaError(Exception):
    """A blob matches neither the legacy nor the current archive schema."""


class VerifyError(Exception):
    """A rewritten file failed its check; nothing further is written."""


@dataclass
class Summary:
    legacy: int = 0
    current: int = 0
    rewritten: int = 0
    skipped: int = 0
    rows: int = 0


def classify(schema: pa.Schema) -> Literal["legacy", "current"]:
    """Which archive generation ``schema`` is; raises UnknownSchemaError otherwise."""
    if schema.equals(LEGACY_ARCHIVE_SCHEMA):
        return "legacy"
    if schema.equals(ARCHIVE_SCHEMA):
        return "current"
    msg = f"unrecognized archive schema: {schema.names}"
    raise UnknownSchemaError(msg)


def rehash_table(table: pa.Table, hasher: IpHasher) -> pa.Table:
    """Swap ``client_ip`` for its HMAC and key ID; every other column is untouched."""
    ips = table.column("client_ip").to_pylist()
    derived = {
        "client_ip_hmac": pa.array(
            [None if ip is None else hasher.hash(ip) for ip in ips], pa.string()
        ),
        "client_ip_key_id": pa.array([hasher.key_id] * len(ips), pa.string()),
    }
    columns = [derived[n] if n in derived else table.column(n) for n in ARCHIVE_SCHEMA.names]
    return pa.Table.from_arrays(columns, schema=ARCHIVE_SCHEMA)


def _check_rewritten(path: Path, ids: pa.ChunkedArray) -> None:
    """The file at ``path`` is current-schema, holds no client_ip, and has the same ids."""
    if not pq.read_schema(path).equals(ARCHIVE_SCHEMA):
        msg = f"{path.name}: schema is not ARCHIVE_SCHEMA after rewrite"
        raise VerifyError(msg)
    got = pq.read_table(path, columns=["id"]).column("id")
    if not got.equals(ids):
        msg = f"{path.name}: id column changed in rewrite"
        raise VerifyError(msg)


def rehash_file(src: Path, dest: Path, hasher: IpHasher) -> int:
    """Rewrite legacy ``src`` to ``dest`` and verify it. Returns the row count."""
    if classify(pq.read_schema(src)) != "legacy":
        msg = f"{src.name}: not a legacy archive file"
        raise UnknownSchemaError(msg)
    table = pq.read_table(src)
    out = rehash_table(table, hasher)
    with _archive_writer(dest) as writer:
        writer.write_table(out, row_group_size=_ROW_GROUP_SIZE)
    _check_rewritten(dest, table.column("id"))
    return table.num_rows


def rehash_bucket(
    bucket: gcs.Bucket,
    prefix: str,
    hasher: IpHasher,
    *,
    apply: bool,
    workdir: Path,
) -> Summary:
    """Walk the blobs under ``prefix`` and rewrite the legacy ones (when ``apply``)."""
    summary = Summary()
    src, dest, check = (workdir / n for n in ("src.parquet", "dest.parquet", "check.parquet"))
    for listed in bucket.list_blobs(prefix=prefix):
        name = listed.name
        if not name.endswith(".parquet"):
            logger.warning("Skipping non-Parquet blob %s.", name)
            summary.skipped += 1
            continue
        generation = listed.generation
        bucket.blob(name).download_to_filename(str(src), if_generation_match=generation)
        if classify(pq.read_schema(src)) == "current":
            summary.current += 1
            continue
        summary.legacy += 1
        if not apply:
            logger.info("Would rewrite %s (%d rows).", name, pq.read_metadata(src).num_rows)
            continue

        rows = rehash_file(src, dest, hasher)
        target = bucket.blob(name)
        target.upload_from_filename(str(dest), if_generation_match=generation)
        # The upload response sets target.generation: re-read exactly what was written.
        target.download_to_filename(str(check), if_generation_match=target.generation)
        _check_rewritten(check, pq.read_table(src, columns=["id"]).column("id"))
        summary.rewritten += 1
        summary.rows += rows
        logger.info("Rewrote %s (%d rows).", name, rows)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Rewrite archived raw IPs as keyed hashes.")
    parser.add_argument("--apply", action="store_true", help="Write; default is a dry run.")
    args = parser.parse_args(argv)

    try:
        hasher = load_hasher(os.environ)
    except KeyConfigError as exc:
        logger.error("%s", exc)
        return 1
    bucket_name = os.environ.get("AUDIT_ARCHIVE_BUCKET", "").strip()
    if not bucket_name:
        logger.error("AUDIT_ARCHIVE_BUCKET not set")
        return 1
    prefix = os.environ.get("AUDIT_ARCHIVE_PREFIX", "audit/").strip()

    mode = "apply" if args.apply else "dry run"
    logger.info(
        "Rewriting gs://%s/%s* with key id %s (%s).", bucket_name, prefix, hasher.key_id, mode
    )
    bucket = gcs.Client().bucket(bucket_name)
    with tempfile.TemporaryDirectory() as tmp:
        summary = rehash_bucket(bucket, prefix, hasher, apply=args.apply, workdir=Path(tmp))
    logger.info(
        "Done (%s): %d legacy, %d already current, %d rewritten (%d rows), %d skipped.",
        mode,
        summary.legacy,
        summary.current,
        summary.rewritten,
        summary.rows,
        summary.skipped,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
