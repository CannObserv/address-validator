"""Integration tests for GET /api/v1/health."""

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestHealth:
    def test_health_ok(self, client) -> None:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["api_version"] == "1"
        # VALIDATION_CACHE_DSN is set to the test DB by tests/conftest.py, so
        # the engine initialises and database reports "ok" (not "unconfigured").
        assert body["database"] == "ok"

    def test_health_no_auth_required(self, client_no_auth) -> None:
        """Health check must be accessible without an API key."""
        response = client_no_auth.get("/api/v1/health")
        assert response.status_code == 200

    def test_api_version_header(self, client) -> None:
        response = client.get("/api/v1/health")
        assert response.headers.get("api-version") == "1"

    def test_health_database_ok(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        """When engine is configured and SELECT 1 succeeds, database is 'ok'."""
        mock_conn = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)

        # monkeypatch.setattr uses setattr/getattr which correctly round-trips
        # through Starlette State.__setattr__ / __getattr__ (storing in _state).
        # patch.object + create=True would DELETE the attribute on exit because
        # State stores values in _state, not __dict__ (local=False in mock internals).
        monkeypatch.setattr(client.app.state, "engine", mock_engine)
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        assert response.json()["database"] == "ok"
        assert response.json()["status"] == "ok"
        mock_conn.execute.assert_awaited_once()

    def test_health_database_error(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        """When SELECT 1 fails, status is 'degraded' and HTTP 503 is returned."""
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(
            side_effect=Exception("connection refused")
        )

        monkeypatch.setattr(client.app.state, "engine", mock_engine)
        response = client.get("/api/v1/health")

        assert response.status_code == 503
        assert response.json()["status"] == "degraded"
        assert response.json()["database"] == "error"
