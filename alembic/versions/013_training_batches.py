"""Add training_batches + candidate_batch_assignments; extend model_training_candidates.

Revision ID: 013
Revises: 012
Create Date: 2026-04-14

Introduces batch-level lifecycle and many-to-many candidate assignment.
Denormalises endpoint/provider/api_version/failure_reason onto
model_training_candidates so triage context survives audit archival.
Relaxes the candidate status CHECK: drops 'reviewed', adds 'assigned';
existing 'reviewed' rows are migrated to 'new'.

Seeds the pre-existing multi_unit batch as status='deployed'.
"""

revision: str = "013"
down_revision: str = "012"
branch_labels = None
depends_on = None

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE training_batches (
            id TEXT PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            description TEXT NOT NULL,
            targeted_failure_pattern TEXT,
            status TEXT NOT NULL,
            current_step TEXT,
            manifest_path TEXT,
            upstream_pr TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            activated_at TIMESTAMPTZ,
            deployed_at TIMESTAMPTZ,
            closed_at TIMESTAMPTZ,
            CONSTRAINT ck_training_batches_status CHECK (
                status IN ('planned', 'active', 'deployed', 'observing', 'closed')
            ),
            CONSTRAINT ck_training_batches_current_step CHECK (
                current_step IS NULL OR current_step IN (
                    'identifying', 'labeling', 'training', 'testing',
                    'deployed', 'observing', 'contributed'
                )
            )
        )
        """
    )
    op.create_index("ix_training_batches_status", "training_batches", ["status"])

    op.execute(
        """
        CREATE TABLE candidate_batch_assignments (
            raw_address_hash TEXT NOT NULL,
            batch_id TEXT NOT NULL REFERENCES training_batches(id) ON DELETE CASCADE,
            assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            assigned_by TEXT,
            PRIMARY KEY (raw_address_hash, batch_id)
        )
        """
    )
    op.create_index(
        "ix_candidate_batch_assignments_batch",
        "candidate_batch_assignments",
        ["batch_id"],
    )

    op.execute("ALTER TABLE model_training_candidates ADD COLUMN endpoint TEXT")
    op.execute("ALTER TABLE model_training_candidates ADD COLUMN provider TEXT")
    op.execute("ALTER TABLE model_training_candidates ADD COLUMN api_version TEXT")
    op.execute("ALTER TABLE model_training_candidates ADD COLUMN failure_reason TEXT")

    op.execute("UPDATE model_training_candidates SET status = 'new' WHERE status = 'reviewed'")

    op.execute(
        "ALTER TABLE model_training_candidates DROP CONSTRAINT ck_model_training_candidates_status"
    )
    op.execute(
        "ALTER TABLE model_training_candidates "
        "ADD CONSTRAINT ck_model_training_candidates_status "
        "CHECK (status IN ('new', 'assigned', 'labeled', 'rejected'))"
    )

    op.execute(
        """
        INSERT INTO training_batches (
            id, slug, description, targeted_failure_pattern,
            status, current_step, manifest_path,
            created_at, activated_at, deployed_at
        ) VALUES (
            '01KMV1103Q0000000000000000',
            '2026_03_28-multi_unit',
            'Multi-unit designator handling — BLDG + APT/STE/UNIT/ROOM patterns (issue #72)',
            'repeated_label_error',
            'deployed',
            'deployed',
            'training/batches/2026_03_28-multi_unit',
            '2026-03-28T20:09:04.375357+00:00',
            '2026-03-28T20:09:04.375357+00:00',
            '2026-03-28T20:09:04.375357+00:00'
        )
        """
    )


def downgrade() -> None:
    # Note: the 'reviewed' -> 'new' data migration in upgrade() is not reversed
    # here; the two statuses are indistinguishable after the fact. The old CHECK
    # is restored so post-downgrade writes can re-introduce 'reviewed', but
    # historical rows already migrated to 'new' stay 'new'.
    op.execute("DROP TABLE IF EXISTS candidate_batch_assignments")
    op.execute("DROP TABLE IF EXISTS training_batches")

    op.execute(
        "ALTER TABLE model_training_candidates DROP CONSTRAINT ck_model_training_candidates_status"
    )
    op.execute(
        "ALTER TABLE model_training_candidates "
        "ADD CONSTRAINT ck_model_training_candidates_status "
        "CHECK (status IN ('new', 'reviewed', 'labeled', 'rejected'))"
    )
    op.execute("ALTER TABLE model_training_candidates DROP COLUMN failure_reason")
    op.execute("ALTER TABLE model_training_candidates DROP COLUMN api_version")
    op.execute("ALTER TABLE model_training_candidates DROP COLUMN provider")
    op.execute("ALTER TABLE model_training_candidates DROP COLUMN endpoint")
