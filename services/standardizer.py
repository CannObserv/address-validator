"""Address standardization per USPS Publication 28."""

import re

from models import StandardizeResponse
from usps_data.directionals import DIRECTIONAL_MAP
from usps_data.states import STATE_MAP
from usps_data.suffixes import SUFFIX_MAP
from usps_data.units import UNIT_MAP


def _lookup(value: str, table: dict[str, str]) -> str:
    """Return the USPS abbreviation for *value*, or *value* unchanged.

    Performs its own defensive uppercasing / period-stripping so it is
    safe to call with raw input as well as pre-cleaned values.
    """
    cleaned = value.upper().replace(".", "").strip()
    return table.get(cleaned, cleaned)


def _std_zip(raw: str) -> str:
    """Normalise ZIP: keep 5 or 5+4 digits only.

    Returns the cleaned digit string.  If the input does not contain at
    least 5 digits a warning suffix is *not* added here — the caller is
    responsible for any validation messaging.
    """
    digits = re.sub(r"[^\d]", "", raw)
    if len(digits) >= 9:
        return f"{digits[:5]}-{digits[5:9]}"
    if len(digits) >= 5:
        return digits[:5]
    # Fewer than 5 digits — return what we have (may be empty).
    return digits


def _get(components: dict[str, str], key: str) -> str:
    """Return the value for *key*, uppercased and period-stripped.

    Returns ``""`` when the key is missing, ``None``, or blank.
    """
    val = components.get(key, "")
    if val is None:
        return ""
    val = val.strip().upper().replace(".", "")
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
    ``"second_"`` the intersection’s second-street keys are used.
    """
    keys = (
        f"{prefix}street_name_pre_directional",
        f"{prefix}street_name_pre_modifier",
        f"{prefix}street_name_pre_type",
        f"{prefix}street_name",
        f"{prefix}street_name_post_type",
        f"{prefix}street_name_post_directional",
        f"{prefix}street_name_post_modifier",
    )
    return [std[k] for k in keys if std.get(k)]


def _standardize_street_fields(
    components: dict[str, str],
    std: dict[str, str],
    prefix: str = "",
) -> None:
    """Populate *std* with standardised street fields for a given *prefix*."""
    v = _get(components, f"{prefix}street_name_pre_directional")
    if v:
        std[f"{prefix}street_name_pre_directional"] = _lookup(v, DIRECTIONAL_MAP)

    v = _get(components, f"{prefix}street_name_pre_modifier")
    if v:
        std[f"{prefix}street_name_pre_modifier"] = v

    v = _get(components, f"{prefix}street_name_pre_type")
    if v:
        std[f"{prefix}street_name_pre_type"] = _lookup(v, SUFFIX_MAP)

    v = _get(components, f"{prefix}street_name")
    if v:
        std[f"{prefix}street_name"] = v

    v = _get(components, f"{prefix}street_name_post_type")
    if v:
        std[f"{prefix}street_name_post_type"] = _lookup(v, SUFFIX_MAP)

    v = _get(components, f"{prefix}street_name_post_directional")
    if v:
        std[f"{prefix}street_name_post_directional"] = _lookup(v, DIRECTIONAL_MAP)

    v = _get(components, f"{prefix}street_name_post_modifier")
    if v:
        std[f"{prefix}street_name_post_modifier"] = v


def standardize(components: dict[str, str]) -> StandardizeResponse:
    """Return a standardized address from parsed *components*."""
    std: dict[str, str] = {}

    # --- primary number ---
    v = _get(components, "address_number")
    if v:
        std["address_number"] = v
    v = _get(components, "address_number_prefix")
    if v:
        std["address_number_prefix"] = v
    v = _get(components, "address_number_suffix")
    if v:
        std["address_number_suffix"] = v

    # --- primary street ---
    _standardize_street_fields(components, std)

    # --- second street (intersections) ---
    _standardize_street_fields(components, std, prefix="second_")

    # --- secondary / occupancy ---
    unit_type = ""
    unit_id = ""
    for type_key in ("occupancy_type", "subaddress_type"):
        v = _get(components, type_key)
        if v:
            unit_type = _lookup(v, UNIT_MAP)
            break
    for id_key in ("occupancy_identifier", "subaddress_identifier"):
        v = _get(components, id_key)
        if v:
            unit_id = v
            break
    # Per USPS Pub 28, a secondary identifier without a recognized
    # designator should use '#' as the designator.
    if unit_id and not unit_type:
        # usaddress sometimes folds '#' into the identifier itself
        # (e.g. "# 4B"); split it back out.
        if unit_id.startswith("# "):
            unit_id = unit_id[2:].strip()
        elif unit_id.startswith("#"):
            unit_id = unit_id[1:].strip()
        unit_type = "#"
    if unit_type:
        std["occupancy_type"] = unit_type
    if unit_id:
        std["occupancy_identifier"] = unit_id

    # --- city ---
    v = _get(components, "city")
    if v:
        std["city"] = v

    # --- state ---
    v = _get(components, "state")
    if v:
        std["state"] = _lookup(v, STATE_MAP)

    # --- ZIP ---
    v = _get(components, "zip_code")
    if v:
        std["zip_code"] = _std_zip(v)

    # --- PO Box ---
    v = _get(components, "usps_box_type")
    if v:
        std["usps_box_type"] = v
    v = _get(components, "usps_box_id")
    if v:
        std["usps_box_id"] = v

    # --- assemble address line 1 ---
    number_parts: list[str] = []
    for k in ("address_number_prefix", "address_number", "address_number_suffix"):
        val = std.get(k, "")
        if val:
            number_parts.append(val)

    first_street = _street_parts(std)
    second_street = _street_parts(std, prefix="second_")

    if first_street and second_street:
        # Intersection: FIRST ST & SECOND ST
        line1 = " ".join(
            [*number_parts, *first_street, "&", *second_street]
        )
    elif first_street or number_parts:
        line1 = " ".join([*number_parts, *first_street])
    elif std.get("usps_box_type") or std.get("usps_box_id"):
        po_parts = []
        if std.get("usps_box_type"):
            po_parts.append(std["usps_box_type"])
        if std.get("usps_box_id"):
            po_parts.append(std["usps_box_id"])
        line1 = " ".join(po_parts)
    else:
        line1 = ""

    # --- address line 2 ---
    line2_parts: list[str] = []
    if unit_type:
        line2_parts.append(unit_type)
    if unit_id:
        line2_parts.append(unit_id)
    line2 = " ".join(line2_parts)

    # --- last line ---
    city = std.get("city", "")
    state = std.get("state", "")
    zip_code = std.get("zip_code", "")

    last_line_parts: list[str] = []
    if city and state:
        last_line_parts.append(f"{city}, {state}")
    elif city:
        last_line_parts.append(city)
    elif state:
        last_line_parts.append(state)
    if zip_code:
        last_line_parts.append(zip_code)
    last_line = " ".join(last_line_parts)

    full_parts = [p for p in (line1, line2, last_line) if p]
    standardized = "  ".join(full_parts) if full_parts else ""

    return StandardizeResponse(
        address_line_1=line1,
        address_line_2=line2,
        city=city,
        state=state,
        zip_code=zip_code,
        standardized=standardized,
        components=std,
    )
