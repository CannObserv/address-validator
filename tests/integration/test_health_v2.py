"""Integration tests for GET /api/v2/health."""

from unittest.mock import AsyncMock, MagicMock, patch


class TestHealthV2:
    def test_health_ok(self, client) -> None:
        response = client.get("/api/v2/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["api_version"] == "2"
        assert data["database"] == "unconfigured"

    def test_health_no_auth_required(self, client_no_auth) -> None:
        """Health check must be accessible without an API key."""
        response = client_no_auth.get("/api/v2/health")
        assert response.status_code == 200

    def test_api_version_header(self, client) -> None:
        response = client.get("/api/v2/health")
        assert response.headers.get("api-version") == "2"

    def test_health_database_ok(self, client) -> None:
        """When engine is configured and SELECT 1 succeeds, database is 'ok'."""
        mock_conn = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch.object(client.app.state, "engine", mock_engine, create=True):
            response = client.get("/api/v2/health")

        assert response.status_code == 200
        assert response.json()["database"] == "ok"
        assert response.json()["status"] == "ok"
        mock_conn.execute.assert_awaited_once()

    def test_health_database_error(self, client) -> None:
        """When SELECT 1 fails, status is 'degraded' and HTTP 503 is returned."""
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(
            side_effect=Exception("connection refused")
        )

        with patch.object(client.app.state, "engine", mock_engine, create=True):
            response = client.get("/api/v2/health")

        assert response.status_code == 503
        assert response.json()["status"] == "degraded"
        assert response.json()["database"] == "error"

    def test_libpostal_ok(self, client) -> None:
        """When libpostal sidecar is reachable, libpostal field is 'ok'."""
        mock_client = AsyncMock()
        mock_client.health_check = AsyncMock(return_value=True)

        with patch.object(client.app.state, "libpostal_client", mock_client, create=True):
            response = client.get("/api/v2/health")

        assert response.status_code == 200
        assert response.json()["libpostal"] == "ok"

    def test_libpostal_unavailable_does_not_degrade_status(self, client) -> None:
        """When libpostal is down, status stays 'ok' and HTTP 200 is returned."""
        mock_client = AsyncMock()
        mock_client.health_check = AsyncMock(return_value=False)

        with patch.object(client.app.state, "libpostal_client", mock_client, create=True):
            response = client.get("/api/v2/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["libpostal"] == "unavailable"

    def test_libpostal_unavailable_with_database_error(self, client) -> None:
        """503 when DB is down regardless of libpostal state."""
        mock_libpostal = AsyncMock()
        mock_libpostal.health_check = AsyncMock(return_value=False)
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(
            side_effect=Exception("connection refused")
        )

        with (
            patch.object(client.app.state, "libpostal_client", mock_libpostal, create=True),
            patch.object(client.app.state, "engine", mock_engine, create=True),
        ):
            response = client.get("/api/v2/health")

        assert response.status_code == 503
        assert response.json()["status"] == "degraded"
        assert response.json()["libpostal"] == "unavailable"
