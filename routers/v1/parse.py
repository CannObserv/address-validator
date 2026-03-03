"""v1 parse endpoint."""

from fastapi import APIRouter, Depends, HTTPException, Response, status

from auth import require_api_key
from models import (
    ErrorResponse,
    ParseRequestV1,
    ParseResponseV1,
    _SUPPORTED_COUNTRIES,
    _VALID_ISO2,
)
from services.parser import parse_address

router = APIRouter(
    prefix="/api/v1",
    tags=["v1"],
    dependencies=[Depends(require_api_key)],
)


@router.post(
    "/parse",
    response_model=ParseResponseV1,
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
def parse_address_v1(req: ParseRequestV1, response: Response) -> ParseResponseV1:
    response.headers["API-Version"] = "1"

    country = req.country  # already uppercased by validator
    if country not in _VALID_ISO2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorResponse(
                error="invalid_country_code",
                message=f"'{country}' is not a valid ISO 3166-1 alpha-2 country code.",
            ).model_dump(),
        )
    if country not in _SUPPORTED_COUNTRIES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ErrorResponse(
                error="country_not_supported",
                message=f"Country '{country}' is not yet supported. Currently supported: US.",
            ).model_dump(),
        )

    raw = req.address.strip()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ErrorResponse(
                error="address_required",
                message="address is required and must not be blank.",
            ).model_dump(),
        )

    return parse_address(raw, country=country)
