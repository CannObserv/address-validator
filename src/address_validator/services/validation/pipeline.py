"""Validation pipeline helpers — parse → standardize → provider selection.

These functions contain the business logic that was previously inline in the
v1 and v2 validate route handlers.  Routers call these functions and then
handle only HTTP-level concerns (error codes, headers, response model
construction).

Public API
----------
``build_non_us_std``          — build a passthrough StandardizedAddress for non-US components
``run_us_pipeline``           — US parse/standardize path; returns (std, raw_input, provider)
``run_non_us_pipeline_v1``    — v1 non-US path (components only, no CA libpostal)
``run_non_us_pipeline_v2``    — v2 non-US path (CA supports raw strings via libpostal)
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from address_validator.core.address_format import build_validated_string
from address_validator.core.countries import VALID_ISO2
from address_validator.core.errors import APIError
from address_validator.models import (
    ComponentSet,
    StandardizedAddress,
    StandardizeResponseV1,
)
from address_validator.services.component_profiles import translate_components_to_iso
from address_validator.services.libpostal_client import LibpostalUnavailableError
from address_validator.services.parser import parse_address
from address_validator.services.standardizer import standardize

if TYPE_CHECKING:
    from address_validator.models import ValidateRequestV1
    from address_validator.services.libpostal_client import LibpostalClient
    from address_validator.services.validation.registry import ProviderRegistry

logger = logging.getLogger(__name__)

# Type alias for what pipeline functions return.
# provider type is object because ValidationProvider is a Protocol (not a base class)
PipelineResult = tuple[StandardizedAddress, "str | None", object]


def build_non_us_std(components: dict[str, str], country: str) -> StandardizedAddress:
    """Build a passthrough StandardizedAddress from raw components for non-US addresses.

    Skips the USPS Pub 28 pipeline entirely.  Components are used verbatim.
    The ``components.spec`` is ``"raw"`` to indicate no standardization was applied.
    """
    address_line_1 = components.get("address_line_1", "")
    address_line_2 = components.get("address_line_2", "")
    city = components.get("city", "")
    region = components.get("region", "")
    postal_code = components.get("postal_code", "")
    standardized = build_validated_string(address_line_1, address_line_2, city, region, postal_code)
    return StandardizeResponseV1(
        address_line_1=address_line_1,
        address_line_2=address_line_2,
        city=city,
        region=region,
        postal_code=postal_code,
        country=country,
        standardized=standardized,
        components=ComponentSet(spec="raw", spec_version="1", values=components),
    )


async def run_us_pipeline(
    req: ValidateRequestV1,
    registry: ProviderRegistry,
    component_profile: str = "usps-pub28",
) -> PipelineResult:
    """Run the US parse → standardize pipeline and return the provider.

    Parameters
    ----------
    req:
        The validated request model.
    registry:
        Active ``ProviderRegistry`` instance.
    component_profile:
        Component vocabulary to use when translating pre-parsed ``components``.
        Defaults to ``"usps-pub28"`` (v1 behavior).  Pass ``"iso-19160-4"``
        for the v2 default.

    Returns
    -------
    ``(std, raw_input, provider)`` ready to pass to ``provider.validate()``.
    """
    upstream_warnings: list[str] = []

    if req.components:
        comps = translate_components_to_iso(req.components, component_profile)
        raw_input: str | None = json.dumps(req.components, separators=(",", ":"), ensure_ascii=True)
    else:
        # model_validator guarantees address is non-blank when components is absent
        parse_result = await parse_address(req.address.strip(), country=req.country)  # type: ignore[union-attr]
        comps = parse_result.components.values
        upstream_warnings = parse_result.warnings
        raw_input = req.address

    std = standardize(comps, country=req.country, upstream_warnings=upstream_warnings)
    provider = registry.get_provider()
    return std, raw_input, provider


async def run_non_us_pipeline_v1(
    req: ValidateRequestV1,
    registry: ProviderRegistry,
) -> PipelineResult:
    """Run the v1 non-US validation setup and return (std, raw_input, provider).

    v1 only supports pre-parsed ``components`` for non-US addresses.  Raw
    address strings are rejected with 422 ``country_not_supported``.

    Raises
    ------
    APIError
        422 ``invalid_country_code`` — unrecognised ISO 3166-1 alpha-2 code.
        422 ``country_not_supported`` — raw string supplied for non-US country.
        422 ``country_not_supported`` — active provider does not support non-US.
    """
    if req.country not in VALID_ISO2:
        raise APIError(
            status_code=422,
            error="invalid_country_code",
            message=f"'{req.country}' is not a valid ISO 3166-1 alpha-2 country code.",
        )
    if not req.components:
        raise APIError(
            status_code=422,
            error="country_not_supported",
            message=(
                "Raw address strings are only supported for US. "
                "Supply pre-parsed 'components' for non-US addresses."
            ),
        )
    provider = registry.get_provider()
    if not provider.supports_non_us:
        raise APIError(
            status_code=422,
            error="country_not_supported",
            message=(
                "Non-US address validation requires the Google provider. "
                "Set VALIDATION_PROVIDER=google or VALIDATION_PROVIDER=usps,google."
            ),
        )
    std: StandardizedAddress = build_non_us_std(req.components, req.country)
    raw_input: str | None = json.dumps(req.components, separators=(",", ":"), ensure_ascii=True)
    return std, raw_input, provider


async def run_non_us_pipeline_v2(
    req: ValidateRequestV1,
    registry: ProviderRegistry,
    libpostal_client: LibpostalClient | None,
) -> PipelineResult:
    """Run the v2 non-US validation setup and return (std, raw_input, provider).

    v2 supports pre-parsed ``components`` for all non-US countries, plus raw
    address strings for CA via the libpostal sidecar.  Other non-US raw strings
    are rejected with 422 ``country_not_supported``.

    Raises
    ------
    APIError
        422 ``invalid_country_code`` — unrecognised ISO 3166-1 alpha-2 code.
        422 ``country_not_supported`` — raw string supplied for non-CA non-US country.
        422 ``country_not_supported`` — active provider does not support non-US.
        503 ``parsing_unavailable`` — CA raw string, libpostal sidecar unreachable.
    """
    if req.country not in VALID_ISO2:
        raise APIError(
            status_code=422,
            error="invalid_country_code",
            message=f"'{req.country}' is not a valid ISO 3166-1 alpha-2 country code.",
        )
    if not req.components and req.country != "CA":
        raise APIError(
            status_code=422,
            error="country_not_supported",
            message=(
                "Raw address strings are only supported for US and CA. "
                "Supply pre-parsed 'components' for other countries."
            ),
        )
    provider = registry.get_provider()
    if not provider.supports_non_us:
        raise APIError(
            status_code=422,
            error="country_not_supported",
            message=(
                "Non-US address validation requires the Google provider. "
                "Set VALIDATION_PROVIDER=google or VALIDATION_PROVIDER=usps,google."
            ),
        )
    if req.components:
        std: StandardizedAddress = build_non_us_std(req.components, req.country)
        raw_input: str | None = json.dumps(req.components, separators=(",", ":"), ensure_ascii=True)
    else:
        # CA raw string: parse via libpostal then CA standardize
        try:
            parse_result = await parse_address(  # type: ignore[union-attr]
                req.address.strip(), country="CA", libpostal_client=libpostal_client
            )
        except LibpostalUnavailableError as exc:
            raise APIError(
                status_code=503,
                error="parsing_unavailable",
                message=(
                    "CA address parsing is currently unavailable. Provide pre-parsed components."
                ),
            ) from exc
        std = standardize(
            parse_result.components.values, country="CA", upstream_warnings=parse_result.warnings
        )
        raw_input = req.address
    return std, raw_input, provider
