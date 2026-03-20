"""Tests for admin authentication dependency."""

from starlette.requests import Request as StarletteRequest
from starlette.responses import RedirectResponse

from address_validator.routers.admin.deps import AdminUser, get_admin_user


def test_get_admin_user_with_headers() -> None:
    """Authenticated request returns AdminUser."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/admin/",
        "query_string": b"",
        "headers": [
            (b"x-exedev-userid", b"user123"),
            (b"x-exedev-email", b"admin@example.com"),
        ],
    }
    request = StarletteRequest(scope)
    result = get_admin_user(request)
    assert isinstance(result, AdminUser)
    assert result.user_id == "user123"
    assert result.email == "admin@example.com"


def test_get_admin_user_missing_headers_redirects() -> None:
    """Unauthenticated request returns RedirectResponse."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/admin/",
        "query_string": b"",
        "headers": [],
    }
    request = StarletteRequest(scope)
    result = get_admin_user(request)
    assert isinstance(result, RedirectResponse)
    assert "/__exe.dev/login" in result.headers["location"]
    assert "redirect=" in result.headers["location"]
