"""Unit tests for services/standardizer.py."""

import logging

import pytest

from address_validator.services.standardizer import _get, _lookup, _std_zip, standardize
from address_validator.usps_data.directionals import DIRECTIONAL_MAP
from address_validator.usps_data.states import STATE_MAP
from address_validator.usps_data.suffixes import SUFFIX_MAP

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
            "premise_number": "123",
            "thoroughfare_name": "MAIN",
            "thoroughfare_trailing_type": "STREET",
            "locality": "SPRINGFIELD",
            "administrative_area": "IL",
            "postcode": "62701",
        }
        result = standardize(comps)
        assert result.address_line_1 == "123 MAIN ST"
        assert result.city == "SPRINGFIELD"
        assert result.region == "IL"
        assert result.postal_code == "62701"

    def test_directional_abbreviated(self) -> None:
        comps = {
            "premise_number": "100",
            "thoroughfare_pre_direction": "NORTH",
            "thoroughfare_name": "OAK",
            "thoroughfare_trailing_type": "AVE",
        }
        result = standardize(comps)
        assert "N" in result.address_line_1
        assert "NORTH" not in result.address_line_1

    def test_state_abbreviated(self) -> None:
        comps = {"locality": "OLYMPIA", "administrative_area": "WASHINGTON", "postcode": "98501"}
        result = standardize(comps)
        assert result.region == "WA"

    def test_zip_nine_digit_formatted(self) -> None:
        comps = {"postcode": "981011234"}
        result = standardize(comps)
        assert result.postal_code == "98101-1234"

    def test_unit_without_designator_gets_hash(self) -> None:
        comps = {
            "premise_number": "10",
            "thoroughfare_name": "ELM",
            "sub_premise_number": "4B",
        }
        result = standardize(comps)
        assert result.address_line_2 == "# 4B"

    def test_suite_in_line_2(self) -> None:
        comps = {
            "premise_number": "10",
            "thoroughfare_name": "ELM",
            "sub_premise_type": "SUITE",
            "sub_premise_number": "300",
        }
        result = standardize(comps)
        assert result.address_line_2 == "STE 300"

    def test_building_name_recovery(self) -> None:
        """BLD C in premise_name should be recovered as BLDG C."""
        comps = {
            "premise_number": "1",
            "thoroughfare_name": "CAMPUS",
            "thoroughfare_trailing_type": "DR",
            "premise_name": "BLD C",
        }
        result = standardize(comps)
        assert "BLDG" in result.address_line_2
        assert "C" in result.address_line_2

    def test_po_box_address_line1(self) -> None:
        """general_delivery_type/general_delivery produce PO BOX line 1."""
        comps = {
            "general_delivery_type": "PO BOX",
            "general_delivery": "42",
            "locality": "SMALLTOWN",
            "administrative_area": "TX",
            "postcode": "79901",
        }
        result = standardize(comps)
        assert result.address_line_1 == "PO BOX 42"
        assert result.city == "SMALLTOWN"
        assert result.region == "TX"
        assert result.postal_code == "79901"
        assert "general_delivery_type" in result.components.values
        assert "general_delivery" in result.components.values

    def test_both_occupancy_and_subaddress_in_line2(self) -> None:
        """STE 300 and SMP 2 should both appear on line 2."""
        comps = {
            "premise_number": "100",
            "thoroughfare_name": "MAIN",
            "sub_premise_type": "STE",
            "sub_premise_number": "300",
            "dependent_sub_premise_type": "SMP",
            "dependent_sub_premise_number": "2",
        }
        result = standardize(comps)
        assert "STE" in result.address_line_2
        assert "300" in result.address_line_2
        assert "SMP" in result.address_line_2
        assert "2" in result.address_line_2

    def test_standardized_two_space_separator(self) -> None:
        comps = {
            "premise_number": "123",
            "thoroughfare_name": "MAIN",
            "thoroughfare_trailing_type": "ST",
            "locality": "SPRINGFIELD",
            "administrative_area": "IL",
            "postcode": "62701",
        }
        result = standardize(comps)
        # Non-empty parts should be joined with two spaces.
        assert "  " in result.standardized

    def test_intersection_assembly(self) -> None:
        comps = {
            "thoroughfare_name": "FIRST",
            "thoroughfare_trailing_type": "ST",
            "second_thoroughfare_name": "SECOND",
            "second_thoroughfare_trailing_type": "AVE",
        }
        result = standardize(comps)
        assert "&" in result.address_line_1

    def test_designator_word_extracted_from_identifier(self) -> None:
        """'NO. 16' in sub_premise_number should become type='#', id='16'."""
        comps = {
            "premise_number": "5",
            "thoroughfare_name": "ELM",
            "sub_premise_number": "NO. 16",
        }
        result = standardize(comps)
        assert "16" in result.address_line_2

    def test_country_propagated(self) -> None:
        result = standardize({}, country="US")
        assert result.country == "US"

    def test_components_have_spec(self) -> None:
        result = standardize({"premise_number": "1", "thoroughfare_name": "A"})
        assert result.components.spec == "usps-pub28"

    def test_no_warnings_on_clean_input(self) -> None:
        comps = {
            "premise_number": "123",
            "thoroughfare_name": "MAIN",
            "thoroughfare_trailing_type": "STREET",
            "locality": "SPRINGFIELD",
            "administrative_area": "IL",
            "postcode": "62701",
        }
        result = standardize(comps)
        assert result.warnings == []

    def test_upstream_warnings_propagated(self) -> None:
        comps = {"premise_number": "1", "thoroughfare_name": "ELM"}
        result = standardize(comps, upstream_warnings=["Parenthesized text removed: '(FOO)'"])
        assert "Parenthesized text removed: '(FOO)'" in result.warnings

    def test_warnings_empty_list_by_default(self) -> None:
        result = standardize({})
        assert isinstance(result.warnings, list)
        assert result.warnings == []


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class TestStandardizerLogging:
    def test_debug_emitted_on_standardize(self, caplog: pytest.LogCaptureFixture) -> None:
        components = {
            "premise_number": "123",
            "thoroughfare_name": "MAIN",
            "locality": "SPRINGFIELD",
        }
        with caplog.at_level(logging.DEBUG, logger="address_validator.services.standardizer"):
            standardize(components)
        assert "standardizing components" in caplog.text
        assert "count=3" in caplog.text


