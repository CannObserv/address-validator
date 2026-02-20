"""Address parsing service using the usaddress library."""

import usaddress


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
}


def parse_address(raw: str) -> dict:
    """Parse *raw* address string into labelled components.

    Returns a dict with:
      - ``input``: the original string
      - ``components``: dict of component_name -> value
      - ``type``: "Street Address" or "Intersection"
    """
    try:
        tagged, addr_type = usaddress.tag(raw)
    except usaddress.RepeatedLabelError as exc:
        # Fallback: return the raw token pairs when tagging is ambiguous.
        components = {}
        for token, label in exc.parsed_string:
            key = TAG_NAMES.get(label, label)
            if key in components:
                components[key] += f" {token}"
            else:
                components[key] = token
        return {
            "input": raw,
            "components": components,
            "type": "Ambiguous",
            "warning": "Repeated labels detected; parse may be inaccurate.",
        }

    components = {
        TAG_NAMES.get(label, label): value
        for label, value in tagged.items()
    }
    return {
        "input": raw,
        "components": components,
        "type": addr_type,
    }
