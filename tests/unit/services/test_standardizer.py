"""Unit tests for services/standardizer.py."""

import logging

import pytest

from address_validator.core.warnings import GENERAL_DELIVERY_DISCARDED
from address_validator.services.parser import parse_address
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

    @pytest.mark.parametrize(
        ("designator", "identifier", "line_2"),
        [
            ("UNITS", "3-4", "UNIT 3-4"),
            ("SUITES", "100", "STE 100"),
            ("STES", "100-102", "STE 100-102"),
            ("SUTE", "5", "STE 5"),
            ("FLOORS", "2-3", "FL 2-3"),
            ("FLR", "2", "FL 2"),
            ("BLG", "A", "BLDG A"),
        ],
    )
    def test_plural_and_misspelt_designators_normalised(
        self, designator: str, identifier: str, line_2: str
    ) -> None:
        """GH-286: a plural or misspelt designator gets its Pub 28 form; a
        range identifier is kept whole, not cut to its first unit."""
        comps = {
            "premise_number": "123",
            "thoroughfare_name": "MAIN",
            "thoroughfare_trailing_type": "ST",
            "sub_premise_type": designator,
            "sub_premise_number": identifier,
        }
        result = standardize(comps)
        assert result.address_line_2 == line_2
        assert result.warnings == []

    @pytest.mark.parametrize(
        ("tail", "line_2"),
        [
            ("UNITS 3-4", "UNIT 3-4"),
            ("STES 100-102", "STE 100-102"),
            ("SUTE 5", "STE 5"),
            ("FLR 2", "FL 2"),
        ],
    )
    async def test_parsed_variant_designator_normalised(self, tail: str, line_2: str) -> None:
        """GH-286: end to end, the variants usaddress tags as a unit reach
        line 2 in Pub 28 form (they used to pass through unchanged)."""
        parsed = (await parse_address(f"123 MAIN ST {tail}, SEATTLE, WA 98101")).response
        result = standardize(parsed.components.values, upstream_warnings=parsed.warnings)
        assert result.address_line_1 == "123 MAIN ST"
        assert result.address_line_2 == line_2

    @pytest.mark.parametrize(
        ("tail", "line_2"),
        [
            ("STE 100 - 102", "STE 100-102"),
            ("UNIT 5 - 6", "UNIT 5-6"),
            ("APT 4 - B", "APT 4-B"),
            ("STE 100 \u2013 102", "STE 100-102"),  # en dash
            ("STE 100\u2013102", "STE 100-102"),  # CR 3: unspaced en dash
            ("# 2 - 3", "# 2-3"),
            ("STE 1 - 2 - 3", "STE 1-2-3"),  # CR 4: a chain joins in one pass
        ],
    )
    async def test_spaced_hyphen_kept_in_identifier(self, tail: str, line_2: str) -> None:
        """GH-289: usaddress drops a lone '-' token, so '100 - 102' read as two
        numbers ('STE 100 102').  It is rejoined before parsing."""
        parsed = (await parse_address(f"123 MAIN ST {tail}, SEATTLE, WA 98101")).response
        result = standardize(parsed.components.values, upstream_warnings=parsed.warnings)
        assert result.address_line_1 == "123 MAIN ST"
        assert result.address_line_2 == line_2

    async def test_spaced_hyphen_address_number_range(self) -> None:
        """GH-289: a spaced address-number range no longer leaks into the
        street name ('100 102 MAIN ST')."""
        parsed = (await parse_address("100 - 102 MAIN ST, SEATTLE, WA 98101")).response
        result = standardize(parsed.components.values, upstream_warnings=parsed.warnings)
        assert result.address_line_1 == "100-102 MAIN ST"

    async def test_spaced_hyphen_beside_directional_left_alone(self) -> None:
        """GH-289 CR 1: a grid address ('1234 S - 500 E') keeps its directional
        apart from the number instead of fusing to 'S-500'."""
        parsed = (await parse_address("1234 S - 500 E, SALT LAKE CITY, UT 84106")).response
        result = standardize(parsed.components.values, upstream_warnings=parsed.warnings)
        assert result.address_line_1 == "1234 S 500 E"

    @pytest.mark.parametrize(
        "tail",
        [
            "STE 100 2ND FLOOR",
            "STE 100, 2ND FLOOR",
            "2ND FLOOR STE 100",
            "STE 100 2ND FLR",
            "STE 100 - 2ND FLOOR",  # CR 2: the ordinal is not dash-joined
        ],
    )
    async def test_ordinal_floor_beside_another_unit(self, tail: str) -> None:
        """GH-289: '<ordinal> FLOOR' next to another unit hits the ambiguous
        path, which fused the ordinal into the suite ('FL STE 100 2ND').  The
        floor gets its own slot and renders first as a container."""
        parsed = (await parse_address(f"123 MAIN ST {tail}, SEATTLE, WA 98101")).response
        result = standardize(parsed.components.values, upstream_warnings=parsed.warnings)
        assert result.address_line_1 == "123 MAIN ST"
        assert result.address_line_2 == "FL 2ND STE 100"
        assert result.city == "SEATTLE"

    @pytest.mark.parametrize(
        ("tail", "line_2", "item"),
        [
            ("SUITE 100 & 200", "STE 100 & 200", "200"),  # '&' tagged as an intersection
            ("STE 100 & 101", "STE 100 & 101", "101"),
            ("STE 100 &101", "STE 100 & 101", "101"),
            ("BLDG 1 STE 100 & 101", "BLDG 1 STE 100 & 101", "101"),
            ("STE 100 AND 101", "STE 100 & 101", "101"),  # 'AND' tagged as a USPS box
            ("UNIT 1 AND 2", "UNIT 1 & 2", "2"),  # 'AND' tagged as a unit type
            ("# 5 AND 6", "# 5 & 6", "6"),
        ],
    )
    async def test_unit_list_joined_by_ampersand_or_and_kept_on_line2(
        self, tail: str, line_2: str, item: str
    ) -> None:
        """GH-288/297: the second identifier of '<unit> & <id>' / '<unit> AND
        <id>' was moved to line 1 as an intersection street, or dropped as a PO
        box.  It stays in the unit, and the recovery is reported."""
        parsed = (await parse_address(f"123 MAIN ST {tail}, SEATTLE, WA 98101")).response
        result = standardize(parsed.components.values, upstream_warnings=parsed.warnings)
        assert result.address_line_1 == "123 MAIN ST"
        assert result.address_line_2 == line_2
        assert f"Unit list item recovered from mis-tagged field: '{item}'" in result.warnings
        assert not any("omitted" in w or "Unrecognized" in w for w in result.warnings)

    async def test_unit_list_tagged_as_second_unit_without_zip(self) -> None:
        """GH-297: with no ZIP, usaddress tags 'AND' as a second unit's type on
        the clean path; the item still joins the unit and the city survives."""
        parsed = (await parse_address("123 MAIN ST STE 100 AND 101 SEATTLE WA")).response
        result = standardize(parsed.components.values, upstream_warnings=parsed.warnings)
        assert result.address_line_2 == "STE 100 & 101"
        assert result.city == "SEATTLE"
        assert "Unit list item recovered from mis-tagged field: '101'" in result.warnings

    @pytest.mark.parametrize(
        ("tail", "line_2"),
        [
            ("UNIT 1 & 2", "UNIT 1 & 2"),  # usaddress keeps '&' in the identifier
            ("STE 100 & STE 101", "STE 100 & STE 101"),
        ],
    )
    async def test_unit_list_already_on_line2_unchanged(self, tail: str, line_2: str) -> None:
        parsed = (await parse_address(f"123 MAIN ST {tail}, SEATTLE, WA 98101")).response
        result = standardize(parsed.components.values, upstream_warnings=parsed.warnings)
        assert result.address_line_2 == line_2
        assert not any("Unit list item" in w for w in result.warnings)

    @pytest.mark.parametrize(
        ("raw", "line_1", "line_2"),
        [
            ("123 MAIN ST & 101, SEATTLE, WA 98101", "123 MAIN ST & 101", ""),  # no unit
            ("123 MAIN ST APT 5 & 6TH AVE, SEATTLE, WA 98101", "123 MAIN ST & 6TH AVE", "APT 5"),
            ("MAIN ST & 5TH AVE, SEATTLE, WA 98101", "MAIN ST & 5TH AVE", ""),
        ],
    )
    async def test_intersection_without_unit_list_left_alone(
        self, raw: str, line_1: str, line_2: str
    ) -> None:
        """GH-288: only a bare identifier right after a unit is a list item."""
        parsed = (await parse_address(raw)).response
        result = standardize(parsed.components.values, upstream_warnings=parsed.warnings)
        assert result.address_line_1 == line_1
        assert result.address_line_2 == line_2

    @pytest.mark.parametrize(
        ("tail", "line_2"),
        [
            ("STE 1 #1", "STE 1"),
            ("STE 1 # 1", "STE 1"),
            ("APT 1 NO 1", "APT 1"),
            ("UNIT 1, NO 1", "UNIT 1"),
            ("SUITE 1 NUMBER 1", "STE 1"),
        ],
    )
    async def test_trailing_hash_phrase_restating_unit_collapsed(
        self, tail: str, line_2: str
    ) -> None:
        """GH-290: a '#' phrase after a named unit with the same identifier is
        the unit stated twice, as '#1 STE 1' already collapses."""
        parsed = (await parse_address(f"123 MAIN ST {tail}, SEATTLE, WA 98101")).response
        result = standardize(parsed.components.values, upstream_warnings=parsed.warnings)
        assert result.address_line_2 == line_2
        assert f"Duplicate secondary unit collapsed into '{line_2}'" in result.warnings

    @pytest.mark.parametrize(
        ("tail", "line_2"),
        [
            ("STE NO 5", "STE 5"),
            ("STE NUMBER 5", "STE 5"),
            ("STE #5", "STE 5"),
            ("APT # 4B", "APT 4B"),
        ],
    )
    async def test_hash_word_after_designator_dropped(self, tail: str, line_2: str) -> None:
        """GH-290: '#' (or 'NO') after a designator stands in for one; the
        Pub 28 form is the designator and identifier alone."""
        parsed = (await parse_address(f"123 MAIN ST {tail}, SEATTLE, WA 98101")).response
        result = standardize(parsed.components.values, upstream_warnings=parsed.warnings)
        assert result.address_line_2 == line_2
        assert result.warnings == []

    @pytest.mark.parametrize(
        ("tail", "line_2"),
        [("STE 1 #2", "STE 1 # 2"), ("APT 1 NO 2", "APT 1 # 2")],
    )
    async def test_trailing_hash_phrase_distinct_unit_kept(self, tail: str, line_2: str) -> None:
        """GH-290: a different identifier is a second unit, in its own slot."""
        parsed = (await parse_address(f"123 MAIN ST {tail}, SEATTLE, WA 98101")).response
        result = standardize(parsed.components.values, upstream_warnings=parsed.warnings)
        assert result.address_line_2 == line_2
        assert result.components.values["dependent_sub_premise_number"] == "2"

    def test_same_level_units_render_in_source_order(self) -> None:
        """GH-170 CR: '#108 STE B' — neither unit is a container, so line 2
        preserves source order (insertion order of the component keys)
        instead of forcing the dependent slot first.
        """
        comps = {
            "premise_number": "5041",
            "thoroughfare_name": "RAINIER",
            "thoroughfare_trailing_type": "AVE",
            "sub_premise_number": "# 108",
            "dependent_sub_premise_type": "STE",
            "dependent_sub_premise_number": "B",
        }
        result = standardize(comps)
        assert result.address_line_2 == "# 108 STE B"

    def test_same_level_units_dependent_first_in_source(self) -> None:
        """Source order also wins when the dependent slot's keys were
        inserted first — the pair renders dependent-then-primary."""
        comps = {
            "premise_number": "1210",
            "thoroughfare_name": "WENATCHEE",
            "thoroughfare_trailing_type": "AVE",
            "dependent_sub_premise_type": "SMP",
            "dependent_sub_premise_number": "2",
            "sub_premise_type": "STE",
            "sub_premise_number": "J",
        }
        result = standardize(comps)
        assert result.address_line_2 == "SMP 2 STE J"

    def test_container_unit_renders_first_regardless_of_source_order(self) -> None:
        """USPS Pub 28: a container designator (BLDG/FL) in the dependent
        slot renders before the specific unit even when it appeared later
        in the source ('STE 120 BLDG C' → 'BLDG C STE 120')."""
        comps = {
            "premise_number": "1",
            "thoroughfare_name": "CAMPUS",
            "thoroughfare_trailing_type": "DR",
            "sub_premise_type": "STE",
            "sub_premise_number": "120",
            "dependent_sub_premise_type": "BLDG",
            "dependent_sub_premise_number": "C",
        }
        result = standardize(comps)
        assert result.address_line_2 == "BLDG C STE 120"

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

    @pytest.mark.parametrize(
        ("raw", "line_2", "city", "state"),
        [
            ("123 MAIN ST SUITES 100, SEATTLE, WA 98101", "STE 100", "SEATTLE", "WA"),
            ("123 MAIN ST FLOORS 2-3, SEATTLE, WA 98101", "FL 2-3", "SEATTLE", "WA"),
            ("123 MAIN ST BLG A, SEATTLE, WA 98101", "BLDG A", "SEATTLE", "WA"),
            ("123 MAIN ST SUITES 100 SEATTLE WA 98101", "STE 100", "SEATTLE", "WA"),
            ("123 MAIN ST BLG A SEATTLE WA 98101", "BLDG A", "SEATTLE", "WA"),
            ("123 MAIN ST BLG A SEATTLE WA", "BLDG A", "SEATTLE", "WA"),
            ("123 MAIN ST BLG A SEATTLE", "BLDG A", "SEATTLE", ""),
        ],
    )
    async def test_unit_tagged_outside_unit_slots_reaches_line_2(
        self, raw: str, line_2: str, city: str, state: str
    ) -> None:
        """GH-285: a unit usaddress tags as a USPS box, as part of the city, or
        inside a trailing recipient is recovered onto line 2 — never dropped."""
        parsed = (await parse_address(raw)).response
        result = standardize(parsed.components.values, upstream_warnings=parsed.warnings)
        assert result.address_line_1 == "123 MAIN ST"
        assert result.address_line_2 == line_2
        assert result.city == city
        assert result.region == state
        assert any("Unit designator recovered" in w for w in result.warnings)
        # Moved, not copied: the box fields are gone, so no "omitted" warning.
        omitted = GENERAL_DELIVERY_DISCARDED.split("{")[0]
        assert not any(w.startswith(omitted) for w in result.warnings)

    @pytest.mark.parametrize(
        ("box_type", "box_id", "dropped"),
        [("LOCKER", "7", "LOCKER 7"), ("PO BOX", "5", "PO BOX 5")],
    )
    def test_general_delivery_beside_street_warns(
        self, box_type: str, box_id: str, dropped: str
    ) -> None:
        """GH-285: a box the standardizer cannot place next to a street is
        still left off the lines, but the client is told."""
        comps = {
            "premise_number": "123",
            "thoroughfare_name": "MAIN",
            "thoroughfare_trailing_type": "ST",
            "general_delivery_type": box_type,
            "general_delivery": box_id,
            "locality": "SEATTLE",
        }
        result = standardize(comps)
        assert result.address_line_1 == "123 MAIN ST"
        assert result.address_line_2 == ""
        assert result.warnings == [GENERAL_DELIVERY_DISCARDED.format(text=dropped)]

    @pytest.mark.parametrize(
        ("comps", "line_1"),
        [
            (
                {
                    "general_delivery_group_type": "RR",
                    "general_delivery_group": "2",
                    "general_delivery_type": "BOX",
                    "general_delivery": "152",
                },
                "RR 2 BOX 152",
            ),
            # usaddress tags 'RR 2,' with no box as group type + box ID.
            ({"general_delivery_group_type": "RR", "general_delivery": "2,"}, "RR 2"),
            ({"general_delivery_type": "GENERAL DELIVERY"}, "GENERAL DELIVERY"),
        ],
    )
    def test_route_group_rendered_on_line_1(self, comps: dict[str, str], line_1: str) -> None:
        """GH-292: the rural route / highway contract group is part of the
        delivery line ('RR 2 BOX 152'), not dropped."""
        result = standardize(comps)
        assert result.address_line_1 == line_1
        assert result.warnings == []

    @pytest.mark.parametrize(
        ("raw", "line_1", "city", "state"),
        [
            ("RR 2 BOX 152, GLENNALLEN, AK 99588", "RR 2 BOX 152", "GLENNALLEN", "AK"),
            ("HC 1 BOX 5, SEATTLE, WA 98101", "HC 1 BOX 5", "SEATTLE", "WA"),
            ("HC 68 BOX 23A, MAGDALENA, NM 87825", "HC 68 BOX 23A", "MAGDALENA", "NM"),
            ("PSC 1234 BOX 5678, APO, AE 09001", "PSC 1234 BOX 5678", "APO", "AE"),
            ("PSC 802 BOX 74 APO AE 09499", "PSC 802 BOX 74", "APO", "AE"),
            ("CMR 450 BOX 123, APO, AE 09001", "CMR 450 BOX 123", "APO", "AE"),
            ("UNIT 2050 BOX 4190, APO, AP 96278", "UNIT 2050 BOX 4190", "APO", "AP"),
            ("GENERAL DELIVERY, SEATTLE, WA 98101", "GENERAL DELIVERY", "SEATTLE", "WA"),
            ("GENERAL DELIVERY SEATTLE WA 98101", "GENERAL DELIVERY", "SEATTLE", "WA"),
            ("general delivery seattle wa", "GENERAL DELIVERY", "SEATTLE", "WA"),
            (
                "JOHN SMITH, GENERAL DELIVERY, SEATTLE, WA 98101",
                "GENERAL DELIVERY",
                "SEATTLE",
                "WA",
            ),
            ("GENERAL DELIVERY JOHN SMITH, SEATTLE, WA 98101", "GENERAL DELIVERY", "SEATTLE", "WA"),
        ],
    )
    async def test_delivery_line_without_street_rendered_whole(
        self, raw: str, line_1: str, city: str, state: str
    ) -> None:
        """GH-292/293: rural route, highway contract, military and general
        delivery lines render whole on line 1 (Pub 28), with nothing on line 2."""
        parsed = (await parse_address(raw)).response
        result = standardize(parsed.components.values, upstream_warnings=parsed.warnings)
        assert result.address_line_1 == line_1
        assert result.address_line_2 == ""
        assert result.city == city
        assert result.region == state

    def test_general_delivery_without_street_does_not_warn(self) -> None:
        comps = {"general_delivery_type": "PO BOX", "general_delivery": "42"}
        assert standardize(comps).warnings == []

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

    def test_split_dual_designators_with_unknown_type(self) -> None:
        """GH-129: the post-parse component dict for
        '… STE J, SMP - 2 …' standardizes to a clean two-designator line2.
        The unknown 'SMP' designator is preserved; the comma the parser left
        on 'J,' is stripped during line assembly.

        GH-170 CR: neither designator is a container, so line 2 follows
        source order ('STE J' first) — the pre-GH-170 'SMP 2 STE J' was an
        artifact of the fixed dependent-first assembly.
        """
        comps = {
            "premise_number": "1210",
            "thoroughfare_pre_direction": "N",
            "thoroughfare_name": "WENATCHEE",
            "thoroughfare_trailing_type": "AVE",
            "sub_premise_type": "STE",
            "sub_premise_number": "J,",
            "dependent_sub_premise_type": "SMP",
            "dependent_sub_premise_number": "2",
            "locality": "WENATCHEE",
            "administrative_area": "WA",
            "postcode": "98801",
        }
        result = standardize(comps)
        assert result.address_line_2 == "STE J SMP 2"
        assert "STE SMP" not in result.standardized

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
        assert any("Unrecognized" in w for w in result.warnings)
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
