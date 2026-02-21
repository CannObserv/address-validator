"""Address parsing service using the usaddress library."""

import re

import usaddress

from models import ParseResponse
from usps_data.directionals import DIRECTIONAL_MAP
from usps_data.states import STATE_MAP
from usps_data.suffixes import SUFFIX_MAP
from usps_data.units import UNIT_MAP

# Combined lookup for tokens that are valid address vocabulary.
_ADDRESS_VOCABULARY: set[str] = (
    set(UNIT_MAP) | set(SUFFIX_MAP) | set(DIRECTIONAL_MAP) | set(STATE_MAP)
)

# Designators that never require an identifier (USPS Pub 28 Appendix H).
# Only these are recognised as bare leading words in phase 2 of city
# recovery.  Designators that require an identifier (KEY, LOT, UNIT,
# STE …) are excluded to avoid false positives on city names like
# KEY WEST or FRONT ROYAL.
_NO_ID_DESIGNATORS: set[str] = {
    "BASEMENT", "BSMT", "FRONT", "FRNT", "LOBBY", "LBBY",
    "LOWER", "LOWR", "PENTHOUSE", "PH", "REAR", "SIDE",
    "UPPER", "UPPR",
}


# Map usaddress tag names to friendlier keys.
TAG_NAMES: dict[str, str] = {
    "AddressNumber": "address_number",
    "AddressNumberPrefix": "address_number_prefix",
    "AddressNumberSuffix": "address_number_suffix",
    "StreetNamePreDirectional": "street_name_pre_directional",
    "StreetNamePreModifier": "street_name_pre_modifier",
    "StreetNamePreType": "street_name_pre_type",
    "StreetName": "street_name",
    "StreetNamePostDirectional": "street_name_post_directional",
    "StreetNamePostModifier": "street_name_post_modifier",
    "StreetNamePostType": "street_name_post_type",
    "SubaddressType": "subaddress_type",
    "SubaddressIdentifier": "subaddress_identifier",
    "OccupancyType": "occupancy_type",
    "OccupancyIdentifier": "occupancy_identifier",
    "PlaceName": "city",
    "StateName": "state",
    "ZipCode": "zip_code",
    "USPSBoxType": "usps_box_type",
    "USPSBoxID": "usps_box_id",
    "USPSBoxGroupType": "usps_box_group_type",
    "USPSBoxGroupID": "usps_box_group_id",
    "BuildingName": "building_name",
    "Recipient": "recipient",
    "NotAddress": "not_address",
    "IntersectionSeparator": "intersection_separator",
    "LandmarkName": "landmark_name",
    "CornerOf": "corner_of",
    # Second street (intersections)
    "SecondStreetName": "second_street_name",
    "SecondStreetNamePreDirectional": "second_street_name_pre_directional",
    "SecondStreetNamePreModifier": "second_street_name_pre_modifier",
    "SecondStreetNamePreType": "second_street_name_pre_type",
    "SecondStreetNamePostDirectional": "second_street_name_post_directional",
    "SecondStreetNamePostModifier": "second_street_name_post_modifier",
    "SecondStreetNamePostType": "second_street_name_post_type",
}


# Designator slots in priority order: occupancy first, then subaddress.
_UNIT_SLOT_PAIRS = (
    ("occupancy_type", "occupancy_identifier"),
    ("subaddress_type", "subaddress_identifier"),
)


def _next_free_unit_slot(
    components: dict[str, str],
) -> tuple[str, str] | None:
    """Return the first empty (type_key, id_key) pair, or *None*."""
    for type_key, id_key in _UNIT_SLOT_PAIRS:
        if not components.get(type_key) and not components.get(id_key):
            return type_key, id_key
    return None


def _try_extract_designator(segment: str) -> tuple[str, str] | None:
    """If *segment* starts with a UNIT_MAP key return (type, identifier).

    Returns ``None`` when the leading word is not a known designator.
    """
    segment = segment.strip()
    if not segment:
        return None
    parts = segment.split(None, 1)
    word = parts[0].upper().replace(".", "")
    if word not in UNIT_MAP:
        return None
    identifier = parts[1] if len(parts) > 1 else ""
    return parts[0], identifier


