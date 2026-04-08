"""ISO 19160-4 component key translation profiles.

The service layer uses strict ISO 19160-4 element names throughout.
This module translates those keys into alternative vocabularies at the
response boundary — e.g. the ``usps-pub28`` profile restores the
snake_case USPS key names used by v1 clients.

``translate_components`` is a pure function: it does not modify the
input dict and unknown keys always pass through unchanged.
"""

# Keys in this mapping are ISO 19160-4 element names.
# Values are the target vocabulary keys for that profile.
_USPS_PUB28: dict[str, str] = {
    "premise_number": "address_number",
    "premise_number_prefix": "address_number_prefix",
    "premise_number_suffix": "address_number_suffix",
    "premise_name": "building_name",
    "thoroughfare_pre_direction": "street_name_pre_directional",
    "thoroughfare_pre_modifier": "street_name_pre_modifier",
    "thoroughfare_leading_type": "street_name_pre_type",
    "thoroughfare_name": "street_name",
    "thoroughfare_trailing_type": "street_name_post_type",
    "thoroughfare_post_direction": "street_name_post_directional",
    "thoroughfare_post_modifier": "street_name_post_modifier",
    "sub_premise_type": "occupancy_type",
    "sub_premise_number": "occupancy_identifier",
    "dependent_sub_premise_type": "subaddress_type",
    "dependent_sub_premise_number": "subaddress_identifier",
    "locality": "city",
    "administrative_area": "state",
    "postcode": "zip_code",
    "general_delivery_type": "usps_box_type",
    "general_delivery": "usps_box_id",
    "general_delivery_group_type": "usps_box_group_type",
    "general_delivery_group": "usps_box_group_id",
    "addressee": "recipient",
    "landmark": "landmark_name",
    "second_thoroughfare_name": "second_street_name",
    "second_thoroughfare_pre_direction": "second_street_name_pre_directional",
    "second_thoroughfare_pre_modifier": "second_street_name_pre_modifier",
    "second_thoroughfare_leading_type": "second_street_name_pre_type",
    "second_thoroughfare_post_direction": "second_street_name_post_directional",
    "second_thoroughfare_post_modifier": "second_street_name_post_modifier",
    "second_thoroughfare_trailing_type": "second_street_name_post_type",
}

# Profile registry.  ``iso-19160-4`` and ``canada-post`` use an empty
# mapping (identity transform).  Add entries here as new profiles are needed.
_PROFILES: dict[str, dict[str, str]] = {
    "iso-19160-4": {},
    "usps-pub28": _USPS_PUB28,
    "canada-post": {},  # reserved; diverges from ISO as Canada Post spec requires
}

#: Set of valid profile identifiers accepted by the API.
VALID_PROFILES: frozenset[str] = frozenset(_PROFILES)


def translate_components(values: dict[str, str], profile: str) -> dict[str, str]:
    """Return *values* with keys renamed per *profile*.

    Unknown keys pass through unchanged.  Unknown *profile* strings are
    treated as the identity transform (ISO 19160-4).
    """
    mapping = _PROFILES.get(profile, {})
    if not mapping:
        return values
    return {mapping.get(k, k): v for k, v in values.items()}
