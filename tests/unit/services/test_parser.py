"""Unit tests for services/parser.py."""

import logging
from unittest import mock
from unittest.mock import AsyncMock

import pytest
import usaddress

from address_validator.core.warnings import DELIVERY_LINE_RECOVERED
from address_validator.services.audit import get_audit_parse_type, reset_audit_context
from address_validator.services.libpostal_client import LibpostalUnavailableError
from address_validator.services.parse_recovery import (
    RecoveryEvent,
    RecoveryKind,
    _recover_general_delivery_from_name,
    _recover_identifier_fragment_from_city,
    _recover_locality_from_trailing_addressee,
    _recover_name_from_box_type,
    _recover_po_box_designation,
    _recover_route_from_unit_slot,
    _recover_split_box_designation,
    _recover_unit_from_city,
    _recover_unit_from_general_delivery,
    _recover_unit_list_item,
    _splice,
    _split_hash_phrase,
    recover_components,
)
from address_validator.services.parser import (
    apply_parse_side_effects,
    parse_address,
)
from address_validator.services.standardizer import standardize
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

    async def test_variant_designator_extracted(self) -> None:
        """GH-286: a plural designator before a comma is a unit (via UNIT_MAP),
        not a city prefix and not dropped as wayfinding."""
        c: dict[str, str] = {"locality": "SUITES 100, SEATTLE"}
        _recover_unit_from_city(c)
        assert c["sub_premise_type"] == "SUITES"
        assert c["sub_premise_number"] == "100"
        assert c["locality"] == "SEATTLE"

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

    @pytest.mark.parametrize(
        ("city", "designator", "identifier", "rest"),
        [
            ("BLG A SEATTLE", "BLG", "A", "SEATTLE"),
            ("STE 100 SEATTLE", "STE", "100", "SEATTLE"),
            ("UNIT 4B NEW YORK", "UNIT", "4B", "NEW YORK"),
        ],
    )
    async def test_bare_designator_with_identifier_extracted(
        self, city: str, designator: str, identifier: str, rest: str
    ) -> None:
        """GH-285: a designator followed by an identifier-shaped token at the
        head of the city is a unit — 'BLG A SEATTLE' must not stay the city."""
        c: dict[str, str] = {"locality": city}
        events: list[RecoveryEvent] = []
        _recover_unit_from_city(c, events)
        assert c["sub_premise_type"] == designator
        assert c["sub_premise_number"] == identifier
        assert c["locality"] == rest
        assert [e.kind for e in events] == [RecoveryKind.UNIT_RECOVERED]

    @pytest.mark.parametrize("city", ["KEY WEST", "KEY LARGO", "UNIT A", "LOT WEST HAVEN"])
    async def test_designator_without_identifier_shape_untouched(self, city: str) -> None:
        """GH-285: the identifier lift needs an identifier-shaped token (digit
        or single letter) AND a city left over — real cities are left alone."""
        c: dict[str, str] = {"locality": city}
        _recover_unit_from_city(c)
        assert c == {"locality": city}

    async def test_designator_with_identifier_fills_dependent_slot(self) -> None:
        """GH-285: an occupied primary slot routes the lifted unit to the
        dependent slot rather than overwriting it."""
        c: dict[str, str] = {
            "sub_premise_type": "STE",
            "sub_premise_number": "100",
            "locality": "BLG A SEATTLE",
        }
        _recover_unit_from_city(c)
        assert c["sub_premise_type"] == "STE"
        assert c["dependent_sub_premise_type"] == "BLG"
        assert c["dependent_sub_premise_number"] == "A"
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

    @pytest.mark.parametrize("dep_type", ["SUITE", "SUITES", "SUTE"])
    async def test_variant_spelling_of_same_type_collapsed(self, dep_type: str) -> None:
        """GH-286 CR: 'STE B, SUITES B' names one unit twice in two spellings;
        types compare by their UNIT_MAP form, so it must not render 'STE B STE B'."""
        c: dict[str, str] = {
            "sub_premise_type": "STE",
            "sub_premise_number": "B",
            "dependent_sub_premise_type": dep_type,
            "dependent_sub_premise_number": "B",
        }
        recover_components(c)
        assert c["sub_premise_type"] == "STE"
        assert "dependent_sub_premise_type" not in c
        assert "dependent_sub_premise_number" not in c

    @pytest.mark.parametrize("hash_word", ["NO", "NUM", "NUMBER"])
    async def test_hash_alias_restated_by_named_unit_collapsed(self, hash_word: str) -> None:
        """GH-286 CR: 'NO 1, UNIT 1' is the GH-170 '#1, UNIT 1' idiom — the
        '#' aliases compare by UNIT_MAP form, so it must not render '# 1 UNIT 1'."""
        c: dict[str, str] = {
            "sub_premise_type": hash_word,
            "sub_premise_number": "1",
            "dependent_sub_premise_type": "UNIT",
            "dependent_sub_premise_number": "1",
        }
        events = recover_components(c)
        assert c["sub_premise_type"] == "UNIT"
        assert c["sub_premise_number"] == "1"
        assert "dependent_sub_premise_type" not in c
        assert "dependent_sub_premise_number" not in c
        assert [e.kind for e in events] == [RecoveryKind.DUPLICATE_UNIT_COLLAPSED]

    @pytest.mark.parametrize("hash_word", ["#", "NO", "NUMBER"])
    async def test_hash_alias_in_dependent_slot_collapsed(self, hash_word: str) -> None:
        """GH-286 CR: the mirror of GH-170 — the '#' unit in the dependent slot,
        the named unit primary ('NO 1 STE 1').  The named unit is kept."""
        c: dict[str, str] = {
            "dependent_sub_premise_type": hash_word,
            "dependent_sub_premise_number": "1",
            "sub_premise_type": "STE",
            "sub_premise_number": "1",
        }
        events = recover_components(c)
        assert c == {"sub_premise_type": "STE", "sub_premise_number": "1"}
        assert [e.warning for e in events] == ["Duplicate secondary unit collapsed into 'STE 1'"]

    async def test_hash_alias_in_dependent_slot_distinct_id_kept(self) -> None:
        """'NO 2 STE 1' names two units; both slots survive."""
        c: dict[str, str] = {
            "dependent_sub_premise_type": "NO",
            "dependent_sub_premise_number": "2",
            "sub_premise_type": "STE",
            "sub_premise_number": "1",
        }
        assert recover_components(c) == []
        assert c["dependent_sub_premise_number"] == "2"

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


_STREET = {"premise_number": "123", "thoroughfare_name": "MAIN", "thoroughfare_trailing_type": "ST"}


# ---------------------------------------------------------------------------
# _split_hash_phrase (GH #290)
# ---------------------------------------------------------------------------


