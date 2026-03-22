"""HTTP-level tests for POST /api/v1/validate."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from address_validator.main import app
from address_validator.models import (
    ComponentSet,
    StandardizeResponseV1,
    ValidateRequestV1,
    ValidateResponseV1,
    ValidationResult,
)
from address_validator.services.validation.errors import ProviderRateLimitedError

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


def _mock_registry_with(provider):
    """Context manager that temporarily sets app.state.registry to return the given provider."""
    mock_reg = MagicMock()
    mock_reg.get_provider.return_value = provider
    return patch.object(app.state, "registry", mock_reg, create=True)


class TestValidateEndpoint:
    def test_raw_string_returns_200(self, client: TestClient) -> None:
        with _mock_registry_with(_make_null_provider(NULL_RESPONSE)):
            resp = client.post(
                "/api/v1/validate",
                json={"address": "123 Main St, Springfield, IL 62701"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["validation"]["status"] == "unavailable"
        assert body["api_version"] == "1"

    def test_raw_string_provider_receives_standardize_response(self, client: TestClient) -> None:
        provider = _make_null_provider(NULL_RESPONSE)
        with _mock_registry_with(provider):
            client.post(
                "/api/v1/validate",
                json={"address": "123 Main St, Springfield, IL 62701"},
            )
        provider.validate.assert_awaited_once()
        call_arg = provider.validate.call_args[0][0]
        assert isinstance(call_arg, StandardizeResponseV1)

    def test_components_dict_returns_200(self, client: TestClient) -> None:
        with _mock_registry_with(_make_null_provider(NULL_RESPONSE)):
            resp = client.post(
                "/api/v1/validate",
                json={
                    "components": {
                        "address_number": "123",
                        "street_name": "MAIN",
                        "street_suffix": "ST",
                        "city": "SPRINGFIELD",
                        "region": "IL",
                        "postal_code": "62701",
                    }
                },
            )
        assert resp.status_code == 200
        assert resp.json()["validation"]["status"] == "unavailable"

    def test_components_takes_precedence_over_address(self, client: TestClient) -> None:
        provider = _make_null_provider(NULL_RESPONSE)
        with _mock_registry_with(provider):
            client.post(
                "/api/v1/validate",
                json={
                    "address": "should be ignored",
                    "components": {
                        "address_number": "123",
                        "street_name": "MAIN",
                        "street_suffix": "ST",
                        "city": "SPRINGFIELD",
                        "region": "IL",
                        "postal_code": "62701",
                    },
                },
            )
        provider.validate.assert_awaited_once()
        call_arg = provider.validate.call_args[0][0]
        assert isinstance(call_arg, StandardizeResponseV1)
        assert call_arg.city == "SPRINGFIELD"

    def test_confirmed_response_shape(self, client: TestClient) -> None:
        with _mock_registry_with(_make_null_provider(CONFIRMED_RESPONSE)):
            resp = client.post(
                "/api/v1/validate",
                json={"address": "123 Main St, Springfield, IL 62701"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["validation"]["dpv_match_code"] == "Y"
        assert body["validation"]["status"] == "confirmed"
        assert body["city"] == "SPRINGFIELD"

    def test_parse_warnings_merged_into_response(self, client: TestClient) -> None:
        provider_response = ValidateResponseV1(
            country="US",
            validation=ValidationResult(status="unavailable"),
            warnings=[],
        )
        with _mock_registry_with(_make_null_provider(provider_response)):
            resp = client.post(
                "/api/v1/validate",
                json={"address": "123 Main St (rear) Springfield IL 62701"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert any("parenthesized" in w.lower() or "paren" in w.lower() for w in body["warnings"])

    def test_std_warnings_prepend_provider_warnings(self, client: TestClient) -> None:
        provider_warning = "provider-level warning"
        provider_response = ValidateResponseV1(
            country="US",
            validation=ValidationResult(status="unavailable"),
            warnings=[provider_warning],
        )
        provider = _make_null_provider(provider_response)
        with _mock_registry_with(provider):
            resp = client.post(
                "/api/v1/validate",
                json={"address": "123 Main St (rear) Springfield IL 62701"},
            )
        body = resp.json()
        warnings = body["warnings"]
        assert provider_warning in warnings
        assert warnings.index(provider_warning) > 0

    def test_blank_address_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={"address": "   "},
        )
        assert resp.status_code == 422

    def test_missing_both_fields_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={},
        )
        assert resp.status_code == 422

    def test_empty_components_dict_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={"components": {}},
        )
        assert resp.status_code == 422

    def test_unsupported_country_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={"address": "123 Main St", "country": "GB"},
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "country_not_supported"

    def test_no_auth_returns_401(self, client_no_auth: TestClient) -> None:
        resp = client_no_auth.post(
            "/api/v1/validate",
            json={"address": "123 Main St, Springfield, IL 62701"},
        )
        assert resp.status_code == 401

    def test_bad_auth_returns_403(self, client_bad_auth: TestClient) -> None:
        resp = client_bad_auth.post(
            "/api/v1/validate",
            json={"address": "123 Main St, Springfield, IL 62701"},
        )
        assert resp.status_code == 403

    def test_address_too_long_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={"address": "A" * 1001},
        )
        assert resp.status_code == 422

    def test_provider_rate_limited_returns_429(self, client: TestClient) -> None:
        rate_limited = AsyncMock()
        rate_limited.validate = AsyncMock(
            side_effect=ProviderRateLimitedError("all", retry_after_seconds=6.3)
        )
        with _mock_registry_with(rate_limited):
            resp = client.post(
                "/api/v1/validate",
                json={"address": "123 Main St, Springfield, IL 62701"},
            )
        assert resp.status_code == 429
        assert resp.json()["error"] == "provider_rate_limited"
        assert resp.headers["retry-after"] == "7"


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


class TestValidateRequestV1Model:
    def test_accepts_raw_address_string(self) -> None:
        req = ValidateRequestV1(address="123 Main St, Springfield, IL 62701")
        assert req.address == "123 Main St, Springfield, IL 62701"
        assert req.components is None

    def test_accepts_components_dict(self) -> None:
        req = ValidateRequestV1(components={"address_number": "123", "street_name": "MAIN"})
        assert req.components == {"address_number": "123", "street_name": "MAIN"}
        assert req.address is None

    def test_both_fields_none_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            ValidateRequestV1()

    def test_country_defaults_to_us(self) -> None:
        req = ValidateRequestV1(address="123 Main St")
        assert req.country == "US"