class TestStandardizeCA:
    def test_province_abbreviation_normalised(self) -> None:
        comps = {
            "premise_number": "123",
            "thoroughfare_name": "MAIN",
            "thoroughfare_trailing_type": "ST",
            "locality": "TORONTO",
            "administrative_area": "ONTARIO",  # full name
            "postcode": "M5V 2T6",
        }
        result = standardize(comps, country="CA")
        assert result.components.values["administrative_area"] == "ON"
        assert result.region == "ON"

    def test_postal_code_uppercase_and_spaced(self) -> None:
        comps = {
            "premise_number": "100",
            "thoroughfare_name": "OAK",
            "thoroughfare_trailing_type": "AVE",
            "locality": "VANCOUVER",
            "administrative_area": "BC",
            "postcode": "v5k0a1",  # lowercase, no space
        }
        result = standardize(comps, country="CA")
        assert result.components.values["postcode"] == "V5K 0A1"
        assert result.postal_code == "V5K 0A1"

    def test_suffix_normalised(self) -> None:
        comps = {
            "premise_number": "200",
            "thoroughfare_name": "ELM",
            "thoroughfare_trailing_type": "STREET",  # full → ST
            "locality": "OTTAWA",
            "administrative_area": "ON",
            "postcode": "K1A 0A6",
        }
        result = standardize(comps, country="CA")
        assert result.components.values["thoroughfare_trailing_type"] == "ST"

    def test_spec_is_canada_post(self) -> None:
        comps = {
            "premise_number": "1",
            "thoroughfare_name": "TEST",
            "locality": "MONTREAL",
            "administrative_area": "QC",
            "postcode": "H3A 1A1",
        }
        result = standardize(comps, country="CA")
        assert result.components.spec == "canada-post"
        assert result.components.spec_version == "2025"

    def test_standardized_string_built(self) -> None:
        comps = {
            "premise_number": "123",
            "thoroughfare_name": "MAIN",
            "thoroughfare_trailing_type": "ST",
            "locality": "TORONTO",
            "administrative_area": "ON",
            "postcode": "M5V 2T6",
        }
        result = standardize(comps, country="CA")
        assert result.standardized  # non-empty
        assert "TORONTO" in result.standardized
        assert "ON" in result.standardized
        assert "M5V 2T6" in result.standardized

    def test_unrecognised_province_warns(self) -> None:
        comps = {
            "premise_number": "1",
            "thoroughfare_name": "TEST",
            "locality": "TESTVILLE",
            "administrative_area": "XX",  # not in PROVINCE_MAP
            "postcode": "A1A 1A1",
        }
        result = standardize(comps, country="CA")
        assert any("Unrecognised" in w for w in result.warnings)
        assert result.components.values["administrative_area"] == "XX"

    def test_directional_normalised(self) -> None:
        comps = {
            "premise_number": "350",
            "thoroughfare_name": "MAIN",
            "thoroughfare_post_direction": "NORTH",  # full → N
            "locality": "TORONTO",
            "administrative_area": "ON",
            "postcode": "M5V 2T6",
        }
        result = standardize(comps, country="CA")
        assert result.components.values["thoroughfare_post_direction"] == "N"

    def test_french_directional_normalised(self) -> None:
        comps = {
            "premise_number": "350",
            "thoroughfare_name": "DES LILAS",
            "thoroughfare_post_direction": "OUEST",  # French full → O
            "locality": "QUEBEC",
            "administrative_area": "QC",
            "postcode": "G1L 1B6",
        }
        result = standardize(comps, country="CA")
        assert result.components.values["thoroughfare_post_direction"] == "O"

    def test_non_normalized_fields_uppercased(self) -> None:
        """Fields not explicitly normalized (city, street name) are uppercased via _get."""
        comps = {
            "premise_number": "123",
            "thoroughfare_name": "main",  # lowercase
            "locality": "toronto",  # lowercase
            "administrative_area": "ON",
            "postcode": "M5V 2T6",
        }
        result = standardize(comps, country="CA")
        assert result.city == "TORONTO"
        assert result.components.values["thoroughfare_name"] == "MAIN"
