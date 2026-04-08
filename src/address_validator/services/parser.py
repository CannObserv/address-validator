"""Address parsing service using the usaddress library."""

import logging
import re

import usaddress

from address_validator.models import ComponentSet, ParseResponseV1
from address_validator.services.audit import set_audit_context
from address_validator.services.training_candidates import set_candidate_data
from address_validator.usps_data.directionals import DIRECTIONAL_MAP
from address_validator.usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION
from address_validator.usps_data.states import STATE_MAP
from address_validator.usps_data.suffixes import SUFFIX_MAP
from address_validator.usps_data.units import UNIT_MAP

logger = logging.getLogger(__name__)

# Combined lookup for tokens that are valid address vocabulary.
_ADDRESS_VOCABULARY: set[str] = (
    set(UNIT_MAP) | set(SUFFIX_MAP) | set(DIRECTIONAL_MAP) | set(STATE_MAP)
)

# Minimum city string length for identifier-fragment recovery to run.
_MIN_CITY_LEN: int = 3

# Designators that never require an identifier (USPS Pub 28 Appendix H).
# Only these are recognised as bare leading words in phase 2 of city
# recovery.  Designators that require an identifier (KEY, LOT, UNIT,
# STE …) are excluded to avoid false positives on city names like
# KEY WEST or FRONT ROYAL.
_NO_ID_DESIGNATORS: set[str] = {
    "BASEMENT",
    "BSMT",
    "FRONT",
    "FRNT",
    "LOBBY",
    "LBBY",
    "LOWER",
    "LOWR",
    "PENTHOUSE",
    "PH",
    "REAR",
    "SIDE",
    "UPPER",
    "UPPR",
}


# Map usaddress tag names to friendlier keys.
TAG_NAMES: dict[str, str] = {
    "AddressNumber": "premise_number",
    "AddressNumberPrefix": "premise_number_prefix",
    "AddressNumberSuffix": "premise_number_suffix",
    "StreetNamePreDirectional": "thoroughfare_pre_direction",
    "StreetNamePreModifier": "thoroughfare_pre_modifier",
    "StreetNamePreType": "thoroughfare_leading_type",
    "StreetName": "thoroughfare_name",
    "StreetNamePostDirectional": "thoroughfare_post_direction",
    "StreetNamePostModifier": "thoroughfare_post_modifier",
    "StreetNamePostType": "thoroughfare_trailing_type",
    "SubaddressType": "dependent_sub_premise_type",
    "SubaddressIdentifier": "dependent_sub_premise_number",
    "OccupancyType": "sub_premise_type",
    "OccupancyIdentifier": "sub_premise_number",
    "PlaceName": "locality",
    "StateName": "administrative_area",
    "ZipCode": "postcode",
    "USPSBoxType": "general_delivery_type",
    "USPSBoxID": "general_delivery",
    "USPSBoxGroupType": "general_delivery_group_type",
    "USPSBoxGroupID": "general_delivery_group",
    "BuildingName": "premise_name",
    "Recipient": "addressee",
    "NotAddress": "not_address",
    "IntersectionSeparator": "intersection_separator",
    "LandmarkName": "landmark",
    "CornerOf": "corner_of",
    # Second street (intersections)
    "SecondStreetName": "second_thoroughfare_name",
    "SecondStreetNamePreDirectional": "second_thoroughfare_pre_direction",
    "SecondStreetNamePreModifier": "second_thoroughfare_pre_modifier",
    "SecondStreetNamePreType": "second_thoroughfare_leading_type",
    "SecondStreetNamePostDirectional": "second_thoroughfare_post_direction",
    "SecondStreetNamePostModifier": "second_thoroughfare_post_modifier",
    "SecondStreetNamePostType": "second_thoroughfare_trailing_type",
}


# Designator slots in priority order: primary unit first, then sub-unit.
_UNIT_SLOT_PAIRS = (
    ("sub_premise_type", "sub_premise_number"),
    ("dependent_sub_premise_type", "dependent_sub_premise_number"),
)

# Keys that represent unit-type fields (primary or sub-unit type).
_UNIT_TYPE_KEYS: frozenset[str] = frozenset({"sub_premise_type", "dependent_sub_premise_type"})

