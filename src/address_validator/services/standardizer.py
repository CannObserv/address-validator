"""Address standardization per USPS Publication 28 (US) and Canada Post (CA)."""

import logging
import re

from address_validator.canada_post_data.directionals import CA_DIRECTIONAL_MAP
from address_validator.canada_post_data.provinces import PROVINCE_MAP
from address_validator.canada_post_data.spec import CANADA_POST_SPEC, CANADA_POST_SPEC_VERSION
from address_validator.canada_post_data.suffixes import CA_SUFFIX_MAP
from address_validator.core.address_format import build_validated_string
from address_validator.models import ComponentSet, StandardizeResponseV1
from address_validator.usps_data.directionals import DIRECTIONAL_MAP
from address_validator.usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION
from address_validator.usps_data.states import STATE_MAP
from address_validator.usps_data.suffixes import SUFFIX_MAP
from address_validator.usps_data.units import UNIT_MAP

logger = logging.getLogger(__name__)

_ZIP5: int = 5  # digits in a USPS ZIP code
_ZIP9: int = 9  # digits in a ZIP+4 code


def _lookup(value: str, table: dict[str, str]) -> str:
    """Return the USPS abbreviation for *value*, or *value* unchanged.

    Performs its own defensive uppercasing / period-stripping so it is
    safe to call with raw input as well as pre-cleaned values.
    """
    cleaned = value.upper().replace(".", "").replace("(", "").replace(")", "").strip().strip(",;")
    return table.get(cleaned, cleaned)


def _std_zip(raw: str) -> str:
    """Normalise ZIP: keep 5 or 5+4 digits only.

    Returns the cleaned digit string.  If the input does not contain at
    least 5 digits a warning suffix is *not* added here — the caller is
    responsible for any validation messaging.
    """
    digits = re.sub(r"[^\d]", "", raw)
    if len(digits) >= _ZIP9:
        return f"{digits[:_ZIP5]}-{digits[_ZIP5:_ZIP9]}"
    if len(digits) >= _ZIP5:
        return digits[:_ZIP5]
    # Fewer than 5 digits — return what we have (may be empty).
    return digits


def _get(components: dict[str, str], key: str) -> str:
    """Return the value for *key* after the full cleanup chain.

    The chain is: strip surrounding whitespace → uppercase → remove
    periods → remove parentheses → strip trailing commas/semicolons.

    Returns ``""`` when the key is missing, ``None``, or blank.

    Note: parenthesis stripping is redundant for values coming from the
    parser (which removes parenthesized text pre-parse) but is retained
    so that direct component input via ``/api/standardize`` is handled
    correctly.
    """
    val = components.get(key, "")
    if val is None:
        return ""
    val = val.strip().upper().replace(".", "")
    # USPS Pub 28 §354: remove parentheses from address data.
    val = val.replace("(", "").replace(")", "")
    # usaddress keeps trailing commas/semicolons on tokens; strip them.
    val = val.strip(",;")
    return val


# -- small helpers for assembling street fragments --------------------------


def _street_parts(
    std: dict[str, str],
    prefix: str = "",
) -> list[str]:
    """Collect ordered street-line tokens from *std* using an optional key *prefix*.

    When *prefix* is ``""`` the primary street keys are used; when it is
    ``"second_"`` the intersection's second-street keys are used.
    """
    keys = (
        f"{prefix}thoroughfare_pre_direction",
        f"{prefix}thoroughfare_pre_modifier",
        f"{prefix}thoroughfare_leading_type",
        f"{prefix}thoroughfare_name",
        f"{prefix}thoroughfare_trailing_type",
        f"{prefix}thoroughfare_post_direction",
        f"{prefix}thoroughfare_post_modifier",
    )
    return [std[k] for k in keys if std.get(k)]


