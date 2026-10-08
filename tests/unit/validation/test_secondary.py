"""Unit tests for services/validation/secondary.py — the unit sent to providers (GH #287)."""

import pytest

from address_validator.models import ComponentSet, StandardizeResponseV2
from address_validator.services.standardizer import standardize
from address_validator.services.validation.secondary import (
    full_secondary,
    provider_secondary,
)
from address_validator.usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION


def _std(components: dict[str, str], country: str = "US") -> StandardizeResponseV2:
    base = {
        "premise_number": "123",
        "thoroughfare_name": "MAIN",
        "thoroughfare_trailing_type": "ST",
        "locality": "SEATTLE",
        "administrative_area": "WA",
        "postcode": "98101",
    }
    return standardize({**base, **components}, country=country)


def secondary_narrowed(std: StandardizeResponseV2) -> bool:
    return provider_secondary(std) != full_secondary(std)


def _units(*slots: tuple[str, str]) -> dict[str, str]:
    """Build unit components in source order: first slot is the occupancy."""
    keys = (
        ("sub_premise_type", "sub_premise_number"),
        ("dependent_sub_premise_type", "dependent_sub_premise_number"),
    )
    out: dict[str, str] = {}
    for (type_key, id_key), (designator, identifier) in zip(keys, slots, strict=False):
        out[type_key] = designator
        out[id_key] = identifier
    return out


class TestProviderSecondary:
    @pytest.mark.parametrize(
        ("components", "line2", "sent"),
        [
            # GH #287 leading chained SMP (source order per #170)
            (
                {
                    "dependent_sub_premise_type": "SMP",
                    "dependent_sub_premise_number": "2",
                    "sub_premise_type": "STE",
                    "sub_premise_number": "J",
                },
                "SMP 2 STE J",
                "STE J",
            ),
            # trailing SMP
            (_units(("STE", "110"), ("SMP", "2")), "STE 110 SMP 2", "STE 110"),
            # container-first ordering: the specific unit is the second one
            (_units(("STE", "100"), ("BLDG", "1")), "BLDG 1 STE 100", "STE 100"),
            (_units(("#", "4"), ("BLDG", "A")), "BLDG A # 4", "# 4"),
            (_units(("SMP", "2"), ("BLDG", "1")), "BLDG 1 SMP 2", "BLDG 1"),
            # same-level pairs: the first unit on line 2 wins
            (_units(("UNIT", "3"), ("STE", "4")), "UNIT 3 STE 4", "UNIT 3"),
            (_units(("STE", "100"), ("STE", "3")), "STE 100 STE 3", "STE 100"),
            # "# 5 # 6" parses into one slot; the embedded "#" still splits it
            (_units(("#", "5 # 6")), "# 5 # 6", "# 5"),
            (_units(("#", "5, # 6")), "# 5, # 6", "# 5"),
            # a lone "," token (component input) leaves no empty identifier token
            (_units(("#", "5 , # 6")), "# 5 , # 6", "# 5"),
            # a dependent identifier with no designator is a "#" unit (Pub 28)
            (_units(("SMP", "2"), ("", "7")), "SMP 2 7", "# 7"),
            (_units(("STE", "4"), ("", "5")), "STE 4 5", "STE 4"),
            # ...unless the identifier carries a folded designator (CR 8)
            (_units(("SMP", "2"), ("", "STE 5")), "SMP 2 STE 5", "STE 5"),
            (_units(("SMP", "2"), ("", "SUITE 5")), "SMP 2 SUITE 5", "STE 5"),
            (_units(("SMP", "2"), ("", "#5")), "SMP 2 #5", "# 5"),
        ],
    )
    def test_multi_unit_line2_narrows_to_one_pub28_unit(
        self, components: dict[str, str], line2: str, sent: str
    ) -> None:
        std = _std(components)
        assert std.address_line_2 == line2
        assert provider_secondary(std) == sent
        assert secondary_narrowed(std)

    def test_lone_unrecognised_designator_is_not_sent(self) -> None:
        std = _std(_units(("SMP", "2")))
        assert std.address_line_2 == "SMP 2"
        assert provider_secondary(std) is None
        assert secondary_narrowed(std)

    @pytest.mark.parametrize(
        "components",
        [
            _units(("STE", "J")),
            _units(("APT", "PH 2")),  # designator-like id token other than "#"
            _units(("FL", "2 REAR")),
            _units(("BLDG", "1")),  # a bare container is still a unit
            _units(("REAR", "")),  # designator with no identifier
        ],
    )
    def test_single_pub28_unit_sent_unchanged(self, components: dict[str, str]) -> None:
        std = _std(components)
        assert provider_secondary(std) == std.address_line_2
        assert not secondary_narrowed(std)

    def test_no_secondary(self) -> None:
        std = _std({})
        assert provider_secondary(std) is None
        assert not secondary_narrowed(std)

    def test_line2_without_unit_components_sent_verbatim(self) -> None:
        """No unit slots to reason about (hand-built std) → current behaviour."""
        std = StandardizeResponseV2(
            address_line_1="123 MAIN ST",
            address_line_2="  LOT B ",
            city="SPRINGFIELD",
            region="IL",
            postal_code="62701",
            country="US",
            standardized="123 MAIN ST  LOT B  SPRINGFIELD, IL 62701",
            components=ComponentSet(
                spec=USPS_PUB28_SPEC, spec_version=USPS_PUB28_SPEC_VERSION, values={}
            ),
        )
        assert provider_secondary(std) == "LOT B"
        assert not secondary_narrowed(std)

    def test_non_us_line2_sent_verbatim(self) -> None:
        std = StandardizeResponseV2(
            address_line_1="10 MAIN ST",
            address_line_2="SMP 2 STE J",
            city="TORONTO",
            region="ON",
            postal_code="M5V 1A1",
            country="CA",
            standardized="",
            components=ComponentSet(
                spec="raw",
                spec_version="1",
                values={"sub_premise_type": "SMP", "sub_premise_number": "2"},
            ),
        )
        assert provider_secondary(std) == "SMP 2 STE J"
        assert not secondary_narrowed(std)


