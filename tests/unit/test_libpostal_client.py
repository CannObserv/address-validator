"""Unit tests for LibpostalClient — httpx calls are mocked."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from address_validator.services.libpostal_client import (
    LibpostalClient,
    LibpostalUnavailableError,
)

_FAKE_REQUEST = httpx.Request("GET", "http://localhost:4400/parse")


def _ok_response(data: list[dict]) -> httpx.Response:
    """Build an httpx.Response with the request set so raise_for_status() works."""
    resp = httpx.Response(200, json=data, request=_FAKE_REQUEST)
    return resp


@pytest.fixture
def client() -> LibpostalClient:
    return LibpostalClient(base_url="http://localhost:4400")


class TestTagMapping:
    async def test_english_address_mapped_to_iso_keys(self, client: LibpostalClient) -> None:
        raw_response = [
            {"label": "house_number", "value": "123"},
            {"label": "road", "value": "main st"},
            {"label": "city", "value": "seattle"},
            {"label": "state", "value": "wa"},
            {"label": "postcode", "value": "98101"},
        ]
        with patch.object(client._http, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = _ok_response(raw_response)
            result = await client.parse("123 Main St Seattle WA 98101")

        assert result["premise_number"] == "123"
        assert result["locality"] == "SEATTLE"
        assert result["administrative_area"] == "WA"
        assert result["postcode"] == "98101"
        # road should be split into thoroughfare components
        assert "thoroughfare_name" in result or "thoroughfare_trailing_type" in result

    async def test_french_address_maps_rue_as_leading_type(self, client: LibpostalClient) -> None:
        raw_response = [
            {"label": "house_number", "value": "350"},
            {"label": "road", "value": "rue des lilas ouest"},
            {"label": "city", "value": "quebec"},
            {"label": "state", "value": "qc"},
            {"label": "postcode", "value": "g1l 1b6"},
        ]
        with patch.object(client._http, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = _ok_response(raw_response)
            result = await client.parse("350 rue des Lilas Ouest, Quebec QC G1L 1B6")

        assert result["premise_number"] == "350"
        assert result["thoroughfare_leading_type"] == "RUE"
        assert result["locality"] == "QUEBEC"
        assert result["administrative_area"] == "QC"
        assert result["postcode"] == "G1L 1B6"

    async def test_country_label_dropped(self, client: LibpostalClient) -> None:
        raw_response = [
            {"label": "house_number", "value": "1"},
            {"label": "road", "value": "main st"},
            {"label": "country", "value": "canada"},
        ]
        with patch.object(client._http, "get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = _ok_response(raw_response)
            result = await client.parse("1 Main St Canada")

        assert "country" not in result

    async def test_connection_error_raises_unavailable(self, client: LibpostalClient) -> None:
        with (
            patch.object(client._http, "get", side_effect=httpx.ConnectError("refused")),
            pytest.raises(LibpostalUnavailableError),
        ):
            await client.parse("123 Main St")

    async def test_timeout_raises_unavailable(self, client: LibpostalClient) -> None:
        with (
            patch.object(client._http, "get", side_effect=httpx.TimeoutException("timeout")),
            pytest.raises(LibpostalUnavailableError),
        ):
            await client.parse("123 Main St")
