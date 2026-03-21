"""Add audit_daily_stats table for pre-aggregated audit rollups.

Revision ID: 005
Revises: 004
Create Date: 2026-03-21
"""

revision: str = "005"
down_revision: str = "004"
branch_labels = None
depends_on = None

import sqlalchemy as sa  # noqa: E402

from alembic import op  # noqa: E402


def upgrade() -> None:
    op.create_table(
        "audit_daily_stats",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=True),
        sa.Column("status_code", sa.SmallInteger(), nullable=False),
        sa.Column("cache_hit", sa.Boolean(), nullable=True),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("avg_latency_ms", sa.Integer(), nullable=True),
        sa.Column("p95_latency_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    # Unique index with COALESCE handles NULLs — PostgreSQL unique constraints
    # treat NULLs as distinct, so ON CONFLICT wouldn't catch duplicates without this.
    op.create_index(
        "uq_daily_stats_dimensions",
        "audit_daily_stats",
        [
            "date",
            "endpoint",
            sa.text("COALESCE(provider, '')"),
            "status_code",
            sa.text("COALESCE(cache_hit, false)"),
        ],
        unique=True,
    )
    op.create_index("idx_daily_stats_date", "audit_daily_stats", [sa.text("date DESC")])


def downgrade() -> None:
    op.drop_index("idx_daily_stats_date", table_name="audit_daily_stats")
    op.drop_index("uq_daily_stats_dimensions", table_name="audit_daily_stats")
    op.drop_table("audit_daily_stats")
