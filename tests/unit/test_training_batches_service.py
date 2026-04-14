"""Tests for the training_batches service: state machine + CRUD helpers."""

from __future__ import annotations

import pytest

from address_validator.services.training_batches import (
    ALLOWED_TRANSITIONS,
    InvalidTransitionError,
    assert_transition_allowed,
)


def test_planned_to_active_allowed() -> None:
    assert_transition_allowed("planned", "active")


def test_active_to_deployed_allowed() -> None:
    assert_transition_allowed("active", "deployed")


def test_planned_to_deployed_rejected() -> None:
    with pytest.raises(InvalidTransitionError):
        assert_transition_allowed("planned", "deployed")


def test_closed_is_terminal_from_anywhere() -> None:
    for src in ("planned", "active", "deployed", "observing"):
        assert_transition_allowed(src, "closed")


def test_closed_has_no_outgoing_transitions() -> None:
    assert "closed" not in ALLOWED_TRANSITIONS or not ALLOWED_TRANSITIONS["closed"]


def test_identity_transition_rejected() -> None:
    with pytest.raises(InvalidTransitionError):
        assert_transition_allowed("active", "active")
