"""USPS Publication 28 spec identifiers.

These constants tag :class:`~models.ComponentSet` instances produced by
the parser and standardizer, identifying the schema their ``values``
keys conform to.

Spec version
------------
``USPS_PUB28_SPEC_VERSION`` is the edition date of USPS Publication 28
(Postal Addressing Standards) as published on USPS Postal Explorer.
Confirmed October 2024 edition (PSN 7610-03-000-3688) via
``pe.usps.com/text/pub28/welcome.htm`` on 2026-04-09.
See ``docs/usps-pub28.md`` for additional research notes.
"""

USPS_PUB28_SPEC: str = "usps-pub28"
USPS_PUB28_SPEC_VERSION: str = "2024-10"
