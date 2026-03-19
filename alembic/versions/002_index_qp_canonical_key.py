"""Add index on query_patterns.canonical_key.

Revision ID: 002
Revises: 001
Create Date: 2026-03-19

Without this index PostgreSQL must scan the full query_patterns table when
verifying the FK constraint on validated_addresses deletes.
"""

revision: str = "002"
down_revision: str = "001"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.create_index(
        "idx_qp_canonical_key",
        "query_patterns",
        ["canonical_key"],
    )


def downgrade() -> None:
    op.drop_index("idx_qp_canonical_key", table_name="query_patterns")
