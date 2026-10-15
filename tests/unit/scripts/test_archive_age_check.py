"""Tests for the daily audit_log archive-age check (GH #249).

Timestamps are synthetic and ``now`` is fixed. No request reaches a real
notifier: dispatch runs against a fake client, and the one test that drives the
real SDK points it at a closed local port.
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import archive_age_check as aac
import archive_audit
import audit_retention
import httpx
import pytest
from sqlalchemy import text

from tests.conftest import TEST_CACHE_DSN

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

# 06:00 UTC, when the timer fires. Retention 90 + 1 grace day puts the alert
# threshold at 2026-07-16 00:00 UTC: one missed archive run stays under it.
NOW = datetime(2026, 10, 15, 6, 0, tzinfo=UTC)
THRESHOLD = datetime(2026, 7, 16, tzinfo=UTC)
BREACHING = THRESHOLD - timedelta(minutes=1)
ONE_MISSED_RUN = THRESHOLD

DB_ENV = {"VALIDATION_CACHE_DSN": TEST_CACHE_DSN, "AUDIT_RETENTION_DAYS": "90"}
NOTIFIER_ENV = {
    "NOTIFIER_URL": "http://notifier.test:9000",
    "NOTIFIER_API_KEY": "nk_test",
    "NOTIFIER_ARCHIVE_AGE_TEMPLATE_ID": "01ARCHIVEAGE000000000000000",
    "NOTIFIER_UNIT_FAILURE_CHANNEL_IDS": "01CHANNELA0000000000000000,01CHANNELB0000000000000000",
}
TEMPLATE = json.loads((Path(aac.__file__).parent / "notifier" / "archive-age.json").read_text())


class FakeClient:
    """Stands in for NotifierClient; records calls, returns canned responses."""

    def __init__(self, *, environment="production", status="succeeded", raises=None, **kwargs):
        self.init_kwargs = kwargs
        self.environment = environment
        self.status = status
        self.raises = raises
        self.dispatched: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def health(self):
        if self.raises:
            raise self.raises
        return {"status": "ok", "environment": self.environment}

    async def dispatch(self, **kwargs):
        self.dispatched.append(kwargs)
        delivered = SimpleNamespace(status="succeeded" if self.status != "failed" else "failed")
        return SimpleNamespace(id="01DISPATCH", status=self.status, attempts=[delivered] * 2)


def _factory(**fake_kwargs):
    made: list[FakeClient] = []

    def factory(**kwargs):
        client = FakeClient(**fake_kwargs, **kwargs)
        made.append(client)
        return client

    return factory, made


def _no_client(**kwargs):
    raise AssertionError("no notifier client expected")


async def _seed(engine: AsyncEngine, *stamps: datetime) -> None:
    async with engine.begin() as conn:
        for ts in stamps:
            await conn.execute(
                text(
                    "INSERT INTO audit_log (timestamp, client_ip, method, endpoint, status_code)"
                    " VALUES (:ts, '192.0.2.1', 'POST', '/api/v1/validate', 200)"
                ),
                {"ts": ts},
            )


# --- breach rule -------------------------------------------------------------


@pytest.mark.parametrize(
    ("oldest", "breached"),
    [
        (None, False),
        (NOW, False),
        (ONE_MISSED_RUN, False),
        (BREACHING, True),
        (THRESHOLD - timedelta(days=30), True),
    ],
    ids=["empty", "fresh", "one-missed-run", "just-past", "long-past"],
)
def test_breach_rule(oldest, breached):
    report = aac.AgeReport(oldest=oldest, now=NOW, retention_days=90)
    assert report.breached is breached


def test_breach_threshold_shares_the_archiver_cutoff():
    """Same UTC-midnight floor as archive_audit, one grace day further back."""
    report = aac.AgeReport(oldest=None, now=NOW, retention_days=90)
    assert report.threshold == THRESHOLD
    assert aac.GRACE_DAYS == 1


def test_report_fields():
    report = aac.AgeReport(oldest=BREACHING, now=NOW, retention_days=90)
    assert report.age_days == 91
    assert report.idempotency_key == "archive-age:2026-10-15"


def test_idempotency_key_is_the_utc_date():
    """One dispatch per UTC day, whatever the host's local zone (#249)."""
    evening_pst = datetime(2026, 10, 16, 2, 0, tzinfo=UTC).astimezone(timezone(timedelta(hours=-8)))
    assert evening_pst.date().isoformat() == "2026-10-15"
    report = aac.AgeReport(oldest=None, now=evening_pst, retention_days=90)
    assert report.idempotency_key == "archive-age:2026-10-16"


