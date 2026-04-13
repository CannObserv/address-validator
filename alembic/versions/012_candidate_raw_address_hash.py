"""Add raw_address_hash generated column + pgcrypto extension.

Revision ID: 012
Revises: 011
Create Date: 2026-04-13

The admin candidate triage surface needs a stable URL slug per raw_address.
sha256 hex reuses the hashing convention established by cache_provider
(_make_pattern_key). pgcrypto provides sha256() in SQL; the column is
GENERATED STORED so it stays in lock-step with raw_address without app code.
"""

revision: str = "012"
down_revision: str = "011"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(
        "ALTER TABLE model_training_candidates "
        "ADD COLUMN raw_address_hash TEXT "
        "GENERATED ALWAYS AS (encode(sha256(raw_address::bytea), 'hex')) STORED"
    )
    op.execute("ALTER TABLE model_training_candidates ALTER COLUMN raw_address_hash SET NOT NULL")
    op.create_index(
        "ix_model_training_candidates_raw_address_hash",
        "model_training_candidates",
        ["raw_address_hash"],
    )


def downgrade() -> None:
    op.drop_index("ix_model_training_candidates_raw_address_hash")
    op.execute("ALTER TABLE model_training_candidates DROP COLUMN raw_address_hash")
    # Leave pgcrypto installed — harmless and may be used by other future work.
