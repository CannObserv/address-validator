"""Address standardization per USPS Publication 28 (US) and Canada Post (CA).

The public entry point is :func:`standardize`, which dispatches to the
country-specific implementation in :mod:`us` or :mod:`ca`.  Both paths share
the field-cleanup and address-line assembly helpers in :mod:`_lines` so the
two-space ``standardized`` separator and unit-ordering rules have a single
implementation.

``_get``, ``_lookup`` and ``_std_zip`` are re-exported for backward
compatibility with existing importers and tests.
"""

from address_validator.models import StandardizeResponseV2
from address_validator.services.standardizer._lines import _get, _lookup, _std_zip
from address_validator.services.standardizer.ca import standardize_ca
from address_validator.services.standardizer.us import standardize_us

__all__ = ["_get", "_lookup", "_std_zip", "standardize"]


def standardize(
    components: dict[str, str],
    country: str = "US",
    upstream_warnings: list[str] | None = None,
) -> StandardizeResponseV2:
    """Return a standardized address from parsed *components*.

    Dispatches to ``standardize_ca()`` for ``country="CA"`` and the USPS
    Pub 28 pipeline (``standardize_us()``) for ``country="US"`` (default).
    """
    warnings = list(upstream_warnings) if upstream_warnings else []
    if country == "CA":
        return standardize_ca(components, warnings)
    return standardize_us(components, country, warnings)
