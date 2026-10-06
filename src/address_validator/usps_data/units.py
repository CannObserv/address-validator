"""USPS Publication 28 - Secondary Unit Designator Abbreviations."""

UNIT_MAP: dict[str, str] = {
    "APARTMENT": "APT",
    "APT": "APT",
    "BASEMENT": "BSMT",
    "BSMT": "BSMT",
    "BUILDING": "BLDG",
    "BLDG": "BLDG",
    "BLD": "BLDG",
    "BLG": "BLDG",
    "DEPARTMENT": "DEPT",
    "DEPT": "DEPT",
    "FLOOR": "FL",
    "FL": "FL",
    "FLOORS": "FL",
    "FLR": "FL",
    "FRONT": "FRNT",
    "FRNT": "FRNT",
    "HANGAR": "HNGR",
    "HNGR": "HNGR",
    "KEY": "KEY",
    "LOBBY": "LBBY",
    "LBBY": "LBBY",
    "LOT": "LOT",
    "LOWER": "LOWR",
    "LOWR": "LOWR",
    "OFFICE": "OFC",
    "OFC": "OFC",
    "PENTHOUSE": "PH",
    "PH": "PH",
    "PIER": "PIER",
    "REAR": "REAR",
    "ROOM": "RM",
    "RM": "RM",
    "SIDE": "SIDE",
    "SLIP": "SLIP",
    "SPACE": "SPC",
    "SPC": "SPC",
    "STOP": "STOP",
    "SUITE": "STE",
    "STE": "STE",
    "SUITES": "STE",
    "STES": "STE",
    "SUTE": "STE",
    "TRAILER": "TRLR",
    "TRLR": "TRLR",
    "UNIT": "UNIT",
    "UN": "UNIT",
    "UNITS": "UNIT",
    "UPPER": "UPPR",
    "UPPR": "UPPR",
    "#": "#",
    "NUMBER": "#",
    "NUM": "#",
    "NO": "#",
}

# Canonical Pub 28 designators (the abbreviations UNIT_MAP normalises to).
PUB28_DESIGNATORS: frozenset[str] = frozenset(UNIT_MAP.values())

# Container designators (USPS Pub 28 secondary-unit hierarchy): these render
# before the specific unit on line 2 regardless of source order.  Other
# arguably-hierarchical Pub 28 designators (PIER, SLIP, STOP) were considered
# and deliberately excluded — pairs like "PIER 5 SLIP 3" have no dominant
# container convention, so they follow source order (GH #170 CR round 2).
CONTAINER_DESIGNATORS: frozenset[str] = frozenset({"BLDG", "FL"})
