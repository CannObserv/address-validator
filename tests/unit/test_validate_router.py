"""HTTP-level tests for POST /api/v1/validate."""

from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from models import ValidateResponseV1

NULL_RESPONSE = ValidateResponseV1(
    input_address="123 Main St",
    country="US",
    validation_status="unavailable",
    provider=None,
    dpv_match_code=None,
    zip_plus4=None,
    vacant=None,
    corrected_components=None,
)

CONFIRMED_RESPONSE = ValidateResponseV1(
    input_address="123 Main St",
    country="US",
    validation_status="confirmed",
    provider="usps",
    dpv_match_code="Y",
    zip_plus4="1234",
    vacant="N",
    corrected_components={
        "address_line": "123 MAIN ST",
        "secondary_address": "",
        "city": "SPRINGFIELD",
        "region": "IL",
        "postal_code": "62701",
    },
)


class TestValidateEndpoint:
    def test_null_provider_returns_200(
        self, client: TestClient
    ) -> None:
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
        assert body["validation_status"] == "unavailable"
        assert body["api_version"] == "1"

    def test_confirmed_response_shape(
        self, client: TestClient
    ) -> None:
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
        assert body["dpv_match_code"] == "Y"
        assert body["zip_plus4"] == "1234"
        assert body["corrected_components"]["city"] == "SPRINGFIELD"

    def test_blank_address_returns_400(
        self, client: TestClient
    ) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={"address": "   ", "city": "Springfield", "region": "IL"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "address_required"

    def test_missing_address_field_returns_422(
        self, client: TestClient
    ) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={"city": "Springfield", "region": "IL"},
        )
        assert resp.status_code == 422

    def test_unsupported_country_returns_422(
        self, client: TestClient
    ) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={"address": "123 Main St", "city": "London", "region": "ENG", "country": "GB"},
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "country_not_supported"

    def test_no_auth_returns_401(
        self, client_no_auth: TestClient
    ) -> None:
        resp = client_no_auth.post(
            "/api/v1/validate",
            json={"address": "123 Main St", "city": "Springfield", "region": "IL"},
        )
        assert resp.status_code == 401

    def test_bad_auth_returns_403(
        self, client_bad_auth: TestClient
    ) -> None:
        resp = client_bad_auth.post(
            "/api/v1/validate",
            json={"address": "123 Main St", "city": "Springfield", "region": "IL"},
        )
        assert resp.status_code == 403

    def test_address_too_long_returns_422(
        self, client: TestClient
    ) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={"address": "A" * 1001, "city": "Springfield", "region": "IL"},
        )
        assert resp.status_code == 422


def _make_null_provider(response: ValidateResponseV1) -> object:
    """Return a mock provider whose validate() coroutine returns *response*."""
    provider = AsyncMock()
    provider.validate = AsyncMock(return_value=response)
    return provider