# --- oldest row --------------------------------------------------------------


async def test_oldest_timestamp_empty_table(db: AsyncEngine):
    assert await aac.oldest_timestamp(db) is None


async def test_oldest_timestamp_is_the_minimum(db: AsyncEngine):
    await _seed(db, NOW - timedelta(days=3), BREACHING, NOW)
    assert await aac.oldest_timestamp(db) == BREACHING


# --- run: breach, no breach, notifier states ---------------------------------


async def test_breach_dispatches_once_with_a_daily_key(db: AsyncEngine, caplog):
    await _seed(db, BREACHING, NOW)
    factory, made = _factory()
    with caplog.at_level(logging.INFO):
        code = await aac.run({**DB_ENV, **NOTIFIER_ENV}, now=NOW, client_factory=factory)
    assert code == 0
    (client,) = made
    (call,) = client.dispatched
    assert call["template_id"] == "01ARCHIVEAGE000000000000000"
    assert call["channel_ids"] == ["01CHANNELA0000000000000000", "01CHANNELB0000000000000000"]
    assert call["idempotency_key"] == "archive-age:2026-10-15"
    assert call["variables"] == {
        "host": socket.gethostname(),
        "oldest": "2026-07-15 23:59 UTC",
        "age_days": "91",
        "retention_days": "90",
        "grace_days": "1",
    }
    assert call["metadata"]["event"] == "archive-age"
    breach_lines = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("past AUDIT_RETENTION_DAYS=90" in r.getMessage() for r in breach_lines)


async def test_no_breach_sends_nothing(db: AsyncEngine, caplog):
    await _seed(db, ONE_MISSED_RUN, NOW)
    with caplog.at_level(logging.INFO, logger="archive_age_check"):
        code = await aac.run({**DB_ENV, **NOTIFIER_ENV}, now=NOW, client_factory=_no_client)
    assert code == 0
    assert "no breach" in caplog.text
    assert "2026-07-16 00:00 UTC" in caplog.text


async def test_empty_audit_log_is_no_breach(db: AsyncEngine, caplog):
    with caplog.at_level(logging.INFO, logger="archive_age_check"):
        code = await aac.run({**DB_ENV, **NOTIFIER_ENV}, now=NOW, client_factory=_no_client)
    assert code == 0
    assert "no breach" in caplog.text


async def test_breach_with_notifier_unset_is_journal_only(db: AsyncEngine, caplog):
    await _seed(db, BREACHING)
    with caplog.at_level(logging.INFO, logger="archive_age_check"):
        code = await aac.run(DB_ENV, now=NOW, client_factory=_no_client)
    assert code == 0
    assert "journal only" in caplog.text


async def test_breach_with_incomplete_notifier_config_is_journal_only(db: AsyncEngine, caplog):
    await _seed(db, BREACHING)
    env = {**DB_ENV, **NOTIFIER_ENV, "NOTIFIER_ARCHIVE_AGE_TEMPLATE_ID": ""}
    with caplog.at_level(logging.WARNING, logger="archive_age_check"):
        code = await aac.run(env, now=NOW, client_factory=_no_client)
    assert code == 0
    assert "NOTIFIER_ARCHIVE_AGE_TEMPLATE_ID" in caplog.text


@pytest.mark.parametrize(
    "fake_kwargs",
    [
        {"raises": httpx.ConnectError("unreachable")},
        {"raises": RuntimeError("unexpected")},
        {"environment": "development"},
        {"status": "failed"},
    ],
    ids=["unreachable", "unexpected", "non-production", "delivered-nowhere"],
)
async def test_breach_that_reaches_no_channel_exits_1(db: AsyncEngine, caplog, fake_kwargs):
    """The breach WARNING stands, and unit-failure@ reports the run (#249)."""
    await _seed(db, BREACHING)
    factory, _ = _factory(**fake_kwargs)
    with caplog.at_level(logging.WARNING):
        code = await aac.run({**DB_ENV, **NOTIFIER_ENV}, now=NOW, client_factory=factory)
    assert code == 1
    assert "past AUDIT_RETENTION_DAYS" in caplog.text


