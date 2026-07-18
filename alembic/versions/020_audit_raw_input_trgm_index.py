"""Add trigram GIN index on audit_log.raw_input for admin substring search.

Revision ID: 020
Revises: 019
Create Date: 2026-07-18

The admin audit view filters with `raw_input ILIKE '%<q>%'` (GH #179) — a
leading-wildcard pattern no btree can serve, forcing a sequential scan of
`audit_log` (the hottest write table) on every search. A `pg_trgm` GIN index
serves arbitrary-position ILIKE patterns directly; the query needs no change.

`pg_trgm` is a trusted extension since PostgreSQL 13, so CREATE EXTENSION
requires only CREATE on the database (which the app user has), not superuser.
"""

revision: str = "020"
down_revision: str = "019"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX idx_audit_raw_input_trgm ON audit_log USING gin (raw_input gin_trgm_ops)"
    )


def downgrade() -> None:
    op.drop_index("idx_audit_raw_input_trgm", table_name="audit_log")
    # The extension is left installed — other objects may depend on it.