class TestSplitHashPhrase:
    """A '#' phrase usaddress folds into a named unit's identifier."""

    @pytest.mark.parametrize("identifier", ["NO 5", "# 5", "NUMBER 5", "NO. 5"])
    def test_leading_hash_word_dropped(self, identifier: str) -> None:
        c = {"sub_premise_type": "STE", "sub_premise_number": identifier}
        _split_hash_phrase(c)
        assert c == {"sub_premise_type": "STE", "sub_premise_number": "5"}

    @pytest.mark.parametrize("identifier", ["1 # 1", "1 NO 1", "1, NO 1", "1 # 1,"])
    def test_restated_identifier_collapsed(self, identifier: str) -> None:
        c = {"sub_premise_type": "APT", "sub_premise_number": identifier, "locality": "X"}
        events: list[RecoveryEvent] = []
        _split_hash_phrase(c, events)
        assert c == {"sub_premise_type": "APT", "sub_premise_number": "1", "locality": "X"}
        assert [e.warning for e in events] == ["Duplicate secondary unit collapsed into 'APT 1'"]

    def test_distinct_identifier_routed_after_source_slot(self) -> None:
        c = {"sub_premise_type": "STE", "sub_premise_number": "1 NO 2", "locality": "X"}
        events: list[RecoveryEvent] = []
        _split_hash_phrase(c, events)
        assert list(c.items()) == [
            ("sub_premise_type", "STE"),
            ("sub_premise_number", "1"),
            ("dependent_sub_premise_type", "NO"),
            ("dependent_sub_premise_number", "2"),
            ("locality", "X"),
        ]
        assert events == []

    def test_distinct_identifier_kept_when_no_slot_free(self) -> None:
        c = {
            "sub_premise_type": "STE",
            "sub_premise_number": "1 # 2",
            "dependent_sub_premise_type": "BLDG",
            "dependent_sub_premise_number": "A",
        }
        before = dict(c)
        _split_hash_phrase(c)
        assert c == before

    @pytest.mark.parametrize(
        "c",
        [
            {"sub_premise_number": "# 5 # 6"},  # no designator: the standardizer's job
            {"sub_premise_type": "#", "sub_premise_number": "5 # 6"},
            {"sub_premise_type": "STE", "sub_premise_number": "5 NO"},  # nothing after
            {"sub_premise_type": "STE", "sub_premise_number": "NO"},
            {"sub_premise_type": "STE", "sub_premise_number": "5 NO SMOKING"},
            {"sub_premise_type": "APT", "sub_premise_number": "PH 2"},
        ],
    )
    def test_left_alone(self, c: dict[str, str]) -> None:
        before = dict(c)
        _split_hash_phrase(c)
        assert c == before

    def test_dependent_slot_split(self) -> None:
        c = {"dependent_sub_premise_type": "BLDG", "dependent_sub_premise_number": "NO 3"}
        _split_hash_phrase(c)
        assert c == {"dependent_sub_premise_type": "BLDG", "dependent_sub_premise_number": "3"}


# ---------------------------------------------------------------------------
# _recover_unit_list_item (GH #288, #297)
# ---------------------------------------------------------------------------

_UNIT = {"sub_premise_type": "STE", "sub_premise_number": "100"}
_CITY = {"locality": "SEATTLE", "administrative_area": "WA"}


class TestRecoverUnitListItem:
    """A list item after a unit, tagged as an intersection, box or second unit."""

    @pytest.mark.parametrize(
        "item_fields",
        [
            {"intersection_separator": "&", "second_thoroughfare_name": "101,"},
            {"intersection_separator": "AND", "second_thoroughfare_name": "101"},
            {"general_delivery_type": "AND", "general_delivery": "101"},
            {"general_delivery_type": "&", "general_delivery": "101"},
            {"dependent_sub_premise_type": "AND", "dependent_sub_premise_number": "101"},
        ],
    )
    def test_item_folded_into_unit(self, item_fields: dict[str, str]) -> None:
        c = {**_STREET, **_UNIT, **item_fields, **_CITY}
        events: list[RecoveryEvent] = []
        _recover_unit_list_item(c, events)
        assert c == {
            **_STREET,
            "sub_premise_type": "STE",
            "sub_premise_number": "100 & 101",
            **_CITY,
        }
        assert [e.kind for e in events] == [RecoveryKind.UNIT_RECOVERED]
        assert [e.warning for e in events] == [
            "Unit list item recovered from mis-tagged field: '101'"
        ]

    def test_folds_into_the_unit_before_it(self) -> None:
        c = {
            **_STREET,
            "dependent_sub_premise_type": "BLDG",
            "dependent_sub_premise_number": "1",
            **_UNIT,
            "intersection_separator": "&",
            "second_thoroughfare_name": "B",
        }
        _recover_unit_list_item(c)
        assert c["sub_premise_number"] == "100 & B"
        assert c["dependent_sub_premise_number"] == "1"

    @pytest.mark.parametrize(
        "c",
        [
            # no unit before the separator: a real intersection
            {**_STREET, "intersection_separator": "&", "second_thoroughfare_name": "101"},
            # a street after the unit, not a bare identifier
            {
                **_STREET,
                **_UNIT,
                "intersection_separator": "&",
                "second_thoroughfare_name": "6TH",
                "second_thoroughfare_trailing_type": "AVE",
            },
            {**_STREET, **_UNIT, "intersection_separator": "&", "second_thoroughfare_name": "OAK"},
            {**_STREET, **_UNIT, "intersection_separator": "AT", "second_thoroughfare_name": "1"},
            # a real box beside the unit
            {**_STREET, **_UNIT, "general_delivery_type": "PO BOX", "general_delivery": "5"},
            {**_STREET, **_UNIT, "general_delivery_type": "AND", "general_delivery": "1 2"},
            {
                **_STREET,
                **_UNIT,
                "general_delivery_group_type": "RR",
                "general_delivery_group": "2",
                "general_delivery_type": "AND",
                "general_delivery": "5",
            },
            # not adjacent: the separator does not follow the unit
            {**_UNIT, **_STREET, "intersection_separator": "&", "second_thoroughfare_name": "1"},
        ],
    )
    def test_left_alone(self, c: dict[str, str]) -> None:
        before = dict(c)
        assert recover_components(c) == []
        assert c == before


# ---------------------------------------------------------------------------
# _recover_unit_from_general_delivery (GH #285)
# ---------------------------------------------------------------------------


