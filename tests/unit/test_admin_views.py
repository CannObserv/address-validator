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
    # All 5 sparklines should render (even if "No data").
    assert html.count('role="img"') >= 5
    # Spot-check one aria-label.
    assert "aria-label=" in html
