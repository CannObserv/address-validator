#!/usr/bin/env python3
"""Recovery check for patching-hosts' held Postgres step (#311).

The app fails open while Postgres is down (#235): cache lookups miss, audit
writes are dropped, and callers still get 200s. So ``/api/v2/health`` going
green after a Postgres restart proves liveness, not that the database path came
back. This proves it:

1. Postgres accepts a connection. If not, exit 1 before any request: the skill
   polls each ``health`` line every second through the outage, and every
   validate would miss the cache and spend provider quota.
2. ``POST /api/v2/validate`` twice with one fixed address; both 200.
3. Both requests' ``audit_log`` rows land (matched by ``X-Request-ID``), and the
   second is a cache hit.

The address is the Washington State Capitol campus: public and non-residential,
so no PII, and deliverable, so the providers answer with a cacheable status. A
made-up address risks an HTTP 400 from the provider, which is ``error``, never
cached, and the repeat could never hit. The first call after the 30-day TTL
spends one provider call; later ones are hits.

A knob ``health`` line (``.skills/patching-hosts``): it runs as the invoking
user, never root, under the skill's 60 s limit. ``API_KEY`` and
``VALIDATION_CACHE_DSN`` come only from /etc/address-validator/.env
(root:exedev 0640), the service's own ``EnvironmentFile=``, never the caller's
environment: a shell still exporting the test DSN would read ``audit_log`` from
another database than the one behind port 8000. It prints one line, never the
address, key or DSN.

Usage:
    .venv/bin/python infra/patching_recovery_check.py

Exit status: 0 recovered; 1 not recovered, or config unreadable (the line says
which step).
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import create_async_engine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

ENV_FILE = Path("/etc/address-validator/.env")
BASE_URL = "http://localhost:8000"
ADDRESS = "416 Sid Snyder Ave SW, Olympia, WA 98504"

DB_CONNECT_TIMEOUT_S = 5
DB_COMMAND_TIMEOUT_S = 5
HTTP_TIMEOUT_S = 15
AUDIT_WAIT_S = 10
POLL_S = 0.25
# The skill TERMs a knob command at 60 s and reports only "timed out", losing
# the step. httpx's timeout bounds each phase (connect, read, ...), not a call,
# so the per-call limits don't add up to a bound: this does, with startup to
# spare.
CHECK_BUDGET_S = 45

# Rows are matched by request ID; the time bound lets idx_audit_ts do the work.
_AUDIT_ROWS = text(
    "SELECT request_id, cache_hit FROM audit_log"
    " WHERE timestamp >= now() - interval '10 minutes' AND request_id IN :ids"
).bindparams(bindparam("ids", expanding=True))


class ConfigError(Exception):
    """A setting is missing, or the env file can't be read."""


def _read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines, as systemd's ``EnvironmentFile=`` reads them."""
    values: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) > 1 and value[0] in "'\"" and value[-1] == value[0]:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def load_config(env_file: Path) -> tuple[str, str]:
    """``(api_key, dsn)`` from ``env_file`` alone (see the module docstring)."""
    try:
        values = _read_env_file(env_file)
    except OSError as exc:
        raise ConfigError(f"can't read {env_file} ({exc.strerror})") from None
    api_key, dsn = values.get("API_KEY"), values.get("VALIDATION_CACHE_DSN")
    missing = [name for name, v in (("API_KEY", api_key), ("VALIDATION_CACHE_DSN", dsn)) if not v]
    if missing:
        raise ConfigError(f"{', '.join(missing)} unset")
    return api_key, dsn


def make_engine(dsn: str) -> AsyncEngine:
    return create_async_engine(
        dsn,
        connect_args={"timeout": DB_CONNECT_TIMEOUT_S, "command_timeout": DB_COMMAND_TIMEOUT_S},
    )