class TestRecoverUnitFromGeneralDelivery:
    """usaddress tags some unit phrases as a USPS box (``USPSBoxType`` /
    ``USPSBoxID``).  With a street present the box never renders, so a box
    type that is a Pub 28 unit designator moves to a free unit slot."""

    @pytest.mark.parametrize(
        ("gd_type", "gd_id"),
        [("SUITES", "100"), ("FLOORS", "2-3"), ("BLG", "A"), ("#", "5")],
    )
    def test_designator_moved_to_unit_slot(self, gd_type: str, gd_id: str) -> None:
        c = {**_STREET, "general_delivery_type": gd_type, "general_delivery": gd_id}
        events: list[RecoveryEvent] = []
        _recover_unit_from_general_delivery(c, events)
        assert c["sub_premise_type"] == gd_type
        assert c["sub_premise_number"] == gd_id
        assert "general_delivery_type" not in c
        assert "general_delivery" not in c
        assert [e.kind for e in events] == [RecoveryKind.UNIT_RECOVERED]
        assert gd_type in events[0].warning

    def test_moved_unit_keeps_source_position(self) -> None:
        """The unit takes the box keys' place in the dict, so source order —
        which decides line-2 slot order for same-level pairs — survives."""
        c = {
            **_STREET,
            "general_delivery_type": "SUITES",
            "general_delivery": "100",
            "sub_premise_type": "APT",
            "sub_premise_number": "4",
            "locality": "SEATTLE",
        }
        _recover_unit_from_general_delivery(c)
        assert list(c) == [
            *_STREET,
            "dependent_sub_premise_type",
            "dependent_sub_premise_number",
            "sub_premise_type",
            "sub_premise_number",
            "locality",
        ]
        assert c["sub_premise_type"] == "APT"
        assert c["dependent_sub_premise_type"] == "SUITES"

    def test_splice_replaces_an_existing_empty_key(self) -> None:
        """Callers treat an empty value as absent; a spliced-in key must win
        over a stale empty one wherever it sat."""
        c = {"general_delivery": "A SEATTLE", "locality": ""}
        _splice(c, ("general_delivery",), {"locality": "SEATTLE"})
        assert c == {"locality": "SEATTLE"}

    def test_trailing_city_split_from_identifier(self) -> None:
        """No ZIP/state: usaddress folds the city into the box ID
        ('BLG' / 'A SEATTLE').  With no locality parsed, the identifier
        keeps its first token and the rest becomes the city."""
        c = {**_STREET, "general_delivery_type": "BLG", "general_delivery": "A SEATTLE"}
        _recover_unit_from_general_delivery(c)
        assert c["sub_premise_type"] == "BLG"
        assert c["sub_premise_number"] == "A"
        assert c["locality"] == "SEATTLE"

    def test_compound_identifier_not_split_into_city(self) -> None:
        """'100 B' is one compound identifier, not identifier + city 'B':
        the split needs a word after the identifier that is not itself
        identifier-shaped."""
        c = {**_STREET, "general_delivery_type": "STE", "general_delivery": "100 B"}
        _recover_unit_from_general_delivery(c)
        assert c["sub_premise_number"] == "100 B"
        assert "locality" not in c

    def test_multi_token_identifier_kept_when_locality_present(self) -> None:
        c = {
            **_STREET,
            "general_delivery_type": "STE",
            "general_delivery": "100 B",
            "locality": "SEATTLE",
        }
        _recover_unit_from_general_delivery(c)
        assert c["sub_premise_number"] == "100 B"
        assert c["locality"] == "SEATTLE"

    @pytest.mark.parametrize("gd_type", ["PO BOX", "BOX", "LOCKER", "PMB"])
    def test_non_designator_box_type_left_alone(self, gd_type: str) -> None:
        """A real box type (or an unknown word) is not a unit; the standardizer
        warns about it instead."""
        c = {**_STREET, "general_delivery_type": gd_type, "general_delivery": "7"}
        before = dict(c)
        assert recover_components(c) == []
        assert c == before

    def test_no_street_left_alone(self) -> None:
        """Without a street the box renders as line 1 — nothing is dropped."""
        c = {"general_delivery_type": "SUITES", "general_delivery": "100", "locality": "SEATTLE"}
        before = dict(c)
        _recover_unit_from_general_delivery(c)
        assert c == before

    def test_rural_route_group_left_alone(self) -> None:
        c = {
            **_STREET,
            "general_delivery_group_type": "RR",
            "general_delivery_group": "2",
            "general_delivery_type": "BOX",
            "general_delivery": "152",
        }
        before = dict(c)
        _recover_unit_from_general_delivery(c)
        assert c == before

    def test_both_slots_full_left_alone(self) -> None:
        c = {
            **_STREET,
            "sub_premise_type": "STE",
            "sub_premise_number": "1",
            "dependent_sub_premise_type": "BLDG",
            "dependent_sub_premise_number": "2",
            "general_delivery_type": "FLOORS",
            "general_delivery": "3",
        }
        before = dict(c)
        _recover_unit_from_general_delivery(c)
        assert c == before


# ---------------------------------------------------------------------------
# _recover_locality_from_trailing_addressee (GH #285)
# ---------------------------------------------------------------------------


class TestRecoverLocalityFromTrailingAddressee:
    """Without a ZIP, usaddress can tag a whole post-street tail as
    ``Recipient`` ('BLG A SEATTLE WA').  The standardizer never renders
    ``addressee``, so city, state and unit were all lost."""

    @pytest.mark.parametrize(
        ("tail", "city", "state"),
        [
            ("BLG A SEATTLE WA", "BLG A SEATTLE", "WA"),
            ("SPRINGFIELD ILLINOIS", "SPRINGFIELD", "ILLINOIS"),
            ("BUFFALO NEW YORK", "BUFFALO", "NEW YORK"),
            ("SAIPAN NORTHERN MARIANA ISLANDS", "SAIPAN", "NORTHERN MARIANA ISLANDS"),
        ],
    )
    def test_city_and_state_recovered(self, tail: str, city: str, state: str) -> None:
        c = {**_STREET, "addressee": tail}
        events: list[RecoveryEvent] = []
        _recover_locality_from_trailing_addressee(c, events)
        assert "addressee" not in c
        assert c["locality"] == city
        assert c["administrative_area"] == state
        assert [e.kind for e in events] == [RecoveryKind.LOCALITY_RECOVERED]

    def test_full_recovery_lifts_unit_from_recovered_city(self) -> None:
        c = {**_STREET, "addressee": "BLG A SEATTLE WA"}
        events = recover_components(c)
        assert c["sub_premise_type"] == "BLG"
        assert c["sub_premise_number"] == "A"
        assert c["locality"] == "SEATTLE"
        assert c["administrative_area"] == "WA"
        assert [e.kind for e in events] == [
            RecoveryKind.LOCALITY_RECOVERED,
            RecoveryKind.UNIT_RECOVERED,
        ]

    def test_comma_separated_recipient_kept_out_of_city(self) -> None:
        """The city is the last comma segment; a leading non-designator segment
        is a recipient and stays in addressee, not in the city."""
        c = {**_STREET, "addressee": "ATTN JOHN, SEATTLE WA"}
        recover_components(c)
        assert c["addressee"] == "ATTN JOHN"
        assert c["locality"] == "SEATTLE"
        assert c["administrative_area"] == "WA"

    def test_comma_separated_designator_segment_lifted(self) -> None:
        """A leading segment that is a unit stays ahead of the city so city
        recovery lifts it; a recipient segment beside it stays in addressee."""
        c = {**_STREET, "addressee": "ATTN JOHN, STE 5, SEATTLE WA"}
        recover_components(c)
        assert c["addressee"] == "ATTN JOHN"
        assert c["sub_premise_type"] == "STE"
        assert c["sub_premise_number"] == "5"
        assert c["locality"] == "SEATTLE"

    def test_leading_addressee_left_alone(self) -> None:
        """A recipient before the street is a real recipient, not a tail."""
        c = {"addressee": "JOHN SMITH WA", **_STREET}
        before = dict(c)
        _recover_locality_from_trailing_addressee(c)
        assert c == before

    @pytest.mark.parametrize(
        "extra",
        [{"locality": "SEATTLE"}, {"administrative_area": "WA"}, {"postcode": "98101"}],
    )
    def test_left_alone_when_last_line_parsed(self, extra: dict[str, str]) -> None:
        c = {**_STREET, "addressee": "BLG A SEATTLE WA", **extra}
        before = dict(c)
        _recover_locality_from_trailing_addressee(c)
        assert c == before

    @pytest.mark.parametrize("tail", ["ATTN JOHN SMITH", "WA"])
    def test_left_alone_without_trailing_state_and_city(self, tail: str) -> None:
        c = {**_STREET, "addressee": tail}
        before = dict(c)
        _recover_locality_from_trailing_addressee(c)
        assert c == before

    @pytest.mark.parametrize("tail", [", WA", "ATTN JOHN, , WA"])
    def test_left_alone_when_no_city_before_state(self, tail: str) -> None:
        """No city to recover → no recovery and no 'city recovered' warning."""
        c = {**_STREET, "addressee": tail}
        before = dict(c)
        assert recover_components(c) == []
        assert c == before

    def test_no_street_left_alone(self) -> None:
        c = {"addressee": "SEATTLE WA"}
        _recover_locality_from_trailing_addressee(c)
        assert c == {"addressee": "SEATTLE WA"}


# ---------------------------------------------------------------------------
# _recover_general_delivery_from_name (GH #293)
# ---------------------------------------------------------------------------

_LAST_LINE = {"locality": "SEATTLE", "administrative_area": "WA", "postcode": "98101"}


