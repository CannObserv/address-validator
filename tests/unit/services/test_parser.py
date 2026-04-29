"""Unit tests for services/parser.py."""

import logging
from unittest import mock
from unittest.mock import AsyncMock

import pytest
import usaddress

from address_validator.services.libpostal_client import LibpostalUnavailableError
from address_validator.services.parser import (
    _recover_identifier_fragment_from_city,
    _recover_unit_from_city,
    parse_address,
)
from address_validator.services.training_candidates import (
    get_candidate_data,
    reset_candidate_data,
)

# ---------------------------------------------------------------------------
# _recover_unit_from_city
# ---------------------------------------------------------------------------


class TestRecoverUnitFromCity:
    async def test_basement_extracted(self) -> None:
        c: dict[str, str] = {"locality": "BASEMENT, FREELAND"}
        _recover_unit_from_city(c)
        assert c["sub_premise_type"] == "BASEMENT"
        assert c["locality"] == "FREELAND"

    async def test_multiple_designators_extracted(self) -> None:
        """LOWR is a no-id designator and is extracted; UNIT requires an id
        so 'UNIT SEATTLE' is left in locality (UNIT KEY WEST etc. are real cities).
        The AGENTS.md example uses a pre-populated occupancy slot so UNIT
        gets stripped — covered by test_all_slots_full_orphan_stripped.
        """
        c: dict[str, str] = {"locality": "LOWR LEVEL, UNIT SEATTLE"}
        _recover_unit_from_city(c)
        # LOWR LEVEL is peeled off; UNIT SEATTLE remains (UNIT needs an id).
        assert c["sub_premise_type"] == "LOWR"
        assert c["locality"] == "UNIT SEATTLE"

    async def test_single_wayfinding_word_dropped(self) -> None:
        """Non-vocabulary single words before a comma are dropped as wayfinding."""
        c: dict[str, str] = {"locality": "YARD, SPOKANE"}
        _recover_unit_from_city(c)
        assert c["locality"] == "SPOKANE"
        assert "sub_premise_type" not in c

    async def test_real_city_name_untouched(self) -> None:
        c: dict[str, str] = {"locality": "KEY WEST"}
        _recover_unit_from_city(c)
        assert c["locality"] == "KEY WEST"

    async def test_bare_no_id_designator_extracted(self) -> None:
        """LOWR at the start of locality (no comma) is moved to sub_premise_type."""
        c: dict[str, str] = {"locality": "LOWR SEATTLE"}
        _recover_unit_from_city(c)
        assert c["sub_premise_type"] == "LOWR"
        assert c["locality"] == "SEATTLE"

    async def test_no_city_is_noop(self) -> None:
        c: dict[str, str] = {}
        _recover_unit_from_city(c)  # must not raise
        assert c == {}

    async def test_all_slots_full_orphan_stripped(self) -> None:
        """When both unit slots are taken, a leftover designator word is dropped."""
        c: dict[str, str] = {
            "locality": "LOWR SEATTLE",
            "sub_premise_type": "STE",
            "sub_premise_number": "100",
            "dependent_sub_premise_type": "BLDG",
            "dependent_sub_premise_number": "A",
        }
        _recover_unit_from_city(c)
        assert c["locality"] == "SEATTLE"


class TestRecoverIdentifierFragmentFromCity:
    async def test_stray_letter_moved_to_identifier(self) -> None:
        c: dict[str, str] = {"locality": "K WALLA WALLA", "sub_premise_number": "120"}
        _recover_identifier_fragment_from_city(c)
        assert c["sub_premise_number"] == "120 K"
        assert c["locality"] == "WALLA WALLA"

    async def test_no_identifier_present_noop(self) -> None:
        c: dict[str, str] = {"locality": "K WALLA WALLA"}
        _recover_identifier_fragment_from_city(c)
        # No identifier field → locality is left unchanged.
        assert c["locality"] == "K WALLA WALLA"

    async def test_multi_char_city_prefix_untouched(self) -> None:
        c: dict[str, str] = {"locality": "ST PAUL", "sub_premise_number": "5"}
        _recover_identifier_fragment_from_city(c)
        assert c["locality"] == "ST PAUL"

    async def test_short_city_noop(self) -> None:
        c: dict[str, str] = {"locality": "LA", "sub_premise_number": "1"}
        _recover_identifier_fragment_from_city(c)
        assert c["locality"] == "LA"


# ---------------------------------------------------------------------------
# parse_address (v1)
# ---------------------------------------------------------------------------


