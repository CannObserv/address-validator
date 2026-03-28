"""Add parse_type column to audit_log.

Revision ID: 009
Revises: 008
Create Date: 2026-03-28

Records the address parse type (Street Address, Intersection, Ambiguous)
in the audit log for analytics and debugging.
"""

revision: str = "009"
down_revision: str = "008"
branch_labels = None
depends_on = None

import sqlalchemy as sa  # noqa: E402

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.add_column("audit_log", sa.Column("parse_type", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_log", "parse_type")
