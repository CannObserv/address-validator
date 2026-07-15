"""Post-parse recovery heuristics for the US (usaddress) parse path.

These functions repair common usaddress mis-tagging *after* the raw parse:

- :func:`collect_ambiguous_components` rebuilds a component dict from a
  ``usaddress.RepeatedLabelError`` token list (dual/range addresses and
  multiple secondary-unit designators).
- :func:`recover_components` runs the post-parse recovery heuristics over an
  already-built component dict: moving unit designators and stray identifier
  fragments that usaddress folded into the city back onto the occupancy fields.

Pure helpers — no request-scoped side effects.  Extracted from ``parser.py``
(GH #137); ``parser.py`` retains parse orchestration and the ``TAG_NAMES`` map,
which it passes into :func:`collect_ambiguous_components`.
"""

from __future__ import annotations

from address_validator.core import warnings as warning_catalogue
from address_validator.usps_data.directionals import DIRECTIONAL_MAP
from address_validator.usps_data.states import STATE_MAP
from address_validator.usps_data.suffixes import SUFFIX_MAP
from address_validator.usps_data.units import UNIT_MAP

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


# Designator slots in priority order: primary unit first, then sub-unit.
_UNIT_SLOT_PAIRS = (
    ("sub_premise_type", "sub_premise_number"),
    ("dependent_sub_premise_type", "dependent_sub_premise_number"),
)

# Keys that represent unit-type fields (primary or sub-unit type).
_UNIT_TYPE_KEYS: frozenset[str] = frozenset({"sub_premise_type", "dependent_sub_premise_type"})

# type-key → paired identifier-key (derived from the slot pairs).
_UNIT_TYPE_TO_ID: dict[str, str] = dict(_UNIT_SLOT_PAIRS)

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


def collect_ambiguous_components(
    parsed_string: list[tuple[str, str]],
    warnings: list[str],
    tag_names: dict[str, str],
) -> dict[str, str]:
    """Build a component dict from a usaddress ``RepeatedLabelError`` token list.

    *tag_names* is the ``parser.TAG_NAMES`` usaddress-label → friendly-key map,
    passed in so this module need not import ``parser`` (avoiding an import
    cycle).

    Handles two special cases beyond plain concatenation:

    - **Dual/range addresses** (``"1804 & 1810 Main St"``): an
      ``IntersectionSeparator`` immediately after an ``AddressNumber`` signals
      that the second number is a range partner, not a new address.  The two
      numbers are joined with a hyphen per USPS Pub 28 §232.

    - **Multiple secondary-unit designators** (``"BLDG 201 ROOM 104 T"``):
      when a repeated unit-type label carries a designator-shaped token
      (a known ``UNIT_MAP`` entry, or any alphabetic token such as ``"SMP"``
      that usaddress itself tagged as a unit type — GH #129), it is routed
      to the next free slot instead of being concatenated.  A routed token
      that is not in ``UNIT_MAP`` adds an "Unrecognized unit designator
      preserved" warning.  Subsequent mislabelled tokens (``AddressNumber``,
      ``StreetName``, …) are redirected into that slot's identifier until a
      city/state/zip token appears.
    """
    component_values: dict[str, str] = {}
    prev_key: str | None = None
    separator_before: bool = False
    dual_range: str | None = None
    redirect_id_key: str | None = None

    for token, label in parsed_string:
        key = tag_names.get(label, label)

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

        # Repeated unit-type label → route to the next free slot instead of
        # concatenating.  usaddress already tagged this token as a unit type,
        # so we trust that signal even when the token is not one of the
        # canonical UNIT_MAP designators (GH #129: e.g. "SMP").  We still
        # require the token to *look* like a designator (alphabetic) so a
        # mislabelled number or fragment is not promoted to a slot.
        #
        # The same routing applies when the slot's *identifier* is already
        # occupied even though the type is not (GH #170: "#1, UNIT 1" — the
        # '#' phrase fills sub_premise_number before the first OccupancyType
        # arrives).  Pairing this type with the earlier identifiers would fuse
        # two distinct unit phrases into one.
        if key in _UNIT_TYPE_KEYS and (
            key in component_values or component_values.get(_UNIT_TYPE_TO_ID[key])
        ):
            cleaned_unit_token = token.upper().replace(".", "").strip(",;")
            known_designator = cleaned_unit_token in UNIT_MAP
            if known_designator or cleaned_unit_token.isalpha():
                slot = _next_free_unit_slot(component_values)
                if slot:
                    component_values[slot[0]] = token
                    redirect_id_key = slot[1]
                    if not known_designator:
                        warnings.append(
                            warning_catalogue.UNRECOGNIZED_UNIT_DESIGNATOR.format(
                                designator=cleaned_unit_token
                            )
                        )
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
        warnings.append(warning_catalogue.REPEATED_NUMBERS_RANGE.format(range=dual_range))
    else:
        warnings.append(warning_catalogue.REPEATED_LABELS)

    return component_values


