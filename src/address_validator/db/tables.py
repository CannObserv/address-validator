"""SQLAlchemy Core table definitions for audit and cache tables.

These mirror the schemas created by Alembic migrations 001-006.
No ORM / DeclarativeBase — plain Table objects for type-safe query composition.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from address_validator.core.validation_status import VALIDATION_STATUSES

metadata = sa.MetaData()

# IN-list for the validated_addresses.status CheckConstraint, derived from the
# single source of truth (core/validation_status.py). Keeps the DB constraint
# in lockstep with the ValidationResult.status Literal; the drift test
# tests/unit/test_validation_status_catalogue.py guards against divergence.
_STATUS_IN_LIST = ", ".join(f"'{s}'" for s in VALIDATION_STATUSES)

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
    sa.Column("parse_type", sa.Text(), nullable=True),
    sa.Column("raw_input", sa.Text(), nullable=True),
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
            f"status IN ({_STATUS_IN_LIST})",
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
    # Composite parse/standardize pipeline version that produced this row (#145).
    # Mismatch against core.pipeline_version.get_pipeline_version() at lookup time
    # → treated as a cache miss and lazily re-validated; NULL (pre-#145 rows not
    # yet backfilled) mismatches everything. Nullable so the migration is instant;
    # scripts/db/backfill_pipeline_version.py stamps existing rows at deploy.
    sa.Column("pipeline_version", sa.Text(), nullable=True),
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
        # NOT NULL since migration 018 — the validation path only writes a row via
        # _store on a successful validation, always with a non-NULL canonical_key.
        nullable=False,
    ),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("raw_input", sa.Text(), nullable=True),
)

# Shared query constants
ERROR_STATUS_MIN = 400
RATE_LIMITED_STATUS = 429

# ---------------------------------------------------------------------------
# Model training candidate collection (migration 008)
# ---------------------------------------------------------------------------

model_training_candidates = sa.Table(
    "model_training_candidates",
    metadata,
    sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
    sa.Column("raw_address", sa.Text(), nullable=False),
    sa.Column("raw_address_hash", sa.Text(), nullable=False),
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
            "status IN ('new', 'labeled', 'rejected')",
            name="ck_model_training_candidates_status",
        ),
        nullable=False,
        server_default=sa.text("'new'"),
    ),
    sa.Column("endpoint", sa.Text(), nullable=True),
    sa.Column("provider", sa.Text(), nullable=True),
    sa.Column("api_version", sa.Text(), nullable=True),
    sa.Column("failure_reason", sa.Text(), nullable=True),
    sa.Column("notes", sa.Text(), nullable=True),
)

# ---------------------------------------------------------------------------
# Training batch lifecycle (migration 013)
# ---------------------------------------------------------------------------

training_batches = sa.Table(
    "training_batches",
    metadata,
    sa.Column("id", sa.Text(), primary_key=True),
    sa.Column("slug", sa.Text(), nullable=False, unique=True),
    sa.Column("description", sa.Text(), nullable=False),
    sa.Column("targeted_failure_pattern", sa.Text(), nullable=True),
    sa.Column(
        "status",
        sa.Text(),
        sa.CheckConstraint(
            "status IN ('planned', 'active', 'deployed', 'observing', 'closed')",
            name="ck_training_batches_status",
        ),
        nullable=False,
    ),
    sa.Column(
        "current_step",
        sa.Text(),
        sa.CheckConstraint(
            "current_step IS NULL OR current_step IN ("
            "'identifying', 'labeling', 'training', 'testing',"
            " 'deployed', 'observing', 'contributed')",
            name="ck_training_batches_current_step",
        ),
        nullable=True,
    ),
    sa.Column("manifest_path", sa.Text(), nullable=True),
    sa.Column("upstream_pr", sa.Text(), nullable=True),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
    sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("deployed_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
)

candidate_batch_assignments = sa.Table(
    "candidate_batch_assignments",
    metadata,
    sa.Column("raw_address_hash", sa.Text(), nullable=False, primary_key=True),
    sa.Column(
        "batch_id",
        sa.Text(),
        sa.ForeignKey("training_batches.id", ondelete="CASCADE"),
        nullable=False,
        primary_key=True,
    ),
    sa.Column(
        "assigned_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    ),
    sa.Column("assigned_by", sa.Text(), nullable=True),
)
