"""Address standardization per USPS Publication 28."""

import re

from usps_data.directionals import DIRECTIONAL_MAP
from usps_data.states import STATE_MAP
from usps_data.suffixes import SUFFIX_MAP
from usps_data.units import UNIT_MAP


def _lookup(value: str, table: dict[str, str]) -> str:
    """Look up *value* (case-insensitive, stripped of periods) in *table*."""
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
    """Return the value for *key* if present and non-empty, else ``""``."""
    val = components.get(key, "")
    if val is None:
        return ""
    return val.strip()


def standardize(components: dict[str, str]) -> dict:
    """Return a standardized address dict from parsed *components*.

    The returned dict has:
      - ``address_line_1``
      - ``address_line_2`` (secondary / unit, may be empty)
      - ``city``
      - ``state``
      - ``zip_code``
      - ``standardized`` – the full single-line USPS form
      - ``components`` – the standardized individual fields
    """
    std: dict[str, str] = {}

    # --- primary number ---
    v = _get(components, "address_number")
    if v:
        std["address_number"] = v.upper()
    v = _get(components, "address_number_prefix")
    if v:
        std["address_number_prefix"] = v.upper()
    v = _get(components, "address_number_suffix")
    if v:
        std["address_number_suffix"] = v.upper()

    # --- street pre-directional ---
    v = _get(components, "street_name_pre_directional")
    if v:
        std["street_name_pre_directional"] = _lookup(v, DIRECTIONAL_MAP)

    # --- street pre-modifier / pre-type ---
    v = _get(components, "street_name_pre_modifier")
    if v:
        std["street_name_pre_modifier"] = v.upper()
    v = _get(components, "street_name_pre_type")
    if v:
        std["street_name_pre_type"] = _lookup(v, SUFFIX_MAP)

    # --- street name ---
    v = _get(components, "street_name")
    if v:
        std["street_name"] = v.upper().replace(".", "")

    # --- street suffix (post-type) ---
    v = _get(components, "street_name_post_type")
    if v:
        std["street_name_post_type"] = _lookup(v, SUFFIX_MAP)

    # --- street post-directional ---
    v = _get(components, "street_name_post_directional")
    if v:
        std["street_name_post_directional"] = _lookup(v, DIRECTIONAL_MAP)

    # --- street post-modifier ---
    v = _get(components, "street_name_post_modifier")
    if v:
        std["street_name_post_modifier"] = v.upper()

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
            unit_id = v.upper().replace(".", "")
            break
    if unit_type:
        std["occupancy_type"] = unit_type
    if unit_id:
        std["occupancy_identifier"] = unit_id

    # --- city ---
    v = _get(components, "city")
    if v:
        std["city"] = v.upper().replace(".", "")

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
        std["usps_box_type"] = v.upper()
    v = _get(components, "usps_box_id")
    if v:
        std["usps_box_id"] = v.upper()

    # --- assemble lines ---
    line1_parts: list[str] = []
    for k in (
        "address_number_prefix",
        "address_number",
        "address_number_suffix",
        "street_name_pre_directional",
        "street_name_pre_modifier",
        "street_name_pre_type",
        "street_name",
        "street_name_post_type",
        "street_name_post_directional",
        "street_name_post_modifier",
    ):
        val = std.get(k, "")
        if val:
            line1_parts.append(val)

    # PO Box alternative
    if not line1_parts:
        if std.get("usps_box_type"):
            line1_parts.append(std["usps_box_type"])
        if std.get("usps_box_id"):
            line1_parts.append(std["usps_box_id"])

    line1 = " ".join(line1_parts)

    line2_parts: list[str] = []
    if unit_type:
        line2_parts.append(unit_type)
    if unit_id:
        line2_parts.append(unit_id)
    line2 = " ".join(line2_parts)

    city = std.get("city", "")
    state = std.get("state", "")
    zip_code = std.get("zip_code", "")

    last_line_parts = []
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

    return {
        "address_line_1": line1,
        "address_line_2": line2,
        "city": city,
        "state": state,
        "zip_code": zip_code,
        "standardized": standardized,
        "components": std,
    }