# Keys that signal the end of the street portion of an address.
_POST_STREET_KEYS: frozenset[str] = frozenset({"locality", "administrative_area", "postcode"})


def _next_free_unit_slot(
    components: dict[str, str],
) -> tuple[str, str] | None:
    """Return the first empty (type_key, id_key) pair, or *None*."""
    for type_key, id_key in _UNIT_SLOT_PAIRS:
        if not components.get(type_key) and not components.get(id_key):
            return type_key, id_key
    return None


def _try_extract_designator(segment: str) -> tuple[str, str] | None:
    """If *segment* starts with a UNIT_MAP key return (type, identifier).

    Returns ``None`` when the leading word is not a known designator.
    """
    segment = segment.strip()
    if not segment:
        return None
    parts = segment.split(None, 1)
    word = parts[0].upper().replace(".", "")
    if word not in UNIT_MAP:
        return None
    identifier = parts[1] if len(parts) > 1 else ""
    return parts[0], identifier


def _emit_token(
    component_values: dict[str, str],
    key: str,
    token: str,
    separator_before: bool,
) -> str | None:
    """Write *token* into *component_values* under *key*; return a dual-range
    string when a hyphen-joined range address is detected, else ``None``."""
    if key in component_values:
        if key == "premise_number" and separator_before:
            merged = f"{component_values[key]}-{token}"
            component_values[key] = merged
            return merged
        component_values[key] += f" {token}"
    else:
        component_values[key] = token
    return None


def _collect_ambiguous_components(
    parsed_string: list[tuple[str, str]],
    warnings: list[str],
) -> dict[str, str]:
    """Build a component dict from a usaddress ``RepeatedLabelError`` token list.

    Handles two special cases beyond plain concatenation:

    - **Dual/range addresses** (``"1804 & 1810 Main St"``): an
      ``IntersectionSeparator`` immediately after an ``AddressNumber`` signals
      that the second number is a range partner, not a new address.  The two
      numbers are joined with a hyphen per USPS Pub 28 §232.

    - **Multiple secondary-unit designators** (``"BLDG 201 ROOM 104 T"``):
      when a repeated unit-type label carries a known ``UNIT_MAP`` designator,
      it is routed to the next free slot instead of being concatenated.
      Subsequent mislabelled tokens (``AddressNumber``, ``StreetName``, …) are
      redirected into that slot's identifier until a city/state/zip token
      appears.
    """
    component_values: dict[str, str] = {}
    prev_key: str | None = None
    separator_before: bool = False
    dual_range: str | None = None
    redirect_id_key: str | None = None

    for token, label in parsed_string:
        key = TAG_NAMES.get(label, label)

        # Stop redirecting once we reach city/state/zip tokens.
        if key in _POST_STREET_KEYS:
            redirect_id_key = None

        # Track whether an IntersectionSeparator appeared right before a
        # repeated AddressNumber — that signals a dual/range address
        # ("1804 & 1810"), not a true intersection.
        if key == "intersection_separator":  # noqa: SIM102
            if prev_key == "premise_number":
                separator_before = True
                prev_key = key
                continue  # don't emit the separator yet
            # True intersection separator — emit normally.

        # Repeated unit-type label whose token is a known designator →
        # route to the next free slot instead of concatenating.
        if (
            key in _UNIT_TYPE_KEYS
            and key in component_values
            and token.upper().replace(".", "").strip(",;") in UNIT_MAP
        ):
            slot = _next_free_unit_slot(component_values)
            if slot:
                component_values[slot[0]] = token
                redirect_id_key = slot[1]
                prev_key = key
                separator_before = False
                continue

        # While redirecting, mislabelled tokens after a second designator
        # are really the identifier for that designator.
        if redirect_id_key is not None and key not in _POST_STREET_KEYS:
            clean = token.strip(",;")
            if clean:
                existing = component_values.get(redirect_id_key)
                component_values[redirect_id_key] = f"{existing} {clean}" if existing else clean
            prev_key = key
            separator_before = False
            continue

        # Normal token: concatenate into existing field or create new.
        # Dual-range address numbers are joined with a hyphen (Pub 28 §232).
        dual_range = _emit_token(component_values, key, token, separator_before) or dual_range
        separator_before = False
        prev_key = key

    if dual_range is not None:
        warnings.append(f"Ambiguous parse: repeated address numbers joined as range '{dual_range}'")
    else:
        warnings.append("Ambiguous parse: repeated labels detected; parse may be inaccurate.")

    return component_values


