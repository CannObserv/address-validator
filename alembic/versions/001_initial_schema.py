"""Initial schema — validated_addresses and query_patterns tables.

Revision ID: 001
Revises:
Create Date: 2026-03-19
"""

revision: str = "001"
down_revision: str | None = None
branch_labels = None
depends_on = None

import sqlalchemy as sa  # noqa: E402

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.create_table(
        "validated_addresses",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("canonical_key", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("dpv_match_code", sa.Text(), nullable=True),
        sa.Column("address_line_1", sa.Text(), nullable=True),
        sa.Column("address_line_2", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("region", sa.Text(), nullable=True),
        sa.Column("postal_code", sa.Text(), nullable=True),
        sa.Column("country", sa.Text(), nullable=False),
        sa.Column("validated", sa.Text(), nullable=True),
        sa.Column("components_json", sa.Text(), nullable=True),
        sa.Column("latitude", sa.Double(), nullable=True),
        sa.Column("longitude", sa.Double(), nullable=True),
        sa.Column("warnings_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.Text(), nullable=False),
        sa.Column("validated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_key", name="idx_validated_addresses_canonical_key"),
    )
    op.create_table(
        "query_patterns",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("pattern_key", sa.Text(), nullable=False),
        sa.Column("canonical_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("pattern_key", name="idx_query_patterns_pattern_key"),
        sa.ForeignKeyConstraint(
            ["canonical_key"],
            ["validated_addresses.canonical_key"],
            name="fk_query_patterns_canonical_key",
        ),
    )


def downgrade() -> None:
    op.drop_table("query_patterns")
    op.drop_table("validated_addresses")
