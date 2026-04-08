"""v2 countries format endpoint."""

from fastapi import APIRouter, Depends, Response
from fastapi import status as http_status

from address_validator.auth import require_api_key
from address_validator.models import CountryFormatResponseV2, ErrorResponse
from address_validator.routers.v1.core import VALID_ISO2, APIError
from address_validator.services.country_format import get_country_format

router = APIRouter(
    prefix="/api/v2",
    tags=["v2"],
    dependencies=[Depends(require_api_key)],
)

_CACHE_CONTROL = "public, max-age=86400"


@router.get(
    "/countries/{code}/format",
    response_model=CountryFormatResponseV2,
    responses={
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
    summary="Get per-country address field format",
    description=(
        "Returns per-country address field definitions including labels, "
        "required/optional state, region subdivision options, and postal code "
        "pattern.\n\n"
        "`{code}` is an ISO 3166-1 alpha-2 country code (case-insensitive).\n\n"
        "Fields absent from `fields` should be hidden in the UI. "
        "`options` is present on `region` when the country has a fixed "
        "list of provinces/states. "
        "`pattern` is a postal code regex hint when the country defines one."
    ),
)
async def get_country_format_v2(code: str, response: Response) -> CountryFormatResponseV2:
    country = code.strip().upper()

    if country not in VALID_ISO2:
        raise APIError(
            status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT,
            error="invalid_country_code",
            message=f"'{code}' is not a valid ISO 3166-1 alpha-2 country code.",
        )

    fmt = get_country_format(country)
    if fmt is None:
        raise APIError(
            status_code=http_status.HTTP_404_NOT_FOUND,
            error="country_format_not_found",
            message=f"No address format data available for country '{country}'.",
        )

    # Convert v1 response to v2 by extracting fields and creating new response
    response_v2 = CountryFormatResponseV2(
        country=fmt.country,
        fields=fmt.fields,
    )

    response.headers["Cache-Control"] = _CACHE_CONTROL
    return response_v2
