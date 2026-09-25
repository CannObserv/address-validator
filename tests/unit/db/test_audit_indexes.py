"""Pin the audit_log indexes the admin raw_input filter depends on (GH #149).

``get_audit_rows``' ``raw_input`` filter is a leading-wildcard ILIKE with two
access paths: the ``pg_trgm`` GIN index (migration 020, #179) and the
``raw_input_days`` window over ``idx_audit_ts`` (#152). The window predicate is
covered by the admin query/view tests; this module covers the indexes, which
``test_schema_drift.py`` deliberately leaves out of scope. Dropping either one
passes every other test and silently reintroduces sequential scans.
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from tests.conftest import TEST_CACHE_DSN

_INDEX_QUERY = sa.text(
    """
    SELECT tc.relname AS table_name, a.attname AS column_name,
           am.amname AS access_method, opc.opcname AS opclass, i.indisvalid
    FROM pg_index i
    JOIN pg_class ic ON ic.oid = i.indexrelid
    JOIN pg_class tc ON tc.oid = i.indrelid
    JOIN pg_am am ON am.oid = ic.relam
    JOIN pg_opclass opc ON opc.oid = i.indclass[0]
    JOIN pg_attribute a ON a.attrelid = tc.oid AND a.attnum = i.indkey[0]
    WHERE ic.relname = :name
    """
)


@pytest.fixture(scope="module")
def run_migrations() -> None:
    """Bring the test database to Alembic head (idempotent)."""
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", TEST_CACHE_DSN)
    command.upgrade(cfg, "head")


@pytest.mark.parametrize(
    ("index_name", "column", "access_method", "opclass"),
    [
        ("idx_audit_raw_input_trgm", "raw_input", "gin", "gin_trgm_ops"),
        ("idx_audit_ts", "timestamp", "btree", None),
    ],
)
async def test_audit_log_index_present_and_valid(
    run_migrations: None,
    index_name: str,
    column: str,
    access_method: str,
    opclass: str | None,
) -> None:
    engine = create_async_engine(TEST_CACHE_DSN)
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(_INDEX_QUERY, {"name": index_name})).mappings().first()
    finally:
        await engine.dispose()

    assert row is not None, f"{index_name} missing from migrated schema"
    assert (row["table_name"], row["column_name"]) == ("audit_log", column)
    assert row["access_method"] == access_method
    if opclass is not None:
        assert row["opclass"] == opclass
    assert row["indisvalid"], f"{index_name} is INVALID (failed CONCURRENTLY build?)"
