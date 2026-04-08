"""v2 parse endpoint — ISO 19160-4 component keys by default."""

from fastapi import APIRouter, Depends, Query, Request

from address_validator.auth import require_api_key
from address_validator.models import ComponentSet, ErrorResponse, ParseRequestV1, ParseResponseV2
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
    "/parse",
    response_model=ParseResponseV2,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
    summary="Parse address into ISO 19160-4 components",
)
async def parse(
    req: ParseRequestV1,
    request: Request,
    component_profile: str = Query(
        default="iso-19160-4",
        description=_COMPONENT_PROFILE_DESCRIPTION,
    ),
) -> ParseResponseV2:
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
    result = parse_address(req.address.strip(), country=req.country)
    std = standardize(
        result.components.values,
        country=result.country,
        upstream_warnings=result.warnings,
    )
    translated = translate_components(std.components.values, component_profile)
    return ParseResponseV2(
        input=result.input,
        country=result.country,
        components=ComponentSet(
            spec=std.components.spec,
            spec_version=std.components.spec_version,
            values=translated,
        ),
        type=result.type,
        warnings=std.warnings,
    )
