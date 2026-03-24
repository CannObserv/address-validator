"""Clean up cache table schema: provider NULLs, JSONB, status CHECK.

Revision ID: 006
Revises: 005
Create Date: 2026-03-24

Three changes:
1. provider — convert empty strings to NULL
2. components_json, warnings_json — Text to JSONB
3. status — add CHECK constraint
"""

revision: str = "006"
down_revision: str = "005"
branch_labels = None
depends_on = None

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402

from alembic import op  # noqa: E402


def upgrade() -> None:
    # 1. provider: empty string → NULL
    op.execute("UPDATE validated_addresses SET provider = NULL WHERE provider = ''")

    # 2. Text → JSONB
    op.alter_column(
        "validated_addresses",
        "components_json",
        type_=JSONB(),
        postgresql_using="components_json::JSONB",
    )
    # Drop text default before type change — PG can't auto-cast '[]' to JSONB
    op.alter_column(
        "validated_addresses",
        "warnings_json",
        server_default=None,
    )
    op.alter_column(
        "validated_addresses",
        "warnings_json",
        type_=JSONB(),
        postgresql_using="warnings_json::JSONB",
    )
    op.alter_column(
        "validated_addresses",
        "warnings_json",
        server_default=sa.text("'[]'::jsonb"),
    )

    # 3. CHECK constraint on status
    _valid = (
        "'confirmed', 'confirmed_missing_secondary',"
        " 'confirmed_bad_secondary', 'not_confirmed', 'unavailable'"
    )
    op.create_check_constraint(
        "ck_validated_addresses_status",
        "validated_addresses",
        f"status IN ({_valid})",
    )


def downgrade() -> None:
    # 3. Drop CHECK constraint
    op.drop_constraint("ck_validated_addresses_status", "validated_addresses", type_="check")

    # 2. JSONB → Text
    op.alter_column(
        "validated_addresses",
        "warnings_json",
        server_default=None,
    )
    op.alter_column(
        "validated_addresses",
        "warnings_json",
        type_=sa.Text(),
        postgresql_using="warnings_json::TEXT",
    )
    op.alter_column(
        "validated_addresses",
        "warnings_json",
        server_default="[]",
    )
    op.alter_column(
        "validated_addresses",
        "components_json",
        type_=sa.Text(),
        postgresql_using="components_json::TEXT",
    )

    # 1. No reverse for NULL conversion (data cleanup is one-way)
