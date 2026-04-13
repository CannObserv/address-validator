"""Tests for admin HTMX partial endpoints."""

from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient


@pytest.fixture(autouse=True)
def _mock_engine(client: TestClient):
    """Set a fake engine on app.state so admin routes don't 503."""
    original = getattr(client.app.state, "engine", None)  # type: ignore[union-attr]
    client.app.state.engine = "fake-engine"  # type: ignore[union-attr]
    yield
    client.app.state.engine = original  # type: ignore[union-attr]


def test_candidates_badge_requires_auth(client: TestClient) -> None:
    r = client.get("/admin/_partials/candidates_badge", follow_redirects=False)
    assert r.status_code in (302, 307)


def test_candidates_badge_renders_count(client: TestClient, admin_headers: dict) -> None:
    with patch(
        "address_validator.routers.admin.partials.get_new_candidate_count",
        new=AsyncMock(return_value=7),
    ):
        r = client.get("/admin/_partials/candidates_badge", headers=admin_headers)
    assert r.status_code == 200
    assert "7" in r.text


def test_candidates_badge_zero_renders_empty_span(client: TestClient, admin_headers: dict) -> None:
    with patch(
        "address_validator.routers.admin.partials.get_new_candidate_count",
        new=AsyncMock(return_value=0),
    ):
        r = client.get("/admin/_partials/candidates_badge", headers=admin_headers)
    assert r.status_code == 200
    # Empty fallback span, no number
    assert "<span></span>" in r.text
