"""Unit tests for core/countries.py."""

import pytest

from address_validator.core.countries import (
    US_POSTAL_COUNTRIES,
    check_country,
    territory_hint,
)
from address_validator.core.errors import APIError

_TERRITORIES = sorted(US_POSTAL_COUNTRIES - {"US"})


class TestTerritoryHint:
    def test_covers_the_five_territories(self) -> None:
        assert _TERRITORIES == ["AS", "GU", "MP", "PR", "VI"]

    @pytest.mark.parametrize("code", _TERRITORIES)
    def test_territory_points_at_country_us(self, code: str) -> None:
        hint = territory_hint(code)
        assert "country=US" in hint
        assert f"region={code}" in hint

    @pytest.mark.parametrize("code", ["US", "CA", "GB", "FM", "MH", "PW"])
    def test_other_countries_get_no_hint(self, code: str) -> None:
        """FM/MH/PW are sovereign: USPS delivers there, but they are not US territory."""
        assert territory_hint(code) == ""


class TestCheckCountry:
    @pytest.mark.parametrize("code", ["us", "US", "ca"])
    def test_supported_returns_uppercased(self, code: str) -> None:
        assert check_country(code) == code.upper()

    def test_invalid_code(self) -> None:
        with pytest.raises(APIError) as exc_info:
            check_country("XX")
        assert exc_info.value.error == "invalid_country_code"

    def test_unsupported_message_lists_supported(self) -> None:
        with pytest.raises(APIError) as exc_info:
            check_country("GB")
        assert exc_info.value.status_code == 422
        assert exc_info.value.error == "country_not_supported"
        assert exc_info.value.message == "Currently supported: CA, US."

    @pytest.mark.parametrize("code", _TERRITORIES)
    def test_territory_message_adds_hint(self, code: str) -> None:
        """GH #281: same error code, message steers the caller to country=US."""
        with pytest.raises(APIError) as exc_info:
            check_country(code.lower())
        assert exc_info.value.error == "country_not_supported"
        assert exc_info.value.message == "Currently supported: CA, US." + territory_hint(code)
