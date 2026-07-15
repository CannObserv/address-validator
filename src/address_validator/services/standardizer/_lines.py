"""Shared field-cleanup and address-line assembly helpers.

Used by both the US (USPS Pub 28) and CA (Canada Post) standardization
paths so the line-1/line-2/last-line rules — including the two-space
``standardized`` separator and unit ordering — have a single implementation.
"""

import re

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


# Container designators (USPS Pub 28 secondary-unit hierarchy): these render
# before the specific unit on line 2 regardless of source order.
_CONTAINER_DESIGNATORS: frozenset[str] = frozenset({"BLDG", "FL"})


def _sub_renders_first(components: dict[str, str], sub_type: str) -> bool:
    """Decide line-2 slot order: does the dependent (sub) unit render first?

    A container designator (BLDG/FL) always renders before the specific unit
    per USPS Pub 28 (``"BLDG C STE 120"``).  For same-level pairs there is no
    container relationship, so source order wins: *components* preserves
    insertion order (token order for parsed input, JSON key order for direct
    component input), and the slot whose keys appear first renders first
    (GH #170: ``"#108 STE B"`` must not invert to ``"STE B # 108"``).
    """
    if sub_type in _CONTAINER_DESIGNATORS:
        return True
    keys = list(components)

    def first_pos(*names: str) -> float:
        positions = [keys.index(n) for n in names if n in keys]
        return min(positions) if positions else float("inf")

    sub_pos = first_pos("dependent_sub_premise_type", "dependent_sub_premise_number")
    unit_pos = first_pos("sub_premise_type", "sub_premise_number")
    return sub_pos < unit_pos


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


def _assemble_lines(
    std: dict[str, str],
    unit_type: str,
    unit_id: str,
    sub_type: str,
    sub_id: str,
    *,
    sub_first: bool = True,
) -> tuple[str, str, str]:
    """Build the three address lines from the standardised component dict.

    Returns ``(line1, line2, last_line)``:

    - **line1** — street number + street name, or PO box.
    - **line2** — secondary-unit designators.  *sub_first* controls slot
      order: ``True`` (default) renders the dependent slot first — correct
      when it holds a larger container (USPS Pub 28: ``"BLDG C STE 120"``);
      callers pass :func:`_sub_renders_first` to preserve source order for
      same-level unit pairs (GH #170).
    - **last_line** — city, state/region, and postal code in single-line
      format (``"CITY, ST ZIP"``).
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
    # Slot order per *sub_first* (container-first by default; source order
    # for same-level pairs — see _sub_renders_first).
    if sub_first:
        ordered = (sub_type, sub_id, unit_type, unit_id)
    else:
        ordered = (unit_type, unit_id, sub_type, sub_id)
    line2 = " ".join(p for p in ordered if p)

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
