#!/usr/bin/env python3
"""Alert when audit_log holds rows past AUDIT_RETENTION_DAYS (#249).

``audit-archive`` failing is reported by its ``OnFailure=`` hook. It *not
running* is not: a disabled timer, a removed unit or a moved path (#109, #228)
fails nothing, and rows past retention, ``raw_input`` PII included, pile up
silently. This daily check reads the oldest row's timestamp and dispatches the
``archive-age`` template through notifier when it is past the archiver's cutoff
plus ``GRACE_DAYS``. It reads ``min(timestamp)`` only, never row content.

Not covered: this check itself not running. That is silence detection, which
waits for notifier's ``co-status`` (notifier#83).

Exit status:
    0  no breach; or a breach dispatched; or a breach with the notifier config
       unset or incomplete (journal only, as ``notify_unit_failure.py`` does)
    1  the check could not run (config, database), or a breach reached no
       channel, so ``unit-failure@`` reports the run and its WARNING lines

The key is ``archive-age:<UTC date>``. Notifier records a dispatch under it even
when every channel fails, so a same-day re-run replays that ``failed`` result,
delivers nothing and exits 1 again; the next UTC day gets a fresh key.

Usage:
    PYTHONPATH=src .venv/bin/python infra/archive_age_check.py

Env vars:
    VALIDATION_CACHE_DSN                PostgreSQL DSN (required)
    AUDIT_RETENTION_DAYS                hot window in days (default: 90), as archive_audit.py
    NOTIFIER_URL                        notifier base URL; unset = journal only
    NOTIFIER_API_KEY                    tenant ``nk_…`` key
    NOTIFIER_ARCHIVE_AGE_TEMPLATE_ID    stored template ULID (``publish_notifier_template.py``)
    NOTIFIER_UNIT_FAILURE_CHANNEL_IDS   comma-separated channel ULIDs, shared with unit-failure
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
from audit_retention import compute_cutoff, parse_retention_days
from journal_logging import configure_logging
from notifier_client import NotifierClient, NotifierError
from notifier_dispatch import ConfigError, dispatch, load_config
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from address_validator.db.tables import audit_log

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger("archive_age_check")

# audit-archive runs daily at 03:00 UTC and archives whole UTC days, so after a
# good run the oldest row is no older than compute_cutoff(now, retention). One
# grace day absorbs a single missed run; the second consecutive miss alerts.
GRACE_DAYS = 1
TEMPLATE_VAR = "NOTIFIER_ARCHIVE_AGE_TEMPLATE_ID"
RUN_BUDGET_SECONDS = 60
DELIVERED = ("succeeded", "partial")


@dataclass(frozen=True)
class AgeReport:
    oldest: datetime | None
    now: datetime
    retention_days: int

    @property
    def threshold(self) -> datetime:
        """Rows strictly before this UTC midnight are a breach."""
        return compute_cutoff(self.now, self.retention_days + GRACE_DAYS)

    @property
    def breached(self) -> bool:
        return self.oldest is not None and self.oldest < self.threshold

    @property
    def age_days(self) -> int:
        return (self.now - self.oldest).days if self.oldest else 0

    @property
    def idempotency_key(self) -> str:
        """One dispatch per UTC day: a re-run replays the day's dispatch, sends nothing.

        That includes a ``failed`` one; see the module docstring.
        """
        return f"archive-age:{self.now.astimezone(UTC).date().isoformat()}"


def _fmt(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def template_variables(report: AgeReport, *, host: str) -> dict[str, str]:
    """The ``archive-age`` template's variables; strings, as unit-failure's are."""
    assert report.oldest is not None
    return {
        "host": host,
        "oldest": _fmt(report.oldest),
        "age_days": str(report.age_days),
        "retention_days": str(report.retention_days),
        "grace_days": str(GRACE_DAYS),
    }


async def oldest_timestamp(engine: AsyncEngine) -> datetime | None:
    """``min(audit_log.timestamp)``, served by ``idx_audit_ts``; None when empty."""
    async with engine.connect() as conn:
        return (await conn.execute(select(func.min(audit_log.c.timestamp)))).scalar_one()


async def _read_oldest(dsn: str) -> datetime | None:
    engine = create_async_engine(dsn)
    try:
        return await oldest_timestamp(engine)
    finally:
        await engine.dispose()


async def _alert(
    report: AgeReport,
    env: Mapping[str, str],
    client_factory: Callable[..., NotifierClient],
) -> int:
    try:
        config = load_config(env, template_var=TEMPLATE_VAR)
    except ConfigError as exc:
        logger.warning("%s; the breach is reported to the journal only", exc)
        return 0
    if config is None:
        logger.info("NOTIFIER_URL unset; the breach is reported to the journal only")
        return 0
    try:
        async with asyncio.timeout(RUN_BUDGET_SECONDS):
            status = await dispatch(
                config,
                subject="archive-age",
                variables=template_variables(report, host=socket.gethostname()),
                idempotency_key=report.idempotency_key,
                metadata={
                    "source": "address-validator",
                    "event": "archive-age",
                    "oldest": _fmt(report.oldest),
                },
                client_factory=client_factory,
            )
    except (NotifierError, httpx.HTTPError, TimeoutError) as exc:
        logger.warning(
            "archive-age dispatch failed (%s: %s)",
            type(exc).__name__,
            getattr(exc, "status_code", None) or exc,
        )
        return 1
    except Exception:  # any failure to alert is reported through unit-failure@
        logger.warning("archive-age dispatch failed unexpectedly", exc_info=True)
        return 1
    if status in DELIVERED:
        return 0
    logger.warning("archive-age dispatch reached no channel (status %s)", status)
    return 1


async def run(
    env: Mapping[str, str],
    *,
    now: datetime | None = None,
    client_factory: Callable[..., NotifierClient] | None = None,
) -> int:
    dsn = env.get("VALIDATION_CACHE_DSN", "").strip()
    if not dsn:
        logger.error("VALIDATION_CACHE_DSN not set")
        return 1
    try:
        retention = parse_retention_days(env)
    except ValueError as exc:
        logger.error("%s", exc)
        return 1
    try:
        oldest = await _read_oldest(dsn)
    except Exception as exc:  # the checker failed: exit 1 so unit-failure@ reports it
        logger.error("could not read audit_log: %s: %s", type(exc).__name__, exc)
        return 1

    report = AgeReport(oldest=oldest, now=now or datetime.now(UTC), retention_days=retention)
    if not report.breached:
        logger.info(
            "oldest audit_log row: %s (%d days old); threshold %s "
            "(AUDIT_RETENTION_DAYS=%d + %d grace); no breach",
            _fmt(oldest) if oldest else "none, table empty",
            report.age_days,
            _fmt(report.threshold),
            retention,
            GRACE_DAYS,
        )
        return 0
    logger.warning(
        "oldest audit_log row %s is %d days old: past AUDIT_RETENTION_DAYS=%d + %d grace "
        "(threshold %s); is audit-archive.timer running?",
        _fmt(oldest),
        report.age_days,
        retention,
        GRACE_DAYS,
        _fmt(report.threshold),
    )
    return await _alert(report, env, client_factory or NotifierClient)


def main() -> int:
    configure_logging()
    return asyncio.run(run(os.environ))


if __name__ == "__main__":
    sys.exit(main())