class TestRangesAndLists:
    """GH #289: a range or list of identifiers sends providers its first unit."""

    @pytest.mark.parametrize(
        ("components", "line2", "sent"),
        [
            # numeric range: same width, ascending
            (_units(("STE", "100-102")), "STE 100-102", "STE 100"),
            (_units(("#", "2-3")), "# 2-3", "# 2"),
            (_units(("BLDG", "1-2")), "BLDG 1-2", "BLDG 1"),
            # comma list (with or without spaces), lone "," token
            (_units(("STE", "100, 101")), "STE 100, 101", "STE 100"),
            (_units(("STE", "1, 2, 3")), "STE 1, 2, 3", "STE 1"),
            (_units(("STE", "100 , 101")), "STE 100 , 101", "STE 100"),
            # a range heading a list
            (_units(("STE", "100-102, 105")), "STE 100-102, 105", "STE 100"),
            # space-separated numbers
            (_units(("UNIT", "5 6")), "UNIT 5 6", "UNIT 5"),
            # composes with #287: SMP dropped, then the range narrowed
            (_units(("STE", "100-102"), ("SMP", "2")), "STE 100-102 SMP 2", "STE 100"),
            (_units(("#", "5-6 # 7")), "# 5-6 # 7", "# 5"),
            # GH #297: '&' / 'AND' join a list like ','
            (_units(("UNIT", "1 & 2")), "UNIT 1 & 2", "UNIT 1"),
            (_units(("#", "5 & 6")), "# 5 & 6", "# 5"),
            (_units(("STE", "A & B")), "STE A & B", "STE A"),
            (_units(("STE", "100 AND 101")), "STE 100 AND 101", "STE 100"),
            (_units(("STE", "100 & 101 & 102")), "STE 100 & 101 & 102", "STE 100"),
            (_units(("STE", "100-102 & 105")), "STE 100-102 & 105", "STE 100"),
            # a bare '&' left on the first of two units is not identifier text
            (_units(("STE", "100 &"), ("STE", "101")), "STE 100 & STE 101", "STE 100"),
        ],
    )
    def test_range_or_list_narrows_to_first_unit(
        self, components: dict[str, str], line2: str, sent: str
    ) -> None:
        std = _std(components)
        assert std.address_line_2 == line2
        assert provider_secondary(std) == sent
        assert secondary_narrowed(std)

    @pytest.mark.parametrize(
        "identifier",
        [
            "A-1",  # hyphenated alphanumeric identifier
            "1-A",
            "9-1",  # descending: not a range
            "2-2",
            "2-100",  # different widths: floor-suite style identifier
            "4 B",  # space-separated, not all numeric
            "100-102-104",  # not a two-sided range
            "C&F 1",  # '&' inside a token is identifier text (GH #297)
        ],
    )
    def test_identifier_that_is_not_a_range_or_list_sent_unchanged(self, identifier: str) -> None:
        std = _std(_units(("STE", identifier)))
        assert provider_secondary(std) == std.address_line_2
        assert not secondary_narrowed(std)
