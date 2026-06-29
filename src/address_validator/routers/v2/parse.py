"""v2 parse endpoint — ISO 19160-4 component keys by default."""

from fastapi import APIRouter, Depends

from address_validator.auth import require_api_key
from address_validator.core.countries import check_country
from address_validator.core.errors import APIError
from address_validator.models import ErrorResponse, ParseRequest, ParseResponseV2
from address_validator.routers.deps import get_libpostal_client
from address_validator.services.component_profiles import (
    build_output_component_set,
    valid_component_profile,
)
from address_validator.services.libpostal_client import LibpostalClient, LibpostalUnavailableError
from address_validator.services.parser import parse_address

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
        "- `usps-pub28` — USPS Publication 28 snake_case names\n"
        "- `canada-post` — reserved; currently identical to `iso-19160-4`"
    ),
)
async def parse(
    req: ParseRequest,
    component_profile: str = Depends(valid_component_profile),
    libpostal_client: LibpostalClient | None = Depends(get_libpostal_client),
) -> ParseResponseV2:
    country = check_country(req.country)
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
    return ParseResponseV2(
        input=result.input,
        country=result.country,
        components=build_output_component_set(result.components, component_profile, result.country),
        type=result.type,
        warnings=result.warnings,
    )
