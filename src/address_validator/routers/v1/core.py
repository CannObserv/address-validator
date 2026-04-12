"""Backward-compat re-exports — real definitions live in ``address_validator.core``.

This module is kept so that existing v1 callers continue to work without
changes.  All symbols are imported from their canonical locations in the
``core`` package and re-exported verbatim.
"""

from address_validator.core.countries import (
    SUPPORTED_COUNTRIES,
    SUPPORTED_COUNTRIES_V2,
    VALID_ISO2,
    check_country,
)
from address_validator.core.errors import APIError, api_error_response

__all__ = [
    "SUPPORTED_COUNTRIES",
    "SUPPORTED_COUNTRIES_V2",
    "VALID_ISO2",
    "APIError",
    "api_error_response",
    "check_country",
]
