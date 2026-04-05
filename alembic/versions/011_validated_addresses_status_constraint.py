"""Add not_found and invalid to validated_addresses status CHECK constraint.

Revision ID: 011
Revises: 010
Create Date: 2026-04-05
"""

from alembic import op

revision: str = "011"
down_revision: str = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE validated_addresses DROP CONSTRAINT ck_validated_addresses_status")
    op.execute(
        "ALTER TABLE validated_addresses ADD CONSTRAINT ck_validated_addresses_status "
        "CHECK (status IN ("
        "'confirmed', 'confirmed_missing_secondary', 'confirmed_bad_secondary',"
        " 'not_confirmed', 'not_found', 'invalid', 'unavailable'"
        "))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE validated_addresses DROP CONSTRAINT ck_validated_addresses_status")
    op.execute(
        "ALTER TABLE validated_addresses ADD CONSTRAINT ck_validated_addresses_status "
        "CHECK (status IN ("
        "'confirmed', 'confirmed_missing_secondary', 'confirmed_bad_secondary',"
        " 'not_confirmed', 'unavailable'"
        "))"
    )
