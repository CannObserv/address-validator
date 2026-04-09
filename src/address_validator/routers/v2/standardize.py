"""v2 standardize endpoint — ISO 19160-4 component keys by default."""

from fastapi import APIRouter, Depends, Query

from address_validator.auth import require_api_key
from address_validator.models import (
    ComponentSet,
    ErrorResponse,
    StandardizeRequestV1,
    StandardizeResponseV2,
)
from address_validator.routers.v1.core import APIError, check_country
from address_validator.services.component_profiles import VALID_PROFILES, translate_components
from address_validator.services.parser import parse_address
from address_validator.services.standardizer import standardize

router = APIRouter(
    prefix="/api/v2",
    tags=["v2"],
    dependencies=[Depends(require_api_key)],
)

_COMPONENT_PROFILE_DESCRIPTION = (
    "Component key vocabulary. "
    "`iso-19160-4` (default): ISO 19160-4 element names. "
    "`usps-pub28`: USPS Publication 28 snake_case names (v1 backward compat). "
    "`canada-post`: reserved; currently identical to `iso-19160-4`."
)


@router.post(
    "/standardize",
    response_model=StandardizeResponseV2,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
    summary="Standardize address per national postal profile",
)
async def standardize_address_v2(
    req: StandardizeRequestV1,
    component_profile: str = Query(
        default="iso-19160-4",
        description=_COMPONENT_PROFILE_DESCRIPTION,
    ),
) -> StandardizeResponseV2:
    if component_profile not in VALID_PROFILES:
        raise APIError(
            status_code=422,
            error="invalid_component_profile",
            message=(
                f"Unknown component_profile '{component_profile}'. "
                f"Valid values: {sorted(VALID_PROFILES)}."
            ),
        )
    check_country(req.country)

    upstream_warnings: list[str] = []

    if req.components:
        # v2 clients send ISO keys directly — no input translation needed
        comps = req.components
    else:
        # model_validator guarantees address is non-blank when components is absent
        parse_result = parse_address(req.address.strip(), country=req.country)  # type: ignore[union-attr]
        comps = parse_result.components.values
        upstream_warnings = parse_result.warnings

    result = standardize(comps, country=req.country, upstream_warnings=upstream_warnings)
    translated = translate_components(result.components.values, component_profile)
    if component_profile == "usps-pub28":
        spec = result.components.spec
        spec_version = result.components.spec_version
    else:
        spec = "iso-19160-4"
        spec_version = "2020"
    return StandardizeResponseV2(
        address_line_1=result.address_line_1,
        address_line_2=result.address_line_2,
        city=result.city,
        region=result.region,
        postal_code=result.postal_code,
        country=result.country,
        standardized=result.standardized,
        components=ComponentSet(
            spec=spec,
            spec_version=spec_version,
            values=translated,
        ),
        warnings=result.warnings,
    )