def _recover_unit_from_city(components: dict[str, str]) -> None:
    """Move unit designators mis-tagged as part of city back to occupancy.

    usaddress sometimes tags secondary designators that follow the street
    line as ``PlaceName``, concatenating them with the real city.  An
    address like ``"BLDG 1, LOWR LEVEL, UNIT  SEATTLE"`` can produce
    ``city = "LOWR LEVEL, UNIT SEATTLE"`` (after usaddress already
    extracted BLDG).

    This function peels off comma-separated leading segments whose first
    word is a known unit designator, storing each in the next free
    occupancy/subaddress slot.  After commas are exhausted it also
    checks for a bare designator word (no comma) at the start of city.
    Designators that cannot fit in any slot are still removed from city
    since they are not city data.
    """
    # --- Phase 1: comma-separated leading designators ---
    while True:
        city = components.get("city", "")
        if not city or "," not in city:
            break

        before, _, after = city.partition(",")
        before = before.strip()
        after = after.strip()
        if not before or not after:
            break

        result = _try_extract_designator(before)
        if result is not None:
            desig_type, desig_id = result
            slot = _next_free_unit_slot(components)
            if slot:
                components[slot[0]] = desig_type
                if desig_id:
                    components[slot[1]] = desig_id
            # Either way, strip this segment from city.
            components["city"] = after
            continue

        # A single word before the comma that isn't in any address
        # vocabulary is likely wayfinding text (e.g. "YARD", "GATE").
        # Drop it.  Multi-word segments are left alone — they could
        # be a real multi-word city name prefix.
        word = before.upper().replace(".", "")
        if " " not in before and word not in _ADDRESS_VOCABULARY:
            components["city"] = after
            continue

        break

    # --- Phase 2: bare leading designator (no comma) ---
    # Only no-identifier designators (BSMT, FRNT, LOWR …) are stored
    # into a slot here.  Designators like KEY, LOT, UNIT always expect
    # an identifier, so a bare "KEY WEST" is almost certainly a city.
    # However, when all unit slots are already full, any UNIT_MAP word
    # at the start of city is stripped — it's leftover designator data,
    # not a city name, and there's nowhere to store it.
    city = components.get("city", "")
    if not city or " " not in city:
        return

    first, _, rest = city.partition(" ")
    word = first.upper().replace(".", "")
    rest = rest.strip()
    if not rest:
        return

    slot = _next_free_unit_slot(components)

    if word in _NO_ID_DESIGNATORS:
        if slot:
            components[slot[0]] = first
        components["city"] = rest
    elif word in UNIT_MAP and slot is None:
        # All slots full — just strip the orphaned designator word.
        components["city"] = rest


def _recover_identifier_fragment_from_city(components: dict[str, str]) -> None:
    """Move a stray single-letter unit qualifier from the start of city.

    usaddress sometimes splits a compound identifier like ``120 K`` and
    absorbs the trailing letter into ``PlaceName``, producing a city of
    ``"K WALLA WALLA"`` instead of ``"WALLA WALLA"``.  When the city
    begins with a single letter followed by a space and an occupancy or
    subaddress identifier already exists, move that letter back onto the
    identifier.
    """
    city = components.get("city", "")
    if not city or len(city) < 3:
        return

    # Must start with exactly one letter then a space.  This is
    # intentionally aggressive — a single leading letter is almost
    # always a stray identifier fragment, not the start of a real city
    # name.  The only guard is that an identifier field must already
    # exist (so there is something to append to).  Edge cases like
    # "O FALLON" (O'Fallon with dropped apostrophe) are theoretically
    # possible but unlikely in practice with usaddress output.
    if not city[0].isalpha() or city[1] != " ":
        return

    fragment = city[0]
    rest = city[2:].strip()

    if not rest:
        return

    # Append to whichever identifier field is present.
    for key in ("occupancy_identifier", "subaddress_identifier"):
        if components.get(key):
            components[key] += f" {fragment}"
            components["city"] = rest
            return


def parse_address(raw: str) -> ParseResponse:
    """Parse *raw* address string into labelled components.

    Returns a :class:`ParseResponse` with:
      - ``input``: the original string
      - ``components``: dict of component_name -> value
      - ``type``: ``"Street Address"``, ``"Intersection"``, or ``"Ambiguous"``
    """
    # USPS Pub 28 §354: parentheses are not valid in standardised
    # addresses.  Parenthesized text is typically wayfinding notes
    # (e.g. "(EAST)", "(UPPER LEVEL)") that confuse usaddress.  Strip
    # it before parsing and collapse any resulting extra whitespace.
    cleaned = re.sub(r"\([^)]*\)", "", raw)
    # Strip any remaining unmatched parentheses (e.g. "123 Main) St").
    cleaned = cleaned.replace("(", "").replace(")", "")
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    try:
        tagged, addr_type = usaddress.tag(cleaned)
    except usaddress.RepeatedLabelError as exc:
        # Fallback: return the raw token pairs when tagging is ambiguous.
        components: dict[str, str] = {}
        prev_key: str | None = None
        separator_before: bool = False
        for token, label in exc.parsed_string:
            key = TAG_NAMES.get(label, label)

            # Track whether an IntersectionSeparator appeared right
            # before a repeated AddressNumber — that signals a dual/
            # range address ("1804 & 1810"), not a true intersection.
            if key == "intersection_separator":
                if prev_key == "address_number":
                    separator_before = True
                    prev_key = key
                    continue  # don't emit the separator yet
                # True intersection separator — emit normally.

            if key in components:
                if key == "address_number" and separator_before:
                    # Dual address: join with hyphen (USPS Pub 28 §232).
                    components[key] += f"-{token}"
                else:
                    components[key] += f" {token}"
            else:
                components[key] = token

            separator_before = False
            prev_key = key
        return ParseResponse(
            input=raw,
            components=components,
            type="Ambiguous",
            warning="Repeated labels detected; parse may be inaccurate.",
        )

    components = {
        TAG_NAMES.get(label, label): value
        for label, value in tagged.items()
    }

    _recover_unit_from_city(components)
    _recover_identifier_fragment_from_city(components)

    return ParseResponse(
        input=raw,
        components=components,
        type=addr_type,
    )
