"""DPV status mapping shared across validation providers."""

from typing import Literal

from address_validator.core.validation_status import (
    CONFIRMED,
    CONFIRMED_BAD_SECONDARY,
    CONFIRMED_MISSING_SECONDARY,
    NOT_CONFIRMED,
)

# Maps a USPS DPV match code to a validation status. Values are drawn from the
# single source of truth (core/validation_status.py); the drift test
# tests/unit/test_validation_status_catalogue.py asserts they stay a subset of
# VALIDATION_STATUSES.
_DPV_TO_STATUS: dict[
    str,
    Literal[
        "confirmed",
        "confirmed_missing_secondary",
        "confirmed_bad_secondary",
        "not_confirmed",
    ],
] = {
    "Y": CONFIRMED,
    "S": CONFIRMED_MISSING_SECONDARY,
    "D": CONFIRMED_BAD_SECONDARY,
    "N": NOT_CONFIRMED,
}
