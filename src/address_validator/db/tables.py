"""SQLAlchemy Core table definitions for audit and cache tables.

These mirror the schemas created by Alembic migrations 001-006.
No ORM / DeclarativeBase — plain Table objects for type-safe query composition.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

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
    sa.Column("pattern_key", sa.Text(), nullable=True),
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

# ---------------------------------------------------------------------------
# Cache tables (migrations 001 + 002 + 003 + 006)
# ---------------------------------------------------------------------------

validated_addresses = sa.Table(
    "validated_addresses",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("canonical_key", sa.Text(), nullable=False, unique=True),
    sa.Column("provider", sa.Text(), nullable=True),
    sa.Column(
        "status",
        sa.Text(),
        sa.CheckConstraint(
            "status IN ("
            "'confirmed', 'confirmed_missing_secondary', 'confirmed_bad_secondary',"
            " 'not_confirmed', 'unavailable')",
            name="ck_validated_addresses_status",
        ),
        nullable=False,
    ),
    sa.Column("dpv_match_code", sa.Text(), nullable=True),
    sa.Column("address_line_1", sa.Text(), nullable=True),
    sa.Column("address_line_2", sa.Text(), nullable=True),
    sa.Column("city", sa.Text(), nullable=True),
    sa.Column("region", sa.Text(), nullable=True),
    sa.Column("postal_code", sa.Text(), nullable=True),
    sa.Column("country", sa.Text(), nullable=False),
    sa.Column("validated", sa.Text(), nullable=True),
    sa.Column("components_json", JSONB(), nullable=True),
    sa.Column("latitude", sa.Double(), nullable=True),
    sa.Column("longitude", sa.Double(), nullable=True),
    sa.Column("warnings_json", JSONB(), nullable=False, server_default=sa.text("'[]'")),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("validated_at", sa.DateTime(timezone=True), nullable=False),
)

query_patterns = sa.Table(
    "query_patterns",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("pattern_key", sa.Text(), nullable=False, unique=True),
    sa.Column(
        "canonical_key",
        sa.Text(),
        sa.ForeignKey(
            "validated_addresses.canonical_key",
            name="fk_query_patterns_canonical_key",
        ),
        nullable=False,
    ),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("raw_input", sa.Text(), nullable=True),
)

# Shared query constants
ERROR_STATUS_MIN = 400
