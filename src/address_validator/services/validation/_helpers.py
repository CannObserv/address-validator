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
# VALIDATION_STATUSES and match the DPV tables in docs/VALIDATION-STATUS.md and
# docs/VALIDATION-PROVIDERS.md.
#
# Code meanings per the USPS spec (docs/usps-addresses-v3r2_4.yaml,
# DPVConfirmation); Google's uspsData.dpvConfirmation uses the same codes:
#   D — primary confirmed, secondary missing
#   S — primary confirmed, secondary present but not confirmed
# Swapped before GH #253; migration 022 relabelled the cached rows.
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
    "D": CONFIRMED_MISSING_SECONDARY,
    "S": CONFIRMED_BAD_SECONDARY,
    "N": NOT_CONFIRMED,
}
