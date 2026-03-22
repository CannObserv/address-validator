"""Integration tests for GET /api/v1/health."""

from unittest.mock import AsyncMock, MagicMock, patch


class TestHealth:
    def test_health_ok(self, client) -> None:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "api_version": "1",
            "database": "unconfigured",
        }

    def test_health_no_auth_required(self, client_no_auth) -> None:
        """Health check must be accessible without an API key."""
        response = client_no_auth.get("/api/v1/health")
        assert response.status_code == 200

    def test_api_version_header(self, client) -> None:
        response = client.get("/api/v1/health")
        assert response.headers.get("api-version") == "1"

    def test_health_database_ok(self, client) -> None:
        """When engine is configured and SELECT 1 succeeds, database is 'ok'."""
        mock_conn = AsyncMock()
        mock_engine = MagicMock()
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch.object(client.app.state, "engine", mock_engine, create=True):
            response = client.get("/api/v1/health")

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
            response = client.get("/api/v1/health")

        assert response.status_code == 503
        assert response.json()["status"] == "degraded"
        assert response.json()["database"] == "error"
