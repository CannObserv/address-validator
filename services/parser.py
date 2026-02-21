"""Address parsing service using the usaddress library."""

import re

import usaddress

from models import ParseResponse
from usps_data.units import UNIT_MAP


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


def _recover_unit_from_city(components: dict[str, str]) -> None:
    """Move a unit designator mis-tagged as part of city back to occupancy.

    usaddress sometimes tags a secondary designator (e.g. BASEMENT, REAR,
    STE 200) that follows the street line as ``PlaceName``, concatenating
    it with the real city: ``"BASEMENT, FREELAND"``.

    If the city value begins with a token that is a known UNIT_MAP key
    followed by a comma (with optional identifier in between), split it
    out into ``occupancy_type`` / ``occupancy_identifier``.
    """
    city = components.get("city", "")
    if not city or "," not in city:
        return

    # Already have occupancy — don't overwrite.
    if components.get("occupancy_type") or components.get("occupancy_identifier"):
        return

    # Split on the first comma: everything before may be designator +
    # optional identifier; everything after is the real city.
    before_comma, _, after_comma = city.partition(",")
    before_comma = before_comma.strip()
    after_comma = after_comma.strip()

    if not before_comma or not after_comma:
        return

    parts = before_comma.split(None, 1)
    first_word = parts[0].upper().replace(".", "")

    if first_word not in UNIT_MAP:
        return

    components["occupancy_type"] = parts[0]
    if len(parts) > 1:
        components["occupancy_identifier"] = parts[1]
    components["city"] = after_comma


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

    # Must start with exactly one letter then a space.
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