def _standardize_street_fields(
    components: dict[str, str],
    std: dict[str, str],
    prefix: str = "",
) -> None:
    """Populate *std* with standardised street fields for a given *prefix*."""
    v = _get(components, f"{prefix}thoroughfare_pre_direction")
    if v:
        std[f"{prefix}thoroughfare_pre_direction"] = _lookup(v, DIRECTIONAL_MAP)

    v = _get(components, f"{prefix}thoroughfare_pre_modifier")
    if v:
        std[f"{prefix}thoroughfare_pre_modifier"] = v

    v = _get(components, f"{prefix}thoroughfare_leading_type")
    if v:
        std[f"{prefix}thoroughfare_leading_type"] = _lookup(v, SUFFIX_MAP)

    v = _get(components, f"{prefix}thoroughfare_name")
    if v:
        std[f"{prefix}thoroughfare_name"] = v

    v = _get(components, f"{prefix}thoroughfare_trailing_type")
    if v:
        std[f"{prefix}thoroughfare_trailing_type"] = _lookup(v, SUFFIX_MAP)

    v = _get(components, f"{prefix}thoroughfare_post_direction")
    if v:
        std[f"{prefix}thoroughfare_post_direction"] = _lookup(v, DIRECTIONAL_MAP)

    v = _get(components, f"{prefix}thoroughfare_post_modifier")
    if v:
        std[f"{prefix}thoroughfare_post_modifier"] = v


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


