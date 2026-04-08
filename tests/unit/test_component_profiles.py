"""Tests for component_profiles translation layer."""

from address_validator.services.component_profiles import (
    VALID_PROFILES,
    translate_components,
)


class TestTranslateComponents:
    def test_iso_profile_is_identity(self) -> None:
        values = {"thoroughfare_name": "MAIN", "administrative_area": "WA", "postcode": "98101"}
        assert translate_components(values, "iso-19160-4") == values

    def test_usps_pub28_renames_core_keys(self) -> None:
        values = {
            "premise_number": "123",
            "thoroughfare_name": "MAIN",
            "thoroughfare_trailing_type": "ST",
            "locality": "SEATTLE",
            "administrative_area": "WA",
            "postcode": "98101",
        }
        result = translate_components(values, "usps-pub28")
        assert result["address_number"] == "123"
        assert result["street_name"] == "MAIN"
        assert result["street_name_post_type"] == "ST"
        assert result["city"] == "SEATTLE"
        assert result["state"] == "WA"
        assert result["zip_code"] == "98101"
        assert "premise_number" not in result
        assert "thoroughfare_name" not in result

    def test_unknown_keys_pass_through_unchanged(self) -> None:
        values = {"premise_number": "1", "some_future_key": "X"}
        result = translate_components(values, "usps-pub28")
        assert result["address_number"] == "1"
        assert result["some_future_key"] == "X"

    def test_unknown_profile_is_identity(self) -> None:
        values = {"thoroughfare_name": "OAK"}
        assert translate_components(values, "unknown-profile") == values

    def test_canada_post_profile_is_identity(self) -> None:
        # canada-post is reserved; currently identical to iso-19160-4
        values = {"thoroughfare_name": "MAIN", "postcode": "V5K 0A1"}
        assert translate_components(values, "canada-post") == values

    def test_valid_profiles_contains_expected_values(self) -> None:
        assert "iso-19160-4" in VALID_PROFILES
        assert "usps-pub28" in VALID_PROFILES
        assert "canada-post" in VALID_PROFILES
