# src/address_validator/services/street_splitter.py
"""Bilingual street component splitter for Canadian addresses.

Decomposes libpostal's composite ``road`` token into ISO 19160-4
thoroughfare elements.  Handles English trailing-type addresses
(``Main St``) and French leading-type addresses (``rue des Lilas``)
as well as bilingual directionals.

Algorithm (left-to-right, position-aware):
  1. Normalise to uppercase; split on whitespace.
  2. Leading type check: if first token is a known leading/either type,
     extract as ``thoroughfare_leading_type``.
  3. Trailing directional check: if last token(s) match the bilingual
     directional table, extract as ``thoroughfare_post_direction``.
  4. Trailing type check (English): if last remaining token is a known
     trailing/either type, extract as ``thoroughfare_trailing_type``.
  5. Leading directional check: if first remaining token matches the
     directional table, extract as ``thoroughfare_pre_direction``.
  6. Remainder → ``thoroughfare_name``.
  7. Fallback: on any ambiguous construction store the full value in
     ``thoroughfare_name`` without splitting.

French articles (de, des, du, de la, de l') following a leading type
are left attached to the name — they are part of the street name, not
the type.
"""

from __future__ import annotations

from address_validator.canada_post_data.directionals import CA_DIRECTIONAL_MAP

# ---------------------------------------------------------------------------
# Street type table
# ---------------------------------------------------------------------------
# Position values:
#   "leading"  — French-style type before the name  (rue, chemin)
#   "trailing" — English-style type after the name  (street, road)
#   "either"   — valid in both positions             (avenue, boulevard)
#
# Values are the normalised Canada Post abbreviation.

_STREET_TYPES: dict[str, tuple[str, str]] = {
    # token_lower: (position, abbreviation)
    # French leading
    "rue": ("leading", "RUE"),
    "chemin": ("leading", "CH"),
    "côte": ("leading", "CÔTE"),
    "cote": ("leading", "CÔTE"),
    "montée": ("leading", "MONTÉE"),
    "montee": ("leading", "MONTÉE"),
    "rang": ("leading", "RANG"),
    "route": ("leading", "ROUT"),
    "voie": ("leading", "VOIE"),
    "allée": ("leading", "ALLÉE"),
    "allee": ("leading", "ALLÉE"),
    "impasse": ("leading", "IMP"),
    "ruelle": ("leading", "RUELLE"),
    "sentier": ("leading", "SENT"),
    "traverse": ("leading", "TRAV"),
    # English trailing
    "street": ("trailing", "ST"),
    "st": ("trailing", "ST"),
    "drive": ("trailing", "DR"),
    "dr": ("trailing", "DR"),
    "road": ("trailing", "RD"),
    "rd": ("trailing", "RD"),
    "lane": ("trailing", "LANE"),
    "ln": ("trailing", "LANE"),
    "court": ("trailing", "CRT"),
    "crt": ("trailing", "CRT"),
    "crescent": ("trailing", "CRES"),
    "cres": ("trailing", "CRES"),
    "way": ("trailing", "WAY"),
    "trail": ("trailing", "TRAIL"),
    "terrace": ("trailing", "TERR"),
    "heights": ("trailing", "HTS"),
    "hts": ("trailing", "HTS"),
    "close": ("trailing", "CLOSE"),
    "gate": ("trailing", "GATE"),
    "green": ("trailing", "GREEN"),
    "grove": ("trailing", "GROVE"),
    "heath": ("trailing", "HEATH"),
    "hollow": ("trailing", "HOLLOW"),
    "mews": ("trailing", "MEWS"),
    "park": ("trailing", "PARK"),
    "path": ("trailing", "PATH"),
    "rise": ("trailing", "RISE"),
    "run": ("trailing", "RUN"),
    "vale": ("trailing", "VALE"),
    "view": ("trailing", "VIEW"),
    "walk": ("trailing", "WALK"),
    "wood": ("trailing", "WOOD"),
    "woods": ("trailing", "WOODS"),
    # Either position
    "avenue": ("either", "AVE"),
    "ave": ("either", "AVE"),
    "boulevard": ("either", "BLVD"),
    "blvd": ("either", "BLVD"),
    "place": ("either", "PL"),
    "pl": ("either", "PL"),
    "promenade": ("either", "PROM"),
    "prom": ("either", "PROM"),
    "quai": ("either", "QUAI"),
    "square": ("either", "SQ"),
    "sq": ("either", "SQ"),
    "croissant": ("either", "CROIS"),
    "crois": ("either", "CROIS"),
    "esplanade": ("either", "ESPL"),
    "espl": ("either", "ESPL"),
    "passage": ("either", "PASS"),
    "pass": ("either", "PASS"),
    "terr": ("either", "TERR"),
    "circle": ("either", "CIRC"),
    "circ": ("either", "CIRC"),
    "bypass": ("either", "BYPASS"),
    "line": ("either", "LINE"),
    "concession": ("either", "CONC"),
    "conc": ("either", "CONC"),
}


