"""Tests for Canada Post data tables."""

from address_validator.canada_post_data.provinces import PROVINCE_MAP
from address_validator.canada_post_data.spec import CANADA_POST_SPEC, CANADA_POST_SPEC_VERSION
from address_validator.canada_post_data.suffixes import CA_SUFFIX_MAP


class TestProvinceMap:
    def test_has_13_distinct_abbreviations(self) -> None:
        assert len(set(PROVINCE_MAP.values())) == 13

    def test_all_values_are_2_char_uppercase(self) -> None:
        for abbr in PROVINCE_MAP.values():
            assert len(abbr) == 2
            assert abbr == abbr.upper()

    def test_lookup_by_full_name_case_insensitive(self) -> None:
        assert PROVINCE_MAP.get("ONTARIO") == "ON"
        assert PROVINCE_MAP.get("BRITISH COLUMBIA") == "BC"
        assert PROVINCE_MAP.get("QUEBEC") == "QC"

    def test_abbreviation_maps_to_itself(self) -> None:
        # Abbreviations should round-trip: ON → ON
        assert PROVINCE_MAP.get("ON") == "ON"
        assert PROVINCE_MAP.get("BC") == "BC"
        assert PROVINCE_MAP.get("QC") == "QC"

    def test_all_13_provinces_and_territories_present(self) -> None:
        expected_abbrs = {
            "AB",
            "BC",
            "MB",
            "NB",
            "NL",
            "NS",
            "NT",
            "NU",
            "ON",
            "PE",
            "QC",
            "SK",
            "YT",
        }
        assert expected_abbrs <= set(PROVINCE_MAP.values())


class TestSuffixMap:
    def test_common_english_suffixes_present(self) -> None:
        assert CA_SUFFIX_MAP.get("STREET") == "ST"
        assert CA_SUFFIX_MAP.get("AVENUE") == "AVE"
        assert CA_SUFFIX_MAP.get("BOULEVARD") == "BLVD"
        assert CA_SUFFIX_MAP.get("DRIVE") == "DR"
        assert CA_SUFFIX_MAP.get("ROAD") == "RD"
        assert CA_SUFFIX_MAP.get("CRESCENT") == "CRES"

    def test_french_suffixes_present(self) -> None:
        assert CA_SUFFIX_MAP.get("RUE") == "RUE"
        assert CA_SUFFIX_MAP.get("BOULEVARD") == "BLVD"
        assert CA_SUFFIX_MAP.get("CHEMIN") == "CH"


class TestSpec:
    def test_spec_constants(self) -> None:
        assert CANADA_POST_SPEC == "canada-post"
        assert CANADA_POST_SPEC_VERSION == "2025"
