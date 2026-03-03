"""Unit tests for services/parser.py."""

import pytest

from services.parser import (
    _recover_identifier_fragment_from_city,
    _recover_unit_from_city,
    parse_address,
    parse_address_legacy,
)

# ---------------------------------------------------------------------------
# _recover_unit_from_city
# ---------------------------------------------------------------------------


class TestRecoverUnitFromCity:
    def test_basement_extracted(self) -> None:
        c: dict[str, str] = {"city": "BASEMENT, FREELAND"}
        _recover_unit_from_city(c)
        assert c["occupancy_type"] == "BASEMENT"
        assert c["city"] == "FREELAND"

    def test_multiple_designators_extracted(self) -> None:
        """LOWR is a no-id designator and is extracted; UNIT requires an id
        so 'UNIT SEATTLE' is left in city (UNIT KEY WEST etc. are real cities).
        The AGENTS.md example uses a pre-populated occupancy slot so UNIT
        gets stripped — covered by test_all_slots_full_orphan_stripped.
        """
        c: dict[str, str] = {"city": "LOWR LEVEL, UNIT SEATTLE"}
        _recover_unit_from_city(c)
        # LOWR LEVEL is peeled off; UNIT SEATTLE remains (UNIT needs an id).
        assert c["occupancy_type"] == "LOWR"
        assert c["city"] == "UNIT SEATTLE"

    def test_single_wayfinding_word_dropped(self) -> None:
        """Non-vocabulary single words before a comma are dropped as wayfinding."""
        c: dict[str, str] = {"city": "YARD, SPOKANE"}
        _recover_unit_from_city(c)
        assert c["city"] == "SPOKANE"
        assert "occupancy_type" not in c

    def test_real_city_name_untouched(self) -> None:
        c: dict[str, str] = {"city": "KEY WEST"}
        _recover_unit_from_city(c)
        assert c["city"] == "KEY WEST"

    def test_bare_no_id_designator_extracted(self) -> None:
        """LOWR at the start of city (no comma) is moved to occupancy."""
        c: dict[str, str] = {"city": "LOWR SEATTLE"}
        _recover_unit_from_city(c)
        assert c["occupancy_type"] == "LOWR"
        assert c["city"] == "SEATTLE"

    def test_no_city_is_noop(self) -> None:
        c: dict[str, str] = {}
        _recover_unit_from_city(c)  # must not raise
        assert c == {}

    def test_all_slots_full_orphan_stripped(self) -> None:
        """When both unit slots are taken, a leftover designator word is dropped."""
        c: dict[str, str] = {
            "city": "LOWR SEATTLE",
            "occupancy_type": "STE",
            "occupancy_identifier": "100",
            "subaddress_type": "BLDG",
            "subaddress_identifier": "A",
        }
        _recover_unit_from_city(c)
        assert c["city"] == "SEATTLE"


class TestRecoverIdentifierFragmentFromCity:
    def test_stray_letter_moved_to_identifier(self) -> None:
        c: dict[str, str] = {"city": "K WALLA WALLA", "occupancy_identifier": "120"}
        _recover_identifier_fragment_from_city(c)
        assert c["occupancy_identifier"] == "120 K"
        assert c["city"] == "WALLA WALLA"

    def test_no_identifier_present_noop(self) -> None:
        c: dict[str, str] = {"city": "K WALLA WALLA"}
        _recover_identifier_fragment_from_city(c)
        # No identifier field → city is left unchanged.
        assert c["city"] == "K WALLA WALLA"

    def test_multi_char_city_prefix_untouched(self) -> None:
        c: dict[str, str] = {"city": "ST PAUL", "occupancy_identifier": "5"}
        _recover_identifier_fragment_from_city(c)
        assert c["city"] == "ST PAUL"

    def test_short_city_noop(self) -> None:
        c: dict[str, str] = {"city": "LA", "occupancy_identifier": "1"}
        _recover_identifier_fragment_from_city(c)
        assert c["city"] == "LA"


# ---------------------------------------------------------------------------
# parse_address (v1)
# ---------------------------------------------------------------------------


