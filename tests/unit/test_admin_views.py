"""Integration tests for admin dashboard views."""

from unittest.mock import patch

import pytest
from starlette.testclient import TestClient


@pytest.fixture(autouse=True)
def _mock_engine(client: TestClient):
    """Set a fake engine on app.state so admin routes don't 503.

    Tests that explicitly need engine=None override this by setting it themselves.
    Query functions are patched to return empty results since there is no real DB.
    """
    original = getattr(client.app.state, "engine", None)  # type: ignore[union-attr]
    client.app.state.engine = "fake-engine"  # type: ignore[union-attr]

    async def _empty_stats(_engine):
        return {}

    async def _empty_sparkline(_engine):
        return {}

    async def _empty_rows(_engine, **_kw):
        return [], 0

    async def _empty_endpoint_stats(_engine, _name):
        return {}

    async def _empty_provider_stats(_engine, _name):
        return {}

    with (
        patch(
            "address_validator.routers.admin.dashboard.get_dashboard_stats",
            side_effect=_empty_stats,
        ),
        patch(
            "address_validator.routers.admin.dashboard.get_sparkline_data",
            side_effect=_empty_sparkline,
        ),
        patch(
            "address_validator.routers.admin.audit_views.get_audit_rows",
            side_effect=_empty_rows,
        ),
        patch(
            "address_validator.routers.admin.endpoints.get_audit_rows",
            side_effect=_empty_rows,
        ),
        patch(
            "address_validator.routers.admin.endpoints.get_endpoint_stats",
            side_effect=_empty_endpoint_stats,
        ),
        patch(
            "address_validator.routers.admin.providers.get_audit_rows",
            side_effect=_empty_rows,
        ),
        patch(
            "address_validator.routers.admin.providers.get_provider_stats",
            side_effect=_empty_provider_stats,
        ),
    ):
        yield
    client.app.state.engine = original  # type: ignore[union-attr]


def test_admin_dashboard_503_when_no_engine(client: TestClient, admin_headers: dict) -> None:
    """Authenticated request returns 503 when database engine is None."""
    client.app.state.engine = None  # type: ignore[union-attr]
    response = client.get("/admin/", headers=admin_headers)
    assert response.status_code == 503
    assert "Database Not Available" in response.text


def test_admin_audit_503_when_no_engine(client: TestClient, admin_headers: dict) -> None:
    """Audit view returns 503 when database engine is None."""
    client.app.state.engine = None  # type: ignore[union-attr]
    response = client.get("/admin/audit/", headers=admin_headers)
    assert response.status_code == 503


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


# --- hx-boost navigation must return full pages, not partials (#45) ---


def test_audit_htmx_boosted_returns_full_page(client: TestClient, admin_headers: dict) -> None:
    """Boosted nav to /admin/audit/ must return full layout, not rows partial."""
    headers = {**admin_headers, "HX-Request": "true", "HX-Boosted": "true"}
    response = client.get("/admin/audit/", headers=headers)
    assert response.status_code == 200
    assert "<nav" in response.text


def test_audit_htmx_nonboosted_returns_partial(client: TestClient, admin_headers: dict) -> None:
    """In-page HTMX request to /admin/audit/ returns rows partial."""
    headers = {**admin_headers, "HX-Request": "true"}
    response = client.get("/admin/audit/", headers=headers)
    assert response.status_code == 200
    assert "<nav" not in response.text


def test_audit_clear_link_overrides_hx_target(client: TestClient, admin_headers: dict) -> None:
    """Clear link must set hx-target=body to avoid inheriting the form's #audit-rows target."""
    response = client.get("/admin/audit/", headers=admin_headers)
    html = response.text
    # Find the Clear link — it should target body, not inherit #audit-rows from the form
    assert 'hx-target="body"' in html
    # Verify the form still targets the partial swap container
    assert 'hx-target="#audit-rows"' in html


def test_endpoint_htmx_nonboosted_returns_partial(client: TestClient, admin_headers: dict) -> None:
    """In-page HTMX request to /admin/endpoints/parse returns rows partial."""
    headers = {**admin_headers, "HX-Request": "true"}
    response = client.get("/admin/endpoints/parse", headers=headers)
    assert response.status_code == 200
    assert "<nav" not in response.text


def test_provider_htmx_nonboosted_returns_partial(client: TestClient, admin_headers: dict) -> None:
    """In-page HTMX request to /admin/providers/usps returns rows partial."""
    headers = {**admin_headers, "HX-Request": "true"}
    response = client.get("/admin/providers/usps", headers=headers)
    assert response.status_code == 200
    assert "<nav" not in response.text


def test_endpoint_htmx_boosted_returns_full_page(client: TestClient, admin_headers: dict) -> None:
    headers = {**admin_headers, "HX-Request": "true", "HX-Boosted": "true"}
    response = client.get("/admin/endpoints/parse", headers=headers)
    assert response.status_code == 200
    assert "<nav" in response.text


def test_provider_htmx_boosted_returns_full_page(client: TestClient, admin_headers: dict) -> None:
    headers = {**admin_headers, "HX-Request": "true", "HX-Boosted": "true"}
    response = client.get("/admin/providers/usps", headers=headers)
    assert response.status_code == 200
    assert "<nav" in response.text


def test_admin_dashboard_has_brand_elements(client: TestClient, admin_headers: dict) -> None:
    """Dashboard contains Cannabis Observer branding."""
    response = client.get("/admin/", headers=admin_headers)
    html = response.text
    assert "cannabis_observer-icon-square.svg" in html
    assert "Cannabis Observer" in html
    assert "Address Validator" in html


def test_admin_dashboard_has_dark_mode_toggle(client: TestClient, admin_headers: dict) -> None:
    """Dashboard contains a dark mode toggle button."""
    response = client.get("/admin/", headers=admin_headers)
    assert 'id="theme-toggle"' in response.text


def test_admin_dashboard_has_hamburger_nav(client: TestClient, admin_headers: dict) -> None:
    """Dashboard contains hamburger nav elements for mobile."""
    response = client.get("/admin/", headers=admin_headers)
    html = response.text
    assert 'id="nav-toggle"' in html
    assert 'id="mobile-nav"' in html


def test_admin_dashboard_has_sparklines(client: TestClient, admin_headers: dict) -> None:
    """Dashboard HTML contains sparkline SVG elements."""
    response = client.get("/admin/", headers=admin_headers)
    html = response.text
    # Exactly 5 sparklines should render (even if "No data").
    assert html.count('role="img"') == 5
    # Spot-check a specific sparkline label.
    assert "All requests over 30 days" in html
