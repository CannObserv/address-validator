"""Integration tests for GET /api/v2/health."""

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestHealthV2:
    def test_health_ok(self, client) -> None:
        response = client.get("/api/v2/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["api_version"] == "2"
        # VALIDATION_CACHE_DSN is set to the test DB by tests/conftest.py.
        assert data["database"] == "ok"
        # libpostal field is not asserted here: its value depends on whether the
        # sidecar is reachable in the test environment. Libpostal-specific
        # assertions are covered by test_libpostal_ok and
        # test_libpostal_unavailable_does_not_degrade_status.

    def test_health_no_auth_required(self, client_no_auth) -> None:
        """Health check must be accessible without an API key."""
        response = client_no_auth.get("/api/v2/health")
        assert response.status_code == 200

    def test_api_version_header(self, client) -> None:
        response = client.get("/api/v2/health")
        assert response.headers.get("api-version") == "2"

    def test_health_database_ok(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        """When engine is configured and SELECT 1 succeeds, database is 'ok'."""
        mock_conn = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)

        # Use monkeypatch.setattr instead of patch.object + create=True.
        # patch.object + create=True deletes State attributes on exit because
        # they live in State._state, not __dict__ (local=False in mock internals).
        monkeypatch.setattr(client.app.state, "engine", mock_engine)
        response = client.get("/api/v2/health")

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
        response = client.get("/api/v2/health")

        assert response.status_code == 503
        assert response.json()["status"] == "degraded"
        assert response.json()["database"] == "error"

    def test_libpostal_ok(self, client, monkeypatch: pytest.MonkeyPatch) -> None:
        """When libpostal sidecar is reachable, libpostal field is 'ok'."""
        mock_client = AsyncMock()
        mock_client.health_check = AsyncMock(return_value=True)

        monkeypatch.setattr(client.app.state, "libpostal_client", mock_client)
        response = client.get("/api/v2/health")

        assert response.status_code == 200
        assert response.json()["libpostal"] == "ok"

    def test_libpostal_unavailable_does_not_degrade_status(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When libpostal is down, status stays 'ok' and HTTP 200 is returned."""
        mock_client = AsyncMock()
        mock_client.health_check = AsyncMock(return_value=False)

        monkeypatch.setattr(client.app.state, "libpostal_client", mock_client)
        response = client.get("/api/v2/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["libpostal"] == "unavailable"

    def test_libpostal_unavailable_with_database_error(
        self, client, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """503 when DB is down regardless of libpostal state."""
        mock_libpostal = AsyncMock()
        mock_libpostal.health_check = AsyncMock(return_value=False)
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(
            side_effect=Exception("connection refused")
        )

        monkeypatch.setattr(client.app.state, "libpostal_client", mock_libpostal)
        monkeypatch.setattr(client.app.state, "engine", mock_engine)
        response = client.get("/api/v2/health")

        assert response.status_code == 503
        assert response.json()["status"] == "degraded"
        assert response.json()["libpostal"] == "unavailable"
