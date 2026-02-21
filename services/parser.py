"""Address parsing service using the usaddress library."""

import usaddress

from models import ParseResponse


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


def parse_address(raw: str) -> ParseResponse:
    """Parse *raw* address string into labelled components.

    Returns a :class:`ParseResponse` with:
      - ``input``: the original string
      - ``components``: dict of component_name -> value
      - ``type``: ``"Street Address"``, ``"Intersection"``, or ``"Ambiguous"``
    """
    try:
        tagged, addr_type = usaddress.tag(raw)
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
    return ParseResponse(
        input=raw,
        components=components,
        type=addr_type,
    )
