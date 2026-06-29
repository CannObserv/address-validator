"""Add 'error' to validated_addresses status CHECK constraint.

Revision ID: 015
Revises: 014
Create Date: 2026-06-29

The ``ValidationResult.status`` Literal has long included ``error`` (provider
rejected the input as malformed), but the ``ck_validated_addresses_status``
CHECK constraint omitted it (migration 011), so an ``error`` outcome could
never be cached. This realigns the DB with the single source of truth
(``core/validation_status.py``); see GH #136.
"""

revision: str = "015"
down_revision: str = "014"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.execute("ALTER TABLE validated_addresses DROP CONSTRAINT ck_validated_addresses_status")
    op.execute(
        "ALTER TABLE validated_addresses ADD CONSTRAINT ck_validated_addresses_status "
        "CHECK (status IN ("
        "'confirmed', 'confirmed_missing_secondary', 'confirmed_bad_secondary',"
        " 'not_confirmed', 'not_found', 'invalid', 'unavailable', 'error'"
        "))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE validated_addresses DROP CONSTRAINT ck_validated_addresses_status")
    op.execute(
        "ALTER TABLE validated_addresses ADD CONSTRAINT ck_validated_addresses_status "
        "CHECK (status IN ("
        "'confirmed', 'confirmed_missing_secondary', 'confirmed_bad_secondary',"
        " 'not_confirmed', 'not_found', 'invalid', 'unavailable'"
        "))"
    )
