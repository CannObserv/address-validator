"""Verify the db fixture truncates all relevant tables between tests.

These two tests MUST run in order (pytest executes them top-to-bottom within
a module). Test A inserts into model_training_candidates; Test B asserts the
table is empty — which only holds if the db fixture truncates that table
before each test.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def test_a_insert_into_training_candidates(db: AsyncEngine) -> None:
    """Insert a row so the next test can verify it was cleaned up."""
    async with db.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO model_training_candidates"
                " (raw_address, endpoint, api_version, failure_type,"
                "  parsed_tokens, recovered_components)"
                " VALUES (:addr, :ep, :v, :ft,"
                "  cast(:pt as jsonb), cast(:rc as jsonb))"
            ),
            {
                "addr": "123 Main St, Springfield, IL 62701",
                "ep": "/api/v1/parse",
                "v": "1",
                "ft": "parse_failure",
                "pt": "{}",
                "rc": "{}",
            },
        )
    async with db.connect() as conn:
        result = await conn.execute(text("SELECT count(*) FROM model_training_candidates"))
        assert result.scalar() == 1, "row should exist within this test"


async def test_b_training_candidates_empty_after_fixture_truncate(
    db: AsyncEngine,
) -> None:
    """db fixture must truncate model_training_candidates before this test.

    This test fails if the row inserted by test_a survives across the fixture
    boundary, i.e. if model_training_candidates is missing from the TRUNCATE.
    """
    async with db.connect() as conn:
        result = await conn.execute(text("SELECT count(*) FROM model_training_candidates"))
        count = result.scalar()
    assert count == 0, (
        f"model_training_candidates has {count} row(s) left over from a "
        "previous test — the db fixture must include this table in its TRUNCATE"
    )
