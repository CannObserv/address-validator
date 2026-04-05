"""DPV status mapping shared across validation providers."""

from typing import Literal

_DPV_TO_STATUS: dict[
    str,
    Literal[
        "confirmed",
        "confirmed_missing_secondary",
        "confirmed_bad_secondary",
        "not_confirmed",
    ],
] = {
    "Y": "confirmed",
    "S": "confirmed_missing_secondary",
    "D": "confirmed_bad_secondary",
    "N": "not_confirmed",
}
