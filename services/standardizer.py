"""Address standardization per USPS Publication 28."""

import logging
import re

from models import ComponentSet, StandardizeResponseV1
from usps_data.directionals import DIRECTIONAL_MAP
from usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION
from usps_data.states import STATE_MAP
from usps_data.suffixes import SUFFIX_MAP
from usps_data.units import UNIT_MAP

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


def standardize(
    components: dict[str, str],
    country: str = "US",
    upstream_warnings: list[str] | None = None,
) -> StandardizeResponseV1:
    """Return a standardized address from parsed *components* (v1)."""
    return _standardize(components, country, list(upstream_warnings) if upstream_warnings else [])


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
    unit_type = _get(components, "occupancy_type")
    if unit_type:
        unit_type = _lookup(unit_type, UNIT_MAP)
    unit_id = _get(components, "occupancy_identifier")

    sub_type = _get(components, "subaddress_type")
    if sub_type:
        sub_type = _lookup(sub_type, UNIT_MAP)
    sub_id = _get(components, "subaddress_identifier")

    # When neither occupancy nor subaddress was parsed, usaddress may
    # have tagged the unit info as LandmarkName or BuildingName (e.g.
    # "BLD C", "STE C&F 1").  Recover it if the leading word is a
    # known unit designator.  If the leading word isn't a recognised
    # designator the field is left unhandled — we don't guess.
    if not unit_type and not unit_id and not sub_type and not sub_id:
        for fallback_key in ("building_name", "landmark_name"):
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
        for k in ("address_number_prefix", "address_number", "address_number_suffix")
        if std.get(k)
    ]
    first_street = _street_parts(std)
    second_street = _street_parts(std, prefix="second_")

    if first_street and second_street:
        line1 = " ".join([*number_parts, *first_street, "&", *second_street])
    elif first_street or number_parts:
        line1 = " ".join([*number_parts, *first_street])
    elif std.get("usps_box_type") or std.get("usps_box_id"):
        line1 = " ".join(p for p in (std.get("usps_box_type", ""), std.get("usps_box_id", "")) if p)
    else:
        line1 = ""

    # --- address line 2 ---
    # Larger container (sub) before more specific unit (occupancy).
    line2 = " ".join(p for p in (sub_type, sub_id, unit_type, unit_id) if p)

    # --- last line ---
    city = std.get("city", "")
    state = std.get("state", "")
    zip_code = std.get("zip_code", "")

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
    unit_type, unit_id, sub_type, sub_id = _resolve_unit_slots(components)
    if unit_type:
        std["occupancy_type"] = unit_type
    if unit_id:
        std["occupancy_identifier"] = unit_id
    if sub_type:
        std["subaddress_type"] = sub_type
    if sub_id:
        std["subaddress_identifier"] = sub_id

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

    # --- assemble output lines ---
    line1, line2, last_line = _assemble_lines(std, unit_type, unit_id, sub_type, sub_id)

    city = std.get("city", "")
    state = std.get("state", "")
    zip_code = std.get("zip_code", "")

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
