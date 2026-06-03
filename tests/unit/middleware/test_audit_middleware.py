"""Tests for the audit logging middleware."""

from __future__ import annotations

import asyncio
import logging
import re
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.responses import StreamingResponse
from starlette.testclient import TestClient

from address_validator.db.tables import model_training_candidates as mtc
from address_validator.main import app
from address_validator.middleware.audit import (
    AuditMiddleware,
    _check_validate_invariants,
    _should_audit,
)
from address_validator.middleware.request_id import RequestIdMiddleware
from address_validator.services.audit import set_audit_context
from tests.conftest import TEST_API_KEY

# ULID: 26 Crockford base-32 characters.
_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def test_should_audit_api_routes() -> None:
    assert _should_audit("/api/v2/parse") is True
    assert _should_audit("/api/v2/validate") is True
    assert _should_audit("/api/v2/standardize") is True
    assert _should_audit("/api/v2/health") is True


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
                "/api/v2/parse",
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

    @mini.get("/api/v2/fake")
    async def _fake_endpoint() -> dict[str, str]:
        set_audit_context(provider="usps", validation_status="confirmed", cache_hit=False)
        return {"ok": "true"}

    mock_write = AsyncMock()
    with patch("address_validator.middleware.audit.write_audit_row", mock_write):
        tc = TestClient(mini)
        tc.get("/api/v2/fake")

    mock_write.assert_called_once()
    kwargs = mock_write.call_args.kwargs
    assert kwargs["provider"] == "usps", (
        f"provider should be 'usps', got {kwargs['provider']!r} — ContextVar not propagated"
    )
    assert kwargs["validation_status"] == "confirmed", (
        f"validation_status should be 'confirmed', got {kwargs['validation_status']!r}"
    )
    assert kwargs["cache_hit"] is False, f"cache_hit should be False, got {kwargs['cache_hit']!r}"


# ---------------------------------------------------------------------------
# _check_validate_invariants unit tests
# ---------------------------------------------------------------------------


def test_invariants_pass_when_all_fields_present() -> None:
    assert _check_validate_invariants("/api/v2/validate", 200, "usps", "confirmed", True) is True


def test_invariants_fail_on_null_provider() -> None:
    assert _check_validate_invariants("/api/v2/validate", 200, None, "confirmed", False) is False


def test_invariants_fail_on_null_validation_status() -> None:
    assert _check_validate_invariants("/api/v2/validate", 200, "usps", None, False) is False


def test_invariants_fail_on_null_cache_hit() -> None:
    assert _check_validate_invariants("/api/v2/validate", 200, "usps", "confirmed", None) is False


def test_invariants_skip_non_2xx() -> None:
    """Non-2xx status codes are not checked — NULL fields are expected for 422, 500, etc."""
    assert _check_validate_invariants("/api/v2/validate", 422, None, None, None) is True


def test_invariants_skip_non_validate_endpoint() -> None:
    """Non-validate endpoints are not checked even if all fields are NULL."""
    assert _check_validate_invariants("/api/v2/parse", 200, None, None, None) is True


def test_invariants_apply_to_v2_validate() -> None:
    """v2/validate 2xx with all fields present passes."""
    assert _check_validate_invariants("/api/v2/validate", 200, "google", "confirmed", False) is True


def test_invariants_fail_on_v2_validate_null_fields() -> None:
    """v2/validate 2xx with NULL audit fields fails."""
    assert _check_validate_invariants("/api/v2/validate", 200, None, None, None) is False


