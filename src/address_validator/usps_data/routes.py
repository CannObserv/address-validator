"""USPS route group and PO Box designations (Pub 28 sections 24, 25, 28).

Keys are cleaned input (uppercase, periods stripped); values are the Pub 28
output form.  Each table carries the Pub 28 forms plus common input variants
— the same convention as ``units.py``.  Military route groups (``PSC``,
``CMR``, ``UNIT``) are already Pub 28 forms and are not listed.
"""

# usaddress USPSBoxGroupType → Pub 28 route designator.
ROUTE_GROUP_TYPE_MAP: dict[str, str] = {
    # 241: "RR ## BOX ##" — not RURAL; 244: RFD and RD → RR.
    "RR": "RR",
    "R R": "RR",
    "RURAL ROUTE": "RR",
    "RURAL RT": "RR",
    "RURAL RTE": "RR",
    "RFD": "RR",
    "RD": "RR",
    "RURAL FREE DELIVERY": "RR",
    "RURAL DELIVERY": "RR",
    # 251: "HC ## BOX ##" — not HIGHWAY CONTRACT or ROUTE; 253: STAR ROUTE → HC.
    "HC": "HC",
    "HCR": "HC",
    "HIGHWAY CONTRACT": "HC",
    "HIGHWAY CONTRACT ROUTE": "HC",
    "HWY CONTRACT": "HC",
    "STAR ROUTE": "HC",
    "STAR RT": "HC",
    "STAR RTE": "HC",
}

# usaddress USPSBoxType → Pub 28 PO Box designator.  A bare "BOX" is absent:
# it is also the box on a rural or highway contract route ("RR 2 BOX 152").
BOX_TYPE_MAP: dict[str, str] = {
    # 281: "PO BOX ##".
    "PO BOX": "PO BOX",
    "P O BOX": "PO BOX",
    "POBOX": "PO BOX",
    "POB": "PO BOX",
    "P O B": "PO BOX",
    "POST OFFICE BOX": "PO BOX",
    # 283: CALLER, FIRM CALLER, BIN, LOCKBOX and DRAWER → PO BOX.
    "CALLER": "PO BOX",
    "FIRM CALLER": "PO BOX",
    "BIN": "PO BOX",
    "LOCKBOX": "PO BOX",
    "DRAWER": "PO BOX",
}
