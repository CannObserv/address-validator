# src/address_validator/services/spec.py
"""Spec identifiers for address component schemas.

Constants tag :class:`~models.ComponentSet` instances produced by the
parsing, standardisation, and validation pipeline, identifying the schema
their ``values`` keys conform to.

- ISO 19160-4 is the default v2 surface.
- USPS Pub 28 identifiers live in ``usps_data/spec.py`` alongside the
  lookup tables they describe.
"""

# ISO 19160-4 (Addressing — Digital interchange models for international
# address data) — 2020 edition.
ISO_19160_4_SPEC: str = "iso-19160-4"
ISO_19160_4_SPEC_VERSION: str = "2020"
