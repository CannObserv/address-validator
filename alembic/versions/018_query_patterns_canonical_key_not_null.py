"""Make query_patterns.canonical_key NOT NULL.

Revision ID: 018
Revises: 017
Create Date: 2026-07-01

Hardening follow-up to #150 (#151). After #150 the validation path only writes a
query_patterns row via _store on a successful validation, always with a non-NULL
canonical_key; the one-off cleanup drained all legacy NULL rows from prod. This
enforces "no path writes NULL" at the DB level so the defensive NULL-as-miss guard
in _lookup can be retired.

Pre-flight: any residual NULL row would fail the ALTER. Guard by deleting them
first — safe, nothing reads a NULL-canonical_key row (see #150).
"""

revision: str = "018"
down_revision: str = "017"
branch_labels = None
depends_on = None

import sqlalchemy as sa  # noqa: E402

from alembic import op  # noqa: E402


def upgrade() -> None:
    # Defensive: drain any residual legacy NULL rows before the constraint bites.
    op.execute("DELETE FROM query_patterns WHERE canonical_key IS NULL")
    op.alter_column("query_patterns", "canonical_key", existing_type=sa.Text(), nullable=False)


def downgrade() -> None:
    op.alter_column("query_patterns", "canonical_key", existing_type=sa.Text(), nullable=True)
