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


class TestV2USTerritories:
    """GH #281 option 3: territories are served as country=US, territory as the state."""

    @pytest.mark.parametrize(
        ("code", "name", "city", "zip_code"),
        [
            ("PR", "puerto rico", "san juan", "00901"),
            ("GU", "guam", "tamuning", "96913"),
            ("VI", "virgin islands", "charlotte amalie", "00802"),
            ("AS", "american samoa", "pago pago", "96799"),
            ("MP", "northern mariana islands", "saipan", "96950"),
        ],
    )
    def test_iso_components_standardize_under_pub28(
        self, client, code: str, name: str, city: str, zip_code: str
    ) -> None:
        response = client.post(
            "/api/v2/standardize",
            json={
                "country": "US",
                "components": {
                    "premise_number": "123",
                    "thoroughfare_name": "main",
                    "thoroughfare_trailing_type": "street",
                    "sub_premise_type": "suite",
                    "sub_premise_number": "200",
                    "locality": city,
                    "administrative_area": name,
                    "postcode": zip_code,
                },
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["country"] == "US"
        assert body["region"] == code
        # Response spec names the key vocabulary (iso-19160-4 here); Pub 28 shows
        # in the abbreviations below.
        assert body["standardized"] == f"123 MAIN ST  STE 200  {city.upper()}, {code} {zip_code}"

    @pytest.mark.parametrize("route", ["/api/v2/parse", "/api/v2/standardize", "/api/v2/validate"])
    def test_territory_country_422_points_at_country_us(self, client, route: str) -> None:
        response = client.post(route, json={"address": "123 Main St", "country": "PR"})
        assert response.status_code == 422
        body = response.json()
        assert body["error"] == "country_not_supported"
        assert "country=US with PR as the state" in body["message"]

    def test_territory_country_components_on_standardize_422(self, client) -> None:
        """CR 8: check_country() runs before the components branch, so a territory's
        components get the 422 too."""
        response = client.post(
            "/api/v2/standardize",
            json={"components": {"thoroughfare_name": "calle luna"}, "country": "PR"},
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"] == "country_not_supported"
        assert "country=US with PR as the state" in body["message"]