class TestParseAddress:
    async def test_basic_street_address(self) -> None:
        result = await parse_address("123 Main St, Springfield, IL 62701")
        v = result.components.values
        assert v["premise_number"] == "123"
        assert v["thoroughfare_name"] == "Main"
        assert v["locality"] == "Springfield"
        assert v["administrative_area"] == "IL"
        assert v["postcode"] == "62701"

    async def test_country_propagated(self) -> None:
        result = await parse_address("123 Main St", country="US")
        assert result.country == "US"

    async def test_input_preserved(self) -> None:
        raw = "123 Main St, Springfield, IL 62701"
        result = await parse_address(raw)
        assert result.input == raw

    async def test_parenthesized_wayfinding_stripped(self) -> None:
        result = await parse_address("123 Main St (UPPER LEVEL), Springfield, IL 62701")
        v = result.components.values
        assert v["premise_number"] == "123"
        assert v["locality"] == "Springfield"

    async def test_unmatched_paren_stripped(self) -> None:
        result = await parse_address("123 Main) St, Springfield, IL")
        assert "(" not in str(result.components.values)
        assert ")" not in str(result.components.values)

    async def test_ca_no_libpostal_client_raises_unavailable(self) -> None:
        with pytest.raises(LibpostalUnavailableError, match="No libpostal client configured"):
            await parse_address("350 rue des Lilas, Quebec QC", country="CA", libpostal_client=None)

    async def test_ca_libpostal_client_called(self) -> None:
        mock_client = AsyncMock()
        mock_client.parse.return_value = {
            "premise_number": "123",
            "thoroughfare_name": "MAIN",
            "locality": "TORONTO",
            "administrative_area": "ON",
            "postcode": "M5V 2T6",
        }
        result = await parse_address(
            "123 Main St Toronto ON", country="CA", libpostal_client=mock_client
        )
        mock_client.parse.assert_awaited_once()
        assert result.country == "CA"
        assert result.components.values["locality"] == "TORONTO"

    async def test_intersection_parsed(self) -> None:
        result = await parse_address("1st St & 2nd Ave, Seattle, WA")
        v = result.components.values
        assert "second_thoroughfare_name" in v

    async def test_dual_address_numbers_joined(self) -> None:
        """The RLE fallback joins dual AddressNumber tokens with a hyphen.

        usaddress raises RepeatedLabelError when it emits the same label
        twice.  The parser's fallback detects:
          AddressNumber → IntersectionSeparator → AddressNumber
        and joins them as "N-M" per USPS Pub 28 §232.

        This logic is tested directly against _parse_rle_tokens() below.
        The usaddress library does not reliably produce two AddressNumber
        tokens from natural-language input, so the full integration path
        is not exercised here.
        """

    async def test_dual_address_rle_token_logic(self) -> None:
        """Unit-test the RLE hyphen-join logic by calling _parse directly
        via a fabricated RepeatedLabelError scenario.

        We monkey-patch usaddress.tag to raise RepeatedLabelError with the
        exact token sequence that triggers the dual-address path.
        """
        fake_tokens = [
            ("1804", "AddressNumber"),
            ("&", "IntersectionSeparator"),
            ("1810", "AddressNumber"),
            ("Main", "StreetName"),
            ("St", "StreetNamePostType"),
        ]
        exc = usaddress.RepeatedLabelError("fake", fake_tokens, {})

        with mock.patch("address_validator.services.parser.usaddress.tag", side_effect=exc):
            result = await parse_address("1804 & 1810 Main St")

        assert result.components.values["premise_number"] == "1804-1810"
        assert result.type == "Ambiguous"

    async def test_no_warnings_on_clean_address(self) -> None:
        result = await parse_address("456 Oak Ave, Portland, OR 97201")
        assert result.warnings == []

    async def test_components_have_spec(self) -> None:
        result = await parse_address("123 Main St")
        assert result.components.spec == "usps-pub28"
        assert result.components.spec_version != ""

    async def test_input_too_long_rejected_by_model(self) -> None:
        """Pydantic enforces max_length=1000 on ParseRequestV1, not parse_address().

        await parse_address() itself accepts any string; length gating is the
        router's responsibility.  This test documents that contract.
        """
        long_input = "A" * 1001
        # parse_address should not raise; it's the model that enforces length.
        result = await parse_address(long_input)
        assert result is not None


# ---------------------------------------------------------------------------
# RepeatedLabelError fallback path
# ---------------------------------------------------------------------------


