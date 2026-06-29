"""Tests for component_profiles translation layer."""

import pytest

from address_validator.core.errors import APIError
from address_validator.models import ComponentSet
from address_validator.services.component_profiles import (
    VALID_PROFILES,
    build_output_component_set,
    translate_components,
    translate_components_to_iso,
    valid_component_profile,
)


class TestBuildOutputComponentSet:
    def _src(self, spec: str = "usps-pub28", version: str = "60") -> ComponentSet:
        return ComponentSet(
            spec=spec,
            spec_version=version,
            values={"thoroughfare_name": "MAIN", "administrative_area": "WA"},
        )

    def test_iso_profile_us_relabels_iso(self) -> None:
        result = build_output_component_set(self._src(), "iso-19160-4", "US")
        assert result.spec == "iso-19160-4"
        assert result.spec_version == "2020"
        # identity translation
        assert result.values["thoroughfare_name"] == "MAIN"

    def test_usps_profile_us_keeps_source_spec_and_translates(self) -> None:
        result = build_output_component_set(self._src(), "usps-pub28", "US")
        assert result.spec == "usps-pub28"
        assert result.spec_version == "60"
        assert result.values["street_name"] == "MAIN"
        assert result.values["state"] == "WA"

    def test_ca_iso_profile_keeps_source_spec(self) -> None:
        # standardize CA path: source spec is canada-post.
        src = self._src(spec="canada-post", version="2025")
        result = build_output_component_set(src, "iso-19160-4", "CA")
        assert result.spec == "canada-post"
        assert result.spec_version == "2025"
        # ISO/canada-post profile is identity translation
        assert result.values["thoroughfare_name"] == "MAIN"

    def test_ca_parse_path_keeps_raw_spec(self) -> None:
        # parse CA path: source spec is raw (libpostal, no standardization).
        # This is the #134 convergence — parse now defers to source spec for CA
        # instead of relabelling ISO.
        src = self._src(spec="raw", version="1")
        result = build_output_component_set(src, "iso-19160-4", "CA")
        assert result.spec == "raw"
        assert result.spec_version == "1"

    def test_ca_usps_profile_keeps_source_spec_and_translates(self) -> None:
        src = self._src(spec="raw", version="1")
        result = build_output_component_set(src, "usps-pub28", "CA")
        assert result.spec == "raw"
        assert result.values["street_name"] == "MAIN"


class TestValidComponentProfile:
    def test_returns_valid_profile_unchanged(self) -> None:
        for profile in VALID_PROFILES:
            assert valid_component_profile(profile) == profile

    def test_default_profile_is_valid(self) -> None:
        # The dependency default must itself pass the guard.
        assert valid_component_profile("iso-19160-4") == "iso-19160-4"

    def test_invalid_profile_raises_apierror_422(self) -> None:
        with pytest.raises(APIError) as exc_info:
            valid_component_profile("bad-profile")
        exc = exc_info.value
        assert exc.status_code == 422
        assert exc.error == "invalid_component_profile"
        assert "bad-profile" in exc.message


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


class TestTranslateComponentsToISO:
    def test_usps_pub28_to_iso(self) -> None:
        values = {
            "address_number": "123",
            "street_name": "MAIN",
            "city": "SEATTLE",
            "state": "WA",
            "zip_code": "98101",
        }
        result = translate_components_to_iso(values, "usps-pub28")
        assert result["premise_number"] == "123"
        assert result["thoroughfare_name"] == "MAIN"
        assert result["locality"] == "SEATTLE"
        assert result["administrative_area"] == "WA"
        assert result["postcode"] == "98101"
        assert "address_number" not in result
        assert "street_name" not in result

    def test_iso_profile_is_identity(self) -> None:
        values = {"premise_number": "1", "locality": "SEATTLE"}
        assert translate_components_to_iso(values, "iso-19160-4") == values

    def test_unknown_profile_is_identity(self) -> None:
        values = {"premise_number": "1"}
        assert translate_components_to_iso(values, "unknown") == values

    def test_round_trip(self) -> None:
        """ISO -> USPS -> ISO is identity."""
        iso_values = {
            "premise_number": "123",
            "thoroughfare_name": "MAIN",
            "locality": "SEATTLE",
            "administrative_area": "WA",
            "postcode": "98101",
        }
        usps = translate_components(iso_values, "usps-pub28")
        back = translate_components_to_iso(usps, "usps-pub28")
        assert back == iso_values