def _warn_unit_recovered(warnings: list[str] | None, designator: str) -> None:
    """Append a unit-recovered warning, including the designator token.

    Shared by phase1/phase2 recovery helpers so the message format is
    defined in one place.  No-op when *warnings* is ``None``.
    """
    if warnings is not None:
        warnings.append(warning_catalogue.UNIT_RECOVERED_FROM_FIELD.format(designator=designator))


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
                warnings.append(warning_catalogue.UNIT_FRAGMENT_FROM_CITY)
            return


def _normalize_unit_value(value: str) -> str:
    """Normalize a unit type/identifier for duplicate comparison.

    Upper-cases, drops periods, and strips surrounding punctuation/space so
    ``"B,"`` (the RLE parse layer keeps the comma) compares equal to ``"B"``.
    """
    return value.upper().replace(".", "").strip(",;. ")


def _normalize_unit_identifier(value: str) -> str:
    """Normalize an identifier for duplicate comparison, dropping any '#'.

    A bare ``"# 1"`` phrase and a named ``"UNIT 1"`` carry the same
    identifier; the pound sign is a designator stand-in, not identifier text.
    """
    return _normalize_unit_value(value.replace("#", ""))


def _dedupe_secondary_units(components: dict[str, str]) -> None:
    """Collapse an identical-duplicate secondary unit into a single slot.

    The RLE routing in :func:`collect_ambiguous_components` slots a repeated
    designator into ``dependent_sub_premise`` without yet knowing its
    identifier (the id token arrives later).  When the input simply repeats the
    same unit verbatim (``"STE B, STE B"`` — a data-entry duplicate), both slots
    end up identical and the address would standardize to ``"STE B STE B"``.

    When the dependent unit's normalized (type, id) equals the primary's, drop
    the dependent slot so only one unit survives.  Distinct second units
    (``"STE J, SMP 2"``) differ in type or id and are left untouched.

    A second duplicate shape (GH #170): a bare ``'#'`` phrase restated by a
    named designator (``"#1, UNIT 1"``).  The '#' identifiers land in the
    primary slot with no type; the named unit routes to the dependent slot.
    When the identifiers match, the named designator wins the primary slot and
    the '#' phrase is dropped.  Distinct pairs (``"#108 STE B"``) differ in
    identifier and keep both slots.
    """
    primary_type = components.get("sub_premise_type")
    dep_type = components.get("dependent_sub_premise_type")

    primary_unnamed = not primary_type or _normalize_unit_value(primary_type) == "#"
    if primary_unnamed and dep_type:
        primary_id = _normalize_unit_identifier(components.get("sub_premise_number", ""))
        dep_id = _normalize_unit_identifier(components.get("dependent_sub_premise_number", ""))
        if primary_id and primary_id == dep_id:
            components["sub_premise_type"] = dep_type
            components["sub_premise_number"] = components["dependent_sub_premise_number"]
            components.pop("dependent_sub_premise_type", None)
            components.pop("dependent_sub_premise_number", None)
            return

    # Nothing to fold when either slot lacks a type.
    if not primary_type or not dep_type:
        return

    same_type = _normalize_unit_value(primary_type) == _normalize_unit_value(dep_type)
    same_id = _normalize_unit_value(components.get("sub_premise_number", "")) == (
        _normalize_unit_value(components.get("dependent_sub_premise_number", ""))
    )
    if same_type and same_id:
        components.pop("dependent_sub_premise_type", None)
        components.pop("dependent_sub_premise_number", None)


def recover_components(
    component_values: dict[str, str],
    warnings: list[str] | None = None,
) -> None:
    """Run all post-parse recovery heuristics over *component_values* in place.

    Mutates *component_values* (and appends to *warnings* when supplied):
    moves unit designators mis-tagged into the city back onto occupancy slots,
    repairs a stray single-letter identifier fragment at the city head, then
    collapses an identical-duplicate secondary unit into a single slot.

    This is the single entry point the parser uses for both the clean and the
    ambiguous (RepeatedLabelError) US paths.
    """
    _recover_unit_from_city(component_values, warnings)
    _recover_identifier_fragment_from_city(component_values, warnings)
    _dedupe_secondary_units(component_values)
