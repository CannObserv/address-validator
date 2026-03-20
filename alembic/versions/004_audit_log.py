"""Add audit_log table for request tracking.

Revision ID: 004
Revises: 003
Create Date: 2026-03-20
"""

revision: str = "004"
down_revision: str = "003"
branch_labels = None
depends_on = None

import sqlalchemy as sa  # noqa: E402

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("client_ip", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("status_code", sa.SmallInteger(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("validation_status", sa.Text(), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_audit_ts", "audit_log", [sa.text("timestamp DESC")])
    op.create_index("idx_audit_ip", "audit_log", ["client_ip", sa.text("timestamp DESC")])
    op.create_index("idx_audit_endpoint", "audit_log", ["endpoint", sa.text("timestamp DESC")])
    op.create_index(
        "idx_audit_provider",
        "audit_log",
        ["provider", sa.text("timestamp DESC")],
        postgresql_where=sa.text("provider IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_audit_provider", table_name="audit_log")
    op.drop_index("idx_audit_endpoint", table_name="audit_log")
    op.drop_index("idx_audit_ip", table_name="audit_log")
    op.drop_index("idx_audit_ts", table_name="audit_log")
    op.drop_table("audit_log")
