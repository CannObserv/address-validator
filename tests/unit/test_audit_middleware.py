"""Tests for the audit logging middleware."""

from address_validator.middleware.audit import _should_audit


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
