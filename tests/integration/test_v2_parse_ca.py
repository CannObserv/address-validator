"""Integration tests for POST /api/v2/parse with country=CA."""

from unittest.mock import AsyncMock, patch

import pytest

from address_validator.services.libpostal_client import LibpostalUnavailableError

pytestmark = pytest.mark.integration


class TestV2ParseCA:
    def test_ca_address_returns_200_with_iso_keys(self, client) -> None:
        mock_components = {
            "premise_number": "350",
            "thoroughfare_leading_type": "RUE",
            "thoroughfare_name": "DES LILAS",
            "thoroughfare_post_direction": "O",
            "locality": "QUEBEC",
            "administrative_area": "QC",
            "postcode": "G1L 1B6",
        }
        with patch(
            "address_validator.services.libpostal_client.LibpostalClient.parse",
            new_callable=AsyncMock,
            return_value=mock_components,
        ):
            response = client.post(
                "/api/v2/parse",
                json={
                    "address": "350 rue des Lilas Ouest, Quebec QC G1L 1B6",
                    "country": "CA",
                },
            )
        assert response.status_code == 200
        body = response.json()
        values = body["components"]["values"]
        assert values["premise_number"] == "350"
        assert values["thoroughfare_leading_type"] == "RUE"
        assert values["locality"] == "QUEBEC"
        assert values["administrative_area"] == "QC"
        assert values["postcode"] == "G1L 1B6"
        assert body["api_version"] == "2"
        assert body["country"] == "CA"

    def test_ca_not_available_in_v1(self, client) -> None:
        response = client.post(
            "/api/v1/parse",
            json={"address": "123 Main St", "country": "CA"},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "country_not_supported"

    def test_libpostal_unavailable_returns_503(self, client) -> None:
        with patch(
            "address_validator.services.libpostal_client.LibpostalClient.parse",
            new_callable=AsyncMock,
            side_effect=LibpostalUnavailableError("refused"),
        ):
            response = client.post(
                "/api/v2/parse",
                json={"address": "123 Main St", "country": "CA"},
            )
        assert response.status_code == 503
        assert response.json()["error"] == "parsing_unavailable"

    def test_ca_with_usps_profile_returns_translated_keys(self, client) -> None:
        mock_components = {
            "premise_number": "123",
            "thoroughfare_name": "MAIN",
            "thoroughfare_trailing_type": "ST",
            "locality": "TORONTO",
            "administrative_area": "ON",
            "postcode": "M5V 2T6",
        }
        with patch(
            "address_validator.services.libpostal_client.LibpostalClient.parse",
            new_callable=AsyncMock,
            return_value=mock_components,
        ):
            response = client.post(
                "/api/v2/parse?component_profile=usps-pub28",
                json={"address": "123 Main St Toronto ON M5V 2T6", "country": "CA"},
            )
        values = response.json()["components"]["values"]
        assert values["address_number"] == "123"
        assert values["street_name"] == "MAIN"
        assert values["city"] == "TORONTO"
        assert values["state"] == "ON"
        assert values["zip_code"] == "M5V 2T6"
