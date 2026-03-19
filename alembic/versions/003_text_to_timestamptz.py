"""Migrate timestamp columns from TEXT to TIMESTAMPTZ.

Revision ID: 003
Revises: 002
Create Date: 2026-03-19

Existing TEXT columns store ISO 8601 strings produced by Python's
``datetime.now(UTC).isoformat()``.  PostgreSQL can cast these to
TIMESTAMPTZ directly via ``::TIMESTAMPTZ``.
"""

revision: str = "003"
down_revision: str = "002"
branch_labels = None
depends_on = None

import sqlalchemy as sa  # noqa: E402

from alembic import op  # noqa: E402


def upgrade() -> None:
    for col in ("created_at", "last_seen_at", "validated_at"):
        op.alter_column(
            "validated_addresses",
            col,
            type_=sa.DateTime(timezone=True),
            postgresql_using=f"{col}::TIMESTAMPTZ",
        )
    op.alter_column(
        "query_patterns",
        "created_at",
        type_=sa.DateTime(timezone=True),
        postgresql_using="created_at::TIMESTAMPTZ",
    )


def downgrade() -> None:
    for col in ("created_at", "last_seen_at", "validated_at"):
        op.alter_column(
            "validated_addresses",
            col,
            type_=sa.Text(),
            postgresql_using=f"{col}::TEXT",
        )
    op.alter_column(
        "query_patterns",
        "created_at",
        type_=sa.Text(),
        postgresql_using="created_at::TEXT",
    )
