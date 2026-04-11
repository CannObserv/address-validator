"""v2 standardize endpoint — ISO 19160-4 component keys by default."""

from fastapi import APIRouter, Depends, Query, Request

from address_validator.auth import require_api_key
from address_validator.models import (
    ComponentSet,
    ErrorResponse,
    StandardizeRequestV1,
    StandardizeResponseV2,
)
from address_validator.routers.v1.core import APIError, check_country_v2
from address_validator.services.component_profiles import VALID_PROFILES, translate_components
from address_validator.services.libpostal_client import LibpostalUnavailableError
from address_validator.services.parser import parse_address
from address_validator.services.spec import ISO_19160_4_SPEC, ISO_19160_4_SPEC_VERSION
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
        503: {"model": ErrorResponse},
    },
    summary="Standardize address per national postal profile",
    description=(
        "Standardizes address components according to the national postal profile "
        "for the given country.\n\n"
        "Supported countries: **US** and **CA**. Other country codes → "
        "422 `country_not_supported`.\n\n"
        "Both input modes are supported:\n"
        "- `address` — raw string; parsed then standardized automatically.\n"
        "- `components` — pre-parsed ISO 19160-4 component dict; standardized only "
        "(parse step skipped).\n"
        "When both are supplied, `components` takes precedence.\n\n"
        "**US** standardization applies USPS Publication 28 abbreviation tables "
        "(suffixes, directionals, state codes, unit designators).\n\n"
        "**CA** standardization applies Canada Post tables "
        "(bilingual suffixes, province codes, postal code formatting). "
        "Raw string input requires the libpostal sidecar (port 4400); "
        "returns HTTP 503 `parsing_unavailable` when the sidecar is unreachable.\n\n"
        "The `component_profile` query parameter controls the key vocabulary "
        "in `components.values`:\n"
        "- `iso-19160-4` (default) — ISO 19160-4 element names\n"
        "- `usps-pub28` — USPS Publication 28 snake_case names (v1 backward compat)\n"
        "- `canada-post` — reserved; currently identical to `iso-19160-4`\n\n"
        "CA responses always use `components.spec='canada-post'` regardless of profile."
    ),
)
async def standardize_address_v2(
    req: StandardizeRequestV1,
    request: Request,
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
    check_country_v2(req.country)

    upstream_warnings: list[str] = []

    if req.components:
        # v2 clients send ISO keys directly — no input translation needed
        comps = req.components
    else:
        # model_validator guarantees address is non-blank when components is absent
        libpostal_client = getattr(request.app.state, "libpostal_client", None)
        try:
            parse_result = await parse_address(  # type: ignore[union-attr]
                req.address.strip(), country=req.country, libpostal_client=libpostal_client
            )
        except LibpostalUnavailableError as exc:
            raise APIError(
                status_code=503,
                error="parsing_unavailable",
                message=(
                    "Address parsing for CA is currently unavailable. "
                    "Try again shortly or provide pre-parsed components."
                ),
            ) from exc
        comps = parse_result.components.values
        upstream_warnings = parse_result.warnings

    result = standardize(comps, country=req.country, upstream_warnings=upstream_warnings)
    translated = translate_components(result.components.values, component_profile)
    if component_profile == "usps-pub28":
        spec = result.components.spec
        spec_version = result.components.spec_version
    elif req.country == "CA":
        # CA always uses canada-post spec regardless of component_profile
        spec = result.components.spec
        spec_version = result.components.spec_version
    else:
        spec = ISO_19160_4_SPEC
        spec_version = ISO_19160_4_SPEC_VERSION
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
