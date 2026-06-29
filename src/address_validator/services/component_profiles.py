"""ISO 19160-4 component key translation profiles.

The service layer uses strict ISO 19160-4 element names throughout.
This module translates those keys into alternative vocabularies at the
response boundary — e.g. the ``usps-pub28`` profile emits USPS
Publication 28 snake_case key names.

``translate_components`` is a pure function: it does not modify the
input dict and unknown keys always pass through unchanged.
"""

from fastapi import Query

from address_validator.core.errors import APIError
from address_validator.models import ComponentSet
from address_validator.services.spec import ISO_19160_4_SPEC, ISO_19160_4_SPEC_VERSION

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

#: Human-readable description of the ``component_profile`` query parameter.
COMPONENT_PROFILE_DESCRIPTION = (
    "Component key vocabulary. "
    "`iso-19160-4` (default): ISO 19160-4 element names. "
    "`usps-pub28`: USPS Publication 28 snake_case names. "
    "`canada-post`: reserved; currently identical to `iso-19160-4`."
)


def valid_component_profile(
    component_profile: str = Query(
        default="iso-19160-4",
        description=COMPONENT_PROFILE_DESCRIPTION,
    ),
) -> str:
    """FastAPI dependency: validate the ``component_profile`` query param.

    Returns the profile unchanged when valid; raises ``APIError`` with the
    canonical ``invalid_component_profile`` contract (HTTP 422) otherwise.
    Single source of truth for the guard previously inlined in every v2 route.
    """
    if component_profile not in VALID_PROFILES:
        raise APIError(
            status_code=422,
            error="invalid_component_profile",
            message=(
                f"Unknown component_profile '{component_profile}'. "
                f"Valid values: {sorted(VALID_PROFILES)}."
            ),
        )
    return component_profile


def translate_components(values: dict[str, str], profile: str) -> dict[str, str]:
    """Return *values* with keys renamed per *profile*.

    Unknown keys pass through unchanged.  Unknown *profile* strings are
    treated as the identity transform (ISO 19160-4).
    """
    mapping = _PROFILES.get(profile, {})
    if not mapping:
        return values
    return {mapping.get(k, k): v for k, v in values.items()}


def build_output_component_set(
    result_components: ComponentSet, profile: str, country: str
) -> ComponentSet:
    """Assemble the response ``ComponentSet`` for the parse/standardize tail.

    Owns the spec / spec_version / key-translation decision shared by the v2
    ``parse`` and ``standardize`` routers:

    - ``usps-pub28`` → keep the source spec (the pipeline already produced
      USPS Pub 28 keys) and translate values into the USPS vocabulary.
    - ``country == "CA"`` → keep the source spec (e.g. ``canada-post`` from the
      standardizer, or ``raw`` from a libpostal parse) regardless of profile.
    - otherwise → relabel as ISO 19160-4.

    The CA branch was previously present only in ``standardize``; centralising
    it here converges ``parse`` onto the same rule so a CA parse reports its
    true source spec instead of being silently relabelled ISO.
    """
    translated = translate_components(result_components.values, profile)
    if profile == "usps-pub28" or country == "CA":
        spec = result_components.spec
        spec_version = result_components.spec_version
    else:
        spec = ISO_19160_4_SPEC
        spec_version = ISO_19160_4_SPEC_VERSION
    return ComponentSet(spec=spec, spec_version=spec_version, values=translated)


def translate_components_to_iso(values: dict[str, str], profile: str) -> dict[str, str]:
    """Return *values* with keys translated from *profile* vocabulary to ISO 19160-4.

    This is the inverse of :func:`translate_components`.  Unknown keys pass
    through unchanged.  Unknown *profile* strings are treated as the identity
    transform (already ISO 19160-4).
    """
    mapping = _PROFILES.get(profile, {})
    if not mapping:
        return values
    reverse = {v: k for k, v in mapping.items()}
    return {reverse.get(k, k): v for k, v in values.items()}
