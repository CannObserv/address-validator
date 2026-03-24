"""Tests for the audit logging middleware."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from starlette.testclient import TestClient

from fastapi import FastAPI
from starlette.testclient import TestClient as TC

from address_validator.middleware.audit import AuditMiddleware, _should_audit
from address_validator.middleware.request_id import RequestIdMiddleware
from address_validator.services.audit import set_audit_context

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


def test_audit_row_receives_validation_context_vars() -> None:
    """Regression: ContextVars set during the endpoint must propagate to audit.

    With BaseHTTPMiddleware, call_next() ran the endpoint in a child asyncio
    task.  ContextVars set in the child (by CachingProvider.set_audit_context)
    were invisible to the parent task that writes the audit row.  Pure ASGI
    middleware fixes this by running everything in one task.

    Uses a minimal FastAPI app to isolate the middleware behaviour from the
    full application stack.
    """
    mini = FastAPI()
    mini.add_middleware(AuditMiddleware)
    mini.add_middleware(RequestIdMiddleware)
    mini.state.engine = MagicMock()  # non-None so audit writes

    @mini.get("/api/v1/fake")
    async def _fake_endpoint() -> dict[str, str]:
        set_audit_context(provider="usps", validation_status="confirmed", cache_hit=False)
        return {"ok": "true"}

    mock_write = AsyncMock()
    with patch("address_validator.middleware.audit.write_audit_row", mock_write):
        tc = TC(mini)
        tc.get("/api/v1/fake")

    mock_write.assert_called_once()
    kwargs = mock_write.call_args.kwargs
    assert kwargs["provider"] == "usps", (
        f"provider should be 'usps', got {kwargs['provider']!r} — ContextVar not propagated"
    )
    assert kwargs["validation_status"] == "confirmed", (
        f"validation_status should be 'confirmed', got {kwargs['validation_status']!r}"
    )
    assert kwargs["cache_hit"] is False, f"cache_hit should be False, got {kwargs['cache_hit']!r}"
