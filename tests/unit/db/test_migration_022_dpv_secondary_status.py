"""Migration 022 relabels cached DPV S/D rows (GH #253).

Before #253 the DPV→status map swapped ``D`` and ``S``, and cache hits serve the
stored ``status`` verbatim, so the fix needs a data migration. It derives the
status from ``dpv_match_code`` rather than swapping the two labels: a row the
fixed code already wrote stays correct, and re-applying it is a no-op. The
downgrade restores the pre-fix labels the reverted code expects.

022 changes data only, so these tests run its SQL against the head schema
instead of moving the database's revision: walking down to 021 would execute
every later migration's downgrade, and the test DB is shared across worktrees.
The revision chain itself is exercised by every ``upgrade head`` fixture. The
DSN is the one Alembic resolves (``VALIDATION_CACHE_DSN``, defaulted by
``tests/conftest.py``), so the rows land in the database the migrations ran on.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import command

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_MIGRATION = ScriptDirectory.from_config(Config("alembic.ini")).get_revision("022").module

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

_TRUNCATE = sa.text("TRUNCATE validated_addresses, query_patterns CASCADE")
_SELECT = sa.text("SELECT canonical_key, status FROM validated_addresses")
_INSERT = sa.text(
    "INSERT INTO validated_addresses (canonical_key, provider, status,"
    " dpv_match_code, country, created_at, last_seen_at, validated_at)"
    " VALUES (:key, :provider, :status, :dpv, 'US', now(), now(), now())"
)


def _dsn() -> str:
    return os.environ["VALIDATION_CACHE_DSN"]


def _secondary_rows(labels: dict[str, str]) -> list[tuple[str, str, str, str]]:
    return [
        (f"{provider}-{dpv.lower()}", provider, dpv, labels[dpv])
        for provider in ("usps", "google")
        for dpv in ("D", "S")
    ]


def _expected(labels: dict[str, str]) -> dict[str, str]:
    rows = _secondary_rows(labels) + _UNTOUCHED
    return {key: status for key, _, _, status in rows}


@pytest.fixture(scope="module")
def run_migrations() -> None:
    """Bring the test database to Alembic head (idempotent)."""
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", _dsn())
    command.upgrade(cfg, "head")


@pytest.fixture()
async def engine(run_migrations: None) -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(_dsn())
    async with eng.begin() as conn:
        await conn.execute(_TRUNCATE)
    yield eng
    async with eng.begin() as conn:
        await conn.execute(_TRUNCATE)
    await eng.dispose()


async def _seed(engine: AsyncEngine, labels: dict[str, str]) -> None:
    rows = _secondary_rows(labels) + _UNTOUCHED
    async with engine.begin() as conn:
        await conn.execute(
            _INSERT, [{"key": k, "provider": p, "dpv": d, "status": s} for k, p, d, s in rows]
        )


async def _run(engine: AsyncEngine, sql: str) -> dict[str, str]:
    """Execute *sql*, then return every row's status keyed by canonical_key."""
    async with engine.begin() as conn:
        await conn.execute(sa.text(sql))
        result = await conn.execute(_SELECT)
        return {row.canonical_key: row.status for row in result}


@pytest.mark.parametrize(
    "labels",
    [_PRE_FIX, _POST_FIX],
    ids=["written-by-pre-fix-code", "already-correct"],
)
async def test_upgrade_derives_status_from_dpv_code(
    engine: AsyncEngine, labels: dict[str, str]
) -> None:
    await _seed(engine, labels)

    assert await _run(engine, _MIGRATION._UPGRADE) == _expected(_POST_FIX)


async def test_downgrade_restores_pre_fix_labels(engine: AsyncEngine) -> None:
    await _seed(engine, _POST_FIX)

    assert await _run(engine, _MIGRATION._DOWNGRADE) == _expected(_PRE_FIX)
