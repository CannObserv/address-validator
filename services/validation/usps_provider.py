"""USPSProvider — validation backend backed by USPS Addresses API v3."""

import logging
from typing import Literal

from models import ComponentSet, ValidateRequestV1, ValidateResponseV1, ValidationResult
from services.validation._helpers import _build_validated_string
from services.validation.usps_client import USPSClient
from usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION

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
        logger.debug("USPSProvider.validate: calling USPS API, country=%s", request.country)
        raw = await self._client.validate_address(
            street_address=request.address,
            city=request.city,
            state=request.region,
            zip_code=request.postal_code,
        )

        dpv = raw.get("dpv_match_code")
        status = _DPV_TO_STATUS.get(dpv or "", "not_confirmed")

        address_line_1 = raw.get("address_line_1") or None
        address_line_2 = raw.get("address_line_2") or None
        city = raw.get("city") or None
        region = raw.get("region") or None
        postal_code = raw.get("postal_code") or None
        vacant = raw.get("vacant")

        # Only build components and validated string when we have a street.
        components: ComponentSet | None = None
        validated: str | None = None
        if address_line_1:
            comp_values: dict[str, str] = {
                k: v
                for k, v in {
                    "address_line_1": address_line_1,
                    "address_line_2": address_line_2 or "",
                    "city": city or "",
                    "region": region or "",
                    "postal_code": postal_code or "",
                    "vacant": vacant or "",
                }.items()
                if v
            }
            components = ComponentSet(
                spec=USPS_PUB28_SPEC,
                spec_version=USPS_PUB28_SPEC_VERSION,
                values=comp_values,
            )
            validated = _build_validated_string(
                address_line_1, address_line_2, city, region, postal_code
            )

        return ValidateResponseV1(
            address_line_1=address_line_1,
            address_line_2=address_line_2,
            city=city,
            region=region,
            postal_code=postal_code,
            country=request.country,
            validated=validated,
            components=components,
            validation=ValidationResult(
                status=status,
                dpv_match_code=dpv,  # type: ignore[arg-type]
                provider="usps",
            ),
        )