def _warn_unit_recovered(warnings: list[str] | None, designator: str) -> None:
    """Append a unit-recovered warning, including the designator token.

    Shared by phase1/phase2 recovery helpers so the message format is
    defined in one place.  No-op when *warnings* is ``None``.
    """
    if warnings is not None:
        warnings.append(f"Unit designator recovered from mis-tagged field: '{designator}'")


def _recover_unit_phase1(
    components: dict[str, str],
    warnings: list[str] | None,
) -> None:
    """Phase 1: peel comma-separated leading unit designators from city."""
    while True:
        city = components.get("locality", "")
        if not city or "," not in city:
            break

        before, _, after = city.partition(",")
        before = before.strip()
        after = after.strip()
        if not before or not after:
            break

        result = _try_extract_designator(before)
        if result is not None:
            desig_type, desig_id = result
            slot = _next_free_unit_slot(components)
            if slot:
                components[slot[0]] = desig_type
                if desig_id:
                    components[slot[1]] = desig_id
            components["locality"] = after
            _warn_unit_recovered(warnings, desig_type)
            continue

        # A single word before the comma that isn't in any address
        # vocabulary is likely wayfinding text (e.g. "YARD", "GATE").
        # Drop it.  Multi-word segments are left alone — they could
        # be a real multi-word city name prefix.
        word = before.upper().replace(".", "")
        if " " not in before and word not in _ADDRESS_VOCABULARY:
            components["locality"] = after
            continue

        break


def _recover_unit_phase2(
    components: dict[str, str],
    warnings: list[str] | None,
) -> None:
    """Phase 2: strip bare leading unit designator (no comma) from city.

    Only no-identifier designators (BSMT, FRNT, LOWR …) are stored
    into a slot here.  Designators like KEY, LOT, UNIT always expect
    an identifier, so a bare "KEY WEST" is almost certainly a city.
    When all unit slots are full, orphaned designator words are dropped.
    """
    city = components.get("locality", "")
    if not city or " " not in city:
        return

    first, _, rest = city.partition(" ")
    word = first.upper().replace(".", "")
    rest = rest.strip()
    if not rest:
        return

    slot = _next_free_unit_slot(components)

    if word in _NO_ID_DESIGNATORS:
        if slot:
            components[slot[0]] = first
        components["locality"] = rest
        _warn_unit_recovered(warnings, first)
    elif word in UNIT_MAP and slot is None:
        # All slots full — just strip the orphaned designator word.
        components["locality"] = rest
        _warn_unit_recovered(warnings, first)


def _recover_unit_from_city(components: dict[str, str], warnings: list[str] | None = None) -> None:
    """Move unit designators mis-tagged as part of city back to occupancy.

    usaddress sometimes tags secondary designators that follow the street
    line as ``PlaceName``, concatenating them with the real city.  An
    address like ``"BLDG 1, LOWR LEVEL, UNIT  SEATTLE"`` can produce
    ``city = "LOWR LEVEL, UNIT SEATTLE"`` (after usaddress already
    extracted BLDG).

    This function peels off comma-separated leading segments (Phase 1)
    then checks for a bare leading designator word (Phase 2).
    """
    _recover_unit_phase1(components, warnings)
    _recover_unit_phase2(components, warnings)


