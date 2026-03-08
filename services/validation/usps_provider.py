"""USPSProvider — validation backend backed by USPS Addresses API v3."""

import logging
from typing import Literal

from models import ValidateRequestV1, ValidateResponseV1
from services.validation.usps_client import USPSClient

logger = logging.getLogger(__name__)

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


class USPSProvider:
    """Validates US addresses against the USPS Addresses API v3.

    Constructed by :func:`~services.validation.factory.get_provider`; do not
    instantiate directly in application code.
    """

    def __init__(self, client: USPSClient) -> None:
        self._client = client

    async def validate(self, request: ValidateRequestV1) -> ValidateResponseV1:
        logger.debug(
            "USPSProvider.validate: calling USPS API, country=%s", request.country
        )
        raw = await self._client.validate_address(
            street_address=request.address,
            city=request.city,
            state=request.region,
            zip_code=request.postal_code,
        )

        dpv = raw.get("dpv_match_code")
        status = _DPV_TO_STATUS.get(dpv or "", "not_confirmed")

        return ValidateResponseV1(
            input_address=request.address,
            country=request.country,
            validation_status=status,
            provider="usps",
            dpv_match_code=dpv,  # type: ignore[arg-type]
            zip_plus4=raw.get("zip_plus4"),
            vacant=raw.get("vacant"),
            corrected_components=raw.get("corrected_components"),
        )
