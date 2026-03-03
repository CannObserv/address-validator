"""USPS Publication 28 spec identifiers.

These constants tag :class:`~models.ComponentSet` instances produced by
the parser and standardizer, identifying the schema their ``values``
keys conform to.

The exact edition of USPS Publication 28 our ``usps_data/`` tables were
sourced from has not yet been verified against the USPS website.  Update
``USPS_PUB28_SPEC_VERSION`` once verified (see GitHub Epic #2).
"""

USPS_PUB28_SPEC: str = "usps-pub28"
USPS_PUB28_SPEC_VERSION: str = "unknown"
