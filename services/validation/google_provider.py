"""GoogleProvider — validation backend backed by Google Address Validation API."""

import logging

from models import ComponentSet, StandardizeResponseV1, ValidateResponseV1, ValidationResult
from services.validation._helpers import _DPV_TO_STATUS, _build_validated_string
from services.validation.google_client import GoogleClient
from usps_data.spec import USPS_PUB28_SPEC, USPS_PUB28_SPEC_VERSION

logger = logging.getLogger(__name__)

_WARNING_INFERRED = "Provider inferred one or more address components not present in input"
_WARNING_REPLACED = "Provider replaced one or more address components"
_WARNING_UNCONFIRMED = "One or more address components are unconfirmed"


class GoogleProvider:
    """Validates US addresses against the Google Address Validation API.

    Receives a fully normalised :class:`~models.StandardizeResponseV1` from the
    router (the result of the parse → standardize pipeline).  The
    ``address_line_1`` field carries the standardized street line sent to the
    Google API.

    Uses ``enableUspsCass: true`` to obtain USPS CASS-certified DPV codes,
    making this a full drop-in replacement for :class:`USPSProvider` that
    additionally returns geocoordinates.

    Constructed by :func:`~services.validation.factory.get_provider`; do not
    instantiate directly in application code.
    """

    def __init__(self, client: GoogleClient) -> None:
        self._client = client

    async def validate(self, std: StandardizeResponseV1) -> ValidateResponseV1:
        logger.debug("GoogleProvider.validate: calling Google API, country=%s", std.country)
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
            components = ComponentSet(
                spec=USPS_PUB28_SPEC,
                spec_version=USPS_PUB28_SPEC_VERSION,
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
