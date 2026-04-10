"""Integration tests for POST /api/v2/validate with country=CA."""

from unittest.mock import AsyncMock, MagicMock, patch

from address_validator.main import app
from address_validator.models import ValidateResponseV1, ValidationResult
from address_validator.services.libpostal_client import LibpostalUnavailableError


def _make_non_us_provider(response: ValidateResponseV1) -> AsyncMock:
    """Return a mock provider with supports_non_us=True."""
    provider = AsyncMock()
    provider.validate = AsyncMock(return_value=response)
    provider.supports_non_us = True
    return provider


def _mock_registry_with(provider):
    mock_reg = MagicMock()
    mock_reg.get_provider.return_value = provider
    return patch.object(app.state, "registry", mock_reg)


CA_UNAVAILABLE = ValidateResponseV1(
    country="CA",
    validation=ValidationResult(status="unavailable"),
)


class TestV2ValidateCA:
    def test_ca_raw_string_accepted_in_v2(self, client) -> None:
        """v2 validate accepts a raw CA address string via libpostal parse pipeline."""
        mock_parse = {
            "premise_number": "123",
            "thoroughfare_name": "MAIN",
            "thoroughfare_trailing_type": "ST",
            "locality": "TORONTO",
            "administrative_area": "ON",
            "postcode": "M5V 2T6",
        }
        provider = _make_non_us_provider(CA_UNAVAILABLE)
        with (
            _mock_registry_with(provider),
            patch(
                "address_validator.services.libpostal_client.LibpostalClient.parse",
                new_callable=AsyncMock,
                return_value=mock_parse,
            ),
        ):
            response = client.post(
                "/api/v2/validate",
                json={
                    "address": "123 Main St Toronto ON M5V 2T6",
                    "country": "CA",
                },
            )
        assert response.status_code == 200
        assert response.json()["api_version"] == "2"

    def test_ca_not_available_in_v1_validate_with_raw_string(self, client) -> None:
        response = client.post(
            "/api/v1/validate",
            json={"address": "123 Main St Toronto ON M5V 2T6", "country": "CA"},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "country_not_supported"

    def test_ca_components_input_accepted_in_v2(self, client) -> None:
        provider = _make_non_us_provider(CA_UNAVAILABLE)
        with _mock_registry_with(provider):
            response = client.post(
                "/api/v2/validate",
                json={
                    "country": "CA",
                    "components": {
                        "address_line_1": "123 MAIN ST",
                        "city": "TORONTO",
                        "region": "ON",
                        "postal_code": "M5V 2T6",
                    },
                },
            )
        assert response.status_code == 200

    def test_ca_libpostal_unavailable_returns_503(self, client) -> None:
        provider = _make_non_us_provider(CA_UNAVAILABLE)
        with (
            _mock_registry_with(provider),
            patch(
                "address_validator.services.libpostal_client.LibpostalClient.parse",
                new_callable=AsyncMock,
                side_effect=LibpostalUnavailableError("sidecar down"),
            ),
        ):
            response = client.post(
                "/api/v2/validate",
                json={"address": "123 Main St Toronto ON M5V 2T6", "country": "CA"},
            )
        assert response.status_code == 503
        assert response.json()["error"] == "parsing_unavailable"

    def test_non_ca_non_us_raw_string_still_422(self, client) -> None:
        """DE raw strings still rejected — only US and CA support raw input."""
        response = client.post(
            "/api/v2/validate",
            json={"address": "Unter den Linden 1, Berlin", "country": "DE"},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "country_not_supported"

    def test_ca_without_google_provider_returns_422(self, client) -> None:
        """CA validate without a Google-capable provider returns 422."""
        response = client.post(
            "/api/v2/validate",
            json={
                "country": "CA",
                "components": {
                    "address_line_1": "123 MAIN ST",
                    "city": "TORONTO",
                    "region": "ON",
                    "postal_code": "M5V 2T6",
                },
            },
        )
        assert response.status_code == 422
        assert response.json()["error"] == "country_not_supported"
