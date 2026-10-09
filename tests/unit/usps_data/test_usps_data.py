"""Unit tests for the USPS lookup tables in usps_data/."""

import pytest

from address_validator.usps_data.directionals import DIRECTIONAL_MAP
from address_validator.usps_data.routes import BOX_TYPE_MAP, ROUTE_GROUP_TYPE_MAP
from address_validator.usps_data.states import MILITARY_STATES, STATE_MAP
from address_validator.usps_data.suffixes import SUFFIX_MAP
from address_validator.usps_data.units import UNIT_MAP


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

    @pytest.mark.parametrize(
        ("name", "abbreviation"),
        [
            ("ARMED FORCES AMERICAS", "AA"),
            ("ARMED FORCES EUROPE", "AE"),
            ("ARMED FORCES MIDDLE EAST", "AE"),
            ("ARMED FORCES CANADA", "AE"),
            ("ARMED FORCES PACIFIC", "AP"),
            ("AA", "AA"),
            ("AE", "AE"),
            ("AP", "AP"),
            ("FEDERATED STATES OF MICRONESIA", "FM"),
            ("FM", "FM"),
        ],
    )
    def test_pub28_appendix_b_military_and_freely_associated(
        self, name: str, abbreviation: str
    ) -> None:
        """GH-299: Appendix B's Armed Forces 'states' and FM are in the table."""
        assert STATE_MAP[name] == abbreviation

    def test_military_states_are_the_armed_forces_abbreviations(self) -> None:
        """MILITARY_STATES is exactly what the Armed Forces names map to."""
        armed_forces = {v for k, v in STATE_MAP.items() if k.startswith("ARMED FORCES ")}
        assert {"AA", "AE", "AP"} == armed_forces == MILITARY_STATES


class TestRouteGroupTypeMap:
    @pytest.mark.parametrize(
        ("variant", "canonical"),
        [
            ("RR", "RR"),
            ("RURAL ROUTE", "RR"),
            ("RFD", "RR"),
            ("RD", "RR"),
            ("RURAL FREE DELIVERY", "RR"),
            ("HC", "HC"),
            ("HIGHWAY CONTRACT", "HC"),
            ("HCR", "HC"),
            ("STAR ROUTE", "HC"),
        ],
    )
    def test_route_group_variants(self, variant: str, canonical: str) -> None:
        """GH-298: Pub 28 241/244 (RR) and 251/253 (HC)."""
        assert ROUTE_GROUP_TYPE_MAP[variant] == canonical

    @pytest.mark.parametrize("military", ["PSC", "CMR", "UNIT"])
    def test_military_groups_absent(self, military: str) -> None:
        """Military route designators are already Pub 28 forms; never remapped."""
        assert military not in ROUTE_GROUP_TYPE_MAP


class TestBoxTypeMap:
    @pytest.mark.parametrize(
        "variant",
        [
            "PO BOX",
            "POST OFFICE BOX",
            "P O BOX",
            "POB",
            "CALLER",
            "FIRM CALLER",
            "BIN",
            "LOCKBOX",
            "DRAWER",
            # GH-304: prefixed / two-word input variants usaddress tags as a box.
            "PO DRAWER",
            "P O DRAWER",
            "POST OFFICE DRAWER",
            "CALLER BOX",
            "FIRM CALLER BOX",
            "LOCK BOX",
        ],
    )
    def test_po_box_variants(self, variant: str) -> None:
        """GH-298: Pub 28 281/283 — every PO Box designation outputs PO BOX."""
        assert BOX_TYPE_MAP[variant] == "PO BOX"

    @pytest.mark.parametrize("other", ["BOX", "PMB", "GENERAL DELIVERY"])
    def test_non_po_box_types_absent(self, other: str) -> None:
        """'BOX' is also the rural route box; a PMB is not a PO Box."""
        assert other not in BOX_TYPE_MAP


class TestUnitMap:
    def test_suite_abbreviation(self) -> None:
        assert UNIT_MAP["SUITE"] == "STE"

    def test_building_abbreviation(self) -> None:
        assert UNIT_MAP["BUILDING"] == "BLDG"

    def test_apartment_abbreviation(self) -> None:
        assert UNIT_MAP["APARTMENT"] == "APT"

    def test_canonical_form_maps_to_itself(self) -> None:
        assert UNIT_MAP["STE"] == "STE"

    @pytest.mark.parametrize(
        ("variant", "canonical"),
        [
            ("UNITS", "UNIT"),
            ("SUITES", "STE"),
            ("STES", "STE"),
            ("SUTE", "STE"),
            ("FLOORS", "FL"),
            ("FLR", "FL"),
            ("BLG", "BLDG"),
        ],
    )
    def test_plural_and_misspelt_variants(self, variant: str, canonical: str) -> None:
        """GH-286: variants seen in prod DPV 'D' rows map to their Pub 28 form."""
        assert UNIT_MAP[variant] == canonical
