"""Drift test: db/tables.py metadata vs the Alembic-migrated schema (GH #154).

db/tables.py is never used for DDL — the real schema comes exclusively from
Alembic migrations (get_engine() runs ``alembic upgrade head`` at startup) — so
nothing at runtime catches a Table definition diverging from the database. #154
found two such drifts: ``validated_addresses.provider`` declared nullable while
the DB has NOT NULL (migration 006 stated the intent to relax but never ran the
ALTER), and ``model_training_candidates.raw_address_hash`` declared as a plain
writable column while the DB has GENERATED ALWAYS ... STORED (migration 012).

This test migrates the shared test database to head, reflects
``information_schema.columns``, and compares every metadata table column-by-
column: presence (both directions), nullability, generated-ness, identity, and
coarse type. Constraint/index *names* are deliberately out of scope — indexes
are not modeled in db/tables.py at all (style choice; Alembic owns them).
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from alembic.config import Config
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import create_async_engine

from address_validator.db.tables import metadata
from alembic import command
from tests.conftest import TEST_CACHE_DSN

_COLUMNS_QUERY = sa.text(
    """
    SELECT table_name, column_name, is_nullable, is_generated, is_identity,
           column_default, data_type
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name IN :tables
    """
).bindparams(sa.bindparam("tables", expanding=True))


@pytest.fixture(scope="module")
def run_migrations() -> None:
    """Bring the test database to Alembic head (idempotent)."""
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", TEST_CACHE_DSN)
    command.upgrade(cfg, "head")


def _expected_pg_type(coltype: sa.types.TypeEngine) -> str:
    """PostgreSQL type name as information_schema.columns.data_type reports it."""
    return coltype.compile(dialect=postgresql.dialect()).lower()


def _column_drifts(table_name: str, col: sa.Column, db_col: dict) -> list[str]:
    """Mismatch descriptions for one column present in both metadata and DB."""
    drifts: list[str] = []
    where = f"{table_name}.{col.name}"

    db_nullable = db_col["is_nullable"] == "YES"
    if col.nullable != db_nullable:
        drifts.append(f"{where}: nullable metadata={col.nullable} db={db_nullable}")

    db_generated = db_col["is_generated"] == "ALWAYS"
    if (col.computed is not None) != db_generated:
        drifts.append(f"{where}: generated metadata={col.computed is not None} db={db_generated}")

    db_identity = db_col["is_identity"] == "YES"
    if (col.identity is not None) != db_identity:
        drifts.append(f"{where}: identity metadata={col.identity is not None} db={db_identity}")

    expected_type = _expected_pg_type(col.type)
    if db_col["data_type"] != expected_type:
        drifts.append(f"{where}: type metadata={expected_type!r} db={db_col['data_type']!r}")

    # Server-default presence. Identity and generated columns report no
    # column_default in information_schema; skip them.
    if not db_identity and not db_generated:
        meta_default = col.server_default is not None
        db_default = db_col["column_default"] is not None
        if meta_default != db_default:
            drifts.append(f"{where}: server_default metadata={meta_default} db={db_default}")

    return drifts


async def test_tables_metadata_matches_migrated_schema(run_migrations: None) -> None:
    engine = create_async_engine(TEST_CACHE_DSN)
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(_COLUMNS_QUERY, {"tables": sorted(metadata.tables)})
            ).mappings()
            db_columns = {(r["table_name"], r["column_name"]): dict(r) for r in rows}
    finally:
        await engine.dispose()

    drifts: list[str] = []

    db_tables = {t for t, _ in db_columns}
    for table_name in metadata.tables:
        if table_name not in db_tables:
            drifts.append(f"{table_name}: in metadata but absent from migrated schema")

    for table_name, table in metadata.tables.items():
        meta_cols = {c.name for c in table.columns}
        db_cols = {c for t, c in db_columns if t == table_name}
        for missing in sorted(meta_cols - db_cols):
            drifts.append(f"{table_name}.{missing}: in metadata but absent from DB")
        for extra in sorted(db_cols - meta_cols):
            drifts.append(f"{table_name}.{extra}: in DB but absent from metadata")

        for col in table.columns:
            db_col = db_columns.get((table_name, col.name))
            if db_col is not None:
                drifts.extend(_column_drifts(table_name, col, db_col))

    assert not drifts, "db/tables.py drifted from migrated schema:\n" + "\n".join(drifts)
