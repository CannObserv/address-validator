"""Country validation constants and helpers used by the v2 routers."""

import pycountry
from fastapi import status

from address_validator.core.errors import APIError

# ---------------------------------------------------------------------------
# Country validation sets
# ---------------------------------------------------------------------------

# Countries supported by the API surface (US + CA via libpostal).
SUPPORTED_COUNTRIES: frozenset[str] = frozenset({"US", "CA"})

# Full set of valid ISO 3166-1 alpha-2 codes sourced from the pycountry
# library, which tracks the ISO 3166 Maintenance Agency's official dataset.
# This stays current automatically as pycountry is updated.
VALID_ISO2: frozenset[str] = frozenset(c.alpha_2 for c in pycountry.countries)


def check_country(country: str) -> str:
    """Validate and normalise *country* for v2 endpoints (US, CA).

    Returns the uppercased country code if valid and supported.
    Raises :class:`~address_validator.core.errors.APIError` otherwise.
    """
    country = country.upper()
    if country not in VALID_ISO2:
        raise APIError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            error="invalid_country_code",
            message="Country must be a valid ISO 3166-1 alpha-2 code.",
        )
    if country not in SUPPORTED_COUNTRIES:
        raise APIError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            error="country_not_supported",
            message=f"Currently supported: {', '.join(sorted(SUPPORTED_COUNTRIES))}.",
        )
    return country
