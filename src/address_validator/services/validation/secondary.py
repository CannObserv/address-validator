"""The secondary-unit line sent to validation providers (GH #287).

A standardized US ``address_line_2`` can carry more than one unit: a chained
WA cannabis-licensee unit kept by #129 (``"SMP 2 STE J"``, ``"STE 110 SMP 2"``),
a container rendered before the specific unit by #170 (``"BLDG 1 STE 100"``),
or a same-level pair (``"UNIT 3 STE 4"``, ``"# 5 # 6"``).  USPS's
``secondaryAddress`` (and Google's CASS check) reads one unit, so the whole
line answers DPV ``D`` and is echoed back unmatched.

Providers are sent **one** Pub 28 unit instead: designators outside Pub 28
(``SMP``) are dropped, a specific unit beats a container (``BLDG``/``FL``),
and among equals the first unit on line 2 wins.  A unit whose identifier is a
range or list (``"STE 100-102"``, ``"STE 100, 101"``; GH #289; ``"STE 100 &
101"``; GH #297) is sent as its first identifier.  The standardized line 2 is
unchanged — this only narrows the provider request, so it needs no
``PIPELINE_CODE_VERSION`` bump.  ``_make_pattern_key`` includes the narrowed
unit, so cached answers to the full line are not reused.
"""

import re

from address_validator.models import StandardizedAddress
from address_validator.services.standardizer.us import split_designator
from address_validator.usps_data.spec import USPS_PUB28_SPEC
from address_validator.usps_data.units import CONTAINER_DESIGNATORS, PUB28_DESIGNATORS

_Slot = tuple[str, str]  # designator, identifier
_Unit = tuple[str, list[str]]  # designator, identifier tokens (punctuation kept)

# A two-sided numeric range ("100-102").  Only same-width ascending pairs count:
# "2-100" (floor-suite), "9-1" and "A-1" are read as single identifiers.
_RANGE_RE = re.compile(r"(\d+)-(\d+)")

# Whole tokens that join a list of identifiers like "," does (GH #297).
_LIST_JOINERS = frozenset({"&", "AND"})


def _slot(values: dict[str, str], prefix: str) -> _Slot:
    return (
        values.get(f"{prefix}sub_premise_type", ""),
        values.get(f"{prefix}sub_premise_number", ""),
    )


def _render(slot: _Slot) -> str:
    """The slot as ``_assemble_lines`` renders it on line 2."""
    return " ".join(p for p in slot if p)


def _split_units(slot: _Slot) -> list[_Unit]:
    """Split one slot into units, breaking at an embedded ``#`` (``"# 5 # 6"``).

    usaddress tags a repeated ``#`` unit as one identifier (``"5 # 6"``).  Only
    ``#`` splits: other designator-like tokens are legitimate identifiers
    (``"APT PH 2"``, ``"FL 2 REAR"``).  An identifier with no designator gets
    one as the standardizer gives the occupancy slot (``"STE 5"`` → ``STE``,
    else ``#``); the dependent slot is left untyped there.
    """
    designator, identifier = slot
    if not designator:
        designator, identifier = split_designator(identifier)
    tokens = identifier.split()
    units: list[_Unit] = [(designator, [])]
    for i, tok in enumerate(tokens):
        if tok == "#" and units[-1][1] and i + 1 < len(tokens):
            units.append(("#", []))
        elif tok.strip(",;"):
            units[-1][1].append(tok)
        elif units[-1][1]:  # a lone "," still separates list items
            units[-1][1][-1] += tok
    return units


def _first_identifier(tokens: list[str]) -> list[str]:
    """Narrow a range or list of identifiers to its first one (GH #289).

    USPS reads one unit, so ``"100, 101"``, ``"100 & 101"``, ``"100 AND
    101"``, ``"5 6"`` (all numeric) and ``"100-102"`` (see ``_RANGE_RE``) send
    ``"100"``/``"5"``; a bare trailing ``"&"`` (``"100 &"`` in ``"STE 100 & STE
    101"``) is dropped.  Anything else — ``"PH 2"``, ``"2 REAR"``, ``"A-1"``,
    ``"C&F 1"`` — is one identifier, returned as is.
    """
    clean = [t.strip(",;") for t in tokens]
    joiner = next((i for i, t in enumerate(clean) if i and t.upper() in _LIST_JOINERS), None)
    if joiner is not None:
        tokens, clean = tokens[:joiner], clean[:joiner]
    first = clean
    if any(t[-1] in ",;" for t in tokens[:-1]):
        end = next(i for i, t in enumerate(tokens) if t[-1] in ",;")
        first = clean[: end + 1]
    elif len(clean) > 1 and all(t.isdigit() for t in clean):
        first = clean[:1]
    if len(first) == 1 and (m := _RANGE_RE.fullmatch(first[0])):
        low, high = m.groups()
        if len(low) == len(high) and int(low) < int(high):
            first = [low]
    return first


def full_secondary(std: StandardizedAddress) -> str | None:
    """The whole standardized ``address_line_2`` (stripped; blank → ``None``).

    Compare :func:`provider_secondary` against it: a difference means the
    provider was sent a narrower unit.
    """
    return (std.address_line_2 or "").strip() or None


def provider_secondary(std: StandardizedAddress) -> str | None:
    """Return the secondary-unit text to send to a validation provider.

    Non-US or non-Pub-28 input, and any line 2 that is not exactly the
    standardized unit slots, is sent as :func:`full_secondary`.
    """
    line2 = full_secondary(std)
    if line2 is None or std.country != "US" or std.components.spec != USPS_PUB28_SPEC:
        return line2

    values = std.components.values
    slots = [s for s in (_slot(values, ""), _slot(values, "dependent_")) if any(s)]
    rendered = [_render(s) for s in slots]
    if line2 == " ".join(rendered):
        ordered = slots
    elif line2 == " ".join(reversed(rendered)):
        ordered = slots[::-1]
    else:
        return line2

    units = [u for slot in ordered for u in _split_units(slot)]
    recognised = [u for u in units if u[0] in PUB28_DESIGNATORS]
    if not recognised:
        return None
    specific = [u for u in recognised if u[0] not in CONTAINER_DESIGNATORS] or recognised
    designator, tokens = specific[0]
    identifier = _first_identifier(tokens)
    if len(units) == 1 and identifier == [t.strip(",;") for t in tokens]:
        return line2
    return " ".join([designator, *identifier])
