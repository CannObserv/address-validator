"""v2 standardize endpoint — ISO 19160-4 component keys by default."""

from fastapi import APIRouter, Depends

from address_validator.auth import require_api_key
from address_validator.core.countries import check_country
from address_validator.core.errors import raise_parsing_unavailable
from address_validator.models import (
    ErrorResponse,
    StandardizeRequest,
    StandardizeResponseV2,
)
from address_validator.routers.deps import get_libpostal_client
from address_validator.services.component_profiles import (
    build_output_component_set,
    valid_component_profile,
)
from address_validator.services.libpostal_client import LibpostalClient, LibpostalUnavailableError
from address_validator.services.parser import apply_parse_side_effects, parse_address
from address_validator.services.standardizer import standardize

router = APIRouter(
    prefix="/api/v2",
    tags=["v2"],
    dependencies=[Depends(require_api_key)],
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
        "- `usps-pub28` — USPS Publication 28 snake_case names\n"
        "- `canada-post` — reserved; currently identical to `iso-19160-4`\n\n"
        "CA responses always use `components.spec='canada-post'` regardless of profile."
    ),
)
async def standardize_address(
    req: StandardizeRequest,
    component_profile: str = Depends(valid_component_profile),
    libpostal_client: LibpostalClient | None = Depends(get_libpostal_client),
) -> StandardizeResponseV2:
    check_country(req.country)

    upstream_warnings: list[str] = []

    if req.components:
        # v2 clients send ISO keys directly — no input translation needed
        comps = req.components
    else:
        # model_validator guarantees address is non-blank when components is absent
        try:
            parse_outcome = await parse_address(  # type: ignore[union-attr]
                req.address.strip(), country=req.country, libpostal_client=libpostal_client
            )
        except LibpostalUnavailableError as exc:
            raise_parsing_unavailable(req.country, exc)
        apply_parse_side_effects(parse_outcome)
        parse_result = parse_outcome.response
        comps = parse_result.components.values
        upstream_warnings = parse_result.warnings

    result = standardize(comps, country=req.country, upstream_warnings=upstream_warnings)
    return StandardizeResponseV2(
        address_line_1=result.address_line_1,
        address_line_2=result.address_line_2,
        city=result.city,
        region=result.region,
        postal_code=result.postal_code,
        country=result.country,
        standardized=result.standardized,
        components=build_output_component_set(result.components, component_profile, req.country),
        warnings=result.warnings,
    )