class TestRecoverGeneralDeliveryFromName:
    """usaddress tags a literal ``GENERAL DELIVERY`` as ``LandmarkName`` (with
    a comma) or ``Recipient`` (without).  The standardizer renders neither, so
    line 1 came out empty."""

    @pytest.mark.parametrize("key", ["landmark", "addressee"])
    @pytest.mark.parametrize("text", ["GENERAL DELIVERY", "general delivery,", "General  Delivery"])
    def test_moved_to_general_delivery_type(self, key: str, text: str) -> None:
        c = {key: text, **_LAST_LINE}
        events: list[RecoveryEvent] = []
        _recover_general_delivery_from_name(c, events)
        assert c == {"general_delivery_type": "GENERAL DELIVERY", **_LAST_LINE}
        assert next(iter(c)) == "general_delivery_type"
        assert [e.kind for e in events] == [RecoveryKind.DELIVERY_LINE_RECOVERED]
        assert "GENERAL DELIVERY" in events[0].warning

    def test_city_and_state_split_from_recipient_tail(self) -> None:
        """No ZIP: usaddress tags the whole input as recipient."""
        c = {"addressee": "GENERAL DELIVERY SEATTLE WA"}
        events = recover_components(c)
        assert c == {
            "general_delivery_type": "GENERAL DELIVERY",
            "locality": "SEATTLE",
            "administrative_area": "WA",
        }
        assert [e.kind for e in events] == [
            RecoveryKind.DELIVERY_LINE_RECOVERED,
            RecoveryKind.LOCALITY_RECOVERED,
        ]

    def test_tail_without_state_left_alone(self) -> None:
        """'GENERAL DELIVERY SEATTLE': no state, so the tail can't be told
        from a recipient name — nothing is guessed."""
        c = {"addressee": "GENERAL DELIVERY SEATTLE"}
        assert recover_components(c) == []
        assert c == {"addressee": "GENERAL DELIVERY SEATTLE"}

    @pytest.mark.parametrize(
        "text",
        [
            "JOHN SMITH, GENERAL DELIVERY",
            "JOHN SMITH GENERAL DELIVERY",
            "GENERAL DELIVERY JOHN SMITH",
            "GENERAL DELIVERY, JOHN SMITH,",
        ],
    )
    def test_recipient_beside_phrase_kept(self, text: str) -> None:
        """GH-293 CR 1: a name line beside the phrase stays the recipient; with
        the last line parsed, text after the phrase can't be the city."""
        c = {"addressee": text, **_LAST_LINE}
        events = recover_components(c)
        assert c == {
            "addressee": "JOHN SMITH",
            "general_delivery_type": "GENERAL DELIVERY",
            **_LAST_LINE,
        }
        assert [e.kind for e in events] == [RecoveryKind.DELIVERY_LINE_RECOVERED]

    def test_recipient_before_phrase_with_city_and_state_tail(self) -> None:
        c = {"addressee": "JOHN SMITH GENERAL DELIVERY SEATTLE WA"}
        recover_components(c)
        assert c == {
            "addressee": "JOHN SMITH",
            "general_delivery_type": "GENERAL DELIVERY",
            "locality": "SEATTLE",
            "administrative_area": "WA",
        }

    @pytest.mark.parametrize(
        "extra",
        [_STREET, {"general_delivery_type": "PO BOX", "general_delivery": "5"}],
    )
    def test_left_alone_beside_street_or_box(self, extra: dict[str, str]) -> None:
        c = {"landmark": "GENERAL DELIVERY", **extra, **_LAST_LINE}
        before = dict(c)
        _recover_general_delivery_from_name(c)
        assert c == before

    @pytest.mark.parametrize("text", ["THE GROVE", "GENERAL HOSPITAL", "DELIVERY DEPT"])
    def test_other_names_left_alone(self, text: str) -> None:
        c = {"landmark": text, **_LAST_LINE}
        before = dict(c)
        _recover_general_delivery_from_name(c)
        assert c == before


# ---------------------------------------------------------------------------
# _recover_route_from_unit_slot (GH #292)
# ---------------------------------------------------------------------------

_BOX = {"general_delivery_type": "BOX", "general_delivery": "5678"}
_APO = {"locality": "APO", "administrative_area": "AE", "postcode": "09001"}


class TestRecoverRouteFromUnitSlot:
    """usaddress tags the military route group (``PSC 1234``, ``UNIT 2050``)
    as a unit, so line 2 got the route and line 1 only the box.  Pub 28 puts
    both on line 1: ``PSC 1234 BOX 5678``."""

    @pytest.mark.parametrize(
        ("type_key", "id_key"),
        [
            ("sub_premise_type", "sub_premise_number"),
            ("dependent_sub_premise_type", "dependent_sub_premise_number"),
        ],
    )
    @pytest.mark.parametrize("designator", ["PSC", "CMR", "psc"])
    def test_military_unit_moved_to_route_group(
        self, type_key: str, id_key: str, designator: str
    ) -> None:
        c = {type_key: designator, id_key: "1234", **_BOX, **_APO}
        events: list[RecoveryEvent] = []
        _recover_route_from_unit_slot(c, events)
        assert c == {
            "general_delivery_group_type": designator,
            "general_delivery_group": "1234",
            **_BOX,
            **_APO,
        }
        assert list(c)[:2] == ["general_delivery_group_type", "general_delivery_group"]
        assert [e.kind for e in events] == [RecoveryKind.DELIVERY_LINE_RECOVERED]
        assert f"{designator} 1234" in events[0].warning

    @pytest.mark.parametrize(
        "state", ["AA", "AE", "AP", "ap", "ARMED FORCES PACIFIC", "Armed Forces Europe"]
    )
    def test_unit_moved_with_military_state(self, state: str) -> None:
        c = {"sub_premise_type": "UNIT", "sub_premise_number": "2050", **_BOX}
        c |= {"locality": "APO", "administrative_area": state}
        _recover_route_from_unit_slot(c)
        assert c["general_delivery_group_type"] == "UNIT"
        assert c["general_delivery_group"] == "2050"
        assert "sub_premise_type" not in c

    def test_unit_left_alone_without_military_state(self) -> None:
        """'UNIT' is also a civilian designator; only a military last line
        makes it a route group."""
        c = {"sub_premise_type": "UNIT", "sub_premise_number": "5", **_BOX, **_LAST_LINE}
        before = dict(c)
        assert recover_components(c) == []
        assert c == before

    @pytest.mark.parametrize(
        "extra",
        [
            _STREET,
            {"general_delivery_group_type": "RR", "general_delivery_group": "2"},
        ],
    )
    def test_left_alone_beside_street_or_group(self, extra: dict[str, str]) -> None:
        c = {**extra, "sub_premise_type": "PSC", "sub_premise_number": "1234", **_BOX, **_APO}
        before = dict(c)
        _recover_route_from_unit_slot(c)
        assert c == before

    def test_left_alone_without_box(self) -> None:
        c = {"sub_premise_type": "PSC", "sub_premise_number": "1234", **_APO}
        before = dict(c)
        _recover_route_from_unit_slot(c)
        assert c == before

    def test_civilian_unit_left_alone(self) -> None:
        c = {"sub_premise_type": "STE", "sub_premise_number": "5", **_BOX, **_APO}
        before = dict(c)
        _recover_route_from_unit_slot(c)
        assert c == before


# ---------------------------------------------------------------------------
# _recover_po_box_designation (GH #302)
# ---------------------------------------------------------------------------


