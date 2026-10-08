"""US address standardization per USPS Publication 28."""

import logging

from address_validator.core import warnings as warning_catalogue
from address_validator.models import (  # alias can't be used as constructor
    ComponentSet,
    StandardizeResponseV2,
)
from address_validator.services.standardizer._lines import (
    _GENERAL_DELIVERY_KEYS,
    _assemble_lines,
    _get,
    _lookup,
    _std_zip,
    _street_parts,
    _sub_renders_first,
)
from address_validator.usps_data.directionals import DIRECTIONAL_MAP
from address_validator.usps_data.routes import BOX_TYPE_MAP, ROUTE_GROUP_TYPE_MAP
from address_validator.usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION
from address_validator.usps_data.states import STATE_MAP
from address_validator.usps_data.suffixes import SUFFIX_MAP
from address_validator.usps_data.units import UNIT_MAP

logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# Private helpers for standardize_us
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

    if unit_id and not unit_type:
        unit_type, unit_id = split_designator(unit_id)

    return unit_type, unit_id, sub_type, sub_id


def split_designator(unit_id: str) -> tuple[str, str]:
    """Give a cleaned identifier with no designator its Pub 28 designator.

    Per USPS Pub 28, a secondary identifier without a recognized designator
    uses ``#``.  Returns ``(designator, identifier)``.  Also used by
    ``services.validation.secondary`` for the dependent slot, which
    ``_resolve_unit_slots`` leaves untyped (GH #287).
    """
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
        return UNIT_MAP[parts[0]], parts[1] if len(parts) > 1 else ""
    return "#", unit_id


def _standardize_route_and_box_types(std: dict[str, str]) -> None:
    """Abbreviate the route group and box types to their Pub 28 forms.

    ``RURAL ROUTE`` / ``RFD`` → ``RR``, ``STAR ROUTE`` → ``HC``, ``POST OFFICE
    BOX`` / ``DRAWER`` → ``PO BOX`` (GH #298).  A box type beside a route group
    is the route's box, not a Post Office Box, so it is left as typed.
    """
    group_type = std.get("general_delivery_group_type")
    if group_type:
        std["general_delivery_group_type"] = _lookup(group_type, ROUTE_GROUP_TYPE_MAP)
    elif std.get("general_delivery_type"):
        std["general_delivery_type"] = _lookup(std["general_delivery_type"], BOX_TYPE_MAP)


def _warn_general_delivery_beside_street(std: dict[str, str], warnings: list[str]) -> None:
    """Warn when a PO Box / general-delivery value will be left off the lines.

    ``_assemble_lines`` renders the route group and box only when there is no
    street, and never on line 2.  Parse recovery moves a box that is really a unit
    (``SUITES 100``) onto a unit slot first; anything still here beside a
    street (``PO BOX 5``, ``LOCKER 7``, direct component input) is kept in
    ``components`` but omitted from the address — say so (GH #285).
    """
    has_street = bool(_street_parts(std)) or any(
        std.get(k) for k in ("premise_number_prefix", "premise_number", "premise_number_suffix")
    )
    dropped = " ".join(std[k] for k in _GENERAL_DELIVERY_KEYS if std.get(k))
    if has_street and dropped:
        warnings.append(warning_catalogue.GENERAL_DELIVERY_DISCARDED.format(text=dropped))


def standardize_us(
    components: dict[str, str],
    country: str,
    warnings: list[str],
) -> StandardizeResponseV2:
    """Standardise a US address per USPS Pub 28, returning a StandardizeResponseV2."""
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
    for gd_key in _GENERAL_DELIVERY_KEYS:
        v = _get(components, gd_key)
        if v:
            std[gd_key] = v
    _standardize_route_and_box_types(std)
    _warn_general_delivery_beside_street(std, warnings)

    # --- assemble output lines ---
    line1, line2, last_line = _assemble_lines(
        std,
        unit_type,
        unit_id,
        sub_type,
        sub_id,
        sub_first=_sub_renders_first(components, sub_type),
    )

    city = std.get("locality", "")
    state = std.get("administrative_area", "")
    zip_code = std.get("postcode", "")

    full_parts = [p for p in (line1, line2, last_line) if p]
    standardized = "  ".join(full_parts) if full_parts else ""

    return StandardizeResponseV2(
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