def test_invariants_violation_sets_error_detail(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Integration: audit row gets error_detail='audit_invariant_violated' on violation."""
    mini = FastAPI()
    mini.add_middleware(AuditMiddleware)
    mini.add_middleware(RequestIdMiddleware)
    mini.state.engine = MagicMock()

    @mini.post("/api/v2/validate")
    async def _fake_validate() -> dict[str, str]:
        # Simulate broken ContextVar propagation — no set_audit_context call
        return {"ok": "true"}

    mock_write = AsyncMock()
    with (
        patch("address_validator.middleware.audit.write_audit_row", mock_write),
        caplog.at_level(logging.WARNING, logger="address_validator.middleware.audit"),
    ):
        tc = TestClient(mini)
        tc.post("/api/v2/validate")

    mock_write.assert_called_once()
    kwargs = mock_write.call_args.kwargs
    assert kwargs["error_detail"] == "audit_invariant_violated"
    assert any("audit_invariant_violated" in r.message for r in caplog.records)


def test_invariants_no_override_when_fields_present() -> None:
    """When all audit fields are set, error_detail is not overridden."""
    mini = FastAPI()
    mini.add_middleware(AuditMiddleware)
    mini.add_middleware(RequestIdMiddleware)
    mini.state.engine = MagicMock()

    @mini.post("/api/v2/validate")
    async def _fake_validate() -> dict[str, str]:
        set_audit_context(provider="usps", validation_status="confirmed", cache_hit=False)
        return {"ok": "true"}

    mock_write = AsyncMock()
    with patch("address_validator.middleware.audit.write_audit_row", mock_write):
        tc = TestClient(mini)
        tc.post("/api/v2/validate")

    mock_write.assert_called_once()
    kwargs = mock_write.call_args.kwargs
    assert kwargs["error_detail"] is None


# ---------------------------------------------------------------------------
# Unhandled-exception coverage (issue #116)
# ---------------------------------------------------------------------------


def test_audit_writes_row_when_endpoint_raises() -> None:
    """Audit row must be written even when the inner app raises (issue #116)."""
    mini = FastAPI()
    mini.add_middleware(AuditMiddleware)
    mini.add_middleware(RequestIdMiddleware)
    mini.state.engine = MagicMock()

    @mini.get("/api/v2/boom")
    async def _boom() -> dict[str, str]:
        raise RuntimeError("kaboom")

    mock_write = AsyncMock()
    with patch("address_validator.middleware.audit.write_audit_row", mock_write):
        tc = TestClient(mini, raise_server_exceptions=False)
        resp = tc.get("/api/v2/boom")

    assert resp.status_code == 500
    mock_write.assert_called_once()
    kwargs = mock_write.call_args.kwargs
    assert kwargs["status_code"] == 500
    assert kwargs["error_detail"] == "unhandled_exception:RuntimeError"


def test_audit_reraises_unhandled_exception() -> None:
    """ServerErrorMiddleware must still see the exception so it can produce a 500."""
    mini = FastAPI()
    mini.add_middleware(AuditMiddleware)
    mini.add_middleware(RequestIdMiddleware)
    mini.state.engine = MagicMock()

    @mini.get("/api/v2/boom")
    async def _boom() -> dict[str, str]:
        raise RuntimeError("kaboom")

    mock_write = AsyncMock()
    with (
        patch("address_validator.middleware.audit.write_audit_row", mock_write),
        pytest.raises(RuntimeError, match="kaboom"),
    ):
        tc = TestClient(mini, raise_server_exceptions=True)
        tc.get("/api/v2/boom")

    mock_write.assert_called_once()


def test_audit_uses_exception_label_over_internal_error() -> None:
    """When the inner app raises, error_detail must be the exception class name,
    not the generic `internal_error` mapped from status 500."""
    mini = FastAPI()
    mini.add_middleware(AuditMiddleware)
    mini.add_middleware(RequestIdMiddleware)
    mini.state.engine = MagicMock()

    @mini.post("/api/v2/validate")
    async def _fake_validate() -> dict[str, str]:
        raise ValueError("kaboom")

    mock_write = AsyncMock()
    with patch("address_validator.middleware.audit.write_audit_row", mock_write):
        tc = TestClient(mini, raise_server_exceptions=False)
        tc.post("/api/v2/validate")

    mock_write.assert_called_once()
    kwargs = mock_write.call_args.kwargs
    assert kwargs["error_detail"] == "unhandled_exception:ValueError"


def test_audit_writes_row_on_cancelled_error() -> None:
    """asyncio.CancelledError is a BaseException (not Exception) in 3.8+;
    AuditMiddleware must still produce a labeled audit row and re-raise.

    Regression guard for the BaseException widening — if this widening is
    reverted to `except Exception`, no row is written for cancelled requests
    and the audit log silently drops them (re-opens issue #116 for that path).

    NOTE: TestClient runs the ASGI app via an anyio portal, which translates
    a propagating `asyncio.CancelledError` into `concurrent.futures.CancelledError`
    at the `.result()` boundary. The middleware itself sees the asyncio one
    (proven by `error_detail == "unhandled_exception:CancelledError"`); only
    the exception type surfacing to the test differs.
    """
    mini = FastAPI()
    mini.add_middleware(AuditMiddleware)
    mini.add_middleware(RequestIdMiddleware)
    mini.state.engine = MagicMock()

    @mini.get("/api/v2/boom")
    async def _boom() -> dict[str, str]:
        raise asyncio.CancelledError

    mock_write = AsyncMock()
    with (
        patch("address_validator.middleware.audit.write_audit_row", mock_write),
        pytest.raises(BaseException) as exc_info,
    ):
        tc = TestClient(mini, raise_server_exceptions=True)
        tc.get("/api/v2/boom")

    # Either the asyncio CancelledError or its anyio-portal-translated sibling.
    assert (
        isinstance(exc_info.value, asyncio.CancelledError)
        or type(exc_info.value).__name__ == "CancelledError"
    ), f"unexpected exception type: {type(exc_info.value)!r}"

    mock_write.assert_called_once()
    kwargs = mock_write.call_args.kwargs
    assert kwargs["error_detail"] == "unhandled_exception:CancelledError"


def test_audit_streaming_response_raises_mid_stream() -> None:
    """When a 2xx response starts streaming and then raises:
    - error_detail is the exception label, NOT 'audit_invariant_violated'
      (proves _check_validate_invariants is skipped on exception)
    - status_code is preserved as 200, NOT overwritten to 500
      (proves the partial-status branch in _emit_audit_artifacts)

    NOTE: depends on Starlette propagating mid-stream exceptions from
    StreamingResponse up through middleware rather than catching them
    internally. If a future Starlette version captures these, the
    `error_detail` assertion below will fail loudly (a recoverable failure
    mode), and the test will need to be rewritten against a different
    mid-stream-raise primitive.
    """
    mini = FastAPI()
    mini.add_middleware(AuditMiddleware)
    mini.add_middleware(RequestIdMiddleware)
    mini.state.engine = MagicMock()

    @mini.post("/api/v2/validate")
    async def _stream_then_raise() -> StreamingResponse:
        async def _gen():
            yield b'{"partial":'
            raise RuntimeError("mid-stream boom")

        # 200 status header fires before the generator raises — the invariant
        # check would otherwise trip on NULL audit fields for this 2xx path.
        return StreamingResponse(_gen(), status_code=200, media_type="application/json")

    mock_write = AsyncMock()
    with patch("address_validator.middleware.audit.write_audit_row", mock_write):
        tc = TestClient(mini, raise_server_exceptions=False)
        tc.post("/api/v2/validate")

    mock_write.assert_called_once()
    kwargs = mock_write.call_args.kwargs
    assert kwargs["status_code"] == 200, (
        f"status_code should stay at 200 (partial status preserved), got {kwargs['status_code']}"
    )
    assert kwargs["error_detail"] == "unhandled_exception:RuntimeError", (
        f"error_detail should be exception label, got {kwargs['error_detail']!r}"
    )


def test_audit_skips_candidate_write_on_exception() -> None:
    """No training candidate row should be written when the inner app raises."""
    mini = FastAPI()
    mini.add_middleware(AuditMiddleware)
    mini.add_middleware(RequestIdMiddleware)
    mini.state.engine = MagicMock()

    @mini.get("/api/v2/boom")
    async def _boom() -> dict[str, str]:
        raise RuntimeError("kaboom")

    mock_write = AsyncMock()
    mock_candidate = AsyncMock()
    with (
        patch("address_validator.middleware.audit.write_audit_row", mock_write),
        patch("address_validator.middleware.audit.write_training_candidate", mock_candidate),
    ):
        tc = TestClient(mini, raise_server_exceptions=False)
        tc.get("/api/v2/boom")

    mock_write.assert_called_once()
    mock_candidate.assert_not_called()


def test_audit_row_receives_parse_type() -> None:
    """parse_type ContextVar set during the endpoint must appear in the audit row."""
    mini = FastAPI()
    mini.add_middleware(AuditMiddleware)
    mini.add_middleware(RequestIdMiddleware)
    mini.state.engine = MagicMock()

    @mini.get("/api/v2/fake")
    async def _fake_endpoint() -> dict[str, str]:
        set_audit_context(parse_type="Street Address")
        return {"ok": "true"}

    mock_write = AsyncMock()
    with patch("address_validator.middleware.audit.write_audit_row", mock_write):
        tc = TestClient(mini)
        tc.get("/api/v2/fake")

    mock_write.assert_called_once()
    kwargs = mock_write.call_args.kwargs
    assert kwargs["parse_type"] == "Street Address", (
        f"parse_type should be 'Street Address', got {kwargs.get('parse_type')!r}"
    )


def test_audit_row_receives_pattern_key() -> None:
    """pattern_key ContextVar set during the endpoint must appear in the audit row."""
    mini = FastAPI()
    mini.add_middleware(AuditMiddleware)
    mini.add_middleware(RequestIdMiddleware)
    mini.state.engine = MagicMock()

    @mini.get("/api/v2/fake")
    async def _fake_endpoint() -> dict[str, str]:
        set_audit_context(
            provider="usps",
            validation_status="confirmed",
            cache_hit=True,
            pattern_key="cafebabe1234",
        )
        return {"ok": "true"}

    mock_write = AsyncMock()
    with patch("address_validator.middleware.audit.write_audit_row", mock_write):
        tc = TestClient(mini)
        tc.get("/api/v2/fake")

    mock_write.assert_called_once()
    kwargs = mock_write.call_args.kwargs
    assert kwargs["pattern_key"] == "cafebabe1234", (
        f"pattern_key should be 'cafebabe1234', got {kwargs.get('pattern_key')!r}"
    )


@pytest.mark.asyncio
async def test_audit_writes_candidate_with_endpoint_and_version(db):
    """Post an ambiguous address; candidate row should capture endpoint + api_version."""
    # Clear stale rows from the test DB.
    async with db.begin() as conn:
        await conn.execute(sa.text("TRUNCATE model_training_candidates RESTART IDENTITY CASCADE"))

    # Wire app state so middleware can write audit/candidate rows without a full lifespan.
    saved_engine = getattr(app.state, "engine", None)
    saved_api_key = getattr(app.state, "api_key", None)
    app.state.engine = db
    app.state.api_key = TEST_API_KEY
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v2/parse",
                json={"address": "995 9TH ST APT 201 ROOM 104", "country": "US"},
                headers={"X-API-Key": TEST_API_KEY},
            )
        assert resp.status_code == 200

        await asyncio.sleep(0.3)  # let fire-and-forget audit task complete

        async with db.connect() as conn:
            row = (
                await conn.execute(
                    sa.select(mtc.c.endpoint, mtc.c.api_version, mtc.c.failure_reason)
                    .where(mtc.c.raw_address.like("995 9TH ST APT%"))
                    .order_by(mtc.c.id.desc())
                    .limit(1)
                )
            ).first()
        assert row is not None, "candidate row not written"
        assert row.endpoint == "/api/v2/parse"
        assert row.api_version == "2"
        assert row.failure_reason is not None
    finally:
        app.state.engine = saved_engine
        app.state.api_key = saved_api_key