async def _postgres_unready(engine: AsyncEngine) -> str | None:
    """``None`` when Postgres answers, else why not. The class name only:
    ``str(exc)`` can carry the DSN. A bad password reads differently from an
    outage, so a misconfiguration isn't waited out as a restart."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # any failure to connect is "not ready"
        return f"postgres not accepting connections ({type(exc).__name__})"
    return None


async def _validate_twice(client: httpx.AsyncClient, api_key: str) -> list[str] | str:
    """Both calls' request IDs, or the failed step."""
    request_ids: list[str] = []
    for n in (1, 2):
        try:
            resp = await client.post(
                "/api/v2/validate",
                json={"address": ADDRESS, "country": "US"},
                headers={"X-API-Key": api_key},
            )
        except httpx.HTTPError as exc:
            return f"validate #{n} failed ({type(exc).__name__})"
        if resp.status_code != httpx.codes.OK:
            return f"validate #{n} returned HTTP {resp.status_code}"
        request_id = resp.headers.get("x-request-id")
        if not request_id:
            return f"validate #{n} carried no X-Request-ID"
        request_ids.append(request_id)
    return request_ids


async def _audit_rows(
    engine: AsyncEngine, request_ids: list[str], audit_wait_s: float, poll_s: float
) -> dict[str, bool | None] | str:
    """``{request_id: cache_hit}`` for the rows that landed, or the failed step.

    The audit middleware writes through a background queue, after the response.
    """
    deadline = time.monotonic() + audit_wait_s
    while True:
        try:
            async with engine.connect() as conn:
                result = await conn.execute(_AUDIT_ROWS, {"ids": request_ids})
                rows = dict(result.tuples().all())
        except Exception:  # a read failure is "not recovered", never a crash
            return "audit_log unreadable"
        if len(rows) == len(request_ids) or time.monotonic() >= deadline:
            return rows
        await asyncio.sleep(poll_s)


async def check(
    client: httpx.AsyncClient,
    engine: AsyncEngine,
    api_key: str,
    *,
    audit_wait_s: float = AUDIT_WAIT_S,
    poll_s: float = POLL_S,
) -> str | None:
    """``None`` when the database path has recovered, else the failed step."""
    unready = await _postgres_unready(engine)
    if unready is not None:
        return unready
    request_ids = await _validate_twice(client, api_key)
    if isinstance(request_ids, str):
        return request_ids
    rows = await _audit_rows(engine, request_ids, audit_wait_s, poll_s)
    if isinstance(rows, str):
        return rows
    landed = sum(rid in rows for rid in request_ids)
    if landed < len(request_ids):
        return f"audit_log rows missing ({landed} of {len(request_ids)} after {audit_wait_s:g}s)"
    if rows[request_ids[1]] is not True:
        return "repeat was not a cache hit"
    return None


async def run(
    env_file: Path = ENV_FILE,
    *,
    base_url: str = BASE_URL,
    transport: httpx.AsyncBaseTransport | None = None,
    audit_wait_s: float = AUDIT_WAIT_S,
) -> int:
    try:
        api_key, dsn = load_config(env_file)
    except ConfigError as exc:
        print(f"not recovered: config: {exc}")
        return 1
    try:
        engine = make_engine(dsn)
    except Exception as exc:  # the class only: str(exc) can carry the DSN
        print(f"not recovered: config: VALIDATION_CACHE_DSN unusable ({type(exc).__name__})")
        return 1
    try:
        async with httpx.AsyncClient(
            base_url=base_url, transport=transport, timeout=HTTP_TIMEOUT_S
        ) as client:
            async with asyncio.timeout(CHECK_BUDGET_S):
                reason = await check(client, engine, api_key, audit_wait_s=audit_wait_s)
    except TimeoutError:
        reason = f"timed out after {CHECK_BUDGET_S}s"
    finally:
        await engine.dispose()
    if reason is not None:
        print(f"not recovered: {reason}")
        return 1
    print("recovered")
    return 0


def main() -> int:
    return asyncio.run(run())


if __name__ == "__main__":
    sys.exit(main())
