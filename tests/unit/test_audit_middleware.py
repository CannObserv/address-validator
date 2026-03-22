"""Tests for the audit logging middleware."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

if TYPE_CHECKING:
    from starlette.testclient import TestClient

from address_validator.middleware.audit import _should_audit

# ULID: 26 Crockford base-32 characters.
_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def test_should_audit_api_routes() -> None:
    assert _should_audit("/api/v1/parse") is True
    assert _should_audit("/api/v1/validate") is True
    assert _should_audit("/api/v1/standardize") is True
    assert _should_audit("/api/v1/health") is True


def test_should_not_audit_admin_routes() -> None:
    assert _should_audit("/admin/") is False
    assert _should_audit("/admin/audit/") is False


def test_should_not_audit_static_routes() -> None:
    assert _should_audit("/static/admin/css/tailwind.css") is False


def test_should_not_audit_docs() -> None:
    assert _should_audit("/") is False
    assert _should_audit("/docs") is False
    assert _should_audit("/redoc") is False
    assert _should_audit("/openapi.json") is False


def test_audit_row_receives_request_id(client: TestClient) -> None:
    """Regression: audit_middleware must run *inside* request_id_middleware.

    If someone reorders the middleware registration in main.py, the audit
    row will receive ``request_id=None`` instead of a valid ULID.  This
    test catches that silently-broken scenario.
    """
    mock_write = AsyncMock()
    original_engine = getattr(client.app.state, "engine", None)
    client.app.state.engine = "fake-engine"  # type: ignore[union-attr]
    try:
        with patch(
            "address_validator.middleware.audit.write_audit_row",
            mock_write,
        ):
            client.post(
                "/api/v1/parse",
                json={"address": "123 Main St, Springfield, IL 62704"},
            )
    finally:
        client.app.state.engine = original_engine

    mock_write.assert_called_once()
    request_id = mock_write.call_args.kwargs["request_id"]
    assert request_id is not None, "request_id was None — middleware ordering is broken"
    assert _ULID_RE.match(request_id), f"request_id {request_id!r} is not a valid ULID"
