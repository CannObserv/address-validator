"""SQLAlchemy Core table definitions for audit tables.

These mirror the schemas created by Alembic migrations 004 and 005.
No ORM / DeclarativeBase — plain Table objects for type-safe query composition.
"""

from __future__ import annotations

import sqlalchemy as sa

metadata = sa.MetaData()

audit_log = sa.Table(
    "audit_log",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
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
)

audit_daily_stats = sa.Table(
    "audit_daily_stats",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("date", sa.Date(), nullable=False),
    sa.Column("endpoint", sa.Text(), nullable=False),
    sa.Column("provider", sa.Text(), nullable=True),
    sa.Column("status_code", sa.SmallInteger(), nullable=False),
    sa.Column("cache_hit", sa.Boolean(), nullable=True),
    sa.Column("request_count", sa.Integer(), nullable=False),
    sa.Column("error_count", sa.Integer(), nullable=False),
    sa.Column("avg_latency_ms", sa.Integer(), nullable=True),
    sa.Column("p95_latency_ms", sa.Integer(), nullable=True),
)
