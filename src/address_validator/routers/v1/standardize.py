"""v1 standardize endpoint."""

from fastapi import APIRouter, Depends

from address_validator.auth import require_api_key
from address_validator.models import (
    ComponentSet,
    ErrorResponse,
    StandardizeRequestV1,
    StandardizeResponseV1,
)
from address_validator.routers.v1.core import check_country
from address_validator.services.component_profiles import (
    translate_components,
    translate_components_to_iso,
)
from address_validator.services.parser import parse_address
from address_validator.services.standardizer import standardize

router = APIRouter(
    prefix="/api/v1",
    tags=["v1"],
    dependencies=[Depends(require_api_key)],
)


@router.post(
    "/standardize",
    response_model=StandardizeResponseV1,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
    summary="Standardize a US address per USPS Publication 28",
    description=(
        "Standardizes a US address using USPS Publication 28 abbreviation tables "
        "(suffixes, directionals, state codes, unit designators).\n\n"
        "US only. For CA support use `POST /api/v2/standardize`.\n\n"
        "Both input modes are supported:\n"
        "- `address` — raw string; parsed then standardized automatically.\n"
        "- `components` — pre-parsed USPS Pub 28 component dict; standardized only "
        "(parse step skipped).\n"
        "When both are supplied, `components` takes precedence."
    ),
)
async def standardize_address_v1(req: StandardizeRequestV1) -> StandardizeResponseV1:
    check_country(req.country)

    upstream_warnings: list[str] = []

    if req.components:
        comps = translate_components_to_iso(req.components, "usps-pub28")
    else:
        # model_validator guarantees address is non-blank when components is absent
        parse_result = await parse_address(req.address.strip(), country=req.country)  # type: ignore[union-attr]
        comps = parse_result.components.values
        upstream_warnings = parse_result.warnings

    result = standardize(comps, country=req.country, upstream_warnings=upstream_warnings)
    translated = translate_components(result.components.values, "usps-pub28")
    return StandardizeResponseV1(
        address_line_1=result.address_line_1,
        address_line_2=result.address_line_2,
        city=result.city,
        region=result.region,
        postal_code=result.postal_code,
        country=result.country,
        standardized=result.standardized,
        components=ComponentSet(
            spec=result.components.spec,
            spec_version=result.components.spec_version,
            values=translated,
        ),
        warnings=result.warnings,
    )
