"""Integration tests for admin dashboard views."""

from starlette.testclient import TestClient


def test_admin_dashboard_requires_auth(client_no_auth: TestClient) -> None:
    """Unauthenticated request to /admin/ redirects to login."""
    response = client_no_auth.get("/admin/", follow_redirects=False)
    assert response.status_code == 302
    assert "/__exe.dev/login" in response.headers["location"]


def test_admin_dashboard_authenticated(client: TestClient, admin_headers: dict) -> None:
    """Authenticated request returns 200 with dashboard HTML."""
    response = client.get("/admin/", headers=admin_headers)
    assert response.status_code == 200
    assert "Dashboard" in response.text


def test_admin_audit_requires_auth(client_no_auth: TestClient) -> None:
    response = client_no_auth.get("/admin/audit/", follow_redirects=False)
    assert response.status_code == 302


def test_admin_endpoint_detail_404_for_unknown(client: TestClient, admin_headers: dict) -> None:
    response = client.get("/admin/endpoints/unknown", headers=admin_headers)
    assert response.status_code == 404


def test_admin_provider_detail_404_for_unknown(client: TestClient, admin_headers: dict) -> None:
    response = client.get("/admin/providers/unknown", headers=admin_headers)
    assert response.status_code == 404
