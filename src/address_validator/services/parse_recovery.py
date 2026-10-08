"""Post-parse recovery heuristics for the US (usaddress) parse path.

These functions repair common usaddress mis-tagging *after* the raw parse:

- :func:`collect_ambiguous_components` rebuilds a component dict from a
  ``usaddress.RepeatedLabelError`` token list (dual/range addresses and
  multiple secondary-unit designators).
- :func:`recover_components` runs the post-parse recovery heuristics over an
  already-built component dict: moving unit designators and stray identifier
  fragments that usaddress folded into the city, a USPS box, or a trailing
  recipient back onto the fields they belong in.

Pure helpers — no request-scoped side effects.  Extracted from ``parser.py``
(GH #137); ``parser.py`` retains parse orchestration and the ``TAG_NAMES`` map,
which it passes into :func:`collect_ambiguous_components`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from address_validator.core import warnings as warning_catalogue
from address_validator.usps_data.directionals import DIRECTIONAL_MAP
from address_validator.usps_data.states import MILITARY_STATES, STATE_MAP
from address_validator.usps_data.suffixes import SUFFIX_MAP
from address_validator.usps_data.units import UNIT_MAP


class RecoveryKind(StrEnum):
    """Machine-readable identifier for a post-parse recovery heuristic.

    Callers branch on these (e.g. the parser's training-candidate collection)
    instead of sniffing warning display text, so rewording a warning in
    ``core/warnings.py`` can never silently change behaviour (GH #176).
    """

    UNIT_RECOVERED = "unit_recovered"
    FRAGMENT_RECOVERED = "fragment_recovered"
    LOCALITY_RECOVERED = "locality_recovered"
    DUPLICATE_UNIT_COLLAPSED = "duplicate_unit_collapsed"
    DELIVERY_LINE_RECOVERED = "delivery_line_recovered"


@dataclass(frozen=True)
class RecoveryEvent:
    """A recovery heuristic that fired, with the warning text it emitted.

    ``warning`` carries the fully formatted catalogue string so callers can
    surface it (response warnings, candidate ``failure_reason``) without
    re-deriving it from the kind.
    """

    kind: RecoveryKind
    warning: str


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

# Key prefixes that make up the primary street line (number + street name).
_STREET_KEY_PREFIXES: tuple[str, ...] = ("premise_number", "thoroughfare_")

# The USPS box pair usaddress fills from USPSBoxType / USPSBoxID.
_BOX_KEYS: tuple[str, str] = ("general_delivery_type", "general_delivery")

# The route group pair usaddress fills from USPSBoxGroupType / USPSBoxGroupID
# ("RR 2" in "RR 2 BOX 152"; "PSC 1234" in "PSC 1234 BOX 5678").
_GROUP_KEYS: tuple[str, str] = ("general_delivery_group_type", "general_delivery_group")

# Military route designators: always a route group, never a unit (GH #292).
_MILITARY_GROUP_TYPES: frozenset[str] = frozenset({"PSC", "CMR"})

# The Pub 28 general-delivery line, with any recipient text before or after it.
_GENERAL_DELIVERY = "GENERAL DELIVERY"
_GENERAL_DELIVERY_RE = re.compile(
    r"(?P<before>.*?)\bGENERAL\s+DELIVERY\b(?P<after>.*)", re.IGNORECASE | re.DOTALL
)

# A token shaped like a unit identifier: one letter, or alphanumeric with a
# digit ("100", "4B", "2-3", "#5").  Words like "WEST" in "KEY WEST" fail.
_UNIT_IDENTIFIER_RE = re.compile(r"#?(?:[A-Z]|[A-Z0-9-]*\d[A-Z0-9-]*)")

# An ordinal ("2ND"): a floor written before its designator ("2ND FLOOR").
ORDINAL_RE = re.compile(r"\d+(?:ST|ND|RD|TH)")

# Words that join a list of unit identifiers ("STE 100 & 101", "AND 101").
# Also the list separators of provider narrowing (validation/secondary.py), so
# a list kept on line 2 is always one providers can split (GH #297).
LIST_JOINERS: frozenset[str] = frozenset({"&", "AND"})

# Where usaddress puts "& 101" / "AND 101" after a unit: (joiner key, item
# key, prefix of the sibling keys that must be empty for it to be a list item).
_LIST_ITEM_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("intersection_separator", "second_thoroughfare_name", "second_"),
    ("general_delivery_type", "general_delivery", "general_delivery_group"),
    ("dependent_sub_premise_type", "dependent_sub_premise_number", "dependent_"),
)

# Longest state name in STATE_MAP, in words ("FEDERATED STATES OF MICRONESIA").
_MAX_STATE_WORDS: int = max(len(name.split()) for name in STATE_MAP)


def _is_street_key(key: str) -> bool:
    return key.startswith(_STREET_KEY_PREFIXES)


def _has_street(components: dict[str, str]) -> bool:
    """True when a primary street number or street name was parsed."""
    return any(_is_street_key(k) and v for k, v in components.items())


def _looks_like_unit_identifier(token: str) -> bool:
    return bool(_UNIT_IDENTIFIER_RE.fullmatch(token.upper().strip(",;")))


def _splice(
    components: dict[str, str],
    old_keys: tuple[str, ...],
    new_items: dict[str, str],
) -> None:
    """Replace *old_keys* with *new_items* where the first old key sat.

    Key order is source token order, which decides line-2 slot order for
    same-level unit pairs (``_sub_renders_first``), so recovered fields take
    the position of the fields they came from instead of trailing the dict.
    """
    items = list(components.items())
    pos = next(i for i, (k, _) in enumerate(items) if k in old_keys)
    # A stale (empty) copy of a new key would otherwise override it.
    dropped = {*old_keys, *new_items}
    before = [(k, v) for k, v in items[:pos] if k not in dropped]
    after = [(k, v) for k, v in items[pos:] if k not in dropped]
    components.clear()
    components.update([*before, *new_items.items(), *after])


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


def _floor_ordinals_after_designator(
    parsed_string: list[tuple[str, str]],
    tag_names: dict[str, str],
) -> list[tuple[str, str]]:
    """Swap ``"2ND FLOOR"`` to ``"FLOOR 2ND"`` so the floor is its own unit (GH #289).

    Beside another unit (``"STE 100 2ND FLOOR"``) the ordinal concatenates onto
    the suite's identifier (``"100 2ND"``) and ``FLOOR`` is left with none.
    Designator-first order routes the pair to its own slot.  Trailing
    punctuation stays at the end of the pair.
    """
    tokens = list(parsed_string)
    for i in range(len(tokens) - 1):
        (ordinal, id_label), (floor, type_label) = tokens[i], tokens[i + 1]
        if (
            tag_names.get(id_label, id_label) in _UNIT_TYPE_TO_ID.values()
            and tag_names.get(type_label, type_label) in _UNIT_TYPE_KEYS
            and ORDINAL_RE.fullmatch(ordinal.upper().strip(",;"))
            and UNIT_MAP.get(floor.upper().replace(".", "").strip(",;")) == "FL"
        ):
            trailing = floor[len(floor.rstrip(",;")) :]
            tokens[i] = (floor.rstrip(",;"), type_label)
            tokens[i + 1] = (ordinal.rstrip(",;") + trailing, id_label)
    return tokens


def _military_box_as_route_group(
    parsed_string: list[tuple[str, str]],
    tag_names: dict[str, str],
    warnings: list[str],
) -> list[tuple[str, str]]:
    """Relabel a leading ``PSC``/``CMR`` box phrase as the route group (GH #292).

    usaddress tags ``PSC 802 BOX 74`` as two ``USPSBoxType`` phrases, which
    would concatenate into ``"PSC BOX"`` / ``"802 74"``.  When a second box
    type follows and no route group was tagged, the first phrase and its IDs
    become ``USPSBoxGroupType`` / ``USPSBoxGroupID``, and a recovered
    delivery-line warning naming the route (``"PSC 802"``) is appended to
    *warnings*, as the unit-slot recovery emits for the same address.
    """
    tokens = list(parsed_string)
    keys = [tag_names.get(label, label) for _, label in tokens]
    if "general_delivery_type" not in keys or "general_delivery_group_type" in keys:
        return tokens
    first = keys.index("general_delivery_type")
    second_box = "general_delivery_type" in keys[first + 1 :]
    if not second_box or _normalize_unit_value(tokens[first][0]) not in _MILITARY_GROUP_TYPES:
        return tokens
    tokens[first] = (tokens[first][0], "USPSBoxGroupType")
    route = [tokens[first][0]]
    for i in range(first + 1, len(tokens)):
        if keys[i] != "general_delivery":
            break
        tokens[i] = (tokens[i][0], "USPSBoxGroupID")
        route.append(tokens[i][0])
    route_text = " ".join(t.strip(",;") for t in route)
    warnings.append(warning_catalogue.DELIVERY_LINE_RECOVERED.format(text=route_text))
    return tokens


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
      preserved" warning, except a list joiner (``AND`` in ``"UNIT 1 AND
      2"``), which :func:`_recover_unit_list_item` folds back into the unit
      before it (GH #297).  Subsequent mislabelled tokens (``AddressNumber``,
      ``StreetName``, …) are redirected into that slot's identifier until a
      city/state/zip token appears.

    A floor ordinal before its designator (``"2ND FLOOR"``) is first swapped
    behind it; see :func:`_floor_ordinals_after_designator`.  A military
    route tagged as a second box (``"PSC 802 BOX 74"``) is relabelled as the
    route group; see :func:`_military_box_as_route_group`.
    """
    parsed_string = _floor_ordinals_after_designator(parsed_string, tag_names)
    parsed_string = _military_box_as_route_group(parsed_string, tag_names, warnings)
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
                    # "AND" joins a unit list ("UNIT 1 AND 2"), not an unknown
                    # designator; _recover_unit_list_item folds it back.
                    if not known_designator and cleaned_unit_token not in LIST_JOINERS:
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


def _record_unit_recovered(events: list[RecoveryEvent] | None, designator: str) -> None:
    """Record a unit-recovered event, including the designator token.

    Shared by phase1/phase2 recovery helpers so the message format is
    defined in one place.  No-op when *events* is ``None``.
    """
    if events is not None:
        events.append(
            RecoveryEvent(
                kind=RecoveryKind.UNIT_RECOVERED,
                warning=warning_catalogue.UNIT_RECOVERED_FROM_FIELD.format(designator=designator),
            )
        )


def _recover_unit_phase1(
    components: dict[str, str],
    events: list[RecoveryEvent] | None,
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
            _record_unit_recovered(events, desig_type)
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
    events: list[RecoveryEvent] | None,
) -> None:
    """Phase 2: strip bare leading unit designator (no comma) from city.

    No-identifier designators (BSMT, FRNT, LOWR …) are stored into a slot
    on their own.  Designators like KEY, LOT, UNIT always expect an
    identifier, so they lift only together with an identifier-shaped
    token and a remaining city ("BLG A SEATTLE" → BLG A; GH #285) — a
    bare "KEY WEST" is almost certainly a city.  When all unit slots are
    full, orphaned designator words are dropped.
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
        _record_unit_recovered(events, first)
    elif word in UNIT_MAP and slot is not None:
        # A designator that takes an identifier ("BLG A SEATTLE" — GH #285)
        # lifts only with an identifier-shaped token AND a city left over,
        # so "KEY WEST" / "KEY LARGO" stay cities.
        identifier, _, city_rest = rest.partition(" ")
        city_rest = city_rest.strip()
        if city_rest and _looks_like_unit_identifier(identifier):
            components[slot[0]] = first
            components[slot[1]] = identifier.strip(",;")
            components["locality"] = city_rest
            _record_unit_recovered(events, first)
    elif word in UNIT_MAP and slot is None:
        # All slots full — just strip the orphaned designator word.
        components["locality"] = rest
        _record_unit_recovered(events, first)


def _recover_unit_from_city(
    components: dict[str, str],
    events: list[RecoveryEvent] | None = None,
) -> None:
    """Move unit designators mis-tagged as part of city back to occupancy.

    usaddress sometimes tags secondary designators that follow the street
    line as ``PlaceName``, concatenating them with the real city.  An
    address like ``"BLDG 1, LOWR LEVEL, UNIT  SEATTLE"`` can produce
    ``city = "LOWR LEVEL, UNIT SEATTLE"`` (after usaddress already
    extracted BLDG).

    This function peels off comma-separated leading segments (Phase 1)
    then checks for a bare leading designator word (Phase 2).
    """
    _recover_unit_phase1(components, events)
    _recover_unit_phase2(components, events)


def _recover_unit_from_general_delivery(
    components: dict[str, str],
    events: list[RecoveryEvent] | None = None,
) -> None:
    """Move a unit usaddress tagged as a USPS box onto a free unit slot.

    usaddress tags some unit phrases as ``USPSBoxType`` / ``USPSBoxID``
    (``"123 MAIN ST SUITES 100"`` — GH #285).  With a street present the
    standardizer renders the street on line 1 and never renders the box, so
    the unit was silently lost.  When the box type is a Pub 28 unit
    designator (any UNIT_MAP key) and a unit slot is free, the pair moves
    to that slot.  Real box types (``PO BOX``, ``PMB``), unknown words, and
    rural-route groups are left alone; the standardizer warns about those.

    With no ZIP or state, usaddress can also fold the city into the box ID
    (``"BLG"`` / ``"A SEATTLE"``).  When no locality was parsed, an ID whose
    first token is identifier-shaped, and whose second is not, keeps that
    first token and the rest becomes the city (``"100 B"`` stays whole).
    """
    if not _has_street(components):
        return
    if components.get("general_delivery_group_type") or components.get("general_delivery_group"):
        return
    box_type = components.get("general_delivery_type", "")
    if _normalize_unit_value(box_type) not in UNIT_MAP:
        return
    slot = _next_free_unit_slot(components)
    if slot is None:
        return

    type_key, id_key = slot
    identifier = components.get("general_delivery", "").strip()
    city = ""
    if not components.get("locality"):
        head, _, tail = identifier.partition(" ")
        tail = tail.strip()
        # "100 B" is one compound identifier; only "A SEATTLE" splits.
        if (
            tail
            and _looks_like_unit_identifier(head)
            and not _looks_like_unit_identifier(tail.split()[0])
        ):
            identifier, city = head, tail

    moved = {type_key: box_type}
    if identifier:
        moved[id_key] = identifier
    if city:
        moved["locality"] = city
    _splice(components, _BOX_KEYS, moved)
    _record_unit_recovered(events, box_type)


def _recover_unit_list_item(
    components: dict[str, str],
    events: list[RecoveryEvent] | None = None,
) -> None:
    """Fold the second identifier of a ``&``/``AND`` unit list back into the unit.

    usaddress tags ``"STE 100 & 101"`` as an intersection (second street
    ``101``, rendered on line 1), ``"STE 100 AND 101"`` as a USPS box (dropped
    beside a street), and ``"UNIT 1 AND 2"`` as a second unit whose designator
    is ``AND`` (GH #288, #297).  When the joiner and a single
    identifier-shaped item directly follow a unit identifier, and nothing else
    was tagged on that field family, the item joins the unit as
    ``"100 & 101"``.  A street after the separator (``"APT 5 & 6TH AVE"``) and
    an intersection with no unit before it stay as they are.
    """
    for joiner_key, item_key, sibling_prefix in _LIST_ITEM_FIELDS:
        keys = list(components)
        if joiner_key not in keys:
            continue
        at = keys.index(joiner_key)
        if at == 0 or keys[at - 1] not in _UNIT_TYPE_TO_ID.values():
            continue
        if keys[at + 1 : at + 2] != [item_key]:
            continue
        if _normalize_unit_value(components[joiner_key]) not in LIST_JOINERS:
            continue
        item = components[item_key].strip(",; ")
        if not _looks_like_unit_identifier(item):
            continue
        family = (joiner_key, item_key)
        if any(k.startswith(sibling_prefix) and components[k] for k in keys if k not in family):
            continue
        id_key = keys[at - 1]
        components[id_key] = f"{components[id_key].rstrip(',; ')} & {item}"
        del components[joiner_key], components[item_key]
        if events is not None:
            events.append(
                RecoveryEvent(
                    kind=RecoveryKind.UNIT_RECOVERED,
                    warning=warning_catalogue.UNIT_LIST_ITEM_RECOVERED.format(item=item),
                )
            )


def _split_recipient_from_city(text: str) -> tuple[str, str]:
    """Split comma segments ahead of the city into ``(recipient, city)``.

    The city is the last comma segment.  A leading segment that starts with a
    unit designator stays ahead of it, for :func:`_recover_unit_from_city` to
    lift; any other leading segment (``"ATTN JOHN"``) is the recipient.
    """
    *leading, city = (seg.strip() for seg in text.strip().rstrip(",;").split(","))
    units = [seg for seg in leading if seg and _try_extract_designator(seg)]
    recipient = [seg for seg in leading if seg and not _try_extract_designator(seg)]
    return ", ".join(recipient), ", ".join([*units, city])


def _recover_locality_from_trailing_addressee(
    components: dict[str, str],
    events: list[RecoveryEvent] | None = None,
) -> None:
    """Split a recipient tagged after the street back into city and state.

    With no ZIP, usaddress can tag the whole tail after the street as
    ``Recipient`` (``"123 MAIN ST BLG A SEATTLE WA"`` → ``addressee =
    "BLG A SEATTLE WA"`` — GH #285).  The standardizer never renders
    ``addressee``, so unit, city and state were all lost.

    Fires only when the addressee follows a parsed street, no city, state or
    ZIP was parsed, and the addressee ends in a STATE_MAP entry with at least
    a city before it.  The words before the state become the city, less
    any leading comma segment that is not a unit (``"ATTN JOHN, SEATTLE WA"``
    keeps ``"ATTN JOHN"`` as the recipient); unit recovery from the city
    (:func:`_recover_unit_from_city`) runs after this.  A recipient before
    the street is a real recipient and is left alone.

    Limit: without commas a trailing recipient cannot be told from the city
    (``"ATTN JOHN SEATTLE WA"`` → city ``"ATTN JOHN SEATTLE"``); the
    recovery warning tells the client the city was inferred.
    """
    tail = components.get("addressee", "")
    if not tail or any(components.get(k) for k in _POST_STREET_KEYS):
        return
    keys = list(components)
    if not any(_is_street_key(k) and components[k] for k in keys[: keys.index("addressee")]):
        return

    recovered = _split_trailing_state(tail)
    if recovered is None:
        return
    _splice(components, ("addressee",), recovered)
    _record_locality_recovered(events)


def _split_trailing_state(tail: str) -> dict[str, str] | None:
    """Split a recipient tail ending in a state into last-line fields.

    Returns ``addressee`` (only when a recipient segment is left),
    ``locality`` and ``administrative_area``, or ``None`` when the tail does
    not end in a STATE_MAP entry with a city before it.
    """
    tokens = tail.split()
    # Longest state name first, so "NEW YORK" wins over "YORK".
    for n in range(min(_MAX_STATE_WORDS, len(tokens) - 1), 0, -1):
        state = " ".join(tokens[-n:])
        if state.upper().replace(".", "").strip(",;") in STATE_MAP:
            recipient, city = _split_recipient_from_city(" ".join(tokens[:-n]))
            if not city:
                return None
            recovered = {"addressee": recipient} if recipient else {}
            return recovered | {"locality": city, "administrative_area": state}
    return None


def _record_locality_recovered(events: list[RecoveryEvent] | None) -> None:
    if events is not None:
        events.append(
            RecoveryEvent(
                kind=RecoveryKind.LOCALITY_RECOVERED,
                warning=warning_catalogue.LOCALITY_FROM_RECIPIENT,
            )
        )


def _record_delivery_line_recovered(events: list[RecoveryEvent] | None, text: str) -> None:
    if events is not None:
        events.append(
            RecoveryEvent(
                kind=RecoveryKind.DELIVERY_LINE_RECOVERED,
                warning=warning_catalogue.DELIVERY_LINE_RECOVERED.format(text=text),
            )
        )


def _recover_general_delivery_from_name(
    components: dict[str, str],
    events: list[RecoveryEvent] | None = None,
) -> None:
    """Move a ``GENERAL DELIVERY`` tagged as a landmark or recipient to the box.

    usaddress tags the literal phrase as ``LandmarkName`` (with a comma after
    it) or ``Recipient`` (without one), together with any name beside it
    (``"JOHN SMITH, GENERAL DELIVERY"``).  The standardizer renders neither,
    so line 1 came out empty and validation took the no-street path (GH #293).
    With no street, box or route parsed, the phrase becomes
    ``general_delivery_type``, which renders as line 1; text before or after
    it stays in the field it came from.

    With no ZIP, usaddress can tag the whole input as recipient
    (``"GENERAL DELIVERY SEATTLE WA"``).  When no city, state or ZIP was
    parsed, text after the phrase is split into city and state as
    :func:`_recover_locality_from_trailing_addressee` does; text that does not
    end in a state (``"GENERAL DELIVERY SEATTLE"``) could be the city or a
    name, so the field is left alone.
    """
    if _has_street(components) or any(components.get(k) for k in (*_BOX_KEYS, *_GROUP_KEYS)):
        return
    for key in ("landmark", "addressee"):
        match = _GENERAL_DELIVERY_RE.fullmatch(components.get(key, ""))
        if match is None:
            continue
        before = match["before"].strip(" ,;")
        after = match["after"].strip(" ,;")
        last_line = None
        if after and key == "addressee" and not any(components.get(k) for k in _POST_STREET_KEYS):
            last_line = _split_trailing_state(after)
            if last_line is None:
                return
            after = last_line.pop("addressee", "")
        rest = ", ".join(p for p in (before, after) if p)
        recovered = {key: rest} if rest else {}
        recovered |= {"general_delivery_type": _GENERAL_DELIVERY, **(last_line or {})}
        _splice(components, (key,), recovered)
        _record_delivery_line_recovered(events, _GENERAL_DELIVERY)
        if last_line is not None:
            _record_locality_recovered(events)
        return


def _recover_route_from_unit_slot(
    components: dict[str, str],
    events: list[RecoveryEvent] | None = None,
) -> None:
    """Move a military route group tagged as a unit onto the route group pair.

    usaddress tags ``PSC 1234`` in ``PSC 1234 BOX 5678`` as a subaddress (and
    ``UNIT 2050`` as an occupancy), so line 2 got the route and line 1 only
    the box.  Pub 28 puts both on line 1 (GH #292).  Fires only with no
    street, a box, and no route group already parsed; ``PSC``/``CMR`` always
    qualify, ``UNIT`` only beside a military state (``AE`` or ``ARMED FORCES
    EUROPE``; GH #299).
    """
    if _has_street(components) or any(components.get(k) for k in _GROUP_KEYS):
        return
    if not any(components.get(k) for k in _BOX_KEYS):
        return
    # 'UNIT 2050 BOX 4190' is a route group only beside a military state:
    # UNIT is also a civilian designator.
    state = STATE_MAP.get(_normalize_unit_value(components.get("administrative_area", "")))
    for type_key, id_key in _UNIT_SLOT_PAIRS:
        designator = _normalize_unit_type(components.get(type_key, ""))
        if designator in _MILITARY_GROUP_TYPES or (
            designator == "UNIT" and state in MILITARY_STATES
        ):
            group = {"general_delivery_group_type": components[type_key]}
            if components.get(id_key):
                group["general_delivery_group"] = components[id_key]
            _splice(components, (type_key, id_key), group)
            _record_delivery_line_recovered(events, " ".join(group.values()))
            return


def _recover_identifier_fragment_from_city(
    components: dict[str, str],
    events: list[RecoveryEvent] | None = None,
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
            if events is not None:
                events.append(
                    RecoveryEvent(
                        kind=RecoveryKind.FRAGMENT_RECOVERED,
                        warning=warning_catalogue.UNIT_FRAGMENT_FROM_CITY,
                    )
                )
            return


def _normalize_unit_value(value: str) -> str:
    """Normalize a unit type/identifier for duplicate comparison.

    Upper-cases, drops periods, and strips surrounding punctuation/space so
    ``"B,"`` (the RLE parse layer keeps the comma) compares equal to ``"B"``.
    """
    return value.upper().replace(".", "").strip(",;. ")


def _normalize_unit_type(value: str) -> str:
    """Normalize a unit designator to its UNIT_MAP abbreviation for comparison.

    Variant spellings of one designator (``"SUITES"``, ``"SUTE"``, ``"STE"``)
    compare equal; an unmapped designator compares by its normalized text.
    """
    designator = _normalize_unit_value(value)
    return UNIT_MAP.get(designator, designator)


def _normalize_unit_identifier(value: str) -> str:
    """Normalize an identifier for duplicate comparison, dropping any '#'.

    A bare ``"# 1"`` phrase and a named ``"UNIT 1"`` carry the same
    identifier; the pound sign is a designator stand-in, not identifier text.
    usaddress may also fold a ``'#'`` alias word into the identifier
    (``"NO 1,"``); a leading word that UNIT_MAP maps to ``'#'`` is dropped too.
    """
    normalized = _normalize_unit_value(value.replace("#", ""))
    first, _, rest = normalized.partition(" ")
    if rest and UNIT_MAP.get(first) == "#":
        return rest.strip()
    return normalized


def _record_duplicate_collapsed(
    events: list[RecoveryEvent] | None,
    kept_type: str,
    kept_id: str,
) -> None:
    """Record a duplicate-unit collapse for the unit that was kept.

    Input content is being dropped — signal it, especially on the clean parse
    path where no "Ambiguous parse" warning exists.  Tokens are normalized, and
    the designator uses its UNIT_MAP abbreviation so the warning matches the
    standardized output ('suite' → 'STE').  No-op when *events* is ``None``.
    """
    if events is not None:
        events.append(
            RecoveryEvent(
                kind=RecoveryKind.DUPLICATE_UNIT_COLLAPSED,
                warning=warning_catalogue.DUPLICATE_UNIT_COLLAPSED.format(
                    designator=_normalize_unit_type(kept_type),
                    identifier=_normalize_unit_value(kept_id),
                ),
            )
        )


def _is_hash_word(token: str) -> bool:
    """``'#'`` or a word UNIT_MAP maps to it (``NO``/``NUM``/``NUMBER``)."""
    return UNIT_MAP.get(_normalize_unit_value(token)) == "#"


def _split_hash_phrase(
    components: dict[str, str],
    events: list[RecoveryEvent] | None = None,
) -> None:
    """Split a ``'#'`` phrase usaddress folded into a named unit's identifier.

    After a named designator, usaddress tags a ``'#'`` phrase (or a ``'#'``
    alias word) as part of that unit's identifier (GH #290):

    - leading (``"STE NO 5"``, ``"APT #4"`` → ``"NO 5"`` / ``"# 4"``): the word
      stands in for a designator the unit already has, and is dropped
      (Pub 28: ``"STE 5"``);
    - interior (``"STE 1 #1"``, ``"UNIT 1, NO 1"`` → ``"1 # 1"`` /
      ``"1, NO 1"``): the same identifier is the unit stated twice, collapsed
      with the duplicate-unit warning as ``"#1 STE 1"`` already is (#170,
      #286); a different one (``"STE 1 #2"``) is a second unit, moved to the
      free unit slot right after its source, or left in place when none is free.

    The ``'#'`` word must be followed by an identifier-shaped token
    (``"STE 5 NO"`` is left alone).  A slot with no designator, or a ``'#'``
    one, is the standardizer's (``split_designator``).
    """
    for type_key, id_key in _UNIT_SLOT_PAIRS:
        designator = _normalize_unit_type(components.get(type_key, ""))
        if not designator or designator == "#":
            continue
        tokens = components.get(id_key, "").split()
        if len(tokens) > 1 and _is_hash_word(tokens[0]) and _looks_like_unit_identifier(tokens[1]):
            tokens = tokens[1:]
            components[id_key] = " ".join(tokens)
        at = next(
            (
                i
                for i in range(1, len(tokens) - 1)
                if _is_hash_word(tokens[i]) and _looks_like_unit_identifier(tokens[i + 1])
            ),
            None,
        )
        if at is None:
            continue
        head = " ".join(tokens[:at]).rstrip(",;")
        tail = " ".join(tokens[at + 1 :])
        if _normalize_unit_identifier(head) == _normalize_unit_identifier(tail):
            components[id_key] = head
            _record_duplicate_collapsed(events, components[type_key], head)
            continue
        slot = _next_free_unit_slot(components)
        if slot is not None:
            moved = {id_key: head, slot[0]: tokens[at], slot[1]: tail}
            _splice(components, (id_key,), moved)


def _dedupe_secondary_units(
    components: dict[str, str],
    events: list[RecoveryEvent] | None = None,
) -> None:
    """Collapse an identical-duplicate secondary unit into a single slot.

    The RLE routing in :func:`collect_ambiguous_components` slots a repeated
    designator into ``dependent_sub_premise`` without yet knowing its
    identifier (the id token arrives later).  When the input simply repeats the
    same unit verbatim (``"STE B, STE B"`` — a data-entry duplicate), both slots
    end up identical and the address would standardize to ``"STE B STE B"``.

    When the dependent unit's normalized (type, id) equals the primary's, drop
    the dependent slot so only one unit survives.  Types compare by their
    UNIT_MAP form, so a restatement in another spelling (``"STE B, SUITES B"``
    — GH #286) is the same duplicate; like a verbatim repeat it drops no
    information and emits no warning.  Distinct second units
    (``"STE J, SMP 2"``) differ in type or id and are left untouched.

    A second duplicate shape (GH #170): a bare ``'#'`` phrase (or a UNIT_MAP
    ``'#'`` alias such as ``"NO"``) restated by a named designator
    (``"#1, UNIT 1"``).  The '#' identifiers land in the
    primary slot with no type; the named unit routes to the dependent slot.
    When the identifiers match, the named designator wins the primary slot and
    the '#' phrase is dropped.  The mirror image — the '#' unit in the
    dependent slot, the named unit primary (``"NO 1 STE 1"``, GH #286) — drops
    the '#' phrase the same way.  Distinct pairs (``"#108 STE B"``) differ in
    identifier and keep both slots.
    """
    primary_type = components.get("sub_premise_type")
    dep_type = components.get("dependent_sub_premise_type")

    # Identifiers compare with any '#' (or '#' alias word) dropped.
    primary_id = _normalize_unit_identifier(components.get("sub_premise_number", ""))
    dep_id = _normalize_unit_identifier(components.get("dependent_sub_premise_number", ""))
    same_hashless_id = bool(primary_id) and primary_id == dep_id

    primary_unnamed = not primary_type or _normalize_unit_type(primary_type) == "#"
    if primary_unnamed and dep_type and same_hashless_id:
        kept_id = components["dependent_sub_premise_number"]
        components["sub_premise_type"] = dep_type
        components["sub_premise_number"] = kept_id
        components.pop("dependent_sub_premise_type", None)
        components.pop("dependent_sub_premise_number", None)
        _record_duplicate_collapsed(events, dep_type, kept_id)
        return

    # Mirror image (GH #286): the '#' unit sits in the dependent slot and the
    # named unit is primary ("NO 1 STE 1" — usaddress tags 'NO' as a type).
    # The named unit already holds the primary slot; drop the '#' phrase.
    dep_unnamed = bool(dep_type) and _normalize_unit_type(dep_type) == "#"
    if dep_unnamed and not primary_unnamed and same_hashless_id:
        components.pop("dependent_sub_premise_type", None)
        components.pop("dependent_sub_premise_number", None)
        _record_duplicate_collapsed(events, primary_type, components["sub_premise_number"])
        return

    # Nothing to fold when either slot lacks a type.
    if not primary_type or not dep_type:
        return

    same_type = _normalize_unit_type(primary_type) == _normalize_unit_type(dep_type)
    same_id = _normalize_unit_value(components.get("sub_premise_number", "")) == (
        _normalize_unit_value(components.get("dependent_sub_premise_number", ""))
    )
    if same_type and same_id:
        components.pop("dependent_sub_premise_type", None)
        components.pop("dependent_sub_premise_number", None)


def recover_components(
    component_values: dict[str, str],
    warnings: list[str] | None = None,
) -> list[RecoveryEvent]:
    """Run all post-parse recovery heuristics over *component_values* in place.

    Mutates *component_values*: splits a recipient tagged after the street
    back into city and state, folds a ``&``/``AND`` unit list item tagged as
    an intersection, box or second unit back into its unit, moves a unit
    tagged as a USPS box onto an occupancy slot, restores a street-less
    delivery line (``GENERAL DELIVERY``, a military route group) tagged
    elsewhere to the box fields, moves a unit tagged as part of the city onto
    an occupancy slot, repairs a stray single-letter identifier fragment at
    the city head, splits a ``'#'`` phrase out of a named unit's identifier,
    then collapses an identical-duplicate secondary unit into a single slot.

    Returns the list of :class:`RecoveryEvent` that fired, in order.  When
    *warnings* is supplied, each event's warning text is appended to it — the
    warnings list is derived from the events, so the two never diverge.
    Callers branch on the returned events (never on warning text; GH #176).

    This is the single entry point the parser uses for both the clean and the
    ambiguous (RepeatedLabelError) US paths.
    """
    events: list[RecoveryEvent] = []
    _recover_locality_from_trailing_addressee(component_values, events)
    _recover_unit_list_item(component_values, events)
    _recover_unit_from_general_delivery(component_values, events)
    _recover_general_delivery_from_name(component_values, events)
    _recover_route_from_unit_slot(component_values, events)
    _recover_unit_from_city(component_values, events)
    _recover_identifier_fragment_from_city(component_values, events)
    _split_hash_phrase(component_values, events)
    _dedupe_secondary_units(component_values, events)
    if warnings is not None:
        warnings.extend(e.warning for e in events)
    return events
