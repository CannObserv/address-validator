"""Add raw_input to query_patterns; add pattern_key to audit_log.

Revision ID: 007
Revises: 006
Create Date: 2026-03-27

Two nullable columns — no data migration required:
- query_patterns.raw_input  TEXT — original caller input at first cache-entry time
- audit_log.pattern_key     TEXT — soft FK to query_patterns.pattern_key
"""

revision: str = "007"
down_revision: str = "006"
branch_labels = None
depends_on = None

import sqlalchemy as sa  # noqa: E402

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.add_column("query_patterns", sa.Column("raw_input", sa.Text(), nullable=True))
    op.add_column("audit_log", sa.Column("pattern_key", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("audit_log", "pattern_key")
    op.drop_column("query_patterns", "raw_input")