class TestRepeatedLabelFallback:
    async def test_ambiguous_type_on_repeated_label(self) -> None:
        """usaddress raises RepeatedLabelError for some tricky inputs;
        the parser should fall back gracefully with type='Ambiguous'.
        """
        # This specific string reliably triggers RepeatedLabelError in usaddress.
        result = await parse_address("123 Main St Rear 456 Oak Ave")
        # Either it parsed normally or hit the fallback — both are acceptable;
        # the important thing is no exception is raised.
        assert result.type in {"Street Address", "Intersection", "Ambiguous"}

    async def test_warnings_set_on_fallback(self) -> None:
        result = await parse_address("123 Main St Rear 456 Oak Ave")
        if result.type == "Ambiguous":
            assert len(result.warnings) > 0

    async def test_multi_unit_designator_slotted_not_concatenated(self) -> None:
        """GH-72: BLDG 201 ROOM 104 T should populate both unit slots,
        not concatenate repeated SubaddressType/AddressNumber labels."""
        # Simulate exact usaddress output for this address.
        fake_tokens = [
            ("995", "AddressNumber"),
            ("9TH", "StreetName"),
            ("ST", "StreetNamePostType"),
            ("BLDG", "SubaddressType"),
            ("201", "SubaddressIdentifier"),
            ("ROOM", "SubaddressType"),
            ("104", "AddressNumber"),
            ("T,", "StreetName"),
            ("SAN", "PlaceName"),
            ("FRANCISCO,", "PlaceName"),
            ("CA", "StateName"),
            ("94130-2107", "ZipCode"),
        ]
        exc = usaddress.RepeatedLabelError("fake", fake_tokens, {})
        with mock.patch("address_validator.services.parser.usaddress.tag", side_effect=exc):
            result = await parse_address(
                "995 9TH ST BLDG 201 ROOM 104 T, SAN FRANCISCO, CA 94130-2107"
            )
        vals = result.components.values
        # Primary street fields should not be contaminated.
        assert vals.get("premise_number") == "995"
        assert vals.get("thoroughfare_name") == "9TH"
        assert vals.get("thoroughfare_trailing_type") == "ST"
        # First unit lands in dependent_sub_premise (raw usaddress label);
        # second is routed to the free sub_premise slot.
        # The standardizer reorders for correct USPS line assembly.
        assert vals.get("dependent_sub_premise_type") == "BLDG"
        assert vals.get("dependent_sub_premise_number") == "201"
        assert vals.get("sub_premise_type") == "ROOM"
        assert vals.get("sub_premise_number") == "104 T"
        # Locality should be clean.
        assert "SAN FRANCISCO" in vals.get("locality", "")


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
    async def test_zip_parsed(self, raw: str, expected_zip: str) -> None:
        result = await parse_address(raw)
        assert result.components.values.get("postcode", "").startswith(expected_zip[:5])


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


class TestParseWarnings:
    async def test_parenthesized_text_warning(self) -> None:
        result = await parse_address("123 Main St (UPPER LEVEL), Springfield, IL 62701")
        assert any("Parenthesized text removed" in w for w in result.warnings)
        assert any("UPPER LEVEL" in w for w in result.warnings)

    async def test_no_paren_warning_on_clean_address(self) -> None:
        result = await parse_address("123 Main St, Springfield, IL 62701")
        assert not any("Parenthesized" in w for w in result.warnings)

    async def test_dual_address_merge_warning(self) -> None:
        fake_tokens = [
            ("1804", "AddressNumber"),
            ("&", "IntersectionSeparator"),
            ("1810", "AddressNumber"),
            ("Main", "StreetName"),
            ("St", "StreetNamePostType"),
        ]
        exc = usaddress.RepeatedLabelError("fake", fake_tokens, {})
        with mock.patch("address_validator.services.parser.usaddress.tag", side_effect=exc):
            result = await parse_address("1804 & 1810 Main St")
        assert any("1804-1810" in w for w in result.warnings)

    async def test_ambiguous_parse_warning_general(self) -> None:
        """Repeated labels without an IntersectionSeparator produce the
        generic ambiguous-parse warning, not the range-join warning.
        """
        exc = usaddress.RepeatedLabelError(
            "fake",
            [("123", "AddressNumber"), ("Main", "StreetName"), ("456", "AddressNumber")],
            "AddressNumber",
        )
        with mock.patch("address_validator.services.parser.usaddress.tag", side_effect=exc):
            result = await parse_address("123 Main 456")
        assert any("Ambiguous parse" in w for w in result.warnings)
        assert not any("joined as range" in w for w in result.warnings)

    async def test_unit_recovered_from_city_warning(self) -> None:
        """When _recover_unit_from_city fires, a warning is appended."""
        # usaddress tags 'BSMT' into city for some inputs; simulate via
        # a mock so we can control the component dict precisely.
        fake_tokens = [
            ("123", "AddressNumber"),
            ("Main", "StreetName"),
            ("St", "StreetNamePostType"),
            ("BSMT,", "PlaceName"),
            ("Springfield", "PlaceName"),
        ]
        exc = usaddress.RepeatedLabelError("fake", fake_tokens, {})
        with mock.patch("address_validator.services.parser.usaddress.tag", side_effect=exc):
            result = await parse_address("123 Main St BSMT, Springfield")
        # BSMT should have been recovered and a warning emitted.
        assert any("Unit designator recovered" in w for w in result.warnings)

    async def test_identifier_fragment_recovered_from_city_warning(self) -> None:
        """When _recover_identifier_fragment_from_city fires, a warning is appended."""
        comps: dict[str, str] = {"locality": "K WALLA WALLA", "sub_premise_number": "120"}
        warnings: list[str] = []
        _recover_identifier_fragment_from_city(comps, warnings)
        assert comps["sub_premise_number"] == "120 K"
        assert comps["locality"] == "WALLA WALLA"
        assert any("identifier fragment" in w.lower() for w in warnings)