async def test_partial_delivery_exits_0(db: AsyncEngine):
    await _seed(db, BREACHING)
    factory, made = _factory(status="partial")
    code = await aac.run({**DB_ENV, **NOTIFIER_ENV}, now=NOW, client_factory=factory)
    assert code == 0
    assert len(made[0].dispatched) == 1


async def test_stalled_notifier_is_bounded(db: AsyncEngine, monkeypatch, caplog):
    class Stalling(FakeClient):
        async def health(self):
            await asyncio.sleep(5)

    await _seed(db, BREACHING)
    monkeypatch.setattr(aac, "RUN_BUDGET_SECONDS", 0.05)
    with caplog.at_level(logging.WARNING, logger="archive_age_check"):
        code = await aac.run({**DB_ENV, **NOTIFIER_ENV}, now=NOW, client_factory=Stalling)
    assert code == 1
    assert "TimeoutError" in caplog.text


async def test_real_sdk_unreachable_notifier(db: AsyncEngine, caplog):
    """The real client against a closed port: the SDK's own error type is caught."""
    await _seed(db, BREACHING)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**DB_ENV, **NOTIFIER_ENV, "NOTIFIER_URL": f"http://127.0.0.1:{port}"}
    with caplog.at_level(logging.WARNING, logger="archive_age_check"):
        code = await aac.run(env, now=NOW)
    assert code == 1
    assert "ConnectError" in caplog.text


# --- config errors: the checker failed --------------------------------------


async def test_missing_dsn_exits_1(caplog):
    with caplog.at_level(logging.ERROR, logger="archive_age_check"):
        assert await aac.run({}, now=NOW, client_factory=_no_client) == 1
    assert "VALIDATION_CACHE_DSN" in caplog.text


async def test_bad_retention_exits_1(caplog):
    env = {**DB_ENV, "AUDIT_RETENTION_DAYS": "ninety"}
    with caplog.at_level(logging.ERROR, logger="archive_age_check"):
        assert await aac.run(env, now=NOW, client_factory=_no_client) == 1
    assert "'ninety'" in caplog.text


async def test_unreachable_database_exits_1(caplog):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {"VALIDATION_CACHE_DSN": f"postgresql+asyncpg://u:p@127.0.0.1:{port}/x"}
    with caplog.at_level(logging.ERROR, logger="archive_age_check"):
        assert await aac.run(env, now=NOW, client_factory=_no_client) == 1
    assert "could not read audit_log" in caplog.text


# --- template ↔ check drift --------------------------------------------------


def test_check_sends_every_variable_the_template_requires():
    """A required variable the check never sends is a 422 — and a lost alert."""
    report = aac.AgeReport(oldest=BREACHING, now=NOW, retention_days=90)
    sent = aac.template_variables(report, host="h")
    schema = TEMPLATE["variables_schema"]
    assert set(schema["required"]) == set(sent)
    assert set(schema["required"]) <= set(TEMPLATE["sample_variables"])
    assert all(isinstance(v, str) for v in sent.values())


def test_run_budget_fits_inside_unit_timeout():
    unit = (Path(aac.__file__).parent / "archive-age-check.service").read_text()
    (line,) = [ln for ln in unit.splitlines() if ln.startswith("TimeoutStartSec=")]
    # DB connect + query (asyncpg's 60s connect timeout) + the notifier budget.
    assert int(line.split("=")[1]) > 60 + aac.RUN_BUDGET_SECONDS


def test_retention_default_is_the_archivers():
    """One default for both: the check must not alert on a window the archiver never used."""
    assert audit_retention.retention_days({}) == 90
    assert archive_audit.retention_days is audit_retention.retention_days
    assert archive_audit.compute_cutoff is aac.compute_cutoff
