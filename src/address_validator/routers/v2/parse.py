"""v2 parse endpoint — ISO 19160-4 component keys by default."""

from fastapi import APIRouter, Depends, Query

from address_validator.auth import require_api_key
from address_validator.models import ComponentSet, ErrorResponse, ParseRequestV1, ParseResponseV2
from address_validator.routers.v1.core import APIError, check_country
from address_validator.services.component_profiles import VALID_PROFILES, translate_components
from address_validator.services.parser import parse_address

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
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
    summary="Parse address into ISO 19160-4 components",
)
async def parse(
    req: ParseRequestV1,
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
    raw = req.address.strip()
    if not raw:
        raise APIError(
            status_code=400,
            error="address_required",
            message="address is required and must not be blank.",
        )
    result = parse_address(raw, country=req.country)
    translated = translate_components(result.components.values, component_profile)
    if component_profile == "usps-pub28":
        spec = result.components.spec
        spec_version = result.components.spec_version
    else:
        spec = "iso-19160-4"
        spec_version = "2020"
    return ParseResponseV2(
        input=result.input,
        country=result.country,
        components=ComponentSet(
            spec=spec,
            spec_version=spec_version,
            values=translated,
        ),
        type=result.type,
        warnings=result.warnings,
    )
