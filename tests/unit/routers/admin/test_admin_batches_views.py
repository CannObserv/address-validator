"""Route-level tests for /admin/batches/."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from address_validator.services.training_batches import InvalidTransitionError


@pytest.fixture(autouse=True)
def _mock_engine(client: TestClient):
    original = getattr(client.app.state, "engine", None)
    client.app.state.engine = "fake-engine"
    yield
    client.app.state.engine = original


def test_batches_list_requires_auth(client: TestClient) -> None:
    r = client.get("/admin/batches/", follow_redirects=False)
    assert r.status_code in (302, 307)


def test_batches_list_renders(client: TestClient, admin_headers: dict) -> None:
    with patch(
        "address_validator.routers.admin.batches.list_batches",
        new=AsyncMock(return_value=[]),
    ):
        r = client.get("/admin/batches/", headers=admin_headers)
    assert r.status_code == 200
    assert "Training Batches" in r.text


def test_batches_list_status_filter_passes_through(client: TestClient, admin_headers: dict) -> None:
    mock = AsyncMock(return_value=[])
    with patch("address_validator.routers.admin.batches.list_batches", new=mock):
        r = client.get("/admin/batches/?status=planned", headers=admin_headers)
    assert r.status_code == 200
    assert mock.call_args.kwargs["status"] == "planned"


def test_batches_list_invalid_status_is_ignored(client: TestClient, admin_headers: dict) -> None:
    mock = AsyncMock(return_value=[])
    with patch("address_validator.routers.admin.batches.list_batches", new=mock):
        r = client.get("/admin/batches/?status=bogus", headers=admin_headers)
    assert r.status_code == 200
    assert mock.call_args.kwargs["status"] is None


def test_batches_create_redirects(client: TestClient, admin_headers: dict) -> None:
    with patch(
        "address_validator.routers.admin.batches.create_batch",
        new=AsyncMock(return_value="01K..."),
    ):
        r = client.post(
            "/admin/batches/",
            data={"slug": "new-batch", "description": "d", "targeted_failure_pattern": ""},
            headers=admin_headers,
            follow_redirects=False,
        )
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/batches/new-batch"


def test_batches_detail_404_on_unknown_slug(client: TestClient, admin_headers: dict) -> None:
    with patch(
        "address_validator.routers.admin.batches.get_batch_by_slug",
        new=AsyncMock(return_value=None),
    ):
        r = client.get("/admin/batches/no-such-thing", headers=admin_headers)
    assert r.status_code == 404


def test_batches_detail_renders(client: TestClient, admin_headers: dict) -> None:
    batch = {
        "id": "01KMV1103Q0000000000000000",
        "slug": "multi-unit",
        "description": "d",
        "targeted_failure_pattern": "repeated_label_error",
        "status": "deployed",
        "current_step": "deployed",
        "manifest_path": "training/batches/multi-unit",
        "upstream_pr": None,
        "created_at": datetime(2026, 3, 28, tzinfo=UTC),
        "activated_at": datetime(2026, 3, 28, tzinfo=UTC),
        "deployed_at": datetime(2026, 3, 28, tzinfo=UTC),
        "closed_at": None,
    }
    with (
        patch(
            "address_validator.routers.admin.batches.get_batch_by_slug",
            new=AsyncMock(return_value=batch),
        ),
        patch(
            "address_validator.routers.admin.batches.get_batch_candidates",
            new=AsyncMock(return_value=[]),
        ),
    ):
        r = client.get("/admin/batches/multi-unit", headers=admin_headers)
    assert r.status_code == 200
    assert "multi-unit" in r.text


def test_batches_invalid_transition_returns_400(client: TestClient, admin_headers: dict) -> None:
    batch = {
        "id": "01KMV1103Q0000000000000000",
        "slug": "plan1",
        "description": "d",
        "targeted_failure_pattern": None,
        "status": "planned",
        "current_step": None,
        "manifest_path": None,
        "upstream_pr": None,
        "created_at": datetime(2026, 4, 1, tzinfo=UTC),
        "activated_at": None,
        "deployed_at": None,
        "closed_at": None,
    }
    with (
        patch(
            "address_validator.routers.admin.batches.get_batch_by_slug",
            new=AsyncMock(return_value=batch),
        ),
        patch(
            "address_validator.routers.admin.batches.transition_status",
            new=AsyncMock(side_effect=InvalidTransitionError("planned -> deployed")),
        ),
    ):
        r = client.post(
            "/admin/batches/plan1/status",
            data={"status": "deployed"},
            headers=admin_headers,
        )
    assert r.status_code == 400
