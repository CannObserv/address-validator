"""Integration tests for POST /api/v2/standardize with country=CA."""

from unittest.mock import AsyncMock, patch

from address_validator.services.libpostal_client import LibpostalUnavailableError


class TestV2StandardizeCA:
    def test_ca_address_returns_canada_post_spec(self, client) -> None:
        mock_parse = {
            "premise_number": "123",
            "thoroughfare_name": "MAIN",
            "thoroughfare_trailing_type": "STREET",
            "locality": "TORONTO",
            "administrative_area": "ONTARIO",
            "postcode": "m5v2t6",
        }
        with patch(
            "address_validator.services.libpostal_client.LibpostalClient.parse",
            new_callable=AsyncMock,
            return_value=mock_parse,
        ):
            response = client.post(
                "/api/v2/standardize",
                json={
                    "address": "123 Main Street Toronto Ontario M5V 2T6",
                    "country": "CA",
                },
            )
        assert response.status_code == 200
        body = response.json()
        assert body["components"]["spec"] == "canada-post"
        assert body["components"]["spec_version"] == "2025"
        values = body["components"]["values"]
        assert values["administrative_area"] == "ON"
        assert values["postcode"] == "M5V 2T6"
        assert values["thoroughfare_trailing_type"] == "ST"
        assert body["region"] == "ON"
        assert body["postal_code"] == "M5V 2T6"

    def test_ca_not_available_in_v1_standardize(self, client) -> None:
        response = client.post(
            "/api/v1/standardize",
            json={"address": "123 Main St Toronto ON M5V 2T6", "country": "CA"},
        )
        assert response.status_code == 422
        assert response.json()["error"] == "country_not_supported"

    def test_ca_standardize_with_components_input(self, client) -> None:
        response = client.post(
            "/api/v2/standardize",
            json={
                "country": "CA",
                "components": {
                    "premise_number": "100",
                    "thoroughfare_name": "OAK",
                    "thoroughfare_trailing_type": "AVENUE",
                    "locality": "VANCOUVER",
                    "administrative_area": "BC",
                    "postcode": "v5k0a1",
                },
            },
        )
        assert response.status_code == 200
        values = response.json()["components"]["values"]
        assert values["administrative_area"] == "BC"
        assert values["postcode"] == "V5K 0A1"
        assert values["thoroughfare_trailing_type"] == "AVE"

    def test_french_ca_address_standardized(self, client) -> None:
        mock_parse = {
            "premise_number": "350",
            "thoroughfare_leading_type": "RUE",
            "thoroughfare_name": "DES LILAS",
            "thoroughfare_post_direction": "O",
            "locality": "QUEBEC",
            "administrative_area": "QC",
            "postcode": "g1l1b6",
        }
        with patch(
            "address_validator.services.libpostal_client.LibpostalClient.parse",
            new_callable=AsyncMock,
            return_value=mock_parse,
        ):
            response = client.post(
                "/api/v2/standardize",
                json={
                    "address": "350 rue des Lilas Ouest, Quebec QC G1L 1B6",
                    "country": "CA",
                },
            )
        assert response.status_code == 200
        values = response.json()["components"]["values"]
        assert values["thoroughfare_leading_type"] == "RUE"
        assert values["postcode"] == "G1L 1B6"
        assert values["administrative_area"] == "QC"

    def test_ca_libpostal_unavailable_returns_503(self, client) -> None:
        with patch(
            "address_validator.services.libpostal_client.LibpostalClient.parse",
            new_callable=AsyncMock,
            side_effect=LibpostalUnavailableError("sidecar down"),
        ):
            response = client.post(
                "/api/v2/standardize",
                json={"address": "123 Main St Toronto ON M5V 2T6", "country": "CA"},
            )
        assert response.status_code == 503
        assert response.json()["error"] == "parsing_unavailable"
