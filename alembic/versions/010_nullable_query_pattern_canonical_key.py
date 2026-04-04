"""Allow query_patterns.canonical_key to be NULL.

Revision ID: 010
Revises: 009
Create Date: 2026-04-04

Before this migration canonical_key was NOT NULL, so query_patterns rows could
only be created after a successful provider call.  This meant rate-limited (429)
requests never got a query_patterns row, leaving audit_log.pattern_key NULL and
the raw_input column blank in the admin audit view.

With canonical_key nullable the cache layer can register a query_patterns row
(with raw_input) before calling the inner provider.  _store() back-fills
canonical_key via ON CONFLICT DO UPDATE when the provider later succeeds.

Downgrade note: will fail if any rows have canonical_key IS NULL at the time of
downgrade.  Clean up those rows first if you need to roll back.
"""

revision: str = "010"
down_revision: str = "009"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.alter_column("query_patterns", "canonical_key", nullable=True)


def downgrade() -> None:
    op.alter_column("query_patterns", "canonical_key", nullable=False)
