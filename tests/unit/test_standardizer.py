"""Unit tests for services/standardizer.py."""

import logging

import pytest

from services.standardizer import _get, _lookup, _std_zip, standardize, standardize_legacy
from usps_data.directionals import DIRECTIONAL_MAP
from usps_data.states import STATE_MAP
from usps_data.suffixes import SUFFIX_MAP

# ---------------------------------------------------------------------------
# _lookup
# ---------------------------------------------------------------------------


class TestLookup:
    def test_suffix_lookup(self) -> None:
        assert _lookup("STREET", SUFFIX_MAP) == "ST"

    def test_directional_lookup(self) -> None:
        assert _lookup("NORTH", DIRECTIONAL_MAP) == "N"

    def test_state_lookup(self) -> None:
        assert _lookup("WASHINGTON", STATE_MAP) == "WA"

    def test_unknown_value_returned_unchanged(self) -> None:
        assert _lookup("ZZZUNKNOWN", {}) == "ZZZUNKNOWN"

    def test_lowercase_normalised(self) -> None:
        assert _lookup("street", SUFFIX_MAP) == "ST"

    def test_periods_stripped(self) -> None:
        assert _lookup("ST.", SUFFIX_MAP) == "ST"


# ---------------------------------------------------------------------------
# _std_zip
# ---------------------------------------------------------------------------


class TestStdZip:
    def test_five_digit_passthrough(self) -> None:
        assert _std_zip("98101") == "98101"

    def test_nine_digit_formatted(self) -> None:
        assert _std_zip("981011234") == "98101-1234"

    def test_hyphenated_nine_digit(self) -> None:
        assert _std_zip("98101-1234") == "98101-1234"

    def test_short_zip_returned_as_is(self) -> None:
        result = _std_zip("981")
        assert result == "981"

    def test_ten_plus_digits_truncated_to_nine(self) -> None:
        """Extra digits beyond 9 are ignored."""
        result = _std_zip("981011234567")
        assert result == "98101-1234"

    def test_empty_string(self) -> None:
        assert _std_zip("") == ""


# ---------------------------------------------------------------------------
# _get
# ---------------------------------------------------------------------------


class TestGet:
    def test_strips_whitespace(self) -> None:
        assert _get({"k": "  hello  "}, "k") == "HELLO"

    def test_uppercases(self) -> None:
        assert _get({"k": "street"}, "k") == "STREET"

    def test_removes_periods(self) -> None:
        assert _get({"k": "N.W."}, "k") == "NW"

    def test_removes_parens(self) -> None:
        assert _get({"k": "(REAR)"}, "k") == "REAR"

    def test_strips_trailing_comma(self) -> None:
        assert _get({"k": "MAIN,"}, "k") == "MAIN"

    def test_strips_trailing_semicolon(self) -> None:
        assert _get({"k": "MAIN;"}, "k") == "MAIN"

    def test_missing_key_returns_empty(self) -> None:
        assert _get({}, "missing") == ""

    def test_none_value_returns_empty(self) -> None:
        assert _get({"k": None}, "k") == ""  # type: ignore[dict-item]


# ---------------------------------------------------------------------------
# standardize (v1)
# ---------------------------------------------------------------------------


