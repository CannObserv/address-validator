"""Integration tests for POST /api/v2/validate."""


class TestV2ValidateBasic:
    def test_us_address_returns_200(self, client) -> None:
        response = client.post(
            "/api/v2/validate",
            json={"address": "123 Main St, Seattle, WA 98101"},
        )
        # Without a real provider configured, status will be "unavailable"
        assert response.status_code == 200

    def test_api_version_is_2(self, client) -> None:
        response = client.post(
            "/api/v2/validate",
            json={"address": "123 Main St, Seattle, WA 98101"},
        )
        assert response.json()["api_version"] == "2"

    def test_invalid_profile_returns_422(self, client) -> None:
        response = client.post(
            "/api/v2/validate?component_profile=bad",
            json={"address": "123 Main St"},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "invalid_component_profile"
