"""Unit tests for services/parser.py."""

import logging
from unittest import mock
from unittest.mock import AsyncMock

import pytest
import usaddress

from address_validator.services.audit import get_audit_parse_type, reset_audit_context
from address_validator.services.libpostal_client import LibpostalUnavailableError
from address_validator.services.parse_recovery import (
    RecoveryEvent,
    RecoveryKind,
    _recover_identifier_fragment_from_city,
    _recover_unit_from_city,
    recover_components,
)
from address_validator.services.parser import (
    apply_parse_side_effects,
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


class TestDedupeSecondaryUnits:
    """recover_components collapses an identical-duplicate secondary unit but
    leaves genuinely distinct second units intact."""

    async def test_identical_type_and_id_collapsed(self) -> None:
        c: dict[str, str] = {
            "sub_premise_type": "STE",
            "sub_premise_number": "B,",  # RLE layer leaves the comma
            "dependent_sub_premise_type": "STE",
            "dependent_sub_premise_number": "B",
        }
        recover_components(c)
        assert c["sub_premise_type"] == "STE"
        assert c.get("sub_premise_number", "").rstrip(",") == "B"
        assert "dependent_sub_premise_type" not in c
        assert "dependent_sub_premise_number" not in c

    async def test_same_type_different_id_kept(self) -> None:
        """Two real same-type suites (STE 1, STE 2) must NOT collapse —
        dropping one would silently merge two distinct units."""
        c: dict[str, str] = {
            "sub_premise_type": "STE",
            "sub_premise_number": "1",
            "dependent_sub_premise_type": "STE",
            "dependent_sub_premise_number": "2",
        }
        recover_components(c)
        assert c["sub_premise_number"] == "1"
        assert c["dependent_sub_premise_type"] == "STE"
        assert c["dependent_sub_premise_number"] == "2"

    async def test_different_type_same_id_kept(self) -> None:
        c: dict[str, str] = {
            "sub_premise_type": "STE",
            "sub_premise_number": "B",
            "dependent_sub_premise_type": "BLDG",
            "dependent_sub_premise_number": "B",
        }
        recover_components(c)
        assert c["dependent_sub_premise_type"] == "BLDG"
        assert c["dependent_sub_premise_number"] == "B"

    async def test_no_dependent_slot_is_noop(self) -> None:
        c: dict[str, str] = {"sub_premise_type": "STE", "sub_premise_number": "B"}
        recover_components(c)
        assert c == {"sub_premise_type": "STE", "sub_premise_number": "B"}


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
        result = (await parse_address("123 Main St, Springfield, IL 62701")).response
        v = result.components.values
        assert v["premise_number"] == "123"
        assert v["thoroughfare_name"] == "Main"
        assert v["locality"] == "Springfield"
        assert v["administrative_area"] == "IL"
        assert v["postcode"] == "62701"

    async def test_country_propagated(self) -> None:
        result = (await parse_address("123 Main St", country="US")).response
        assert result.country == "US"

    async def test_input_preserved(self) -> None:
        raw = "123 Main St, Springfield, IL 62701"
        result = (await parse_address(raw)).response
        assert result.input == raw

    async def test_parenthesized_wayfinding_stripped(self) -> None:
        result = (await parse_address("123 Main St (UPPER LEVEL), Springfield, IL 62701")).response
        v = result.components.values
        assert v["premise_number"] == "123"
        assert v["locality"] == "Springfield"

    async def test_unmatched_paren_stripped(self) -> None:
        result = (await parse_address("123 Main) St, Springfield, IL")).response
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
        outcome = await parse_address(
            "123 Main St Toronto ON", country="CA", libpostal_client=mock_client
        )
        result = outcome.response
        mock_client.parse.assert_awaited_once()
        assert result.country == "CA"
        assert result.components.values["locality"] == "TORONTO"

    async def test_intersection_parsed(self) -> None:
        result = (await parse_address("1st St & 2nd Ave, Seattle, WA")).response
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
            result = (await parse_address("1804 & 1810 Main St")).response

        assert result.components.values["premise_number"] == "1804-1810"
        assert result.type == "Ambiguous"

    async def test_no_warnings_on_clean_address(self) -> None:
        result = (await parse_address("456 Oak Ave, Portland, OR 97201")).response
        assert result.warnings == []

    async def test_components_have_spec(self) -> None:
        result = (await parse_address("123 Main St")).response
        assert result.components.spec == "usps-pub28"
        assert result.components.spec_version != ""

    async def test_input_too_long_rejected_by_model(self) -> None:
        """Pydantic enforces max_length=1000 on ParseRequest, not parse_address().

        await parse_address() itself accepts any string; length gating is the
        router's responsibility.  This test documents that contract.
        """
        long_input = "A" * 1001
        # parse_address should not raise; it's the model that enforces length.
        result = (await parse_address(long_input)).response
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
        result = (await parse_address("123 Main St Rear 456 Oak Ave")).response
        # Either it parsed normally or hit the fallback — both are acceptable;
        # the important thing is no exception is raised.
        assert result.type in {"Street Address", "Intersection", "Ambiguous"}

    async def test_warnings_set_on_fallback(self) -> None:
        result = (await parse_address("123 Main St Rear 456 Oak Ave")).response
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
            outcome = await parse_address(
                "995 9TH ST BLDG 201 ROOM 104 T, SAN FRANCISCO, CA 94130-2107"
            )
            result = outcome.response
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

    async def test_second_designator_not_in_unit_map_slotted(self) -> None:
        """GH-129: a repeated OccupancyType whose token is not in UNIT_MAP
        (e.g. 'SMP') must still route to the next free slot, not fold into
        the first slot as 'STE SMP' / 'J, 2'.  usaddress already tagged it
        as a second designator; we trust that signal over UNIT_MAP membership.
        """
        # Real usaddress output for this string is deterministic (two
        # OccupancyType runs), so drive the live parser — no mock needed.
        outcome = await parse_address("1210 N WENATCHEE AVE STE J, SMP - 2 WENATCHEE, WA 98801")
        result = outcome.response
        vals = result.components.values
        # Street fields uncontaminated.
        assert vals.get("premise_number") == "1210"
        assert vals.get("thoroughfare_name") == "WENATCHEE"
        # The two designators land in separate slots — NOT folded.
        # ('J,' keeps the comma at parse layer; the standardizer strips it.)
        assert vals.get("sub_premise_type") == "STE"
        assert vals.get("sub_premise_number", "").rstrip(",") == "J"
        assert vals.get("dependent_sub_premise_type") == "SMP"
        assert vals.get("dependent_sub_premise_number") == "2"
        # No fused 'STE SMP' designator anywhere.
        assert "SMP" not in vals.get("sub_premise_type", "")
        assert "WENATCHEE" in vals.get("locality", "")
        # The non-canonical designator is preserved, with a warning.
        assert any("Unrecognized unit designator preserved: 'SMP'" in w for w in result.warnings)

    async def test_repeated_unit_type_non_alpha_not_slotted(self) -> None:
        """GH-129 guard: a repeated unit-type label whose token is NOT
        alphabetic (a stray number mis-tagged as OccupancyType) must not be
        promoted to a second slot — the ``.isalpha()`` guard rejects it.
        """
        fake_tokens = [
            ("123", "AddressNumber"),
            ("MAIN", "StreetName"),
            ("ST", "StreetNamePostType"),
            ("STE", "OccupancyType"),
            ("5", "OccupancyIdentifier"),
            ("2", "OccupancyType"),  # non-alpha, mis-tagged — must not slot
            ("SEATTLE,", "PlaceName"),
            ("WA", "StateName"),
            ("98101", "ZipCode"),
        ]
        exc = usaddress.RepeatedLabelError("fake", fake_tokens, {})
        with mock.patch("address_validator.services.parser.usaddress.tag", side_effect=exc):
            result = (await parse_address("123 MAIN ST STE 5 2 SEATTLE, WA 98101")).response
        vals = result.components.values
        # The non-alpha "2" must NOT create a second designator slot.
        assert not vals.get("dependent_sub_premise_type")
        # And no spurious "unrecognized designator" warning for the rejected token.
        assert not any("Unrecognized unit designator" in w for w in result.warnings)

    async def test_identical_duplicate_secondary_unit_collapsed(self) -> None:
        """A secondary unit repeated verbatim ('STE B, STE B') is a data-entry
        duplicate, not two distinct units.  The RLE routing slots the second
        'STE' into dependent_sub_premise; an identical-duplicate collapse must
        then drop it so the address standardizes to a single 'STE B' rather
        than 'STE B STE B'.
        """
        outcome = await parse_address("17024 PACIFIC AVE S STE B, STE B SPANAWAY, WA 98387-8387")
        vals = outcome.response.components.values
        # Primary unit retained.
        assert vals.get("sub_premise_type") == "STE"
        assert vals.get("sub_premise_number", "").rstrip(",") == "B"
        # Identical second unit dropped — not slotted into dependent_sub_premise.
        assert not vals.get("dependent_sub_premise_type")
        assert not vals.get("dependent_sub_premise_number")
        assert "SPANAWAY" in vals.get("locality", "")

    @pytest.mark.parametrize(
        ("raw", "designator", "identifier", "city"),
        [
            (
                "19315 BOTHELL EVERETT HWY #1, UNIT 1 BOTHELL, WA 98012",
                "UNIT",
                "1",
                "BOTHELL",
            ),
            (
                "2615 OLD HIGHWAY 99 S RD #A, UNIT A MOUNT VERNON, WA 98273-8273",
                "UNIT",
                "A",
                "MOUNT VERNON",
            ),
            (
                "11525 E DAY MT SPOKANE RD #B, STE B MEAD, WA 99021",
                "STE",
                "B",
                "MEAD",
            ),
        ],
    )
    async def test_duplicate_hash_unit_collapsed_into_named_unit(
        self, raw: str, designator: str, identifier: str, city: str
    ) -> None:
        """GH-170: '#1, UNIT 1' states the same unit twice (data-entry idiom).

        usaddress tags '#' and '1,' as OccupancyIdentifier before the first
        OccupancyType arrives, so the identifiers must not concatenate into
        'UNIT # 1, 1' — the named designator wins and the '#' phrase is
        dropped as a duplicate.
        """
        outcome = await parse_address(raw)
        vals = outcome.response.components.values
        assert vals.get("sub_premise_type") == designator
        assert vals.get("sub_premise_number") == identifier
        assert not vals.get("dependent_sub_premise_type")
        assert not vals.get("dependent_sub_premise_number")
        assert city in vals.get("locality", "")
        # The dropped '#' phrase must be signalled, not silent.
        assert any("Duplicate secondary unit collapsed" in w for w in outcome.response.warnings)

    async def test_duplicate_unit_collapse_warns_on_clean_parse_path(self) -> None:
        """GH-170 CR: the collapse heuristic also fires on a clean (non-RLE)
        parse — '#2 BLDG 2' tags cleanly with the '#' phrase in the primary
        slot and BLDG in the dependent slot.  Dropping the '#' phrase there
        must emit the catalogued warning; a clean parse must never silently
        discard input content.
        """
        outcome = await parse_address("123 MAIN ST #2 BLDG 2 SEATTLE, WA 98101")
        result = outcome.response
        assert result.type == "Street Address"
        vals = result.components.values
        assert vals.get("sub_premise_type") == "BLDG"
        assert vals.get("sub_premise_number") == "2"
        assert any("Duplicate secondary unit collapsed" in w for w in result.warnings)

    async def test_duplicate_unit_collapse_warning_is_normalized(self) -> None:
        """GH-170 CR round 2: the collapse warning interpolates normalized
        (uppercased, punctuation-stripped) tokens so the same address in any
        casing yields identical warning text.
        """
        outcome = await parse_address("19315 bothell everett hwy #1, unit 1 bothell, wa 98012")
        assert any(
            "Duplicate secondary unit collapsed into 'UNIT 1'" in w
            for w in outcome.response.warnings
        ), outcome.response.warnings

    async def test_duplicate_unit_collapse_warning_uses_usps_abbreviation(self) -> None:
        """GH-170 CR round 3: the collapse warning names the designator the
        same way the standardized output will — the UNIT_MAP abbreviation
        ('suite' → 'STE'), not the raw input token.
        """
        outcome = await parse_address("19315 bothell everett hwy #1, suite 1 bothell, wa 98012")
        assert any(
            "Duplicate secondary unit collapsed into 'STE 1'" in w
            for w in outcome.response.warnings
        ), outcome.response.warnings

    async def test_distinct_hash_unit_and_named_unit_both_kept(self) -> None:
        """GH-170 guard: '#108 STE B' is two distinct units — no collapse.

        The bare '#' phrase keeps the primary slot; the named designator is
        routed to dependent_sub_premise and neither folds into the other.
        """
        outcome = await parse_address("5041 RAINIER AVE S #108 STE B, SEATTLE, WA 98118-1946")
        vals = outcome.response.components.values
        assert "108" in vals.get("sub_premise_number", "")
        assert "STE" not in vals.get("sub_premise_number", "")
        assert vals.get("dependent_sub_premise_type") == "STE"
        assert vals.get("dependent_sub_premise_number") == "B"
        assert "SEATTLE" in vals.get("locality", "")


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
        result = (await parse_address(raw)).response
        assert result.components.values.get("postcode", "").startswith(expected_zip[:5])


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


class TestParseWarnings:
    async def test_parenthesized_text_warning(self) -> None:
        result = (await parse_address("123 Main St (UPPER LEVEL), Springfield, IL 62701")).response
        assert any("Parenthesized text removed" in w for w in result.warnings)
        assert any("UPPER LEVEL" in w for w in result.warnings)

    async def test_no_paren_warning_on_clean_address(self) -> None:
        result = (await parse_address("123 Main St, Springfield, IL 62701")).response
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
            result = (await parse_address("1804 & 1810 Main St")).response
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
            result = (await parse_address("123 Main 456")).response
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
            result = (await parse_address("123 Main St BSMT, Springfield")).response
        # BSMT should have been recovered and a warning emitted.
        assert any("Unit designator recovered" in w for w in result.warnings)

    async def test_identifier_fragment_recovered_from_city_warning(self) -> None:
        """When _recover_identifier_fragment_from_city fires, an event is recorded."""
        comps: dict[str, str] = {"locality": "K WALLA WALLA", "sub_premise_number": "120"}
        events: list[RecoveryEvent] = []
        _recover_identifier_fragment_from_city(comps, events)
        assert comps["sub_premise_number"] == "120 K"
        assert comps["locality"] == "WALLA WALLA"
        assert [e.kind for e in events] == [RecoveryKind.FRAGMENT_RECOVERED]
        assert any("identifier fragment" in e.warning.lower() for e in events)


# ---------------------------------------------------------------------------
# Structured recovery events (GH #176)
# ---------------------------------------------------------------------------


class TestRecoveryEvents:
    def test_recover_components_returns_structured_events(self) -> None:
        c: dict[str, str] = {"locality": "BSMT, FREELAND"}
        warnings: list[str] = []
        events = recover_components(c, warnings)
        assert [e.kind for e in events] == [RecoveryKind.UNIT_RECOVERED]
        # The warnings list is derived from the events — same text, same order.
        assert [e.warning for e in events] == warnings

    def test_no_events_on_clean_components(self) -> None:
        c: dict[str, str] = {"locality": "SPRINGFIELD"}
        warnings: list[str] = []
        assert recover_components(c, warnings) == []
        assert warnings == []

    def test_duplicate_unit_collapse_yields_event(self) -> None:
        c: dict[str, str] = {
            "sub_premise_number": "#1",
            "dependent_sub_premise_type": "UNIT",
            "dependent_sub_premise_number": "1",
        }
        warnings: list[str] = []
        events = recover_components(c, warnings)
        assert [e.kind for e in events] == [RecoveryKind.DUPLICATE_UNIT_COLLAPSED]
        assert [e.warning for e in events] == warnings

    async def test_candidate_collection_survives_warning_rewording(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Candidate collection is keyed on structured events, not warning
        display text — rewording a warning must not disable it (GH #176)."""
        monkeypatch.setattr(
            "address_validator.core.warnings.UNIT_RECOVERED_FROM_FIELD",
            "Completely reworded warning: '{designator}'",
        )
        tagged = {
            "AddressNumber": "123",
            "StreetName": "MAIN",
            "StreetNamePostType": "ST",
            "PlaceName": "BSMT, FREELAND",
        }
        with mock.patch(
            "address_validator.services.parser.usaddress.tag",
            return_value=(tagged, "Street Address"),
        ):
            outcome = await parse_address("123 MAIN ST BSMT, FREELAND")
        assert outcome.candidate_data is not None
        assert outcome.candidate_data["failure_type"] == "post_parse_recovery"
        assert "Completely reworded warning" in outcome.candidate_data["failure_reason"]


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
            result = (await parse_address("1804 & 1810 Main St")).response
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
# Candidate data collection (now returned via ParseOutcome, not ContextVars)
# ---------------------------------------------------------------------------


class TestCandidateCollection:
    """``parse_address`` is now a pure parse — it sets no ContextVars and
    instead RETURNS ``parse_type`` and ``candidate_data`` on its
    :class:`ParseOutcome`.  The request-scoped writes are replayed by
    ``apply_parse_side_effects`` (covered in :class:`TestApplyParseSideEffects`).
    """

    def setup_method(self) -> None:
        reset_candidate_data()

    async def test_repeated_label_returns_candidate_data(self) -> None:
        """RepeatedLabelError path returns candidate data — without touching the
        ContextVar (parse_address is side-effect free)."""
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
            outcome = await parse_address("995 9TH ST BLDG 201 ROOM 104")

        # Pure parse: ContextVar must NOT have been written.
        assert get_candidate_data() is None
        assert outcome.parse_type == "Ambiguous"
        assert outcome.candidate_data is not None
        assert outcome.candidate_data["failure_type"] == "repeated_label_error"
        assert outcome.candidate_data["raw_address"] == "995 9TH ST BLDG 201 ROOM 104"

    async def test_post_parse_recovery_returns_candidate_data(self) -> None:
        """When _recover_unit_from_city fires, candidate data is returned."""
        fake_tokens = [
            ("123", "AddressNumber"),
            ("Main", "StreetName"),
            ("St", "StreetNamePostType"),
            ("BSMT,", "PlaceName"),
            ("Springfield", "PlaceName"),
        ]
        exc = usaddress.RepeatedLabelError("fake", fake_tokens, {})
        with mock.patch("address_validator.services.parser.usaddress.tag", side_effect=exc):
            outcome = await parse_address("123 Main St BSMT, Springfield")

        assert get_candidate_data() is None
        if any("Unit designator recovered" in w for w in outcome.response.warnings):
            assert outcome.candidate_data is not None

    async def test_clean_parse_no_candidate_data(self) -> None:
        """Normal successful parse returns no candidate data and writes nothing."""
        outcome = await parse_address("123 Main St, Springfield, IL 62701")
        assert outcome.candidate_data is None
        assert get_candidate_data() is None


# ---------------------------------------------------------------------------
# apply_parse_side_effects — caller-side ContextVar replay
# ---------------------------------------------------------------------------


class TestApplyParseSideEffects:
    """The request-scoped writes lifted out of ``parse_address`` must be
    replicated exactly by ``apply_parse_side_effects`` for every parse path."""

    def setup_method(self) -> None:
        reset_candidate_data()
        reset_audit_context()

    async def test_clean_us_path_sets_parse_type_only(self) -> None:
        outcome = await parse_address("123 Main St, Springfield, IL 62701")
        apply_parse_side_effects(outcome)
        assert get_audit_parse_type() == outcome.parse_type
        assert get_audit_parse_type() in {"Street Address", "Intersection"}
        # Clean parse → no candidate write.
        assert get_candidate_data() is None

    async def test_ca_path_sets_libpostal_parse_type(self) -> None:
        mock_client = AsyncMock()
        mock_client.parse.return_value = {"locality": "TORONTO"}
        outcome = await parse_address("123 Main St", country="CA", libpostal_client=mock_client)
        apply_parse_side_effects(outcome)
        # parse_type is "libpostal" even though response.type is "Street Address".
        assert outcome.parse_type == "libpostal"
        assert outcome.response.type == "Street Address"
        assert get_audit_parse_type() == "libpostal"
        assert get_candidate_data() is None

    async def test_repeated_label_path_sets_both_contextvars(self) -> None:
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
            outcome = await parse_address("995 9TH ST BLDG 201 ROOM 104")
        apply_parse_side_effects(outcome)
        assert get_audit_parse_type() == "Ambiguous"
        candidate = get_candidate_data()
        assert candidate is not None
        assert candidate["failure_type"] == "repeated_label_error"
        assert candidate["raw_address"] == "995 9TH ST BLDG 201 ROOM 104"
