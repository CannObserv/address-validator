"""Integration tests for POST /api/v2/parse."""

import pytest

pytestmark = pytest.mark.integration


class TestV2ParseISO:
    def test_returns_iso_keys_by_default(self, client) -> None:
        response = client.post(
            "/api/v2/parse",
            json={"address": "123 Main St, Seattle, WA 98101"},
        )
        assert response.status_code == 200
        values = response.json()["components"]["values"]
        assert values["premise_number"] == "123"
        assert values["thoroughfare_name"] == "Main"
        assert values["thoroughfare_trailing_type"] == "St"
        assert values["locality"] == "Seattle"
        assert values["administrative_area"] == "WA"
        assert values["postcode"] == "98101"
        assert "address_number" not in values
        assert "street_name" not in values

    def test_api_version_in_body(self, client) -> None:
        response = client.post(
            "/api/v2/parse",
            json={"address": "123 Main St"},
        )
        assert response.json()["api_version"] == "2"

    def test_component_profile_usps_pub28_restores_v1_keys(self, client) -> None:
        response = client.post(
            "/api/v2/parse?component_profile=usps-pub28",
            json={"address": "123 Main St, Seattle, WA 98101"},
        )
        assert response.status_code == 200
        values = response.json()["components"]["values"]
        assert values["address_number"] == "123"
        assert values["street_name"] == "Main"
        assert values["city"] == "Seattle"
        assert values["state"] == "WA"
        assert values["zip_code"] == "98101"

    def test_invalid_component_profile_returns_422(self, client) -> None:
        response = client.post(
            "/api/v2/parse?component_profile=bad-profile",
            json={"address": "123 Main St"},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "invalid_component_profile"

    def test_blank_address_returns_400(self, client) -> None:
        response = client.post("/api/v2/parse", json={"address": "   "})
        assert response.status_code == 400
        assert response.json()["error"] == "address_required"