class TestRecoverPoBoxDesignation:
    """usaddress tags a Pub 28 §283 box designation (``DRAWER``, ``CALLER``,
    ``FIRM CALLER``, ``BIN``) as a landmark, building name, recipient or unit,
    so line 1 came out empty or the box landed on line 2."""

    @staticmethod
    def _box(box_type: str, box_id: str) -> dict[str, str]:
        return {"general_delivery_type": box_type, "general_delivery": box_id}

    @pytest.mark.parametrize("key", ["landmark", "premise_name", "addressee"])
    @pytest.mark.parametrize(
        ("text", "box_type", "box_id"),
        [
            ("DRAWER 42", "DRAWER", "42"),
            ("CALLER 42,", "CALLER", "42"),
            ("FIRM CALLER 5000", "FIRM CALLER", "5000"),
            ("Lockbox 42-A", "Lockbox", "42-A"),
            ("BIN A", "BIN", "A"),
        ],
    )
    def test_name_field_moved_to_box(self, key: str, text: str, box_type: str, box_id: str) -> None:
        c = {key: text, **_LAST_LINE}
        events: list[RecoveryEvent] = []
        _recover_po_box_designation(c, events)
        assert c == {**self._box(box_type, box_id), **_LAST_LINE}
        assert next(iter(c)) == "general_delivery_type"
        assert [e.kind for e in events] == [RecoveryKind.DELIVERY_LINE_RECOVERED]
        assert DELIVERY_LINE_RECOVERED.format(text=f"{box_type} {box_id}") == events[0].warning

    @pytest.mark.parametrize(
        "text", ["ACME CORP, DRAWER 42", "DRAWER 42, ACME CORP", "ACME CORP, DRAWER 42,"]
    )
    def test_name_beside_box_kept(self, text: str) -> None:
        c = {"landmark": text, **_LAST_LINE}
        _recover_po_box_designation(c)
        assert c == {"landmark": "ACME CORP", **self._box("DRAWER", "42"), **_LAST_LINE}

    def test_city_and_state_split_from_recipient_tail(self) -> None:
        """No ZIP: usaddress tags the whole input as recipient."""
        c = {"addressee": "FIRM CALLER 42 SEATTLE WA"}
        events = recover_components(c)
        assert c == {
            **self._box("FIRM CALLER", "42"),
            "locality": "SEATTLE",
            "administrative_area": "WA",
        }
        assert [e.kind for e in events] == [
            RecoveryKind.DELIVERY_LINE_RECOVERED,
            RecoveryKind.LOCALITY_RECOVERED,
        ]

    @pytest.mark.parametrize(
        "text", ["JOHN SMITH DRAWER 42 SEATTLE WA", "JOHN SMITH, DRAWER 42 SEATTLE WA"]
    )
    def test_recipient_before_box_with_city_and_state_tail(self, text: str) -> None:
        """GH-302 CR 2: with no last line parsed, a name before the
        designation stays the recipient, as beside GENERAL DELIVERY."""
        c = {"addressee": text}
        recover_components(c)
        assert c == {
            "addressee": "JOHN SMITH",
            **self._box("DRAWER", "42"),
            "locality": "SEATTLE",
            "administrative_area": "WA",
        }

    @pytest.mark.parametrize(
        ("text", "recipient"),
        [
            ("ACME CORP, DRAWER 42, SEATTLE WA", "ACME CORP"),
            ("DRAWER 42, SEATTLE WA", ""),
            ("DRAWER 42 ACME CORP, SEATTLE WA", "ACME CORP"),
        ],
    )
    def test_comma_tail_split_into_city_and_state(self, text: str, recipient: str) -> None:
        """GH-302 CR 6: with no last line parsed, segments after the box are
        the city and state, not more recipient text."""
        c = {"addressee": text}
        events = recover_components(c)
        assert c == {
            **({"addressee": recipient} if recipient else {}),
            **self._box("DRAWER", "42"),
            "locality": "SEATTLE",
            "administrative_area": "WA",
        }
        assert RecoveryKind.LOCALITY_RECOVERED in [e.kind for e in events]

    @pytest.mark.parametrize(
        "c",
        [
            {"addressee": "DRAWER 42, ACME CORP"},  # no state after the box
            {"addressee": "CALLER 42 SEATTLE"},  # no state: city or name?
            {"landmark": "DRAWER 42 SEATTLE WA"},  # only a recipient tail is split
            {"addressee": "DRAWER 42 SEATTLE WA 98101", **_LAST_LINE},  # last line parsed
        ],
    )
    def test_text_after_box_left_alone(self, c: dict[str, str]) -> None:
        before = dict(c)
        _recover_po_box_designation(c)
        assert c == before

    @pytest.mark.parametrize(
        ("type_key", "id_key"),
        [
            ("sub_premise_type", "sub_premise_number"),
            ("dependent_sub_premise_type", "dependent_sub_premise_number"),
        ],
    )
    @pytest.mark.parametrize("designator", ["DRAWER", "CALLER", "BIN", "bin"])
    def test_unit_slot_moved_to_box(self, type_key: str, id_key: str, designator: str) -> None:
        c = {type_key: designator, id_key: "42,", **_LAST_LINE}
        events: list[RecoveryEvent] = []
        _recover_po_box_designation(c, events)
        assert c == {**self._box(designator, "42"), **_LAST_LINE}
        assert next(iter(c)) == "general_delivery_type"
        assert [e.kind for e in events] == [RecoveryKind.DELIVERY_LINE_RECOVERED]

    def test_unit_slot_box_beside_real_unit(self) -> None:
        """'DRAWER 42 STE 5': the box goes to line 1, the suite stays."""
        c = {
            "dependent_sub_premise_type": "DRAWER",
            "dependent_sub_premise_number": "42",
            "sub_premise_type": "STE",
            "sub_premise_number": "5",
            **_LAST_LINE,
        }
        _recover_po_box_designation(c)
        assert c == {
            **self._box("DRAWER", "42"),
            "sub_premise_type": "STE",
            "sub_premise_number": "5",
            **_LAST_LINE,
        }

    @pytest.mark.parametrize(
        ("addressee", "rest", "box_type"),
        [
            ("FIRM CALLER", {}, "FIRM CALLER"),
            ("ACME CORP, FIRM CALLER", {"addressee": "ACME CORP"}, "FIRM CALLER"),
            ("ACME CORP DRAWER", {"addressee": "ACME CORP"}, "DRAWER"),
        ],
    )
    def test_box_number_tagged_as_house_number(
        self, addressee: str, rest: dict[str, str], box_type: str
    ) -> None:
        """usaddress tags the box ID as a street number and the designation
        as the recipient's last words; no street name was parsed."""
        c = {"addressee": addressee, "premise_number": "2000", **_LAST_LINE}
        events: list[RecoveryEvent] = []
        _recover_po_box_designation(c, events)
        assert c == {**rest, **self._box(box_type, "2000"), **_LAST_LINE}
        assert [e.kind for e in events] == [RecoveryKind.DELIVERY_LINE_RECOVERED]

    @pytest.mark.parametrize(
        ("name", "rest", "box_type"),
        [
            ("FIRM CALLER 42", {}, "FIRM CALLER"),
            ("FIRM CALLER 42,", {}, "FIRM CALLER"),
            ("JOHN SMITH CALLER 42", {"addressee": "JOHN SMITH"}, "CALLER"),
            ("ACME CORP POST OFFICE DRAWER 42", {"addressee": "ACME CORP"}, "POST OFFICE DRAWER"),
        ],
    )
    def test_street_name_ending_in_box_moved_to_box(
        self, name: str, rest: dict[str, str], box_type: str
    ) -> None:
        """GH-305: usaddress tags the delivery line, and any name before it,
        as the street name; no street number or other street field was parsed."""
        c = {"thoroughfare_name": name, **_LAST_LINE}
        events: list[RecoveryEvent] = []
        _recover_po_box_designation(c, events)
        assert c == {**rest, **self._box(box_type, "42"), **_LAST_LINE}
        assert next(iter(c)) == next(iter(rest), "general_delivery_type")
        assert [e.kind for e in events] == [RecoveryKind.DELIVERY_LINE_RECOVERED]
        assert DELIVERY_LINE_RECOVERED.format(text=f"{box_type} 42") == events[0].warning

    def test_street_name_box_name_joins_recipient(self) -> None:
        """GH-305: a name before the designation joins a recipient already parsed."""
        c = {"addressee": "ATTN BILLING", "thoroughfare_name": "ACME CORP DRAWER 42", **_LAST_LINE}
        _recover_po_box_designation(c)
        assert c == {
            "addressee": "ATTN BILLING, ACME CORP",
            **self._box("DRAWER", "42"),
            **_LAST_LINE,
        }

    @pytest.mark.parametrize(
        "c",
        [
            # A street: the unit is a real unit, the name a real name.
            {**_STREET, "dependent_sub_premise_type": "BIN", "dependent_sub_premise_number": "4"},
            {**_STREET, "landmark": "DRAWER 42"},
            {"addressee": "ACME DRAWER", **_STREET},
            # A box or route group already parsed.
            {"landmark": "DRAWER 42", "general_delivery_type": "PO BOX", "general_delivery": "5"},
            {"sub_premise_type": "BIN", "sub_premise_number": "4", **_BOX},
            {"landmark": "CALLER 42", "general_delivery_group_type": "RR"},
            # Not '<designation> <id>'.
            {"landmark": "DRAWER"},
            {"landmark": "THE BIN"},
            {"landmark": "JOHN DRAWER 42"},
            {"landmark": "CALLER JOHN"},
            {"premise_name": "BINGHAM 42"},
            {"sub_premise_type": "BIN"},
            {"sub_premise_type": "STE", "sub_premise_number": "42"},
            {"addressee": "ACME CORP", "premise_number": "2000"},
            {"addressee": "FIRM CALLER", "premise_number": "2000", "premise_number_suffix": "A"},
            # GH-305: a street name that is a street, or does not end in a box.
            {"premise_number": "123", "thoroughfare_name": "FIRM CALLER 42"},
            {"thoroughfare_name": "CALLER 42", "thoroughfare_trailing_type": "RD"},
            {"thoroughfare_pre_direction": "N", "thoroughfare_name": "BIN 4"},
            {"thoroughfare_name": "DRAWER 42 WEST"},
            {"thoroughfare_name": "BINGHAM 42"},
            {"thoroughfare_name": "JOHN CALLER"},
            {
                "thoroughfare_name": "CALLER 42",
                "general_delivery_type": "PO BOX",
                "general_delivery": "5",
            },
            # GH-305 CR 1: the first street of an intersection.
            {
                "thoroughfare_name": "DRAWER 42",
                "intersection_separator": "&",
                "second_thoroughfare_name": "MAIN",
                "second_thoroughfare_trailing_type": "ST",
            },
        ],
    )
    def test_left_alone(self, c: dict[str, str]) -> None:
        c |= _LAST_LINE
        before = dict(c)
        _recover_po_box_designation(c)
        assert c == before

    @pytest.mark.parametrize(
        ("raw", "line1", "line2", "recovered"),
        [
            ("DRAWER 42, SEATTLE, WA 98101", "PO BOX 42", "", "DRAWER 42"),
            ("CALLER 42, SEATTLE, WA 98101", "PO BOX 42", "", "CALLER 42"),
            ("FIRM CALLER 42, SEATTLE, WA 98101", "PO BOX 42", "", "FIRM CALLER 42"),
            ("BIN 42, SEATTLE, WA 98101", "PO BOX 42", "", "BIN 42"),
            ("DRAWER 4200, PORTLAND, OR 97201", "PO BOX 4200", "", "DRAWER 4200"),
            ("FIRM CALLER 5000, SEATTLE, WA 98101", "PO BOX 5000", "", "FIRM CALLER 5000"),
            ("FIRM CALLER 2000 AUSTIN TX 78701", "PO BOX 2000", "", "FIRM CALLER 2000"),
            ("ACME CORP, DRAWER 42, SEATTLE, WA 98101", "PO BOX 42", "", "DRAWER 42"),
            ("DRAWER 42 STE 5, SEATTLE, WA 98101", "PO BOX 42", "STE 5", "DRAWER 42"),
            ("123 MAIN ST BIN 4, SEATTLE, WA 98101", "123 MAIN ST", "BIN 4", None),
            # GH-305: tagged as the street name.
            ("FIRM CALLER 42, SEATTLE WA", "PO BOX 42", "", "FIRM CALLER 42"),
            ("JOHN SMITH CALLER 42, SEATTLE, WA 98101", "PO BOX 42", "", "CALLER 42"),
            ("DRAWER 42 & MAIN ST, SEATTLE WA", "DRAWER 42 & MAIN ST", "", None),
        ],
    )
    async def test_end_to_end(
        self, raw: str, line1: str, line2: str, recovered: str | None
    ) -> None:
        """GH-302 acceptance: parse → standardize renders the box on line 1."""
        response = (await parse_address(raw)).response
        result = standardize(response.components.values, "US")
        assert (result.address_line_1, result.address_line_2) == (line1, line2)
        prefix = DELIVERY_LINE_RECOVERED.partition("{")[0]
        delivery_warnings = [w for w in response.warnings if w.startswith(prefix)]
        expected = [DELIVERY_LINE_RECOVERED.format(text=recovered)] if recovered else []
        assert delivery_warnings == expected


