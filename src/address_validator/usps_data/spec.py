"""USPS Publication 28 spec identifiers.

These constants tag :class:`~models.ComponentSet` instances produced by
the parser and standardizer, identifying the schema their ``values``
keys conform to.

Spec version
------------
The exact edition of USPS Publication 28 our ``usps_data/`` tables were
sourced from has not yet been verified against the USPS website.
``USPS_PUB28_SPEC_VERSION`` will be updated once the edition is confirmed.
See ``docs/usps-pub28.md`` for research notes and the verification procedure.
"""

USPS_PUB28_SPEC: str = "usps-pub28"
# TODO: pin edition once confirmed — see docs/usps-pub28.md
USPS_PUB28_SPEC_VERSION: str = "unknown"
