"""The secondary-unit line sent to validation providers (GH #287).

A standardized US ``address_line_2`` can carry more than one unit: a chained
WA cannabis-licensee unit kept by #129 (``"SMP 2 STE J"``, ``"STE 110 SMP 2"``),
a container rendered before the specific unit by #170 (``"BLDG 1 STE 100"``),
or a same-level pair (``"UNIT 3 STE 4"``, ``"# 5 # 6"``).  USPS's
``secondaryAddress`` (and Google's CASS check) reads one unit, so the whole
line answers DPV ``D`` and is echoed back unmatched.

Providers are sent **one** Pub 28 unit instead: designators outside Pub 28
(``SMP``) are dropped, a specific unit beats a container (``BLDG``/``FL``),
and among equals the first unit on line 2 wins.  The standardized line 2 is
unchanged — this only narrows the provider request, so it needs no
``PIPELINE_CODE_VERSION`` bump.  ``_make_pattern_key`` includes the narrowed
unit, so cached answers to the full line are not reused.
"""

from address_validator.models import StandardizedAddress
from address_validator.usps_data.spec import USPS_PUB28_SPEC
from address_validator.usps_data.units import CONTAINER_DESIGNATORS, PUB28_DESIGNATORS

_Unit = tuple[str, list[str]]  # designator, identifier tokens


def _slot(values: dict[str, str], prefix: str) -> str:
    parts = (
        values.get(f"{prefix}sub_premise_type", ""),
        values.get(f"{prefix}sub_premise_number", ""),
    )
    return " ".join(p for p in parts if p)


def _split_units(slot: str) -> list[_Unit]:
    """Split one slot into units, breaking at an embedded ``#`` (``"# 5 # 6"``).

    usaddress tags a repeated ``#`` unit as one identifier (``"5 # 6"``).  Only
    ``#`` splits: other designator-like tokens are legitimate identifiers
    (``"APT PH 2"``, ``"FL 2 REAR"``).
    """
    designator, *tokens = slot.split()
    units: list[_Unit] = [(designator, [])]
    for i, tok in enumerate(tokens):
        if tok == "#" and units[-1][1] and i + 1 < len(tokens):
            units.append(("#", []))
        else:
            units[-1][1].append(tok.strip(",;"))
    return units


def provider_secondary(std: StandardizedAddress) -> str | None:
    """Return the secondary-unit text to send to a validation provider.

    Non-US or non-Pub-28 input, and any line 2 that is not exactly the
    standardized unit slots, is sent verbatim (stripped; blank → ``None``).
    """
    line2 = (std.address_line_2 or "").strip()
    if not line2 or std.country != "US" or std.components.spec != USPS_PUB28_SPEC:
        return line2 or None

    values = std.components.values
    unit, dependent = _slot(values, ""), _slot(values, "dependent_")
    slots = [s for s in (unit, dependent) if s]
    if line2 == " ".join(slots):
        ordered = slots
    elif line2 == " ".join(reversed(slots)):
        ordered = slots[::-1]
    else:
        return line2

    units = [u for slot in ordered for u in _split_units(slot)]
    recognised = [u for u in units if u[0] in PUB28_DESIGNATORS]
    if not recognised:
        return None
    if len(units) == 1:
        return line2
    specific = [u for u in recognised if u[0] not in CONTAINER_DESIGNATORS] or recognised
    designator, identifier = specific[0]
    return " ".join([designator, *identifier])


def secondary_narrowed(std: StandardizedAddress) -> bool:
    """True when providers are sent less than the full ``address_line_2``."""
    return provider_secondary(std) != ((std.address_line_2 or "").strip() or None)
