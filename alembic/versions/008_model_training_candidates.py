"""Add model_training_candidates table.

Revision ID: 008
Revises: 007
Create Date: 2026-03-27

Collects addresses where usaddress required post-parse recovery,
as training candidates for improved CRF models.
"""

revision: str = "008"
down_revision: str = "007"
branch_labels = None
depends_on = None

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.create_table(
        "model_training_candidates",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("raw_address", sa.Text(), nullable=False),
        sa.Column("failure_type", sa.Text(), nullable=False),
        sa.Column("parsed_tokens", JSONB(), nullable=False),
        sa.Column("recovered_components", JSONB(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "status",
            sa.Text(),
            sa.CheckConstraint(
                "status IN ('new', 'reviewed', 'labeled', 'rejected')",
                name="ck_model_training_candidates_status",
            ),
            nullable=False,
            server_default=sa.text("'new'"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("model_training_candidates")