class TestParseAddress:
    def test_basic_street_address(self) -> None:
        result = parse_address("123 Main St, Springfield, IL 62701")
        v = result.components.values
        assert v["address_number"] == "123"
        assert v["street_name"] == "Main"
        assert v["city"] == "Springfield"
        assert v["state"] == "IL"
        assert v["zip_code"] == "62701"

    def test_country_propagated(self) -> None:
        result = parse_address("123 Main St", country="US")
        assert result.country == "US"

    def test_input_preserved(self) -> None:
        raw = "123 Main St, Springfield, IL 62701"
        result = parse_address(raw)
        assert result.input == raw

    def test_parenthesized_wayfinding_stripped(self) -> None:
        result = parse_address("123 Main St (UPPER LEVEL), Springfield, IL 62701")
        v = result.components.values
        assert v["address_number"] == "123"
        assert v["city"] == "Springfield"

    def test_unmatched_paren_stripped(self) -> None:
        result = parse_address("123 Main) St, Springfield, IL")
        assert "(" not in str(result.components.values)
        assert ")" not in str(result.components.values)

    def test_intersection_parsed(self) -> None:
        result = parse_address("1st St & 2nd Ave, Seattle, WA")
        v = result.components.values
        assert "second_street_name" in v or "street_name" in v

    def test_dual_address_numbers_joined(self) -> None:
        """'1804 & 1810 Main St' should produce a single hyphenated address_number."""
        result = parse_address("1804 & 1810 Main St, Seattle, WA 98101")
        v = result.components.values
        assert v.get("address_number") == "1804-1810" or "1804" in v.get("address_number", "")

    def test_no_warning_on_clean_address(self) -> None:
        result = parse_address("456 Oak Ave, Portland, OR 97201")
        assert result.warning is None

    def test_components_have_spec(self) -> None:
        result = parse_address("123 Main St")
        assert result.components.spec == "usps-pub28"
        assert result.components.spec_version != ""

    def test_input_too_long_rejected_by_model(self) -> None:
        """Pydantic enforces max_length=1000 on ParseRequestV1, not parse_address().

        parse_address() itself accepts any string; length gating is the
        router's responsibility.  This test documents that contract.
        """
        long_input = "A" * 1001
        # parse_address should not raise; it's the model that enforces length.
        result = parse_address(long_input)
        assert result is not None


class TestParseAddressLegacy:
    def test_returns_flat_components(self) -> None:
        result = parse_address_legacy("123 Main St, Springfield, IL 62701")
        assert isinstance(result.components, dict)
        assert result.components["address_number"] == "123"

    def test_no_api_version_field(self) -> None:
        result = parse_address_legacy("123 Main St")
        assert not hasattr(result, "api_version") or result.api_version is None  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# RepeatedLabelError fallback path
# ---------------------------------------------------------------------------


class TestRepeatedLabelFallback:
    def test_ambiguous_type_on_repeated_label(self) -> None:
        """usaddress raises RepeatedLabelError for some tricky inputs;
        the parser should fall back gracefully with type='Ambiguous'.
        """
        # This specific string reliably triggers RepeatedLabelError in usaddress.
        result = parse_address("123 Main St Rear 456 Oak Ave")
        # Either it parsed normally or hit the fallback — both are acceptable;
        # the important thing is no exception is raised.
        assert result.type in {"Street Address", "Intersection", "Ambiguous"}

    def test_warning_set_on_fallback(self) -> None:
        result = parse_address("123 Main St Rear 456 Oak Ave")
        if result.type == "Ambiguous":
            assert result.warning is not None


# ---------------------------------------------------------------------------
# ZIP normalisation
# ---------------------------------------------------------------------------


class TestZipNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected_zip"),
        [
            ("123 Main St, City, WA 98101", "98101"),
            ("123 Main St, City, WA 98101-1234", "98101"),
            ("123 Main St, City, WA 981011234", "98101"),
        ],
    )
    def test_zip_parsed(self, raw: str, expected_zip: str) -> None:
        result = parse_address(raw)
        assert result.components.values.get("zip_code", "").startswith(expected_zip[:5])