# ---------------------------------------------------------------------------
# _recover_split_box_designation (GH #304)
# ---------------------------------------------------------------------------


class TestRecoverSplitBoxDesignation:
    """usaddress splits a multi-word PO Box designation across fields: its
    head as the box type and the rest into the ID (``P`` / ``O DRAWER 42``),
    or its head into the recipient (``FIRM CALLER`` / ``BOX``), so the
    designation never reached BOX_TYPE_MAP whole."""

    @pytest.mark.parametrize(
        ("c", "expected", "recovered"),
        [
            (
                {"general_delivery_type": "P", "general_delivery": "O DRAWER 42"},
                {"general_delivery_type": "P O DRAWER", "general_delivery": "42"},
                "P O DRAWER 42",
            ),
            (
                {"general_delivery_type": "POST OFFICE", "general_delivery": "DRAWER 42,"},
                {"general_delivery_type": "POST OFFICE DRAWER", "general_delivery": "42"},
                "POST OFFICE DRAWER 42",
            ),
            (
                {
                    "addressee": "FIRM CALLER",
                    "general_delivery_type": "BOX",
                    "general_delivery": "42",
                },
                {"general_delivery_type": "FIRM CALLER BOX", "general_delivery": "42"},
                "FIRM CALLER BOX 42",
            ),
            (
                {
                    "addressee": "JOHN SMITH CALLER",
                    "general_delivery_type": "BOX",
                    "general_delivery": "42",
                },
                {
                    "addressee": "JOHN SMITH",
                    "general_delivery_type": "CALLER BOX",
                    "general_delivery": "42",
                },
                "CALLER BOX 42",
            ),
            (
                {
                    "addressee": "ACME CORP, CALLER",
                    "general_delivery_type": "BOX",
                    "general_delivery": "42",
                },
                {
                    "addressee": "ACME CORP",
                    "general_delivery_type": "CALLER BOX",
                    "general_delivery": "42",
                },
                "CALLER BOX 42",
            ),
        ],
    )
    def test_designation_rejoined(
        self, c: dict[str, str], expected: dict[str, str], recovered: str
    ) -> None:
        c |= _LAST_LINE
        events: list[RecoveryEvent] = []
        _recover_split_box_designation(c, events)
        assert c == {**expected, **_LAST_LINE}
        assert list(c) == [*expected, *_LAST_LINE]
        assert [e.kind for e in events] == [RecoveryKind.DELIVERY_LINE_RECOVERED]
        assert DELIVERY_LINE_RECOVERED.format(text=recovered) == events[0].warning

    @pytest.mark.parametrize(
        "c",
        [
            # Already whole, or no longer designation to rejoin.
            {"general_delivery_type": "PO BOX", "general_delivery": "42"},
            {"general_delivery_type": "BOX", "general_delivery": "42"},
            {"general_delivery_type": "PO BOX", "general_delivery": "42 DRAWER 5"},
            {"addressee": "JOHN SMITH", "general_delivery_type": "BOX", "general_delivery": "42"},
            # No identifier after the designation, or text after it.
            {"general_delivery_type": "P", "general_delivery": "O DRAWER"},
            {"general_delivery_type": "P", "general_delivery": "O DRAWER 42 WEST"},
            # The recipient follows the box: not the designation's head.
            {"general_delivery_type": "BOX", "general_delivery": "42", "addressee": "FIRM CALLER"},
            # A street or route group: the box is not the delivery line.
            {**_STREET, "general_delivery_type": "P", "general_delivery": "O DRAWER 42"},
            {
                "general_delivery_group_type": "RR",
                "general_delivery_group": "2",
                "general_delivery_type": "P",
                "general_delivery": "O DRAWER 42",
            },
        ],
    )
    def test_left_alone(self, c: dict[str, str]) -> None:
        c |= _LAST_LINE
        before = dict(c)
        _recover_split_box_designation(c)
        assert c == before

    @pytest.mark.parametrize(
        ("raw", "recovered"),
        [
            ("P O DRAWER 42, SEATTLE, WA 98101", "P O DRAWER 42"),
            ("POST OFFICE DRAWER 42, SEATTLE, WA 98101", "POST OFFICE DRAWER 42"),
            ("FIRM CALLER BOX 42 SEATTLE WA 98101", "FIRM CALLER BOX 42"),
            ("JOHN SMITH CALLER BOX 42, SEATTLE, WA 98101", "CALLER BOX 42"),
            ("P.O. DRAWER 42, SEATTLE, WA 98101", None),
            ("CALLER BOX 42, SEATTLE, WA 98101", None),
        ],
    )
    async def test_end_to_end(self, raw: str, recovered: str | None) -> None:
        """GH-304 acceptance: parse → standardize renders PO BOX on line 1."""
        response = (await parse_address(raw)).response
        result = standardize(response.components.values, "US")
        assert (result.address_line_1, result.address_line_2) == ("PO BOX 42", "")
        prefix = DELIVERY_LINE_RECOVERED.partition("{")[0]
        delivery_warnings = [w for w in response.warnings if w.startswith(prefix)]
        expected = [DELIVERY_LINE_RECOVERED.format(text=recovered)] if recovered else []
        assert delivery_warnings == expected


