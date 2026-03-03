"""Unit tests for the USPS lookup tables in usps_data/."""

from usps_data.directionals import DIRECTIONAL_MAP
from usps_data.states import STATE_MAP
from usps_data.suffixes import SUFFIX_MAP
from usps_data.units import UNIT_MAP


class TestSuffixMap:
    def test_street_abbreviation(self) -> None:
        assert SUFFIX_MAP["STREET"] == "ST"

    def test_avenue_abbreviation(self) -> None:
        assert SUFFIX_MAP["AVENUE"] == "AVE"

    def test_drive_abbreviation(self) -> None:
        assert SUFFIX_MAP["DRIVE"] == "DR"

    def test_boulevard_abbreviation(self) -> None:
        assert SUFFIX_MAP["BOULEVARD"] == "BLVD"

    def test_canonical_form_maps_to_itself(self) -> None:
        """Canonical abbreviations are idempotent (ST -> ST)."""
        assert SUFFIX_MAP["ST"] == "ST"

    def test_all_values_are_uppercase(self) -> None:
        assert all(v == v.upper() for v in SUFFIX_MAP.values())

    def test_all_keys_are_uppercase(self) -> None:
        assert all(k == k.upper() for k in SUFFIX_MAP)


class TestDirectionalMap:
    def test_north_abbreviation(self) -> None:
        assert DIRECTIONAL_MAP["NORTH"] == "N"

    def test_southeast_abbreviation(self) -> None:
        assert DIRECTIONAL_MAP["SOUTHEAST"] == "SE"

    def test_canonical_form_maps_to_itself(self) -> None:
        assert DIRECTIONAL_MAP["NW"] == "NW"

    def test_all_values_are_uppercase(self) -> None:
        assert all(v == v.upper() for v in DIRECTIONAL_MAP.values())


class TestStateMap:
    def test_washington_abbreviation(self) -> None:
        assert STATE_MAP["WASHINGTON"] == "WA"

    def test_california_abbreviation(self) -> None:
        assert STATE_MAP["CALIFORNIA"] == "CA"

    def test_canonical_form_maps_to_itself(self) -> None:
        assert STATE_MAP["WA"] == "WA"

    def test_all_values_are_two_chars(self) -> None:
        assert all(len(v) == 2 for v in STATE_MAP.values())


class TestUnitMap:
    def test_suite_abbreviation(self) -> None:
        assert UNIT_MAP["SUITE"] == "STE"

    def test_building_abbreviation(self) -> None:
        assert UNIT_MAP["BUILDING"] == "BLDG"

    def test_apartment_abbreviation(self) -> None:
        assert UNIT_MAP["APARTMENT"] == "APT"

    def test_canonical_form_maps_to_itself(self) -> None:
        assert UNIT_MAP["STE"] == "STE"
