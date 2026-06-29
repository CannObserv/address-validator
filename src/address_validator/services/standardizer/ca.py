"""Canadian address standardization per Canada Post Addressing Guidelines."""

import re

from address_validator.canada_post_data.directionals import CA_DIRECTIONAL_MAP
from address_validator.canada_post_data.provinces import PROVINCE_MAP
from address_validator.canada_post_data.spec import CANADA_POST_SPEC, CANADA_POST_SPEC_VERSION
from address_validator.canada_post_data.suffixes import CA_SUFFIX_MAP
from address_validator.core import warnings as warning_catalogue
from address_validator.core.address_format import build_validated_string
from address_validator.models import (  # alias can't be used as constructor
    ComponentSet,
    StandardizeResponseV2,
)
from address_validator.services.standardizer._lines import _assemble_lines, _get


def _std_postal_code_ca(raw: str) -> str:
    """Normalise a Canadian postal code to ``A1A 1A1`` format.

    Strips whitespace, uppercases, and inserts the required space after
    the FSA (first three characters).  Returns the raw value uppercased
    if it does not match the expected six-character pattern after cleaning.
    """
    cleaned = raw.upper().replace(" ", "").replace("-", "")
    if re.fullmatch(r"[A-Z]\d[A-Z]\d[A-Z]\d", cleaned):
        return f"{cleaned[:3]} {cleaned[3:]}"
    return raw.upper()


def standardize_ca(
    components: dict[str, str],
    upstream_warnings: list[str],
) -> StandardizeResponseV2:
    """Standardise a Canadian address per Canada Post Addressing Guidelines.

    Normalises:
    - ``administrative_area``: full province name → 2-letter abbreviation
    - ``postcode``: uppercase + FSA-space-LDU format
    - ``thoroughfare_trailing_type`` / ``thoroughfare_leading_type``: CA suffix table
    - ``thoroughfare_pre_direction`` / ``thoroughfare_post_direction``: CA directionals

    Components not present in the input are omitted from the output.

    Address-line assembly (line 1 / line 2 / last line and the two-space
    ``standardized`` separator) is delegated to the shared
    :func:`~address_validator.services.standardizer._lines._assemble_lines`
    helper, matching the US path exactly.
    """
    std: dict[str, str] = {}
    warnings: list[str] = list(upstream_warnings)

    # Normalise all components via _get (strip, uppercase, clean) — mirrors
    # the US standardizer which applies the same chain to every field.
    for k in components:
        val = _get(components, k)
        if val:
            std[k] = val

    # --- administrative_area (province) ---
    region = _get(components, "administrative_area")
    if region:
        abbr = PROVINCE_MAP.get(region)
        if abbr:
            std["administrative_area"] = abbr
        else:
            warnings.append(warning_catalogue.UNRECOGNIZED_REGION.format(region=region))
            std["administrative_area"] = region

    # --- postcode ---
    postcode = _get(components, "postcode")
    if postcode:
        std["postcode"] = _std_postal_code_ca(postcode)

    # --- thoroughfare types ---
    for key in ("thoroughfare_trailing_type", "thoroughfare_leading_type"):
        v = _get(components, key)
        if v:
            std[key] = CA_SUFFIX_MAP.get(v, v)

    # --- directionals ---
    for key in ("thoroughfare_pre_direction", "thoroughfare_post_direction"):
        v = _get(components, key)
        if v:
            std[key] = CA_DIRECTIONAL_MAP.get(v.lower(), v)

    # --- assemble output lines (shared with the US path) ---
    unit_type = std.get("sub_premise_type", "")
    unit_id = std.get("sub_premise_number", "")
    address_line_1, address_line_2, _last_line = _assemble_lines(
        std, unit_type, unit_id, sub_type="", sub_id=""
    )

    locality = std.get("locality", "")
    admin_area = std.get("administrative_area", "")
    postcode_out = std.get("postcode", "")

    standardized = build_validated_string(
        address_line_1, address_line_2, locality, admin_area, postcode_out
    )

    return StandardizeResponseV2(
        address_line_1=address_line_1,
        address_line_2=address_line_2,
        city=locality,
        region=admin_area,
        postal_code=postcode_out,
        country="CA",
        standardized=standardized,
        components=ComponentSet(
            spec=CANADA_POST_SPEC,
            spec_version=CANADA_POST_SPEC_VERSION,
            values=std,
        ),
        warnings=warnings,
    )
