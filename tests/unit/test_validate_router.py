"""HTTP-level tests for POST /api/v1/validate."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from address_validator.main import app
from address_validator.models import (
    ComponentSet,
    StandardizeResponseV1,
    ValidateResponseV1,
    ValidationResult,
)
from address_validator.services.validation.errors import ProviderRateLimitedError
from address_validator.services.validation.google_provider import GoogleProvider

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
        assert resp.json()["error"] == "validation_error"

    def test_missing_both_fields_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={},
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "validation_error"

    def test_empty_components_dict_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={"components": {}},
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "validation_error"

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
        assert resp.json()["error"] == "validation_error"

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

    def test_address_string_raw_input_passed_to_provider(self, client: TestClient) -> None:
        """The original address string is passed as raw_input to the provider."""
        provider = _make_null_provider(NULL_RESPONSE)
        with _mock_registry_with(provider):
            client.post(
                "/api/v1/validate",
                json={"address": "123 Main St, Springfield, IL 62701"},
            )
        kwargs = provider.validate.call_args.kwargs
        assert kwargs.get("raw_input") == "123 Main St, Springfield, IL 62701"

    def test_components_raw_input_is_json(self, client: TestClient) -> None:
        """Component dict input is JSON-serialised as raw_input."""
        comps = {
            "address_number": "123",
            "street_name": "MAIN",
            "street_suffix": "ST",
            "city": "SPRINGFIELD",
            "region": "IL",
            "postal_code": "62701",
        }
        provider = _make_null_provider(NULL_RESPONSE)
        with _mock_registry_with(provider):
            client.post("/api/v1/validate", json={"components": comps})

        raw = provider.validate.call_args.kwargs.get("raw_input")
        assert raw is not None
        assert json.loads(raw) == comps


def _make_null_provider(response: ValidateResponseV1) -> AsyncMock:
    """Return a mock provider whose validate() coroutine returns *response*."""
    provider = AsyncMock()
    provider.validate = AsyncMock(return_value=response)
    return provider


def _make_google_provider(response: ValidateResponseV1) -> AsyncMock:
    """Return a GoogleProvider-typed mock whose validate() coroutine returns *response*."""
    provider = AsyncMock(spec=GoogleProvider)
    provider.validate = AsyncMock(return_value=response)
    return provider


# -- Non-US tests ----------------------------------------------------------


class TestValidateNonUS:
    def test_invalid_country_code_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={
                "components": {"address_line_1": "1 Main St", "city": "Testville"},
                "country": "XX",
            },
        )
        assert resp.status_code == 422
        assert resp.json()["error"] == "invalid_country_code"

    def test_non_us_raw_string_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={"address": "10 Downing St, London SW1A 2AA", "country": "GB"},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"] == "country_not_supported"

    def test_non_us_raw_string_error_message_mentions_components(self, client: TestClient) -> None:
        resp = client.post(
            "/api/v1/validate",
            json={"address": "10 Downing St, London SW1A 2AA", "country": "GB"},
        )
        assert "components" in resp.json()["message"].lower()

    def test_non_us_components_calls_provider(self, client: TestClient) -> None:
        provider = _make_google_provider(
            ValidateResponseV1(
                country="GB",
                validation=ValidationResult(status="unavailable"),
            )
        )
        with _mock_registry_with(provider):
            resp = client.post(
                "/api/v1/validate",
                json={
                    "components": {
                        "address_line_1": "10 Downing St",
                        "city": "London",
                        "postal_code": "SW1A 2AA",
                    },
                    "country": "GB",
                },
            )
        assert resp.status_code == 200
        provider.validate.assert_awaited_once()

    def test_non_us_components_provider_receives_correct_country(self, client: TestClient) -> None:
        provider = _make_google_provider(
            ValidateResponseV1(
                country="GB",
                validation=ValidationResult(status="unavailable"),
            )
        )
        with _mock_registry_with(provider):
            client.post(
                "/api/v1/validate",
                json={
                    "components": {
                        "address_line_1": "10 Downing St",
                        "city": "London",
                        "postal_code": "SW1A 2AA",
                    },
                    "country": "GB",
                },
            )
        std_arg = provider.validate.call_args[0][0]
        assert std_arg.country == "GB"

    def test_non_us_components_skips_usps_standardize(self, client: TestClient) -> None:
        # The provider should receive the raw component values, not USPS-munged ones
        provider = _make_google_provider(
            ValidateResponseV1(
                country="GB",
                validation=ValidationResult(status="unavailable"),
            )
        )
        with _mock_registry_with(provider):
            client.post(
                "/api/v1/validate",
                json={
                    "components": {
                        "address_line_1": "10 Downing St",
                        "city": "London",
                        "postal_code": "SW1A 2AA",
                    },
                    "country": "GB",
                },
            )
        std_arg = provider.validate.call_args[0][0]
        # city should be exactly as supplied — not USPS-uppercased/truncated
        assert std_arg.city == "London"
        assert std_arg.address_line_1 == "10 Downing St"

    def test_non_us_components_no_google_provider_returns_422(self, client: TestClient) -> None:
        # A non-Google provider must 422 for non-US addresses
        non_google_provider = _make_null_provider(NULL_RESPONSE)
        with _mock_registry_with(non_google_provider):
            resp = client.post(
                "/api/v1/validate",
                json={
                    "components": {
                        "address_line_1": "10 Downing St",
                        "city": "London",
                        "postal_code": "SW1A 2AA",
                    },
                    "country": "GB",
                },
            )
        assert resp.status_code == 422
        assert resp.json()["error"] == "country_not_supported"

    def test_us_requests_still_work(self, client: TestClient) -> None:
        with _mock_registry_with(_make_null_provider(NULL_RESPONSE)):
            resp = client.post(
                "/api/v1/validate",
                json={"address": "123 Main St, Springfield, IL 62701"},
            )
        assert resp.status_code == 200


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
