"""Shared constants, exceptions, and utilities for v1 route handlers."""

import pycountry
from fastapi import status
from fastapi.responses import JSONResponse

from models import ErrorResponse

# ---------------------------------------------------------------------------
# Country validation
# ---------------------------------------------------------------------------

# ISO 3166-1 alpha-2 codes currently supported by this service.
# Extend as non-US parsing is added in future versions.
SUPPORTED_COUNTRIES: frozenset[str] = frozenset({"US"})

# Full set of valid ISO 3166-1 alpha-2 codes sourced from the pycountry
# library, which tracks the ISO 3166 Maintenance Agency's official dataset.
# This stays current automatically as pycountry is updated.
VALID_ISO2: frozenset[str] = frozenset(c.alpha_2 for c in pycountry.countries)

# ---------------------------------------------------------------------------
# Structured API errors
# ---------------------------------------------------------------------------


class APIError(Exception):
    """Raised by v1 route handlers to produce a structured ErrorResponse body.

    Caught by the ``api_error_handler`` registered in ``main.py``, which
    serialises it directly as the response body (no ``{"detail": ...}``
    wrapping).  The ``API-Version`` header is added by middleware.
    """

    def __init__(self, status_code: int, error: str, message: str) -> None:
        self.status_code = status_code
        self.error = error
        self.message = message


def api_error_response(exc: "APIError") -> JSONResponse:
    """Serialise *exc* to a :class:`JSONResponse` with the correct status code.

    Called from the exception handler registered in ``main.py``.
    Uses :class:`~models.ErrorResponse` directly to ensure the wire format
    stays in sync with the model schema.  ``models`` imports nothing from
    ``routers``, so there is no circular dependency.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content=ErrorResponse(
            error=exc.error,
            message=exc.message,
        ).model_dump(),
    )


def check_country(country: str) -> None:
    """Validate *country* against VALID_ISO2 and SUPPORTED_COUNTRIES.

    Raises :class:`APIError` with an appropriate status code and
    machine-readable error code if the value is invalid or unsupported.
    Does nothing when the country is valid and supported.
    """
    if country not in VALID_ISO2:
        raise APIError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            error="invalid_country_code",
            message=f"'{country}' is not a valid ISO 3166-1 alpha-2 country code.",
        )
    if country not in SUPPORTED_COUNTRIES:
        raise APIError(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            error="country_not_supported",
            message=f"Country '{country}' is not yet supported. Currently supported: US.",
        )