# ---------------------------------------------------------------------------
# _recover_name_from_box_type (GH #307)
# ---------------------------------------------------------------------------


class TestRecoverNameFromBoxType:
    """usaddress folds a recipient name before the designation into the box
    type (``ACME LOCK BOX``), so the type missed BOX_TYPE_MAP and line 1 kept
    the name."""

    @pytest.mark.parametrize(
        ("c", "expected", "recovered"),
        [
            (
                {"general_delivery_type": "ACME LOCK BOX", "general_delivery": "42"},
                {
                    "addressee": "ACME",
                    "general_delivery_type": "LOCK BOX",
                    "general_delivery": "42",
                },
                "LOCK BOX 42",
            ),
            (
                {"general_delivery_type": "ACME POST OFFICE BOX", "general_delivery": "42,"},
                {
                    "addressee": "ACME",
                    "general_delivery_type": "POST OFFICE BOX",
                    "general_delivery": "42,",
                },
                "POST OFFICE BOX 42",
            ),
            (
                {"general_delivery_type": "ACME, P.O. BOX", "general_delivery": "42"},
                {
                    "addressee": "ACME",
                    "general_delivery_type": "P.O. BOX",
                    "general_delivery": "42",
                },
                "P.O. BOX 42",
            ),
            # A recipient already parsed keeps its place; the name joins it.
            (
                {
                    "addressee": "ATTN BILLING",
                    "general_delivery_type": "ACME LOCKBOX",
                    "general_delivery": "42",
                },
                {
                    "addressee": "ATTN BILLING, ACME",
                    "general_delivery_type": "LOCKBOX",
                    "general_delivery": "42",
                },
                "LOCKBOX 42",
            ),
            # No ID parsed: the name still moves.
            (
                {"general_delivery_type": "ACME PO BOX"},
                {"addressee": "ACME", "general_delivery_type": "PO BOX"},
                "PO BOX",
            ),
        ],
    )
    def test_name_moved_to_recipient(
        self, c: dict[str, str], expected: dict[str, str], recovered: str
    ) -> None:
        c |= _LAST_LINE
        events: list[RecoveryEvent] = []
        _recover_name_from_box_type(c, events)
        assert c == {**expected, **_LAST_LINE}
        assert list(c) == [*expected, *_LAST_LINE]
        assert [e.kind for e in events] == [RecoveryKind.DELIVERY_LINE_RECOVERED]
        assert DELIVERY_LINE_RECOVERED.format(text=recovered) == events[0].warning

    @pytest.mark.parametrize(
        "c",
        [
            # Already a designation, or no designation to keep (bare BOX).
            {"general_delivery_type": "PO BOX", "general_delivery": "42"},
            {"general_delivery_type": "FIRM CALLER BOX", "general_delivery": "42"},
            {"general_delivery_type": "ACME BOX", "general_delivery": "42"},
            {"general_delivery_type": "ACME", "general_delivery": "42"},
            # Starts with a designation: the first words are not a name.
            {"general_delivery_type": "DRAWER LOCK BOX", "general_delivery": "42"},
            # A street or route group: the box is not the delivery line.
            {**_STREET, "general_delivery_type": "ACME PO BOX", "general_delivery": "42"},
            {
                "general_delivery_group_type": "RR",
                "general_delivery_group": "2",
                "general_delivery_type": "ACME PO BOX",
                "general_delivery": "42",
            },
            {},
        ],
    )
    def test_left_alone(self, c: dict[str, str]) -> None:
        c |= _LAST_LINE
        before = dict(c)
        _recover_name_from_box_type(c)
        assert c == before

    @pytest.mark.parametrize(
        ("raw", "recipient", "recovered"),
        [
            ("ACME LOCK BOX 42, SEATTLE, WA 98101", "ACME", "LOCK BOX 42"),
            ("ACME LOCKBOX 42, SEATTLE, WA 98101", "ACME", "LOCKBOX 42"),
            ("ACME PO BOX 42, SEATTLE, WA 98101", "ACME", "PO BOX 42"),
            ("ACME P O BOX 42, SEATTLE, WA 98101", "ACME", "P O BOX 42"),
            ("ACME POST OFFICE BOX 42, SEATTLE, WA 98101", "ACME", "POST OFFICE BOX 42"),
            ("ACME PO DRAWER 42, SEATTLE, WA 98101", "ACME", "PO DRAWER 42"),
            ("SMITH CALLER BOX 42, SEATTLE, WA 98101", "SMITH", "CALLER BOX 42"),
            ("ACME, LOCK BOX 42, SEATTLE, WA 98101", "ACME", "LOCK BOX 42"),
            ("ACME LOCK BOX 42", "ACME", "LOCK BOX 42"),
            # The name is already the recipient: nothing to recover.
            ("ACME CORP LOCK BOX 42, SEATTLE, WA 98101", "ACME CORP", None),
        ],
    )
    async def test_end_to_end(self, raw: str, recipient: str, recovered: str | None) -> None:
        """GH-307 acceptance: parse → standardize renders PO BOX on line 1."""
        response = (await parse_address(raw)).response
        assert response.components.values["addressee"] == recipient
        result = standardize(response.components.values, "US")
        assert (result.address_line_1, result.address_line_2) == ("PO BOX 42", "")
        prefix = DELIVERY_LINE_RECOVERED.partition("{")[0]
        delivery_warnings = [w for w in response.warnings if w.startswith(prefix)]
        expected = [DELIVERY_LINE_RECOVERED.format(text=recovered)] if recovered else []
        assert delivery_warnings == expected


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

    async def test_single_newline_reaches_usaddress(self) -> None:
        """GH-289 CR 8: usaddress keeps a trailing newline on a token as a
        line-break signal, so whitespace cleanup collapses only runs."""
        raw = "123 Main St\nApt 4\nSeattle, WA 98101"
        with mock.patch(
            "address_validator.services.parser.usaddress.tag",
            wraps=usaddress.tag,
        ) as tag:
            await parse_address(raw)
        assert tag.call_args.args[0] == raw

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

    @pytest.mark.parametrize(
        ("raw", "group_type", "group"),
        [
            ("PSC 802 BOX 74, APO, AE 09499", "PSC", "802"),
            ("CMR 450, BOX 123, APO AE 09001", "CMR", "450"),
        ],
    )
    async def test_military_box_type_repeat_becomes_route_group(
        self, raw: str, group_type: str, group: str
    ) -> None:
        """GH-292: usaddress tags PSC/CMR and BOX both as USPSBoxType; the
        first box phrase is the military route group, not more box text."""
        response = (await parse_address(raw)).response
        values = response.components.values
        assert DELIVERY_LINE_RECOVERED.format(text=f"{group_type} {group}") in response.warnings
        assert values["general_delivery_group_type"] == group_type
        assert values["general_delivery_group"].strip(",") == group
        assert values["general_delivery_type"] == "BOX"
        assert values["general_delivery"].strip(",") in {"74", "123"}

    async def test_military_box_left_alone_when_route_group_tagged(self) -> None:
        """A route group usaddress already tagged is never overwritten."""
        fake_tokens = [
            ("RR", "USPSBoxGroupType"),
            ("2", "USPSBoxGroupID"),
            ("PSC", "USPSBoxType"),
            ("5", "USPSBoxID"),
            ("BOX", "USPSBoxType"),
            ("6", "USPSBoxID"),
        ]
        exc = usaddress.RepeatedLabelError("fake", fake_tokens, {})
        with mock.patch("address_validator.services.parser.usaddress.tag", side_effect=exc):
            response = (await parse_address("RR 2 PSC 5 BOX 6")).response
        values = response.components.values
        assert values["general_delivery_group_type"] == "RR"
        assert values["general_delivery_group"] == "2"
        assert values["general_delivery_type"] == "PSC BOX"
        assert not any(w.startswith("Delivery address line") for w in response.warnings)

    async def test_repeated_po_box_not_made_a_route_group(self) -> None:
        fake_tokens = [("PO BOX", "USPSBoxType"), ("5", "USPSBoxID")] * 2
        exc = usaddress.RepeatedLabelError("fake", fake_tokens, {})
        with mock.patch("address_validator.services.parser.usaddress.tag", side_effect=exc):
            values = (await parse_address("PO BOX 5 PO BOX 5")).response.components.values
        assert "general_delivery_group_type" not in values

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

    async def test_second_variant_designator_recognized(self) -> None:
        """GH-286: a repeated designator in a UNIT_MAP variant spelling ('STES')
        is a known designator — slotted with no 'Unrecognized' warning."""
        fake_tokens = [
            ("123", "AddressNumber"),
            ("MAIN", "StreetName"),
            ("ST", "StreetNamePostType"),
            ("BLDG", "OccupancyType"),
            ("2", "OccupancyIdentifier"),
            ("STES", "OccupancyType"),
            ("100-102,", "OccupancyIdentifier"),
            ("SEATTLE,", "PlaceName"),
            ("WA", "StateName"),
            ("98101", "ZipCode"),
        ]
        exc = usaddress.RepeatedLabelError("fake", fake_tokens, {})
        with mock.patch("address_validator.services.parser.usaddress.tag", side_effect=exc):
            result = (
                await parse_address("123 MAIN ST BLDG 2 STES 100-102, SEATTLE, WA 98101")
            ).response
        vals = result.components.values
        assert vals.get("dependent_sub_premise_type") == "STES"
        assert vals.get("dependent_sub_premise_number", "").rstrip(",") == "100-102"
        assert not any("Unrecognized unit designator" in w for w in result.warnings)

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
            # GH-286 CR: usaddress folds a '#' alias word into the identifier
            # ('NO 1,'); it must compare equal to the named unit's '1'.
            (
                "19315 BOTHELL EVERETT HWY NO 1, UNIT 1 BOTHELL, WA 98012",
                "UNIT",
                "1",
                "BOTHELL",
            ),
            # GH-286 CR: 'NO' tagged as a unit type lands in the dependent
            # slot with the named unit primary — the mirror-image duplicate.
            (
                "123 MAIN ST NO 1 STE 1 SEATTLE, WA 98101",
                "STE",
                "1",
                "SEATTLE",
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

    @pytest.mark.parametrize(
        "raw",
        [
            "123 MAIN ST SUITES 100, SEATTLE, WA 98101",  # unit tagged as a USPS box
            "123 MAIN ST BLG A SEATTLE WA",  # unit/city/state tagged as recipient
            "123 MAIN ST STE 100 & 101, SEATTLE, WA 98101",  # GH-288: list item as a street
            "123 MAIN ST STE 100 AND 101, SEATTLE, WA 98101",  # GH-297: list item as a box
        ],
    )
    async def test_gh285_recovery_returns_candidate_data(self, raw: str) -> None:
        """GH-285: the tag itself is the defect, so each recovery marks the
        input for CRF labelling."""
        outcome = await parse_address(raw)
        assert outcome.candidate_data is not None
        assert outcome.candidate_data["failure_type"] == "post_parse_recovery"
        assert outcome.candidate_data["raw_address"] == raw

    @pytest.mark.parametrize(
        "raw",
        [
            "GENERAL DELIVERY, SEATTLE, WA 98101",  # tagged as landmark
            "GENERAL DELIVERY SEATTLE WA 98101",  # tagged as recipient
            "PSC 1234 BOX 5678, APO, AE 09001",  # route tagged as a unit
        ],
    )
    async def test_delivery_line_recovery_returns_candidate_data(self, raw: str) -> None:
        """GH-292/293: the delivery line was mis-tagged, so the input is
        marked for CRF labelling."""
        outcome = await parse_address(raw)
        assert outcome.candidate_data is not None
        assert outcome.candidate_data["failure_type"] == "post_parse_recovery"

    async def test_locality_recovery_alone_returns_candidate_data(self) -> None:
        """LOCALITY_RECOVERED is a candidate kind in its own right, not only
        via the unit recovery that usually follows it."""
        tagged = {
            "AddressNumber": "123",
            "StreetName": "MAIN",
            "StreetNamePostType": "ST",
            "Recipient": "SEATTLE WA",
        }
        with mock.patch(
            "address_validator.services.parser.usaddress.tag",
            return_value=(tagged, "Street Address"),
        ):
            outcome = await parse_address("123 MAIN ST SEATTLE WA")
        assert outcome.response.components.values["locality"] == "SEATTLE"
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
