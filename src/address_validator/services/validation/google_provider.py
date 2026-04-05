"""GoogleProvider — validation backend backed by Google Address Validation API."""

import logging

from address_validator.models import (
    ComponentSet,
    StandardizeResponseV1,
    ValidateResponseV1,
    ValidationResult,
)
from address_validator.services.validation._helpers import _build_validated_string
from address_validator.services.validation.google_client import GoogleClient
from address_validator.usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION

logger = logging.getLogger(__name__)

_WARNING_INFERRED = "Provider inferred one or more address components not present in input"
_WARNING_REPLACED = "Provider replaced one or more address components"
_WARNING_UNCONFIRMED = "One or more address components are unconfirmed"


class GoogleProvider:
    """Validates addresses against the Google Address Validation API.

    Receives a fully normalised :class:`~models.StandardizeResponseV1` from the
    router (the result of the parse → standardize pipeline).  The
    ``address_line_1`` field carries the standardized street line sent to the
    Google API.

    Uses ``enableUspsCass: true`` to obtain USPS CASS-certified DPV codes,
    making this a full drop-in replacement for :class:`USPSProvider` that
    additionally returns geocoordinates.

    Constructed by :class:`~services.validation.registry.ProviderRegistry`; do not
    instantiate directly in application code.
    """

    def __init__(self, client: GoogleClient) -> None:
        self._client = client

    @property
    def client(self) -> GoogleClient:
        """Expose the client for quota state inspection."""
        return self._client

    async def validate(
        self, std: StandardizeResponseV1, *, raw_input: str | None = None
    ) -> ValidateResponseV1:
        logger.debug("GoogleProvider.validate: calling Google API, country=%s", std.country)
        raw = await self._client.validate_address(
            street_address=std.address_line_1,
            city=std.city,
            state=std.region,
            zip_code=std.postal_code,
            country=std.country,
        )

        status = raw["status"]
        dpv = raw.get("dpv_match_code")

        address_line_1 = raw.get("address_line_1") or None
        address_line_2 = raw.get("address_line_2") or None
        city = raw.get("city") or None
        region = raw.get("region") or None
        postal_code = raw.get("postal_code") or None
        vacant = raw.get("vacant")
        latitude = raw.get("latitude")
        longitude = raw.get("longitude")

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
            # US results follow USPS Pub 28; non-US results are raw Google components.
            if std.country == "US":
                comp_spec = USPS_PUB28_SPEC
                comp_spec_version = USPS_PUB28_SPEC_VERSION
            else:
                comp_spec = "raw"
                comp_spec_version = "1"
            components = ComponentSet(
                spec=comp_spec,
                spec_version=comp_spec_version,
                values=comp_values,
            )
            validated = _build_validated_string(
                address_line_1, address_line_2, city, region, postal_code
            )

        warnings: list[str] = []
        if raw.get("has_inferred_components"):
            warnings.append(_WARNING_INFERRED)
        if raw.get("has_replaced_components"):
            warnings.append(_WARNING_REPLACED)
        if raw.get("has_unconfirmed_components"):
            warnings.append(_WARNING_UNCONFIRMED)

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
                provider="google",
            ),
            latitude=latitude,
            longitude=longitude,
            warnings=warnings,
        )
