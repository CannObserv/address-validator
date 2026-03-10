"""HTTP-level tests for POST /api/v1/validate."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from models import ComponentSet, ValidateResponseV1, ValidationResult

NULL_RESPONSE = ValidateResponseV1(
    country="US",
    validation=ValidationResult(status="unavailable"),
)

CONFIRMED_RESPONSE = ValidateResponseV1(
    address_line_1="123 MAIN ST",
    address_line_2="",
    city="SPRINGFIELD",
    region="IL",
    postal_code="62701-1234",
    country="US",
    validated="123 MAIN ST  SPRINGFIELD, IL 62701-1234",
    validation=ValidationResult(status="confirmed", dpv_match_code="Y", provider="usps"),
)


class TestValidateEndpoint:
    def test_null_provider_returns_200(self, client: TestClient) -> None:
        with patch(
            "routers.v1.validate.get_provider",
            return_value=_make_null_provider(NULL_RESPONSE),
        ):
            resp = client.post(
                "/api/v1/validate",
                json={"address": "123 Main St", "city": "Springfield", "region": "IL"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["validation"]["status"] == "unavailable"
        assert body["api_version"] == "1"

    def test_confirmed_response_shape(self, client: TestClient) -> None:
        with patch(
            "routers.v1.validate.get_provider",
            return_value=_make_null_provider(CONFIRMED_RESPONSE),
        ):
            resp = client.post(
                "/api/v1/validate",
                json={"address": "123 Main St", "city": "Springfield", "region": "IL"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["validation"]["dpv_match_code"] == "Y"
        assert body["validation"]["status"] == "confirmed"
        assert body["city"] == "SPRINGFIELD"

    def test_blank_address_returns_400(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={"address": "   ", "city": "Springfield", "region": "IL"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "address_required"

    def test_missing_address_field_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={"city": "Springfield", "region": "IL"},
        )
        assert resp.status_code == 422

    def test_unsupported_country_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={"address": "123 Main St", "city": "London", "region": "ENG", "country": "GB"},
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "country_not_supported"

    def test_no_auth_returns_401(self, client_no_auth: TestClient) -> None:
        resp = client_no_auth.post(
            "/api/v1/validate",
            json={"address": "123 Main St", "city": "Springfield", "region": "IL"},
        )
        assert resp.status_code == 401

    def test_bad_auth_returns_403(self, client_bad_auth: TestClient) -> None:
        resp = client_bad_auth.post(
            "/api/v1/validate",
            json={"address": "123 Main St", "city": "Springfield", "region": "IL"},
        )
        assert resp.status_code == 403

    def test_address_too_long_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={"address": "A" * 1001, "city": "Springfield", "region": "IL"},
        )
        assert resp.status_code == 422


def _make_null_provider(response: ValidateResponseV1) -> AsyncMock:
    """Return a mock provider whose validate() coroutine returns *response*."""
    provider = AsyncMock()
    provider.validate = AsyncMock(return_value=response)
    return provider


class TestValidationResult:
    def test_confirmed_status(self) -> None:
        r = ValidationResult(status="confirmed", dpv_match_code="Y", provider="usps")
        assert r.status == "confirmed"
        assert r.dpv_match_code == "Y"
        assert r.provider == "usps"

    def test_unavailable_has_no_dpv(self) -> None:
        r = ValidationResult(status="unavailable")
        assert r.dpv_match_code is None
        assert r.provider is None


class TestValidateResponseV1Shape:
    def test_address_fields_present(self) -> None:
        r = ValidateResponseV1(
            country="US",
            validation=ValidationResult(status="unavailable"),
        )
        assert r.address_line_1 is None
        assert r.address_line_2 is None
        assert r.city is None
        assert r.region is None
        assert r.postal_code is None
        assert r.validated is None
        assert r.components is None
        assert r.latitude is None
        assert r.longitude is None
        assert r.warnings == []
        assert r.api_version == "1"

    def test_full_confirmed_response(self) -> None:
        r = ValidateResponseV1(
            address_line_1="123 MAIN ST",
            address_line_2="",
            city="SPRINGFIELD",
            region="IL",
            postal_code="62701-1234",
            country="US",
            validated="123 MAIN ST  SPRINGFIELD, IL 62701-1234",
            components=ComponentSet(
                spec="usps-pub28",
                spec_version="unknown",
                values={
                    "address_line_1": "123 MAIN ST",
                    "city": "SPRINGFIELD",
                    "region": "IL",
                    "postal_code": "62701-1234",
                },
            ),
            validation=ValidationResult(status="confirmed", dpv_match_code="Y", provider="usps"),
            latitude=39.7817,
            longitude=-89.6501,
            warnings=[],
        )
        assert r.validation.status == "confirmed"
        assert r.validation.dpv_match_code == "Y"
        assert r.postal_code == "62701-1234"
        assert r.latitude == 39.7817
