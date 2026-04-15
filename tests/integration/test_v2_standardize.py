"""Integration tests for POST /api/v2/standardize."""

import pytest

pytestmark = pytest.mark.integration


class TestV2StandardizeISO:
    def test_returns_iso_keys_by_default(self, client) -> None:
        response = client.post(
            "/api/v2/standardize",
            json={"address": "123 n main st ste 4, seattle wa 98101"},
        )
        assert response.status_code == 200
        values = response.json()["components"]["values"]
        assert values["premise_number"] == "123"
        assert values["thoroughfare_pre_direction"] == "N"
        assert values["thoroughfare_name"] == "MAIN"
        assert values["thoroughfare_trailing_type"] == "ST"
        assert values["sub_premise_type"] == "STE"
        assert values["sub_premise_number"] == "4"
        assert values["locality"] == "SEATTLE"
        assert values["administrative_area"] == "WA"
        assert values["postcode"] == "98101"

    def test_api_version_is_2(self, client) -> None:
        response = client.post(
            "/api/v2/standardize",
            json={"address": "123 Main St, Seattle, WA 98101"},
        )
        assert response.json()["api_version"] == "2"

    def test_top_level_fields_unchanged(self, client) -> None:
        response = client.post(
            "/api/v2/standardize",
            json={"address": "123 Main St, Seattle, WA 98101"},
        )
        body = response.json()
        assert body["city"] == "SEATTLE"
        assert body["region"] == "WA"
        assert body["postal_code"] == "98101"

    def test_component_profile_usps_pub28(self, client) -> None:
        response = client.post(
            "/api/v2/standardize?component_profile=usps-pub28",
            json={"address": "123 Main St, Seattle, WA 98101"},
        )
        values = response.json()["components"]["values"]
        assert values["address_number"] == "123"
        assert values["street_name"] == "MAIN"
        assert values["city"] == "SEATTLE"

    def test_invalid_profile_returns_422(self, client) -> None:
        response = client.post(
            "/api/v2/standardize?component_profile=not-a-profile",
            json={"address": "123 Main St"},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "invalid_component_profile"
