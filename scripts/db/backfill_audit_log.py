#!/usr/bin/env python3
"""One-shot backfill: parse journalctl logs into the audit_log table.

Extracts: timestamp, client IP, HTTP method, endpoint path, status code.
Fields left NULL: request_id, latency_ms, provider, validation_status, cache_hit.

Usage:
    uv run python scripts/db/backfill_audit_log.py

Idempotency: skips if any rows with NULL request_id already exist in the
journal's time range (indicating a previous backfill).
"""

import asyncio
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Matches uvicorn access log format:
# INFO:     1.2.3.4:0 - "POST /api/v1/validate HTTP/1.1" 200 OK
_ACCESS_RE = re.compile(
    r"INFO:\s+"
    r"(?P<ip>[\d.]+):\d+\s+-\s+"
    r'"(?P<method>\w+)\s+(?P<path>/\S+)\s+HTTP/[\d.]+"\s+'
    r"(?P<status>\d+)"
)

_API_PREFIX = "/api/"


def _parse_journal_line(line: str) -> dict | None:
    """Parse a single JSON journal entry into an audit_log row dict, or None."""
    try:
        entry = json.loads(line)
    except json.JSONDecodeError:
        return None

    message = entry.get("MESSAGE", "")
    match = _ACCESS_RE.search(message)
    if not match:
        return None

    path = match.group("path")
    if not path.startswith(_API_PREFIX):
        return None

    ts_us = int(entry.get("__REALTIME_TIMESTAMP", 0))
    if ts_us == 0:
        return None

    return {
        "timestamp": datetime.fromtimestamp(ts_us / 1_000_000, tz=UTC),
        "request_id": None,
        "client_ip": match.group("ip"),
        "method": match.group("method"),
        "endpoint": path,
        "status_code": int(match.group("status")),
        "latency_ms": None,
        "provider": None,
        "validation_status": None,
        "cache_hit": None,
        "error_detail": None,
    }


async def main() -> None:
    dsn = os.environ.get("VALIDATION_CACHE_DSN", "").strip()
    if not dsn:
        print("ERROR: VALIDATION_CACHE_DSN not set", file=sys.stderr)
        sys.exit(1)

    engine = create_async_engine(dsn)

    # Check for existing backfill
    async with engine.connect() as conn:
        existing = (
            await conn.execute(text("SELECT COUNT(*) FROM audit_log WHERE request_id IS NULL"))
        ).scalar()
        if existing and existing > 0:
            print(f"Skipping: {existing} backfilled rows already exist.")
            await engine.dispose()
            return

    # Read journal
    print("Reading journalctl output...")
    proc = subprocess.run(
        ["/usr/bin/journalctl", "-u", "address-validator", "--output=json", "--no-pager"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        print(f"ERROR: journalctl failed: {proc.stderr}", file=sys.stderr)
        sys.exit(1)

    rows = []
    for line in proc.stdout.strip().splitlines():
        row = _parse_journal_line(line)
        if row:
            rows.append(row)

    if not rows:
        print("No API requests found in journal.")
        await engine.dispose()
        return

    print(f"Parsed {len(rows)} API requests from journal.")
    print(f"  Time range: {rows[0]['timestamp']} — {rows[-1]['timestamp']}")

    insert_sql = text("""
        INSERT INTO audit_log (
            timestamp, request_id, client_ip, method, endpoint,
            status_code, latency_ms, provider, validation_status,
            cache_hit, error_detail
        ) VALUES (
            :timestamp, :request_id, :client_ip, :method, :endpoint,
            :status_code, :latency_ms, :provider, :validation_status,
            :cache_hit, :error_detail
        )
    """)

    batch_size = 1000
    inserted = 0
    async with engine.begin() as conn:
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            await conn.execute(insert_sql, batch)
            inserted += len(batch)
            print(f"  Inserted {inserted}/{len(rows)}...")

    print(f"Done. Backfilled {len(rows)} rows.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
