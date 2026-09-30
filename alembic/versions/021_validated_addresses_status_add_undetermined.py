"""Add 'undetermined' to validated_addresses status CHECK constraint.

Revision ID: 021
Revises: 020
Create Date: 2026-09-30

A provider that answers HTTP 200 without a DPV determination (USPS blank
``DPVConfirmation``) now reports ``undetermined`` instead of ``unavailable``,
and that answer is cached (GH #250). Widens the constraint to match the single
source of truth (``core/validation_status.py``).
"""

revision: str = "021"
down_revision: str = "020"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402

_WITH_UNDETERMINED = (
    "'confirmed', 'confirmed_missing_secondary', 'confirmed_bad_secondary',"
    " 'not_confirmed', 'not_found', 'invalid', 'undetermined', 'unavailable', 'error'"
)
_WITHOUT_UNDETERMINED = (
    "'confirmed', 'confirmed_missing_secondary', 'confirmed_bad_secondary',"
    " 'not_confirmed', 'not_found', 'invalid', 'unavailable', 'error'"
)


def upgrade() -> None:
    op.execute("ALTER TABLE validated_addresses DROP CONSTRAINT ck_validated_addresses_status")
    op.execute(
        "ALTER TABLE validated_addresses ADD CONSTRAINT ck_validated_addresses_status "
        f"CHECK (status IN ({_WITH_UNDETERMINED}))"
    )


def downgrade() -> None:
    # Cached undetermined rows cannot satisfy the narrower constraint; drop them,
    # pointers first (fk_query_patterns_canonical_key has no ON DELETE CASCADE).
    op.execute(
        "DELETE FROM query_patterns WHERE canonical_key IN "
        "(SELECT canonical_key FROM validated_addresses WHERE status = 'undetermined')"
    )
    op.execute("DELETE FROM validated_addresses WHERE status = 'undetermined'")
    op.execute("ALTER TABLE validated_addresses DROP CONSTRAINT ck_validated_addresses_status")
    op.execute(
        "ALTER TABLE validated_addresses ADD CONSTRAINT ck_validated_addresses_status "
        f"CHECK (status IN ({_WITHOUT_UNDETERMINED}))"
    )
