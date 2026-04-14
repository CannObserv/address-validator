"""Drop 'assigned' from model_training_candidates status CHECK.

Revision ID: 014
Revises: 013
Create Date: 2026-04-14

'assigned' is now a DERIVED rollup computed by joining to
candidate_batch_assignments rather than a stored per-row status.
Existing rows with status='assigned' are reverted to 'new'; their
assignment rows in candidate_batch_assignments are the source of truth
for the derived rollup.
"""

revision: str = "014"
down_revision: str = "013"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.execute("UPDATE model_training_candidates SET status = 'new' WHERE status = 'assigned'")
    op.execute(
        "ALTER TABLE model_training_candidates DROP CONSTRAINT ck_model_training_candidates_status"
    )
    op.execute(
        "ALTER TABLE model_training_candidates "
        "ADD CONSTRAINT ck_model_training_candidates_status "
        "CHECK (status IN ('new', 'labeled', 'rejected'))"
    )


def downgrade() -> None:
    # Restore 'assigned' in the CHECK. Re-derive per-row 'assigned' from
    # candidate_batch_assignments existence so pre-014 rollup logic works.
    op.execute(
        "ALTER TABLE model_training_candidates DROP CONSTRAINT ck_model_training_candidates_status"
    )
    op.execute(
        "ALTER TABLE model_training_candidates "
        "ADD CONSTRAINT ck_model_training_candidates_status "
        "CHECK (status IN ('new', 'assigned', 'labeled', 'rejected'))"
    )
    op.execute(
        "UPDATE model_training_candidates c "
        "SET status = 'assigned' "
        "WHERE c.status = 'new' "
        "  AND EXISTS ("
        "    SELECT 1 FROM candidate_batch_assignments a "
        "    WHERE a.raw_address_hash = c.raw_address_hash"
        "  )"
    )
