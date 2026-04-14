"""Verify training_batches + candidate_batch_assignments Table defs match DB schema."""

from address_validator.db.tables import (
    candidate_batch_assignments,
    metadata,
    model_training_candidates,
    training_batches,
)


def test_training_batches_columns() -> None:
    cols = {c.name for c in training_batches.columns}
    assert cols == {
        "id",
        "slug",
        "description",
        "targeted_failure_pattern",
        "status",
        "current_step",
        "manifest_path",
        "upstream_pr",
        "created_at",
        "activated_at",
        "deployed_at",
        "closed_at",
    }


def test_candidate_batch_assignments_columns() -> None:
    cols = {c.name for c in candidate_batch_assignments.columns}
    assert cols == {"raw_address_hash", "batch_id", "assigned_at", "assigned_by"}
    pk_cols = {c.name for c in candidate_batch_assignments.primary_key}
    assert pk_cols == {"raw_address_hash", "batch_id"}


def test_model_training_candidates_has_context_columns() -> None:
    cols = {c.name for c in model_training_candidates.columns}
    assert {"endpoint", "provider", "api_version", "failure_reason"} <= cols


def test_tables_registered_on_metadata() -> None:
    names = set(metadata.tables)
    assert {"training_batches", "candidate_batch_assignments"} <= names
