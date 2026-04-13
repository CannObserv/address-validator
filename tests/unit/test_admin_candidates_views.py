"""Integration tests for admin candidate triage views."""

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


def test_candidates_list_requires_auth(client: TestClient) -> None:
    r = client.get("/admin/candidates/", follow_redirects=False)
    # Unauth raises AdminAuthRequired -> handled as a redirect by main.py
    assert r.status_code in (302, 307)


def test_candidates_list_renders(client: TestClient, admin_headers: dict) -> None:
    with (
        patch(
            "address_validator.routers.admin.candidates.get_candidate_groups",
            new=AsyncMock(return_value=([], 0)),
        ),
    ):
        r = client.get("/admin/candidates/", headers=admin_headers)
    assert r.status_code == 200
    assert "Candidates" in r.text


def test_candidates_list_filters_pass_through(client: TestClient, admin_headers: dict) -> None:
    mock = AsyncMock(return_value=([], 0))
    with patch(
        "address_validator.routers.admin.candidates.get_candidate_groups",
        new=mock,
    ):
        r = client.get(
            "/admin/candidates/?status=reviewed&failure_type=repeated_label_error&since=7d",
            headers=admin_headers,
        )
    assert r.status_code == 200
    kwargs = mock.call_args.kwargs
    assert kwargs["status"] == "reviewed"
    assert kwargs["failure_type"] == "repeated_label_error"
    assert kwargs["since"] is not None


def test_candidates_detail_404_on_unknown_hash(client: TestClient, admin_headers: dict) -> None:
    with patch(
        "address_validator.routers.admin.candidates.get_candidate_group",
        new=AsyncMock(return_value=None),
    ):
        r = client.get("/admin/candidates/deadbeef" + "0" * 56, headers=admin_headers)
    assert r.status_code == 404


def test_candidates_detail_renders(client: TestClient, admin_headers: dict) -> None:
    group_mock = AsyncMock(
        return_value={
            "raw_address": "123 MAIN ST STE 1, SMP - 2 SEATTLE WA 98101",
            "raw_hash": "a" * 64,
            "rollup_status": "new",
            "failure_types": ["repeated_label_error"],
            "count": 3,
            "first_seen": None,
            "last_seen": None,
            "notes": None,
        }
    )
    subs_mock = AsyncMock(
        return_value=[
            {
                "id": 1,
                "raw_address": "x",
                "failure_type": "repeated_label_error",
                "parsed_tokens": [["STE", "OccupancyIdentifier"]],
                "recovered_components": None,
                "created_at": None,
                "status": "new",
            },
        ]
    )
    with (
        patch("address_validator.routers.admin.candidates.get_candidate_group", new=group_mock),
        patch(
            "address_validator.routers.admin.candidates.get_candidate_submissions",
            new=subs_mock,
        ),
    ):
        r = client.get("/admin/candidates/" + "a" * 64, headers=admin_headers)
    assert r.status_code == 200
    assert "123 MAIN ST STE 1" in r.text
