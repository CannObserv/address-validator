"""DPV status mapping and unknown-value warnings shared across validation providers."""

import logging
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

# How much of an unrecognised DPV value reaches the log (GH #254). A DPV code
# is one character; the cap guards against an undocumented blob.
_UNKNOWN_DPV_LOG_CHARS = 8

# (logger name, signature) pairs already warned about. Spans the process
# lifetime so each is logged at most once; reset via _reset_warn_once() in tests.
_warned: set[tuple[str, str]] = set()


def _warn_once(logger: logging.Logger, signature: str, msg: str, *args: object) -> None:
    """Log *msg* at WARNING the first time *signature* is seen on *logger* (GH #254).

    #250 made an unexpected provider value silent: it no longer fails response
    validation, so nothing surfaces it. One line per distinct signature per
    process flags a contract change without flooding.
    """
    key = (logger.name, signature)
    if key in _warned:
        return
    _warned.add(key)
    logger.warning(msg, *args)


def _warn_unknown_dpv(logger: logging.Logger, field: str, dpv: str) -> None:
    """Warn once per distinct unrecognised DPV code, which maps to ``undetermined``.

    Logs the first :data:`_UNKNOWN_DPV_LOG_CHARS` characters and the length;
    a DPV code is not address content. *field* names the provider's response
    field, e.g. ``"uspsData.dpvConfirmation"``.
    """
    head = dpv[:_UNKNOWN_DPV_LOG_CHARS]
    _warn_once(
        logger,
        f"dpv={head!r}",
        "unrecognised %s %r (len=%d), mapped to undetermined",
        field,
        head,
        len(dpv),
    )


def _reset_warn_once() -> None:
    """Clear the warn-once dedup set — test-only hook (GH #254)."""
    _warned.clear()
