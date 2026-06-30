"""Add index on validated_addresses.validated_at.

Revision ID: 016
Revises: 015
Create Date: 2026-06-30

The TTL sweeper (`infra/sweep_cache.py`, GH #144) selects and deletes rows by
`validated_at < cutoff` on every daily run, and the `--dry-run` count does the
same. Without this index PostgreSQL must sequentially scan the whole
`validated_addresses` table each sweep. A plain btree suffices because the
sweeper compares the bare column (not a COALESCE expression).
"""

revision: str = "016"
down_revision: str = "015"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.create_index(
        "idx_validated_addresses_validated_at",
        "validated_addresses",
        ["validated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_validated_addresses_validated_at",
        table_name="validated_addresses",
    )
