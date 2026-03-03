"""Integration tests for GET /api/v1/health."""


class TestHealth:
    def test_health_ok(self, client) -> None:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "api_version": "1"}

    def test_health_no_auth_required(self, client_no_auth) -> None:
        """Health check must be accessible without an API key."""
        response = client_no_auth.get("/api/v1/health")
        assert response.status_code == 200

    def test_api_version_header(self, client) -> None:
        response = client.get("/api/v1/health")
        assert response.headers.get("api-version") == "1"