class TestStandardize:
    def test_basic_address(self) -> None:
        comps = {
            "address_number": "123",
            "street_name": "MAIN",
            "street_name_post_type": "STREET",
            "city": "SPRINGFIELD",
            "state": "IL",
            "zip_code": "62701",
        }
        result = standardize(comps)
        assert result.address_line_1 == "123 MAIN ST"
        assert result.city == "SPRINGFIELD"
        assert result.region == "IL"
        assert result.postal_code == "62701"

    def test_directional_abbreviated(self) -> None:
        comps = {
            "address_number": "100",
            "street_name_pre_directional": "NORTH",
            "street_name": "OAK",
            "street_name_post_type": "AVE",
        }
        result = standardize(comps)
        assert "N" in result.address_line_1
        assert "NORTH" not in result.address_line_1

    def test_state_abbreviated(self) -> None:
        comps = {"city": "OLYMPIA", "state": "WASHINGTON", "zip_code": "98501"}
        result = standardize(comps)
        assert result.region == "WA"

    def test_zip_nine_digit_formatted(self) -> None:
        comps = {"zip_code": "981011234"}
        result = standardize(comps)
        assert result.postal_code == "98101-1234"

    def test_unit_without_designator_gets_hash(self) -> None:
        comps = {
            "address_number": "10",
            "street_name": "ELM",
            "occupancy_identifier": "4B",
        }
        result = standardize(comps)
        assert result.address_line_2 == "# 4B"

    def test_suite_in_line_2(self) -> None:
        comps = {
            "address_number": "10",
            "street_name": "ELM",
            "occupancy_type": "SUITE",
            "occupancy_identifier": "300",
        }
        result = standardize(comps)
        assert result.address_line_2 == "STE 300"

    def test_building_name_recovery(self) -> None:
        """BLD C in building_name should be recovered as BLDG C."""
        comps = {
            "address_number": "1",
            "street_name": "CAMPUS",
            "street_name_post_type": "DR",
            "building_name": "BLD C",
        }
        result = standardize(comps)
        assert "BLDG" in result.address_line_2
        assert "C" in result.address_line_2

    def test_both_occupancy_and_subaddress_in_line2(self) -> None:
        """STE 300 and SMP 2 should both appear on line 2."""
        comps = {
            "address_number": "100",
            "street_name": "MAIN",
            "occupancy_type": "STE",
            "occupancy_identifier": "300",
            "subaddress_type": "SMP",
            "subaddress_identifier": "2",
        }
        result = standardize(comps)
        assert "STE" in result.address_line_2
        assert "300" in result.address_line_2
        assert "SMP" in result.address_line_2
        assert "2" in result.address_line_2

    def test_standardized_two_space_separator(self) -> None:
        comps = {
            "address_number": "123",
            "street_name": "MAIN",
            "street_name_post_type": "ST",
            "city": "SPRINGFIELD",
            "state": "IL",
            "zip_code": "62701",
        }
        result = standardize(comps)
        # Non-empty parts should be joined with two spaces.
        assert "  " in result.standardized

    def test_intersection_assembly(self) -> None:
        comps = {
            "street_name": "FIRST",
            "street_name_post_type": "ST",
            "second_street_name": "SECOND",
            "second_street_name_post_type": "AVE",
        }
        result = standardize(comps)
        assert "&" in result.address_line_1

    def test_designator_word_extracted_from_identifier(self) -> None:
        """'NO. 16' in occupancy_identifier should become type='#', id='16'."""
        comps = {
            "address_number": "5",
            "street_name": "ELM",
            "occupancy_identifier": "NO. 16",
        }
        result = standardize(comps)
        assert "16" in result.address_line_2

    def test_country_propagated(self) -> None:
        result = standardize({}, country="US")
        assert result.country == "US"

    def test_components_have_spec(self) -> None:
        result = standardize({"address_number": "1", "street_name": "A"})
        assert result.components.spec == "usps-pub28"


class TestStandardizeLegacy:
    def test_returns_legacy_shape(self) -> None:
        comps = {"state": "WASHINGTON", "zip_code": "98101"}
        result = standardize_legacy(comps)
        assert hasattr(result, "state")
        assert hasattr(result, "zip_code")
        assert result.state == "WA"

    @pytest.mark.parametrize(
        ("raw_state", "expected"),
        [
            ("WASHINGTON", "WA"),
            ("WA", "WA"),
            ("california", "CA"),
        ],
    )
    def test_state_normalisation(self, raw_state: str, expected: str) -> None:
        result = standardize_legacy({"state": raw_state})
        assert result.state == expected


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class TestStandardizerLogging:
    def test_debug_emitted_on_standardize(self, caplog: pytest.LogCaptureFixture) -> None:
        components = {"address_number": "123", "street_name": "MAIN", "city": "SPRINGFIELD"}
        with caplog.at_level(logging.DEBUG, logger="services.standardizer"):
            standardize(components)
        assert "standardizing components" in caplog.text
        assert "count=3" in caplog.text
