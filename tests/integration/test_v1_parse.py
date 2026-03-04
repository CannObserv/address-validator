"""Integration tests for POST /api/v1/parse."""

import pytest


class TestV1ParseAuth:
    def test_missing_key_returns_401(self, client_no_auth) -> None:
        response = client_no_auth.post("/api/v1/parse", json={"address": "123 Main St"})
        assert response.status_code == 401

    def test_wrong_key_returns_403(self, client_bad_auth) -> None:
        response = client_bad_auth.post("/api/v1/parse", json={"address": "123 Main St"})
        assert response.status_code == 403

    def test_valid_key_returns_200(self, client) -> None:
        response = client.post("/api/v1/parse", json={"address": "123 Main St, Seattle, WA 98101"})
        assert response.status_code == 200


class TestV1ParseResponse:
    def test_components_present(self, client) -> None:
        response = client.post("/api/v1/parse", json={"address": "123 Main St, Seattle, WA 98101"})
        body = response.json()
        assert "components" in body
        assert body["components"]["values"]["address_number"] == "123"

    def test_country_defaults_to_us(self, client) -> None:
        response = client.post("/api/v1/parse", json={"address": "123 Main St"})
        assert response.json()["country"] == "US"

    def test_country_lowercase_accepted(self, client) -> None:
        response = client.post("/api/v1/parse", json={"address": "123 Main St", "country": "us"})
        assert response.status_code == 200
        assert response.json()["country"] == "US"

    def test_api_version_in_body(self, client) -> None:
        response = client.post("/api/v1/parse", json={"address": "123 Main St"})
        assert response.json()["api_version"] == "1"

    def test_api_version_header(self, client) -> None:
        response = client.post("/api/v1/parse", json={"address": "123 Main St"})
        assert response.headers.get("api-version") == "1"

    def test_spec_in_components(self, client) -> None:
        response = client.post("/api/v1/parse", json={"address": "123 Main St"})
        assert response.json()["components"]["spec"] == "usps-pub28"


class TestV1ParseValidation:
    def test_blank_address_returns_400(self, client) -> None:
        response = client.post("/api/v1/parse", json={"address": "   "})
        assert response.status_code == 400
        assert response.json()["error"] == "address_required"

    def test_address_too_long_returns_422(self, client) -> None:
        response = client.post("/api/v1/parse", json={"address": "A" * 1001})
        assert response.status_code == 422

    def test_empty_body_returns_422(self, client) -> None:
        response = client.post(
            "/api/v1/parse", content=b"", headers={"content-type": "application/json"}
        )
        assert response.status_code == 422

    def test_invalid_country_returns_422(self, client) -> None:
        response = client.post("/api/v1/parse", json={"address": "123 Main St", "country": "XX"})
        assert response.status_code == 422
        assert response.json()["error"] == "invalid_country_code"

    def test_unsupported_country_returns_422(self, client) -> None:
        response = client.post("/api/v1/parse", json={"address": "123 Main St", "country": "CA"})
        assert response.status_code == 422
        assert response.json()["error"] == "country_not_supported"


class TestRemovedDeprecatedRoute:
    def test_legacy_parse_returns_404(self, client) -> None:
        response = client.post("/api/parse", json={"address": "123 Main St"})
        assert response.status_code == 404


@pytest.mark.parametrize(
    "address",
    [
        "123 Main St, Springfield, IL 62701",
        "1804 & 1810 Elm Ave, Seattle, WA",
        "100 N Oak Blvd Ste 200, Portland, OR 97201",
        "PO Box 42, Smalltown, TX 79901",
    ],
)
def test_parse_various_addresses(client, address: str) -> None:
    """Smoke-test a variety of address shapes."""
    response = client.post("/api/v1/parse", json={"address": address})
    assert response.status_code == 200
    assert response.json()["components"]["values"]