def _standardize_ca(
    components: dict[str, str],
    upstream_warnings: list[str],
) -> StandardizeResponseV1:
    """Standardise a Canadian address per Canada Post Addressing Guidelines.

    Normalises:
    - ``administrative_area``: full province name → 2-letter abbreviation
    - ``postcode``: uppercase + FSA-space-LDU format
    - ``thoroughfare_trailing_type`` / ``thoroughfare_leading_type``: CA suffix table
    - ``thoroughfare_pre_direction`` / ``thoroughfare_post_direction``: CA directionals

    Components not present in the input are omitted from the output.
    """
    std: dict[str, str] = {}
    warnings: list[str] = list(upstream_warnings)

    # Copy all components as-is first; normalise known fields below.
    for k, v in components.items():
        if v:
            std[k] = v

    # --- administrative_area (province) ---
    region = _get(components, "administrative_area")
    if region:
        abbr = PROVINCE_MAP.get(region.upper())
        if abbr:
            std["administrative_area"] = abbr
        else:
            warnings.append(f"Unrecognised province/territory: '{region}'")
            std["administrative_area"] = region.upper()

    # --- postcode ---
    postcode = _get(components, "postcode")
    if postcode:
        std["postcode"] = _std_postal_code_ca(postcode)

    # --- thoroughfare types ---
    for key in ("thoroughfare_trailing_type", "thoroughfare_leading_type"):
        v = _get(components, key)
        if v:
            std[key] = CA_SUFFIX_MAP.get(v.upper(), v.upper())

    # --- directionals ---
    for key in ("thoroughfare_pre_direction", "thoroughfare_post_direction"):
        v = _get(components, key)
        if v:
            std[key] = CA_DIRECTIONAL_MAP.get(v.lower(), v.upper())

    # --- Build top-level response fields ---
    locality = std.get("locality", "")
    admin_area = std.get("administrative_area", "")
    postcode_out = std.get("postcode", "")

    # Build address lines for the standardized string.
    premise = std.get("premise_number", "")
    pre_dir = std.get("thoroughfare_pre_direction", "")
    leading_type = std.get("thoroughfare_leading_type", "")
    name = std.get("thoroughfare_name", "")
    trailing_type = std.get("thoroughfare_trailing_type", "")
    post_dir = std.get("thoroughfare_post_direction", "")
    unit_type = std.get("sub_premise_type", "")
    unit_id = std.get("sub_premise_number", "")

    # address_line_1: number + street
    street_parts = [p for p in (pre_dir, leading_type, name, trailing_type, post_dir) if p]
    street = " ".join(street_parts)
    unit_part = " ".join(p for p in (unit_type, unit_id) if p)
    address_line_1 = " ".join(p for p in (premise, street) if p)
    address_line_2 = unit_part

    standardized = build_validated_string(
        address_line_1, address_line_2, locality, admin_area, postcode_out
    )

    return StandardizeResponseV1(
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


def standardize(
    components: dict[str, str],
    country: str = "US",
    upstream_warnings: list[str] | None = None,
) -> StandardizeResponseV1:
    """Return a standardized address from parsed *components*.

    Dispatches to ``_standardize_ca()`` for ``country="CA"`` and the
    existing USPS Pub 28 pipeline for ``country="US"`` (default).
    """
    warnings = list(upstream_warnings) if upstream_warnings else []
    if country == "CA":
        return _standardize_ca(components, warnings)
    return _standardize(components, country, warnings)


# ---------------------------------------------------------------------------
# Private helpers for _standardize
# ---------------------------------------------------------------------------

_UnitSlots = tuple[str, str, str, str]  # unit_type, unit_id, sub_type, sub_id


def _resolve_unit_slots(components: dict[str, str]) -> _UnitSlots:
    """Extract and normalise secondary-unit fields from *components*.

    Returns a ``(unit_type, unit_id, sub_type, sub_id)`` tuple where each
    element is a clean string (may be empty).  The caller stores non-empty
    values into the *std* dict and uses them for line-2 assembly.

    Resolution order
    ----------------
    1. ``occupancy_type`` / ``occupancy_identifier`` (primary slot).
    2. ``subaddress_type`` / ``subaddress_identifier`` (secondary slot).
    3. If both slots are empty, try ``building_name`` then ``landmark_name``
       — usaddress sometimes mis-tags designators into these fields.
    4. Promote subaddress → occupancy when the occupancy slot is empty.
    5. Default missing designator to ``"#"`` per USPS Pub 28.
    """
    unit_type = _get(components, "sub_premise_type")
    if unit_type:
        unit_type = _lookup(unit_type, UNIT_MAP)
    unit_id = _get(components, "sub_premise_number")

    sub_type = _get(components, "dependent_sub_premise_type")
    if sub_type:
        sub_type = _lookup(sub_type, UNIT_MAP)
    sub_id = _get(components, "dependent_sub_premise_number")

    # When neither occupancy nor subaddress was parsed, usaddress may
    # have tagged the unit info as LandmarkName or BuildingName (e.g.
    # "BLD C", "STE C&F 1").  Recover it if the leading word is a
    # known unit designator.  If the leading word isn't a recognised
    # designator the field is left unhandled — we don't guess.
    if not unit_type and not unit_id and not sub_type and not sub_id:
        for fallback_key in ("premise_name", "landmark"):
            fb = _get(components, fallback_key)
            if fb:
                parts = fb.split(None, 1)
                if parts and parts[0] in UNIT_MAP:
                    unit_type = UNIT_MAP[parts[0]]
                    unit_id = parts[1] if len(parts) > 1 else ""
                    break

    # If subaddress fields are present but occupancy fields are not,
    # promote subaddress to the primary unit slot.
    if not unit_type and not unit_id:
        unit_type, unit_id = sub_type, sub_id
        sub_type = sub_id = ""

    # Per USPS Pub 28, a secondary identifier without a recognized
    # designator should use '#' as the designator.
    if unit_id and not unit_type:
        # usaddress sometimes folds '#' into the identifier itself
        # (e.g. "# 4B"); split it back out.
        if unit_id.startswith("# "):
            unit_id = unit_id[2:].strip()
        elif unit_id.startswith("#"):
            unit_id = unit_id[1:].strip()
        # usaddress may also fold a designator word into the
        # identifier (e.g. "NO. 16" → cleaned "NO 16").  If the
        # leading word is a known designator, split it out.
        parts = unit_id.split(None, 1)
        if parts and parts[0] in UNIT_MAP:
            unit_type = UNIT_MAP[parts[0]]
            unit_id = parts[1] if len(parts) > 1 else ""
        else:
            unit_type = "#"

    return unit_type, unit_id, sub_type, sub_id


def _assemble_lines(
    std: dict[str, str],
    unit_type: str,
    unit_id: str,
    sub_type: str,
    sub_id: str,
) -> tuple[str, str, str]:
    """Build the three USPS address lines from the standardised component dict.

    Returns ``(line1, line2, last_line)``:

    - **line1** — street number + street name, or PO box.
    - **line2** — secondary-unit designators (USPS Pub 28: larger container
      before more specific unit, e.g. ``"BLDG C STE 120"``).
    - **last_line** — city, state, and ZIP in USPS single-line format
      (``"CITY, ST ZIP"``).
    """
    # --- address line 1 ---
    number_parts: list[str] = [
        std[k]
        for k in ("premise_number_prefix", "premise_number", "premise_number_suffix")
        if std.get(k)
    ]
    first_street = _street_parts(std)
    second_street = _street_parts(std, prefix="second_")

    if first_street and second_street:
        line1 = " ".join([*number_parts, *first_street, "&", *second_street])
    elif first_street or number_parts:
        line1 = " ".join([*number_parts, *first_street])
    elif std.get("general_delivery_type") or std.get("general_delivery"):
        gd_parts = (std.get("general_delivery_type", ""), std.get("general_delivery", ""))
        line1 = " ".join(p for p in gd_parts if p)
    else:
        line1 = ""

    # --- address line 2 ---
    # Larger container (sub) before more specific unit (occupancy).
    line2 = " ".join(p for p in (sub_type, sub_id, unit_type, unit_id) if p)

    # --- last line ---
    city = std.get("locality", "")
    state = std.get("administrative_area", "")
    zip_code = std.get("postcode", "")

    if city and state:
        city_state = f"{city}, {state}"
    elif city:
        city_state = city
    elif state:
        city_state = state
    else:
        city_state = ""
    last_line = " ".join(p for p in (city_state, zip_code) if p)

    return line1, line2, last_line


def _standardize(
    components: dict[str, str],
    country: str,
    warnings: list[str],
) -> StandardizeResponseV1:
    """Internal implementation returning v1 response."""
    logger.debug("standardizing components count=%d country=%s", len(components), country)
    std: dict[str, str] = {}

    # --- primary number ---
    v = _get(components, "premise_number")
    if v:
        std["premise_number"] = v
    v = _get(components, "premise_number_prefix")
    if v:
        std["premise_number_prefix"] = v
    v = _get(components, "premise_number_suffix")
    if v:
        std["premise_number_suffix"] = v

    # --- primary street ---
    _standardize_street_fields(components, std)

    # --- second street (intersections) ---
    _standardize_street_fields(components, std, prefix="second_")

    # --- secondary / occupancy ---
    unit_type, unit_id, sub_type, sub_id = _resolve_unit_slots(components)
    if unit_type:
        std["sub_premise_type"] = unit_type
    if unit_id:
        std["sub_premise_number"] = unit_id
    if sub_type:
        std["dependent_sub_premise_type"] = sub_type
    if sub_id:
        std["dependent_sub_premise_number"] = sub_id

    # --- city ---
    v = _get(components, "locality")
    if v:
        std["locality"] = v

    # --- state ---
    v = _get(components, "administrative_area")
    if v:
        std["administrative_area"] = _lookup(v, STATE_MAP)

    # --- ZIP ---
    v = _get(components, "postcode")
    if v:
        std["postcode"] = _std_zip(v)

    # --- PO Box / General Delivery ---
    for gd_key in (
        "general_delivery_type",
        "general_delivery",
        "general_delivery_group_type",
        "general_delivery_group",
    ):
        v = _get(components, gd_key)
        if v:
            std[gd_key] = v

    # --- assemble output lines ---
    line1, line2, last_line = _assemble_lines(std, unit_type, unit_id, sub_type, sub_id)

    city = std.get("locality", "")
    state = std.get("administrative_area", "")
    zip_code = std.get("postcode", "")

    full_parts = [p for p in (line1, line2, last_line) if p]
    standardized = "  ".join(full_parts) if full_parts else ""

    return StandardizeResponseV1(
        address_line_1=line1,
        address_line_2=line2,
        city=city,
        region=state,
        postal_code=zip_code,
        country=country,
        standardized=standardized,
        components=ComponentSet(
            spec=USPS_PUB28_SPEC,
            spec_version=USPS_PUB28_SPEC_VERSION,
            values=std,
        ),
        warnings=warnings,
    )
