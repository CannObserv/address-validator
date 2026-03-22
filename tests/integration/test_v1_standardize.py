"""Integration tests for POST /api/v1/standardize."""


class TestV1StandardizeAuth:
    def test_missing_key_returns_401(self, client_no_auth) -> None:
        response = client_no_auth.post("/api/v1/standardize", json={"address": "123 Main St"})
        assert response.status_code == 401

    def test_wrong_key_returns_403(self, client_bad_auth) -> None:
        response = client_bad_auth.post("/api/v1/standardize", json={"address": "123 Main St"})
        assert response.status_code == 403


class TestV1StandardizeFromAddress:
    def test_basic_standardize(self, client) -> None:
        response = client.post(
            "/api/v1/standardize",
            json={"address": "123 main street, seattle, washington 98101"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["address_line_1"] == "123 MAIN ST"
        assert body["city"] == "SEATTLE"
        assert body["region"] == "WA"
        assert body["postal_code"] == "98101"

    def test_blank_address_returns_422(self, client) -> None:
        response = client.post("/api/v1/standardize", json={"address": "   "})
        assert response.status_code == 422

    def test_no_address_or_components_returns_422(self, client) -> None:
        response = client.post("/api/v1/standardize", json={})
        assert response.status_code == 422

    def test_address_too_long_returns_422(self, client) -> None:
        response = client.post("/api/v1/standardize", json={"address": "A" * 1001})
        assert response.status_code == 422


class TestV1StandardizeFromComponents:
    def test_components_input(self, client) -> None:
        response = client.post(
            "/api/v1/standardize",
            json={
                "components": {
                    "address_number": "100",
                    "street_name": "OAK",
                    "street_name_post_type": "AVENUE",
                    "city": "PORTLAND",
                    "state": "OR",
                    "zip_code": "97201",
                }
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["address_line_1"] == "100 OAK AVE"
        assert body["region"] == "OR"

    def test_components_takes_precedence_over_address(self, client) -> None:
        """When both are provided, components wins."""
        response = client.post(
            "/api/v1/standardize",
            json={
                "address": "999 Ignored Rd",
                "components": {"address_number": "1", "street_name": "REAL"},
            },
        )
        assert response.status_code == 200
        assert response.json()["address_line_1"] == "1 REAL"

    def test_empty_components_falls_through_to_address(self, client) -> None:
        response = client.post(
            "/api/v1/standardize",
            json={"address": "500 Pine St, Seattle, WA", "components": {}},
        )
        assert response.status_code == 200


class TestV1StandardizeResponseShape:
    def test_has_standardized_field(self, client) -> None:
        response = client.post(
            "/api/v1/standardize",
            json={"address": "123 Main St, Springfield, IL 62701"},
        )
        body = response.json()
        assert "standardized" in body
        assert body["standardized"]

    def test_standardized_uses_two_space_separator(self, client) -> None:
        response = client.post(
            "/api/v1/standardize",
            json={"address": "123 Main St, Springfield, IL 62701"},
        )
        assert "  " in response.json()["standardized"]

    def test_api_version_header(self, client) -> None:
        response = client.post("/api/v1/standardize", json={"address": "123 Main St"})
        assert response.headers.get("api-version") == "1"

    def test_api_version_in_body(self, client) -> None:
        response = client.post("/api/v1/standardize", json={"address": "123 Main St"})
        assert response.json()["api_version"] == "1"


class TestLegacyStandardizeRouteRemoved:
    def test_legacy_standardize_returns_404(self, client) -> None:
        response = client.post("/api/standardize", json={"address": "123 Main St"})
        assert response.status_code == 404