def _recover_identifier_fragment_from_city(
    components: dict[str, str],
    warnings: list[str] | None = None,
) -> None:
    """Move a stray single-letter unit qualifier from the start of city.

    usaddress sometimes splits a compound identifier like ``120 K`` and
    absorbs the trailing letter into ``PlaceName``, producing a city of
    ``"K WALLA WALLA"`` instead of ``"WALLA WALLA"``.  When the city
    begins with a single letter followed by a space and an occupancy or
    subaddress identifier already exists, move that letter back onto the
    identifier.
    """
    city = components.get("locality", "")
    if not city or len(city) < _MIN_CITY_LEN:
        return

    # Must start with exactly one letter then a space.  This is
    # intentionally aggressive — a single leading letter is almost
    # always a stray identifier fragment, not the start of a real city
    # name.  The only guard is that an identifier field must already
    # exist (so there is something to append to).  Edge cases like
    # "O FALLON" (O'Fallon with dropped apostrophe) are theoretically
    # possible but unlikely in practice with usaddress output.
    if not city[0].isalpha() or city[1] != " ":
        return

    fragment = city[0]
    rest = city[2:].strip()

    if not rest:
        return

    # Append to whichever identifier field is present.
    for key in ("sub_premise_number", "dependent_sub_premise_number"):
        if components.get(key):
            components[key] += f" {fragment}"
            components["locality"] = rest
            if warnings is not None:
                warnings.append("Unit identifier fragment recovered from city field")
            return


def parse_address(raw: str, country: str = "US") -> ParseResponseV1:
    """Parse *raw* address string into labelled components (v1).

    Returns a :class:`ParseResponseV1`.  Pass ``legacy=True`` via the
    thin wrapper :func:`parse_address_legacy` when the deprecated route
    needs the old response shape.
    """
    return _parse(raw, country)


def _parse(raw: str, country: str) -> ParseResponseV1:
    """Parse *raw* address string into labelled components.

    Returns a :class:`ParseResponse` with:
      - ``input``: the original string
      - ``components``: dict of component_name -> value
      - ``type``: ``"Street Address"``, ``"Intersection"``, or ``"Ambiguous"``
    """
    warnings: list[str] = []

    # USPS Pub 28 §354: parentheses are not valid in standardised
    # addresses.  Parenthesized text is typically wayfinding notes
    # (e.g. "(EAST)", "(UPPER LEVEL)") that confuse usaddress.  Strip
    # it before parsing and collapse any resulting extra whitespace.
    paren_matches = re.findall(r"\([^)]*\)", raw)
    cleaned = re.sub(r"\([^)]*\)", "", raw)
    # Strip any remaining unmatched parentheses (e.g. "123 Main) St").
    cleaned = cleaned.replace("(", "").replace(")", "")
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    for match in paren_matches:
        inner = match[1:-1].strip()
        if inner:
            warnings.append(f"Parenthesized text removed: '{inner}'")

    try:
        tagged, addr_type = usaddress.tag(cleaned)
    except usaddress.RepeatedLabelError as exc:
        logger.warning("ambiguous parse: repeated labels in input")
        component_values: dict[str, str] = _collect_ambiguous_components(
            exc.parsed_string, warnings
        )
        _recover_unit_from_city(component_values, warnings)
        _recover_identifier_fragment_from_city(component_values, warnings)

        set_candidate_data(
            raw_address=raw,
            failure_type="repeated_label_error",
            parsed_tokens=list(exc.parsed_string),
            recovered_components=component_values,
        )

        logger.debug("parsed address type=Ambiguous country=%s", country)
        set_audit_context(parse_type="Ambiguous")
        return ParseResponseV1(
            input=raw,
            country=country,
            components=ComponentSet(
                spec=USPS_PUB28_SPEC,
                spec_version=USPS_PUB28_SPEC_VERSION,
                values=component_values,
            ),
            type="Ambiguous",
            warnings=warnings,
        )

    logger.debug("parsed address type=%s country=%s", addr_type, country)
    component_values = {TAG_NAMES.get(label, label): value for label, value in tagged.items()}

    _recover_unit_from_city(component_values, warnings)
    _recover_identifier_fragment_from_city(component_values, warnings)

    if any(
        "Unit designator recovered" in w or "identifier fragment" in w.lower() for w in warnings
    ):
        set_candidate_data(
            raw_address=raw,
            failure_type="post_parse_recovery",
            parsed_tokens=[(v, k) for k, v in tagged.items()],
            recovered_components=component_values,
        )

    set_audit_context(parse_type=addr_type)
    return ParseResponseV1(
        input=raw,
        country=country,
        components=ComponentSet(
            spec=USPS_PUB28_SPEC,
            spec_version=USPS_PUB28_SPEC_VERSION,
            values=component_values,
        ),
        type=addr_type,
        warnings=warnings,
    )
