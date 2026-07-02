"""Add pipeline_version column to validated_addresses.

Revision ID: 019
Revises: 018
Create Date: 2026-07-02

Targeted cache invalidation on pipeline-output change (#145). Each cached row is
stamped with the composite parse/standardize pipeline version that produced it
(core/pipeline_version.py); a mismatch at lookup time is treated as a miss and
lazily re-validated, with the #144 TTL sweeper reaping the stale rows.

Nullable text — the column add is instant and existing rows stay NULL. Run
scripts/db/backfill_pipeline_version.py at deploy to stamp existing rows with the
current version; unbackfilled NULL rows mismatch everything and lazily re-validate.
"""

revision: str = "019"
down_revision: str = "018"
branch_labels = None
depends_on = None

import sqlalchemy as sa  # noqa: E402

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.add_column("validated_addresses", sa.Column("pipeline_version", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("validated_addresses", "pipeline_version")