_COMPOUND_DIRECTIONAL_MIN_TOKENS: int = 2


def _lookup_directional(token: str) -> str | None:
    return CA_DIRECTIONAL_MAP.get(token.lower().replace("-", ""))


def _lookup_type(token: str) -> tuple[str, str] | None:
    return _STREET_TYPES.get(token.lower())


def _extract_trailing_directional(tokens: list[str]) -> tuple[list[str], str | None]:
    """Try to remove a trailing directional from *tokens*, return (remaining, abbr)."""
    if len(tokens) >= _COMPOUND_DIRECTIONAL_MIN_TOKENS:
        compound = tokens[-2].lower() + tokens[-1].lower().replace("-", "")
        dir_abbr = CA_DIRECTIONAL_MAP.get(compound)
        if dir_abbr:
            return tokens[:-2], dir_abbr
        dir_abbr = _lookup_directional(tokens[-1])
        if dir_abbr:
            return tokens[:-1], dir_abbr
    elif len(tokens) == 1:
        dir_abbr = _lookup_directional(tokens[0])
        if dir_abbr:
            return [], dir_abbr
    return tokens, None


def split_road(road: str) -> dict[str, str]:
    """Split a libpostal ``road`` value into ISO 19160-4 thoroughfare elements.

    Returns a dict containing a subset of:
      - ``thoroughfare_leading_type``
      - ``thoroughfare_pre_direction``
      - ``thoroughfare_name``
      - ``thoroughfare_trailing_type``
      - ``thoroughfare_post_direction``

    Returns ``{}`` for empty input.  Unrecognised constructions fall back
    to storing the full value in ``thoroughfare_name``.
    """
    road = road.strip()
    if not road:
        return {}

    tokens = road.split()
    result: dict[str, str] = {}

    # --- Step 2: leading type check ---
    first = tokens[0]
    type_info = _lookup_type(first)
    if type_info and type_info[0] in ("leading", "either"):
        result["thoroughfare_leading_type"] = type_info[1]
        tokens = tokens[1:]

    # --- Step 3: trailing directional ---
    # Single-token directional is only extracted when no leading type was found.
    if len(tokens) == 1 and "thoroughfare_leading_type" in result:
        pass  # keep single token as the name
    else:
        tokens, dir_abbr = _extract_trailing_directional(tokens)
        if dir_abbr:
            result["thoroughfare_post_direction"] = dir_abbr

    # --- Step 4: trailing type (English) — only when no leading type found ---
    if tokens and "thoroughfare_leading_type" not in result:
        last = tokens[-1]
        type_info = _lookup_type(last)
        if type_info and type_info[0] in ("trailing", "either"):
            result["thoroughfare_trailing_type"] = type_info[1]
            tokens = tokens[:-1]

    # --- Step 5: leading directional (English) — only when no leading type found ---
    if tokens and "thoroughfare_leading_type" not in result:
        first = tokens[0]
        dir_abbr = _lookup_directional(first)
        if dir_abbr:
            result["thoroughfare_pre_direction"] = dir_abbr
            tokens = tokens[1:]

    # --- Step 6: remainder is thoroughfare_name ---
    if tokens:
        result["thoroughfare_name"] = " ".join(t.upper() for t in tokens)
    elif not result:
        # Nothing was parsed at all — store original as name (fallback)
        result["thoroughfare_name"] = road.upper()

    # --- Fallback: if nothing meaningful was extracted, use the full value as name ---
    if "thoroughfare_name" not in result and (
        "thoroughfare_leading_type" not in result
        and "thoroughfare_trailing_type" not in result
        and "thoroughfare_post_direction" not in result
        and "thoroughfare_pre_direction" not in result
    ):
        result = {"thoroughfare_name": road.upper()}

    return result
