"""Add raw_input column to audit_log.

Revision ID: 017
Revises: 016
Create Date: 2026-06-30

Denormalizes the submitted address text onto audit_log at write time (#147) so it
survives the full audit retention window independent of cache TTL / sweeps. Until
now raw_input lived only on query_patterns, and the #144 TTL sweeper deleting those
rows blanked raw_input in the admin audit view at 30 days while the audit row lived
for 90. Nullable text — historical rows stay NULL until backfilled.
"""

revision: str = "017"
down_revision: str = "016"
branch_labels = None
depends_on = None

import sqlalchemy as sa  # noqa: E402

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.add_column("audit_log", sa.Column("raw_input", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_log", "raw_input")