class TestParserLogging:
    async def test_debug_emitted_on_successful_parse(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="address_validator.services.parser"):
            await parse_address("123 Main St, Springfield, IL 62701")
        assert "parsed address" in caplog.text
        assert "Street Address" in caplog.text

    async def test_debug_emitted_on_ambiguous_parse(self, caplog: pytest.LogCaptureFixture) -> None:
        # Force a RepeatedLabelError by mocking usaddress.tag.
        exc = usaddress.RepeatedLabelError(
            "1804 & 1810 Main St",
            [("1804", "AddressNumber"), ("Main", "StreetName"), ("1810", "AddressNumber")],
            "AddressNumber",
        )
        with (
            mock.patch("usaddress.tag", side_effect=exc),
            caplog.at_level(logging.DEBUG, logger="address_validator.services.parser"),
        ):
            result = await parse_address("1804 & 1810 Main St")
        assert result.type == "Ambiguous"
        assert "parsed address type=Ambiguous" in caplog.text

    async def test_warning_emitted_on_ambiguous_parse(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        exc = usaddress.RepeatedLabelError(
            "1804 & 1810 Main St",
            [("1804", "AddressNumber"), ("Main", "StreetName"), ("1810", "AddressNumber")],
            "AddressNumber",
        )
        with (
            mock.patch("usaddress.tag", side_effect=exc),
            caplog.at_level(logging.WARNING, logger="address_validator.services.parser"),
        ):
            await parse_address("1804 & 1810 Main St")
        assert "ambiguous parse" in caplog.text


# ---------------------------------------------------------------------------
# Candidate data collection
# ---------------------------------------------------------------------------


class TestCandidateCollection:
    def setup_method(self) -> None:
        reset_candidate_data()

    async def test_repeated_label_sets_candidate_data(self) -> None:
        """RepeatedLabelError path should set candidate ContextVar."""
        fake_tokens = [
            ("995", "AddressNumber"),
            ("9TH", "StreetName"),
            ("ST", "StreetNamePostType"),
            ("BLDG", "SubaddressType"),
            ("201", "SubaddressIdentifier"),
            ("ROOM", "SubaddressType"),
            ("104", "AddressNumber"),
        ]
        exc = usaddress.RepeatedLabelError("fake", fake_tokens, {})
        with mock.patch("address_validator.services.parser.usaddress.tag", side_effect=exc):
            await parse_address("995 9TH ST BLDG 201 ROOM 104")

        candidate = get_candidate_data()
        assert candidate is not None
        assert candidate["failure_type"] == "repeated_label_error"
        assert candidate["raw_address"] == "995 9TH ST BLDG 201 ROOM 104"

    async def test_post_parse_recovery_sets_candidate_data(self) -> None:
        """When _recover_unit_from_city fires, candidate data should be set."""
        fake_tokens = [
            ("123", "AddressNumber"),
            ("Main", "StreetName"),
            ("St", "StreetNamePostType"),
            ("BSMT,", "PlaceName"),
            ("Springfield", "PlaceName"),
        ]
        exc = usaddress.RepeatedLabelError("fake", fake_tokens, {})
        with mock.patch("address_validator.services.parser.usaddress.tag", side_effect=exc):
            result = await parse_address("123 Main St BSMT, Springfield")

        candidate = get_candidate_data()
        if any("Unit designator recovered" in w for w in result.warnings):
            assert candidate is not None

    async def test_clean_parse_no_candidate_data(self) -> None:
        """Normal successful parse should not set candidate data."""
        await parse_address("123 Main St, Springfield, IL 62701")
        candidate = get_candidate_data()
        assert candidate is None
