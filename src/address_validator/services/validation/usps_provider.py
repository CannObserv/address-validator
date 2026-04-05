"""USPSProvider — validation backend backed by USPS Addresses API v3."""

import logging

from address_validator.core.address_format import build_validated_string
from address_validator.models import (
    ComponentSet,
    StandardizeResponseV1,
    ValidateResponseV1,
    ValidationResult,
)
from address_validator.services.validation._helpers import _DPV_TO_STATUS
from address_validator.services.validation.usps_client import USPSClient
from address_validator.usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION

logger = logging.getLogger(__name__)


class USPSProvider:
    """Validates US addresses against the USPS Addresses API v3.

    Receives a fully normalised :class:`~models.StandardizeResponseV1` from the
    router (the result of the parse → standardize pipeline).  The
    ``address_line_1`` field carries the standardized street line sent to the
    USPS API.

    Constructed by :class:`~services.validation.registry.ProviderRegistry`; do not
    instantiate directly in application code.
    """

    def __init__(self, client: USPSClient) -> None:
        self._client = client

    @property
    def client(self) -> USPSClient:
        """Expose the client for quota state inspection."""
        return self._client

    async def validate(
        self, std: StandardizeResponseV1, *, raw_input: str | None = None
    ) -> ValidateResponseV1:
        logger.debug("USPSProvider.validate: calling USPS API, country=%s", std.country)
        raw = await self._client.validate_address(
            street_address=std.address_line_1,
            city=std.city,
            state=std.region,
            zip_code=std.postal_code,
        )

        dpv = raw.get("dpv_match_code")
        status = _DPV_TO_STATUS[dpv] if dpv is not None else "unavailable"

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
            validated = build_validated_string(
                address_line_1, address_line_2, city, region, postal_code
            )

        return ValidateResponseV1(
            address_line_1=address_line_1,
            address_line_2=address_line_2,
            city=city,
            region=region,
            postal_code=postal_code,
            country=std.country,
            validated=validated,
            components=components,
            validation=ValidationResult(
                status=status,
                dpv_match_code=dpv,  # type: ignore[arg-type]
                provider="usps",
            ),
            warnings=[],
        )
