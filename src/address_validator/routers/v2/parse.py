"""v2 parse endpoint — ISO 19160-4 component keys by default."""

from fastapi import APIRouter, Depends, Query

from address_validator.auth import require_api_key
from address_validator.core.countries import check_country_v2
from address_validator.core.errors import APIError
from address_validator.models import ComponentSet, ErrorResponse, ParseRequestV1, ParseResponseV2
from address_validator.routers.deps import get_libpostal_client
from address_validator.services.component_profiles import (
    COMPONENT_PROFILE_DESCRIPTION,
    VALID_PROFILES,
    translate_components,
)
from address_validator.services.libpostal_client import LibpostalClient, LibpostalUnavailableError
from address_validator.services.parser import parse_address
from address_validator.services.spec import ISO_19160_4_SPEC, ISO_19160_4_SPEC_VERSION

router = APIRouter(
    prefix="/api/v2",
    tags=["v2"],
    dependencies=[Depends(require_api_key)],
)


@router.post(
    "/parse",
    response_model=ParseResponseV2,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
    summary="Parse address into ISO 19160-4 components",
    description=(
        "Parses a raw address string into labelled ISO 19160-4 components.\n\n"
        "Supported countries: **US** and **CA**. Other country codes → "
        "422 `country_not_supported`.\n\n"
        "**US** parsing uses the usaddress CRF model. "
        "**CA** parsing requires the libpostal sidecar (port 4400); "
        "returns HTTP 503 `parsing_unavailable` when the sidecar is unreachable.\n\n"
        "The `component_profile` query parameter controls the key vocabulary "
        "in `components.values`:\n"
        "- `iso-19160-4` (default) — ISO 19160-4 element names\n"
        "- `usps-pub28` — USPS Publication 28 snake_case names (v1 backward compat)\n"
        "- `canada-post` — reserved; currently identical to `iso-19160-4`"
    ),
)
async def parse(
    req: ParseRequestV1,
    component_profile: str = Query(
        default="iso-19160-4",
        description=COMPONENT_PROFILE_DESCRIPTION,
    ),
    libpostal_client: LibpostalClient | None = Depends(get_libpostal_client),
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
    country = check_country_v2(req.country)
    raw = req.address.strip()
    if not raw:
        raise APIError(
            status_code=400,
            error="address_required",
            message="address is required and must not be blank.",
        )
    try:
        result = await parse_address(raw, country=country, libpostal_client=libpostal_client)
    except LibpostalUnavailableError as exc:
        raise APIError(
            status_code=503,
            error="parsing_unavailable",
            message=(
                "Address parsing for CA is currently unavailable. "
                "Try again shortly or provide pre-parsed components via /validate."
            ),
        ) from exc
    translated = translate_components(result.components.values, component_profile)
    if component_profile == "usps-pub28":
        spec = result.components.spec
        spec_version = result.components.spec_version
    else:
        spec = ISO_19160_4_SPEC
        spec_version = ISO_19160_4_SPEC_VERSION
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
