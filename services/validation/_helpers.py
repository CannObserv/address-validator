"""Shared helpers for validation providers."""


def _build_validated_string(
    address_line_1: str | None,
    address_line_2: str | None,
    city: str | None,
    region: str | None,
    postal_code: str | None,
) -> str:
    """Build a single-line canonical address string.

    Uses two-space separators between logical address lines, matching
    the ``standardized`` field convention in ``StandardizeResponseV1``.

    Example output: ``"123 MAIN ST  APT 4  SPRINGFIELD, IL 62701-1234"``
    """
    if city and region:
        city_state = f"{city}, {region}"
    elif city:
        city_state = city
    elif region:
        city_state = region
    else:
        city_state = ""

    last_line = " ".join(p for p in (city_state, postal_code or "") if p)
    parts = [p for p in (address_line_1 or "", address_line_2 or "", last_line) if p]
    return "  ".join(parts)
