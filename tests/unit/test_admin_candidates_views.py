"""Integration tests for admin candidate triage views."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from address_validator.routers.admin.candidates import _parse_since


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
            "/admin/candidates/?status=assigned&failure_type=repeated_label_error&since=7d",
            headers=admin_headers,
        )
    assert r.status_code == 200
    kwargs = mock.call_args.kwargs
    assert kwargs["status"] == "assigned"
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
            "batch_slugs": [],
        }
    )
    subs_mock = AsyncMock(
        return_value=[
            {
                "id": 1,
                "raw_address": "x",
                "failure_type": "repeated_label_error",
                "failure_reason": None,
                "parsed_tokens": [["STE", "OccupancyIdentifier"]],
                "recovered_components": None,
                "created_at": None,
                "status": "new",
                "endpoint": None,
                "api_version": None,
                "provider": None,
            },
        ]
    )
    with (
        patch("address_validator.routers.admin.candidates.get_candidate_group", new=group_mock),
        patch(
            "address_validator.routers.admin.candidates.get_candidate_submissions",
            new=subs_mock,
        ),
        patch(
            "address_validator.routers.admin.candidates.get_assignable_batches",
            new=AsyncMock(return_value=[]),
        ),
    ):
        r = client.get("/admin/candidates/" + "a" * 64, headers=admin_headers)
    assert r.status_code == 200
    assert "123 MAIN ST STE 1" in r.text


def test_candidates_status_post_updates_and_renders_partial(
    client: TestClient, admin_headers: dict
) -> None:
    update_mock = AsyncMock(return_value=2)
    group_mock = AsyncMock(
        return_value={
            "raw_address": "x",
            "raw_hash": "a" * 64,
            "rollup_status": "rejected",
            "failure_types": [],
            "count": 2,
            "first_seen": None,
            "last_seen": None,
            "notes": None,
        }
    )
    with (
        patch(
            "address_validator.routers.admin.candidates.update_candidate_status",
            new=update_mock,
        ),
        patch("address_validator.routers.admin.candidates.get_candidate_group", new=group_mock),
    ):
        r = client.post(
            "/admin/candidates/" + "a" * 64 + "/status",
            headers={**admin_headers, "HX-Request": "true"},
            data={"status": "rejected"},
        )
    assert r.status_code == 200
    update_mock.assert_awaited_once()
    kwargs = update_mock.call_args.kwargs
    assert kwargs["status"] == "rejected"
    assert kwargs["raw_hash"] == "a" * 64


def test_candidates_status_post_rejects_invalid_status(
    client: TestClient, admin_headers: dict
) -> None:
    r = client.post(
        "/admin/candidates/" + "a" * 64 + "/status",
        headers={**admin_headers, "HX-Request": "true"},
        data={"status": "labeled"},
    )
    assert r.status_code == 400


def test_candidates_notes_post_round_trip(client: TestClient, admin_headers: dict) -> None:
    update_mock = AsyncMock(return_value=1)
    group_mock = AsyncMock(
        return_value={
            "raw_address": "x",
            "raw_hash": "a" * 64,
            "rollup_status": "new",
            "failure_types": [],
            "count": 1,
            "first_seen": None,
            "last_seen": None,
            "notes": "chained STE",
        }
    )
    with (
        patch(
            "address_validator.routers.admin.candidates.update_candidate_notes",
            new=update_mock,
        ),
        patch("address_validator.routers.admin.candidates.get_candidate_group", new=group_mock),
    ):
        r = client.post(
            "/admin/candidates/" + "a" * 64 + "/notes",
            headers={**admin_headers, "HX-Request": "true"},
            data={"notes": "chained STE"},
        )
    assert r.status_code == 200
    assert "chained STE" in r.text
    update_mock.assert_awaited_once()
    assert update_mock.call_args.kwargs["notes"] == "chained STE"


def test_candidates_status_post_404_on_unknown_hash(
    client: TestClient, admin_headers: dict
) -> None:
    """POST /status for a hash that resolves to no group must 404, not silently no-op."""
    with (
        patch(
            "address_validator.routers.admin.candidates.update_candidate_status",
            new=AsyncMock(return_value=0),
        ),
        patch(
            "address_validator.routers.admin.candidates.get_candidate_group",
            new=AsyncMock(return_value=None),
        ),
    ):
        r = client.post(
            "/admin/candidates/" + "a" * 64 + "/status",
            headers={**admin_headers, "HX-Request": "true"},
            data={"status": "rejected"},
        )
    assert r.status_code == 404


def test_candidates_notes_post_404_on_unknown_hash(client: TestClient, admin_headers: dict) -> None:
    with (
        patch(
            "address_validator.routers.admin.candidates.update_candidate_notes",
            new=AsyncMock(return_value=0),
        ),
        patch(
            "address_validator.routers.admin.candidates.get_candidate_group",
            new=AsyncMock(return_value=None),
        ),
    ):
        r = client.post(
            "/admin/candidates/" + "a" * 64 + "/notes",
            headers={**admin_headers, "HX-Request": "true"},
            data={"notes": "anything"},
        )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# _parse_since helper — querystring parsing
# ---------------------------------------------------------------------------


def test_parse_since_relative_days() -> None:
    result = _parse_since("7d")
    assert result is not None
    delta = datetime.now(UTC) - result
    assert timedelta(days=6, hours=23) < delta < timedelta(days=7, hours=1)


def test_parse_since_relative_hours() -> None:
    result = _parse_since("12h")
    assert result is not None
    delta = datetime.now(UTC) - result
    assert timedelta(hours=11, minutes=59) < delta < timedelta(hours=12, minutes=1)


def test_parse_since_iso_date() -> None:
    result = _parse_since("2026-01-15")
    assert result is not None
    assert result.year == 2026
    assert result.month == 1
    assert result.day == 15
    assert result.tzinfo is not None


def test_parse_since_all_returns_none() -> None:
    assert _parse_since("all") is None
    assert _parse_since(None) is None
    assert _parse_since("") is None


def test_parse_since_invalid_returns_none() -> None:
    assert _parse_since("garbage") is None
    assert _parse_since("xd") is None  # int conversion fails
    assert _parse_since("not-a-date") is None


def test_candidates_assign_batch_redirects(client: TestClient, admin_headers: dict) -> None:
    with patch(
        "address_validator.routers.admin.candidates.assign_candidates",
        new=AsyncMock(return_value=1),
    ):
        r = client.post(
            "/admin/candidates/" + "a" * 64 + "/batches",
            data={"batch_id": "01KMV1103Q0000000000000000"},
            headers=admin_headers,
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/candidates/" + "a" * 64


def test_candidates_unassign_batch_404_on_unknown_slug(
    client: TestClient, admin_headers: dict
) -> None:
    with patch(
        "address_validator.routers.admin.candidates.get_batch_by_slug",
        new=AsyncMock(return_value=None),
    ):
        r = client.post(
            "/admin/candidates/" + "a" * 64 + "/batches/no-such/unassign",
            headers=admin_headers,
        )
    assert r.status_code == 404
