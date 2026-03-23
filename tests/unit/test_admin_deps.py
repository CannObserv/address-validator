"""Unit tests for admin dependency injection."""

import pytest
from fastapi import FastAPI, Request

from address_validator.routers.admin.deps import (
    AdminAuthRequired,
    AdminContext,
    AdminUser,
    DatabaseUnavailable,
    get_admin_context,
    get_admin_user,
)


def _make_request(app: FastAPI, headers: dict | None = None) -> Request:
    """Build a fake Request with the given headers."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/admin/",
        "query_string": b"",
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "app": app,
    }
    return Request(scope)


class TestGetAdminUser:
    def test_returns_admin_user_when_headers_present(self) -> None:
        app = FastAPI()
        req = _make_request(
            app,
            {
                "x-exedev-userid": "u1",
                "x-exedev-email": "a@b.com",
            },
        )
        user = get_admin_user(req)
        assert isinstance(user, AdminUser)
        assert user.user_id == "u1"
        assert user.email == "a@b.com"

    def test_raises_auth_required_when_no_headers(self) -> None:
        app = FastAPI()
        req = _make_request(app, {})
        with pytest.raises(AdminAuthRequired) as exc_info:
            get_admin_user(req)
        assert "/__exe.dev/login" in exc_info.value.redirect_url

    def test_redirect_url_includes_current_path(self) -> None:
        app = FastAPI()
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/admin/audit/",
            "query_string": b"page=2",
            "headers": [],
            "app": app,
        }
        req = Request(scope)
        with pytest.raises(AdminAuthRequired) as exc_info:
            get_admin_user(req)
        url = exc_info.value.redirect_url
        assert "/admin/audit/" in url
        # query string is percent-encoded inside the redirect param
        assert "page%3D2" in url or "page=2" in url


class TestGetAdminContext:
    def test_returns_context_with_user_and_engine(self) -> None:
        app = FastAPI()
        app.state.engine = "fake-engine"
        req = _make_request(
            app,
            {
                "x-exedev-userid": "u1",
                "x-exedev-email": "a@b.com",
            },
        )
        ctx = get_admin_context(req)
        assert isinstance(ctx, AdminContext)
        assert ctx.user.user_id == "u1"
        assert ctx.engine == "fake-engine"
        assert ctx.request is req

    def test_auth_checked_before_engine(self) -> None:
        """Unauthenticated request raises AdminAuthRequired, not DatabaseUnavailable."""
        app = FastAPI()
        app.state.engine = None
        req = _make_request(app, {})
        with pytest.raises(AdminAuthRequired):
            get_admin_context(req)

    def test_raises_database_unavailable_when_no_engine(self) -> None:
        app = FastAPI()
        app.state.engine = None
        req = _make_request(
            app,
            {
                "x-exedev-userid": "u1",
                "x-exedev-email": "a@b.com",
            },
        )
        with pytest.raises(DatabaseUnavailable) as exc_info:
            get_admin_context(req)
        assert exc_info.value.user.user_id == "u1"

    def test_raises_database_unavailable_when_no_state_attr(self) -> None:
        app = FastAPI()
        req = _make_request(
            app,
            {
                "x-exedev-userid": "u1",
                "x-exedev-email": "a@b.com",
            },
        )
        with pytest.raises(DatabaseUnavailable):
            get_admin_context(req)
