"""Unit tests for API version header middleware."""

from fastapi.testclient import TestClient


class TestApiVersionHeaderMiddleware:
    def test_v1_endpoint_returns_api_version_1_header(self, client: TestClient) -> None:
        """v1 endpoints return API-Version: 1 header."""
        response = client.post(
            "/api/v1/parse",
            json={"address": "123 Main St, Seattle, WA 98101"},
            headers={"X-API-Key": "test-api-key-for-pytest"},
        )
        assert response.headers.get("api-version") == "1"

    def test_v2_endpoint_returns_api_version_2_header(self, client: TestClient) -> None:
        """v2 endpoints return API-Version: 2 header.

        Note: This test requires the v2 router to be registered.
        Will pass after Task 7-8 are complete.
        """
        response = client.post(
            "/api/v2/parse",
            json={"address": "123 Main St, Seattle, WA 98101"},
            headers={"X-API-Key": "test-api-key-for-pytest"},
        )
        assert response.headers.get("api-version") == "2"

    def test_non_api_routes_no_version_header(self, client: TestClient) -> None:
        """Non-API routes don't include API-Version header."""
        response = client.get("/")
        assert "api-version" not in response.headers
