"""Migration 022 relabels cached DPV S/D rows (GH #253).

Before #253 the DPV→status map swapped ``D`` and ``S``, and cache hits serve the
stored ``status`` verbatim, so the fix needs a data migration. It derives the
status from ``dpv_match_code`` rather than swapping the two labels: a row the
fixed code already wrote stays correct, and re-applying it is a no-op. The
downgrade restores the pre-fix labels the reverted code expects.

Alembic's async env calls ``asyncio.run``, so these tests are sync and drive the
DB through ``asyncio.run`` too. The DSN is the one Alembic itself resolves
(``VALIDATION_CACHE_DSN``, defaulted by ``tests/conftest.py``), so the rows land
in whichever database the migrations run against.
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command

if TYPE_CHECKING:
    from collections.abc import Iterator

_PRE_FIX = {"D": "confirmed_bad_secondary", "S": "confirmed_missing_secondary"}
_POST_FIX = {"D": "confirmed_missing_secondary", "S": "confirmed_bad_secondary"}

# (canonical_key, provider, dpv_match_code, status) for rows the migration must
# leave alone: other DPV codes, and Google verdicts with no DPV code.
_UNTOUCHED = [
    ("usps-y", "usps", "Y", "confirmed"),
    ("usps-n", "usps", "N", "not_confirmed"),
    ("usps-undetermined", "usps", None, "undetermined"),
    ("google-verdict", "google", None, "not_found"),
]


def _dsn() -> str:
    return os.environ["VALIDATION_CACHE_DSN"]


def _cfg() -> Config:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", _dsn())
    return cfg


def _secondary_rows(labels: dict[str, str]) -> list[tuple[str, str, str, str]]:
    return [
        (f"{provider}-{dpv.lower()}", provider, dpv, labels[dpv])
        for provider in ("usps", "google")
        for dpv in ("D", "S")
    ]


async def _truncate() -> None:
    engine = create_async_engine(_dsn())
    try:
        async with engine.begin() as conn:
            await conn.execute(sa.text("TRUNCATE validated_addresses, query_patterns CASCADE"))
    finally:
        await engine.dispose()


async def _seed(rows: list[tuple[str, str, str | None, str]]) -> None:
    await _truncate()
    engine = create_async_engine(_dsn())
    try:
        async with engine.begin() as conn:
            await conn.execute(
                sa.text(
                    "INSERT INTO validated_addresses (canonical_key, provider, status,"
                    " dpv_match_code, country, created_at, last_seen_at, validated_at)"
                    " VALUES (:key, :provider, :status, :dpv, 'US', now(), now(), now())"
                ),
                [{"key": k, "provider": p, "dpv": d, "status": s} for k, p, d, s in rows],
            )
    finally:
        await engine.dispose()


async def _statuses() -> dict[str, str]:
    engine = create_async_engine(_dsn())
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                sa.text("SELECT canonical_key, status FROM validated_addresses")
            )
            return {row.canonical_key: row.status for row in result}
    finally:
        await engine.dispose()


def _expected(labels: dict[str, str]) -> dict[str, str]:
    rows = _secondary_rows(labels) + _UNTOUCHED
    return {key: status for key, _, _, status in rows}


@pytest.fixture()
def at_021() -> Iterator[Config]:
    """Hold the DB at revision 021; always leave it at head for other tests."""
    cfg = _cfg()
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "021")
    try:
        yield cfg
    finally:
        command.upgrade(cfg, "head")
        asyncio.run(_truncate())


@pytest.mark.parametrize(
    "labels",
    [_PRE_FIX, _POST_FIX],
    ids=["written-by-pre-fix-code", "already-correct"],
)
def test_upgrade_derives_status_from_dpv_code(at_021: Config, labels: dict[str, str]) -> None:
    asyncio.run(_seed(_secondary_rows(labels) + _UNTOUCHED))

    command.upgrade(at_021, "022")

    assert asyncio.run(_statuses()) == _expected(_POST_FIX)


def test_downgrade_restores_pre_fix_labels(at_021: Config) -> None:
    command.upgrade(at_021, "022")
    asyncio.run(_seed(_secondary_rows(_POST_FIX) + _UNTOUCHED))

    command.downgrade(at_021, "021")

    assert asyncio.run(_statuses()) == _expected(_PRE_FIX)
