"""HTTP-level tests for GET /api/v1/countries/{code}/format."""

from unittest.mock import patch

from fastapi.testclient import TestClient

from address_validator.models import (
    CountryFieldDefinition,
    CountryFormatResponse,
    CountrySubdivision,
)

_US_FORMAT = CountryFormatResponse(
    country="US",
    fields=[
        CountryFieldDefinition(key="address_line_1", label="Address line 1", required=True),
        CountryFieldDefinition(key="address_line_2", label="Address line 2", required=False),
        CountryFieldDefinition(key="city", label="City", required=True),
        CountryFieldDefinition(
            key="region",
            label="State",
            required=True,
            options=[CountrySubdivision(code="CA", label="California")],
        ),
        CountryFieldDefinition(
            key="postal_code",
            label="ZIP code",
            required=True,
            pattern=r"^(\d{5})(?:[ \-](\d{4}))?$",
        ),
    ],
)


class TestCountriesFormatEndpoint:
    def test_valid_country_returns_200(self, client: TestClient) -> None:
        with patch(
            "address_validator.routers.v1.countries.get_country_format",
            return_value=_US_FORMAT,
        ):
            resp = client.get("/api/v1/countries/US/format")
        assert resp.status_code == 200

    def test_response_shape(self, client: TestClient) -> None:
        with patch(
            "address_validator.routers.v1.countries.get_country_format",
            return_value=_US_FORMAT,
        ):
            resp = client.get("/api/v1/countries/US/format")
        body = resp.json()
        assert body["country"] == "US"
        assert isinstance(body["fields"], list)
        assert body["api_version"] == "1"

    def test_field_keys_present(self, client: TestClient) -> None:
        with patch(
            "address_validator.routers.v1.countries.get_country_format",
            return_value=_US_FORMAT,
        ):
            resp = client.get("/api/v1/countries/US/format")
        keys = [f["key"] for f in resp.json()["fields"]]
        assert "address_line_1" in keys
        assert "address_line_2" in keys

    def test_region_options_present(self, client: TestClient) -> None:
        with patch(
            "address_validator.routers.v1.countries.get_country_format",
            return_value=_US_FORMAT,
        ):
            resp = client.get("/api/v1/countries/US/format")
        region = next(f for f in resp.json()["fields"] if f["key"] == "region")
        assert region["options"] is not None
        assert region["options"][0] == {"code": "CA", "label": "California"}

    def test_cache_control_header(self, client: TestClient) -> None:
        with patch(
            "address_validator.routers.v1.countries.get_country_format",
            return_value=_US_FORMAT,
        ):
            resp = client.get("/api/v1/countries/US/format")
        assert resp.headers.get("cache-control") == "public, max-age=86400"

    def test_lowercase_code_normalised(self, client: TestClient) -> None:
        with patch(
            "address_validator.routers.v1.countries.get_country_format",
            return_value=_US_FORMAT,
        ) as mock_fn:
            resp = client.get("/api/v1/countries/us/format")
        assert resp.status_code == 200
        mock_fn.assert_called_once_with("US")

    def test_invalid_iso2_returns_422(self, client: TestClient) -> None:
        resp = client.get("/api/v1/countries/XX/format")
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"] == "invalid_country_code"

    def test_valid_iso2_no_format_data_returns_404(self, client: TestClient) -> None:
        with patch(
            "address_validator.routers.v1.countries.get_country_format",
            return_value=None,
        ):
            resp = client.get("/api/v1/countries/AQ/format")
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"] == "country_format_not_found"

    def test_requires_api_key(self, client_no_auth: TestClient) -> None:
        resp = client_no_auth.get("/api/v1/countries/US/format")
        assert resp.status_code == 401

    def test_rejects_wrong_api_key(self, client_bad_auth: TestClient) -> None:
        resp = client_bad_auth.get("/api/v1/countries/US/format")
        assert resp.status_code == 403


class TestCountriesFormatEndpointV2:
    def test_valid_country_returns_200(self, client: TestClient) -> None:
        with patch(
            "address_validator.routers.v2.countries.get_country_format",
            return_value=_US_FORMAT,
        ):
            resp = client.get("/api/v2/countries/US/format")
        assert resp.status_code == 200

    def test_response_shape_v2(self, client: TestClient) -> None:
        with patch(
            "address_validator.routers.v2.countries.get_country_format",
            return_value=_US_FORMAT,
        ):
            resp = client.get("/api/v2/countries/US/format")
        body = resp.json()
        assert body["country"] == "US"
        assert isinstance(body["fields"], list)
        assert body["api_version"] == "2"

    def test_field_keys_present_v2(self, client: TestClient) -> None:
        with patch(
            "address_validator.routers.v2.countries.get_country_format",
            return_value=_US_FORMAT,
        ):
            resp = client.get("/api/v2/countries/US/format")
        keys = [f["key"] for f in resp.json()["fields"]]
        assert "address_line_1" in keys
        assert "address_line_2" in keys

    def test_cache_control_header_v2(self, client: TestClient) -> None:
        with patch(
            "address_validator.routers.v2.countries.get_country_format",
            return_value=_US_FORMAT,
        ):
            resp = client.get("/api/v2/countries/US/format")
        assert resp.headers.get("cache-control") == "public, max-age=86400"

    def test_lowercase_code_normalised_v2(self, client: TestClient) -> None:
        with patch(
            "address_validator.routers.v2.countries.get_country_format",
            return_value=_US_FORMAT,
        ) as mock_fn:
            resp = client.get("/api/v2/countries/us/format")
        assert resp.status_code == 200
        mock_fn.assert_called_once_with("US")

    def test_invalid_iso2_returns_422_v2(self, client: TestClient) -> None:
        resp = client.get("/api/v2/countries/XX/format")
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"] == "invalid_country_code"

    def test_valid_iso2_no_format_data_returns_404_v2(self, client: TestClient) -> None:
        with patch(
            "address_validator.routers.v2.countries.get_country_format",
            return_value=None,
        ):
            resp = client.get("/api/v2/countries/AQ/format")
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"] == "country_format_not_found"

    def test_requires_api_key_v2(self, client_no_auth: TestClient) -> None:
        resp = client_no_auth.get("/api/v2/countries/US/format")
        assert resp.status_code == 401

    def test_rejects_wrong_api_key_v2(self, client_bad_auth: TestClient) -> None:
        resp = client_bad_auth.get("/api/v2/countries/US/format")
        assert resp.status_code == 403
